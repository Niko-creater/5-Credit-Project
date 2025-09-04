#!/usr/bin/env python3
"""
Web Live Annotator (Flask) — split templates/static + recording
- Start → begin MP4 recording and set t0
- Stop  → stop recording, save JSON + MP4, then shut server down

Install:
  pip install -r requirements.txt
Run:
  python app.py --source rtsp://192.168.0.12:8080/h264_ulaw.sdp --fps 25 --host 0.0.0.0
  # open http://127.0.0.1:5000
"""

import argparse
import json
import os
import threading
import time
from datetime import datetime, timezone
from typing import Optional, List, Dict

import cv2
import numpy as np
from flask import Flask, Response, request, jsonify, render_template

def now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def ts_filename(prefix: str, suffix: str) -> str:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"{prefix}{ts}{suffix}"

class FrameGrabber:
    def __init__(self, source: str, reconnect_delay: float = 1.0, desired_width: Optional[int] = None):
        self.source = source
        self.reconnect_delay = reconnect_delay
        self.desired_width = desired_width
        self._cap: Optional[cv2.VideoCapture] = None
        self._thread: Optional[threading.Thread] = None
        self._stop_evt = threading.Event()
        self._frame_lock = threading.Lock()
        self._latest_bgr: Optional[np.ndarray] = None
        self._status: str = "idle"  # idle | connecting | streaming | error

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop_evt.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop_evt.set()
        if self._thread:
            self._thread.join(timeout=2.0)
        self._release_cap()

    def _release_cap(self):
        if self._cap is not None:
            try:
                self._cap.release()
            except Exception:
                pass
            self._cap = None

    def _open_cap(self) -> bool:
        self._status = "connecting"
        cap = cv2.VideoCapture(self.source)
        if not cap.isOpened():
            self._status = "error"
            return False
        # prime first frame for some sources
        _ = cap.read()
        self._cap = cap
        self._status = "streaming"
        return True

    def _run(self):
        while not self._stop_evt.is_set():
            if self._cap is None:
                if not self._open_cap():
                    time.sleep(self.reconnect_delay)
                    continue
            ok, frame = self._cap.read()
            if not ok or frame is None:
                self._status = "error"
                self._release_cap()
                time.sleep(self.reconnect_delay)
                continue
            if self.desired_width and frame.shape[1] != self.desired_width:
                scale = self.desired_width / frame.shape[1]
                h = int(round(frame.shape[0] * scale))
                frame = cv2.resize(frame, (self.desired_width, h), interpolation=cv2.INTER_AREA)
            with self._frame_lock:
                self._latest_bgr = frame

    def get_latest_frame(self) -> Optional[np.ndarray]:
        with self._frame_lock:
            return None if self._latest_bgr is None else self._latest_bgr.copy()

    def get_latest_jpeg(self, quality: int = 80) -> Optional[bytes]:
        frame = self.get_latest_frame()
        if frame is None:
            return None
        ok, enc = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), int(quality)])
        if not ok:
            return None
        return enc.tobytes()

    @property
    def status(self) -> str:
        return self._status

class Recorder:
    def __init__(self, frame_source: FrameGrabber, fps: float = 25.0, out_dir: str = ".", prefix: str = "recording_"):
        self.src = frame_source
        self.fps = float(fps)
        self.out_dir = out_dir
        self.prefix = prefix
        self.path: Optional[str] = None
        self._writer: Optional[cv2.VideoWriter] = None
        self._thread: Optional[threading.Thread] = None
        self._stop_evt = threading.Event()
        self._active = False

    def start(self):
        if self._active:
            return
        os.makedirs(self.out_dir, exist_ok=True)
        self.path = os.path.join(self.out_dir, ts_filename(self.prefix, ".mp4"))
        self._stop_evt.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        self._active = True

    def stop(self):
        if not self._active:
            return
        self._stop_evt.set()
        if self._thread:
            self._thread.join(timeout=2.0)
        if self._writer is not None:
            try:
                self._writer.release()
            except Exception:
                pass
            self._writer = None
        self._active = False

    @property
    def active(self) -> bool:
        return self._active

    def _open_writer_if_needed(self, frame: np.ndarray):
        if self._writer is not None:
            return
        h, w = frame.shape[:2]
        fourcc = cv2.VideoWriter_fourcc(*"mp4v")
        self._writer = cv2.VideoWriter(self.path, fourcc, self.fps, (w, h))

    def _run(self):
        target_dt = 1.0 / max(1e-6, self.fps)
        next_t = time.perf_counter()
        while not self._stop_evt.is_set():
            frame = self.src.get_latest_frame()
            if frame is None:
                time.sleep(0.01)
                continue
            self._open_writer_if_needed(frame)
            if self._writer is not None:
                self._writer.write(frame)
            next_t += target_dt
            sleep_for = next_t - time.perf_counter()
            if sleep_for > 0:
                time.sleep(min(sleep_for, 0.2))
            else:
                next_t = time.perf_counter()

