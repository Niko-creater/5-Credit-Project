from __future__ import annotations
import time, uuid, json, os, tempfile, cv2, numpy as np
from dataclasses import dataclass, field

import gradio as gr
import rerun as rr
import rerun.blueprint as rrb
from packaging import version
from gradio_rerun import Rerun
from pyvrs.reader import SyncVRSReader

TARGET_VER = "0.23.3"
if version.parse(rr.__version__) != version.parse(TARGET_VER):
    raise RuntimeError(
        f"rerun {rr.__version__} detected; install {TARGET_VER}:\n"
        f"    pip install --force-reinstall 'rerun-sdk=={TARGET_VER}'"
    )

RGB_SID    = "214-1"
PLAY_SPEED = 1.0
ROTATE_90  = True

@dataclass
class PlaybackCtx:
    vrs_path: str
    duration_ms: int
    rec: rr.RecordingStream
    stream: rr.internal.io.BinaryLogStream
    paused: bool = False
    pause_start: float | None = None
    pause_acc: float = 0.0
    quit: bool = False
    base_ts: float | None = None     
    wall0: float | None = None       
    last_us: int | None = None
    last_fmt: str | None = None
    annotations: list[dict] = field(default_factory=list)

CTX: dict[str, PlaybackCtx] = {}     


def get_duration_ms(vrs_path: str, stream_id: str) -> int:
    reader = SyncVRSReader(vrs_path)
    cam_iter = reader.filtered_by_fields(stream_ids=stream_id, record_types="data")
    first, last = None, None
    for rec in cam_iter:
        first = rec.timestamp if first is None else first
        last = rec.timestamp
    if first is None or last is None:
        return 0
    return int((last - first) * 1000)


def stream_vrs(rec_id: str, vrs_file):
    if vrs_file is None:
        yield None, gr.Slider.update()   # keep UI idle
        return

    duration_ms = get_duration_ms(vrs_file.name, RGB_SID)
    rec = rr.RecordingStream(application_id="vrs_rgb_web", recording_id=rec_id)
    rec.send_blueprint(
        rrb.Blueprint(rrb.Spatial2DView(origin="cam"), collapse_panels=True)
    )
    ctx = PlaybackCtx(
        vrs_path=vrs_file.name,
        duration_ms=duration_ms,
        rec=rec,
        stream=rec.binary_stream(),
    )
    CTX[rec_id] = ctx

    reader   = SyncVRSReader(vrs_file.name)
    cam_iter = iter(reader.filtered_by_fields(stream_ids=RGB_SID, record_types="data"))

    for item in cam_iter:
        if ctx.quit:
            break
        if ctx.paused:
            time.sleep(0.05)
            yield ctx.stream.read(), gr.Slider.update(value=(ctx.last_us or 0)//1000)
            continue

        if ctx.base_ts is None:
            ctx.base_ts = item.timestamp
            ctx.wall0   = time.time()
        rel_play     = (item.timestamp - ctx.base_ts) / PLAY_SPEED
        real_elapsed = time.time() - ctx.wall0 - ctx.pause_acc
        wait = rel_play - real_elapsed
        if wait > 0:
            time.sleep(wait)

        rel_us       = int((item.timestamp - ctx.base_ts) * 1e6)
        ctx.last_us  = rel_us
        ctx.last_fmt = time.strftime("%H:%M:%S", time.gmtime(rel_us/1e6)) + f".{rel_us%1_000_000:06d}"[:3]

        blk = item.image_blocks[0]
        buf = blk if isinstance(blk, np.ndarray) else np.frombuffer(blk, np.uint8)
        bgr = cv2.imdecode(buf, cv2.IMREAD_COLOR)
        if bgr is None:
            continue
        img = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
        if ROTATE_90:
            img = cv2.rotate(img, cv2.ROTATE_90_CLOCKWISE)

        # ---- log & yield ----
        ctx.rec.set_time("vrs_time_us", timestamp=rel_us)
        ctx.rec.log("cam/frame", rr.Image(img))
        yield ctx.stream.read(), gr.Slider.update(value=rel_us//1000, maximum=duration_ms)

    CTX.pop(rec_id, None)   # cleanup


# -------- UI callbacks --------
def toggle_pause(rec_id: str):
    ctx = CTX.get(rec_id)
    if ctx is None:
        return "Pause"
    ctx.paused = not ctx.paused
    if ctx.paused:
        ctx.pause_start = time.time()
        return "Resume"
    else:
        ctx.pause_acc += time.time() - ctx.pause_start
        ctx.pause_start = None
        return "Pause"

def add_annotation(rec_id: str, text: str, slider_ms: int):
    text = text.strip()
    ctx  = CTX.get(rec_id)
    if not text or ctx is None:
        return ""
    timestamp_us = slider_ms * 1000
    # Ensure within clip
    timestamp_us = max(0, min(timestamp_us, ctx.duration_ms*1000))
    ctx.rec.set_time("vrs_time_us", timestamp=timestamp_us)
    ctx.rec.log("cam/notes", rr.TextDocument(text))
    hms = time.strftime("%H:%M:%S", time.gmtime(timestamp_us/1e6)) + f".{timestamp_us%1_000_000:06d}"[:3]
    ctx.annotations.append(
        {"time": hms,
         "elapsed_ms": slider_ms,
         "text": text}
    )
    return ""

def quit_and_save(rec_id: str):
    ctx = CTX.get(rec_id)
    if ctx is None:
        return None
    ctx.quit = True
    fd, path = tempfile.mkstemp(prefix="annotations_", suffix=".json")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(ctx.annotations, f, ensure_ascii=False, indent=2)
    return path


# ------------- Gradio Interface -------------
with gr.Blocks(title="VRS RGB Player with Annotations") as demo:
    gr.Markdown("### VRS RGB Player · Pause / Scrub / Annotate · Export JSON")

    with gr.Row():
        vrs_file = gr.File(label="Choose .vrs file")
        play_btn = gr.Button("Play", variant="primary")
        pause_btn = gr.Button("Pause")
        quit_btn  = gr.Button("Quit & Save", variant="stop")

    timeline = gr.Slider(
        minimum=0, maximum=1, step=1, value=0,
        label="Timeline (ms)",
        interactive=True
    )

    viewer = Rerun(
        streaming=True,
        height=480,
        panel_states={"time": "collapsed", "blueprint": "hidden", "selection": "hidden"},
    )

    with gr.Row():
        txt_box = gr.Textbox(lines=2, placeholder="Enter annotation text …")
        send_btn = gr.Button("Send")

    anno_json = gr.File(label="annotations.json (after Quit)")

    rec_state = gr.State(str(uuid.uuid4()))

    play_btn.click(
        stream_vrs,
        inputs=[rec_state, vrs_file],
        outputs=[viewer, timeline],
    )
    pause_btn.click(toggle_pause, rec_state, pause_btn)
    send_btn.click(
        add_annotation,
        inputs=[rec_state, txt_box, timeline],
        outputs=txt_box,
    )
    txt_box.submit(
        add_annotation,
        inputs=[rec_state, txt_box, timeline],
        outputs=txt_box,
    )
    quit_btn.click(
        quit_and_save,
        inputs=rec_state,
        outputs=anno_json,
    )

if __name__ == "__main__":
    demo.launch(ssr_mode=False)
