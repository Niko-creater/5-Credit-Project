"""
Play RGB stream in a VRS file and add annotations.
Ctrl + Q: Quit and Save the annotation
Ctrl + P: Pause
Use input text box to add annotations.
"""

import time, json, queue, threading, tkinter as tk
from collections import deque
from datetime import timedelta

import cv2, numpy as np, rerun as rr
from pynput import keyboard as kb
from pyvrs.reader import SyncVRSReader

VRS_PATH   = "/home/peiyu/Projects/MR/vrs_2025_06_05/person_a.vrs"
OUT_JSON   = "annotations.json"
RGB_SID    = "214-1"
WINDOW_SEC = 10
RESIZE_TO  = (960, 540)
PLAY_SPEED = 1.0
ROTATE_90  = True

anno_q = queue.Queue()
def gui_thread():
    root = tk.Tk(); root.title("Annotation  (Ctrl+Enter)")
    txt = tk.Text(root, width=40, height=4); txt.pack(padx=4, pady=4)
    def send(_=None):
        s = txt.get("1.0", "end").strip()
        if s:
            anno_q.put(s); txt.delete("1.0", "end")
    tk.Button(root, text="Send", command=send).pack(pady=(0,4))
    root.bind('<Control-Return>', send)
    root.mainloop()
threading.Thread(target=gui_thread, daemon=True).start()

paused       = False
pause_start  = None
pause_accum  = 0.0
want_quit    = False
ctrl_down    = False
CTRL_KEYS    = {kb.Key.ctrl_l, kb.Key.ctrl_r}

def on_press(key):
    global paused, pause_start, pause_accum, want_quit, ctrl_down
    if key in CTRL_KEYS:
        ctrl_down = True
        return

    if ctrl_down and isinstance(key, kb.KeyCode) and key.char:
        ch = key.char.lower()
        if ch == 'p':
            paused = not paused
            if paused:
                pause_start = time.time()
                print("Paused (Ctrl+P resume)")
            else:
                pause_accum += time.time() - pause_start
                pause_start = None
                print("Resumed")
        elif ch == 'q':
            want_quit = True

def on_release(key):
    global ctrl_down
    if key in CTRL_KEYS:
        ctrl_down = False

kb.Listener(on_press=on_press, on_release=on_release).start()

rr.init("vrs_rgb_annotation")
rr.spawn(memory_limit="8GB")

reader   = SyncVRSReader(VRS_PATH)
cam_iter = iter(reader.filtered_by_fields(stream_ids=RGB_SID, record_types="data"))

base_ts   = None
wall0     = None
buf_deque = deque()
annotations = []

last_us  = None
last_fmt = None

print("Ctrl+P pause/resume · Ctrl+Q quit")

while True:
    if want_quit:
        print("Exiting…")
        break

    if paused:
        try:
            txt = anno_q.get_nowait()
        except queue.Empty:
            txt = None
        if txt and last_us is not None:
            rr.log("cam/notes", rr.TextDocument(txt))
            annotations.append(
                {"time": last_fmt,
                 "elapsed_ms": int(last_us/1000),
                 "text": txt})
            print(f"📝 @ {last_fmt} : {txt}")
        time.sleep(0.05)
        continue

    try:
        rec = next(cam_iter)
    except StopIteration:
        print("VRS finished.")
        break

    if base_ts is None:
        base_ts = rec.timestamp
        wall0   = time.time()

    rel_play = (rec.timestamp - base_ts) / PLAY_SPEED
    real_elapsed = time.time() - wall0 - pause_accum
    wait = rel_play - real_elapsed
    if wait > 0:
        time.sleep(wait)

    rel_real = rec.timestamp - base_ts
    last_fmt = str(timedelta(seconds=rel_real))[:-3]
    last_us  = int(rel_real * 1e6)
    rr.set_time("vrs_time_us", timestamp=last_us)

    blk = rec.image_blocks[0]
    buf = blk if isinstance(blk, np.ndarray) else np.frombuffer(blk, np.uint8)
    bgr = cv2.imdecode(buf, cv2.IMREAD_COLOR)
    if bgr is None:
        continue
    img = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
    if ROTATE_90:
        img = cv2.rotate(img, cv2.ROTATE_90_CLOCKWISE)
    # if RESIZE_TO:
    #     img = cv2.resize(img, RESIZE_TO, interpolation=cv2.INTER_LINEAR)

    if WINDOW_SEC > 0:
        buf_deque.append(last_us)
        th = last_us - WINDOW_SEC*1_000_000
        if buf_deque[0] < th:
            rr.log("cam", rr.Clear(recursive=True))
            buf_deque = deque(t for t in buf_deque if t >= th)

    rr.log("cam/frame", rr.Image(img))

    try:
        txt = anno_q.get_nowait()
    except queue.Empty:
        txt = None
    if txt:
        rr.log("cam/notes", rr.TextDocument(txt))
        annotations.append(
            {"time": last_fmt,
             "elapsed_ms": int(rel_real*1000),
             "text": txt})
        print(f"📝 @ {last_fmt} : {txt}")

with open(OUT_JSON, "w", encoding="utf-8") as f:
    json.dump(annotations, f, ensure_ascii=False, indent=2)
print(f"Saved {len(annotations)} annotations → {OUT_JSON}")