class Session:
    def __init__(self, source: str):
        self.source = source
        self.started_at_iso: Optional[str] = None
        self.t0_monotonic: Optional[float] = None
        self.annotations: List[Dict] = []
        self.recorder: Optional[Recorder] = None
        self.recording_path: Optional[str] = None

    def start(self, recorder: Recorder):
        self.started_at_iso = now_utc_iso()
        self.t0_monotonic = time.perf_counter()
        self.annotations.clear()
        self.recorder = recorder
        self.recorder.start()
        self.recording_path = recorder.path

    def elapsed(self) -> float:
        if self.t0_monotonic is None:
            return 0.0
        return max(0.0, time.perf_counter() - self.t0_monotonic)

    def add(self, text: str) -> Dict:
        item = {"t": float(self.elapsed()), "text": str(text)}
        self.annotations.append(item)
        return item

    def undo(self) -> Optional[Dict]:
        if not self.annotations:
            return None
        return self.annotations.pop()

    def to_json(self) -> Dict:
        return {
            "video_source": self.source,
            "started_at": self.started_at_iso,
            "duration_sec": self.elapsed(),
            "annotations": self.annotations,
            "recording": self.recording_path,
        }

def create_app(source: str, width: Optional[int] = 960, fps: float = 25.0):
    app = Flask(__name__, static_folder="static", template_folder="templates")
    # Disable aggressive static caching in dev
    app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 0

    grabber = FrameGrabber(source, desired_width=width)
    grabber.start()

    session = Session(source=source)

    @app.get("/")
    def index():
        return render_template("index.html", source=source)

    @app.get("/snap.jpg")
    def snap():
        jpg = grabber.get_latest_jpeg(quality=85)
        if jpg is None:
            return Response("no frame yet", status=503)
        return Response(jpg, mimetype="image/jpeg", headers={
            "Cache-Control": "no-cache, no-store, must-revalidate"
        })

    @app.get("/stream.mjpg")
    def stream():
        def gen():
            boundary = "frame"
            sep = f"--{boundary}\r\n".encode("ascii")
            while True:
                jpg = grabber.get_latest_jpeg(quality=80)
                if jpg is None:
                    time.sleep(0.05)
                    continue
                yield (sep +
                       b"Content-Type: image/jpeg\r\n\r\n" +
                       jpg + b"\r\n")
                # 控制节流（约25fps），避免CPU占用过高
                time.sleep(0.04)

        headers = {
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0",
        }
        return Response(
            gen(),
            mimetype="multipart/x-mixed-replace; boundary=frame",
            headers=headers,
        )

    @app.get("/status")
    def status():
        recording = session.recorder.active if (session.recorder and session.recorder.active) else False
        return jsonify({
            "server_time": now_utc_iso(),
            "grabber_status": grabber.status,
            "source": session.source,
            "started_at": session.started_at_iso,
            "elapsed": session.elapsed(),
            "count": len(session.annotations),
            "annotations": session.annotations,
            "recording": recording,
            "recording_path": session.recording_path,
        })

    @app.post("/start")
    def start_session():
        rec = Recorder(grabber, fps=fps, out_dir=".")
        session.start(rec)
        return jsonify({"started_at": session.started_at_iso, "recording_path": session.recording_path})

    @app.post("/annotate")
    def annotate():
        data = request.get_json(silent=True) or {}
        text = str(data.get("text", "")).strip()
        if text == "":
            return jsonify({"error": "text is empty"}), 400
        item = session.add(text)
        return jsonify(item)

    @app.post("/stop")
    def stop_and_quit():
        if session.recorder and session.recorder.active:
            session.recorder.stop()
        payload = session.to_json()
        json_path = os.path.join(os.path.abspath("."), ts_filename("annotations_", ".json"))
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        video_path = session.recording_path

        grabber.stop()
        func = request.environ.get('werkzeug.server.shutdown')
        def _shutdown():
            try:
                func()
            except Exception:
                pass
        threading.Timer(0.5, _shutdown).start()

        return jsonify({"ok": True, "json_path": json_path, "video_path": video_path, "count": len(payload["annotations"]), "duration_sec": payload["duration_sec"]})

    return app


def main():
    parser = argparse.ArgumentParser(description="Web Live Annotator (Flask) — split templates/static")
    parser.add_argument('--source', required=True, help='RTSP/MJPEG/HTTP video URL, e.g., rtsp://... or http://.../video')
    parser.add_argument('--host', default='127.0.0.1', help='Host to bind (default 127.0.0.1)')
    parser.add_argument('--port', type=int, default=5000, help='Port to bind (default 5000)')
    parser.add_argument('--width', type=int, default=960, help='Resize stream width for the browser (keep aspect)')
    parser.add_argument('--fps', type=float, default=25.0, help='Recording FPS (default 25)')
    args = parser.parse_args()

    app = create_app(source=args.source, width=args.width, fps=args.fps)
    app.run(host=args.host, port=args.port, threaded=True)


if __name__ == '__main__':
    main()
