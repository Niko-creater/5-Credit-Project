## 5 Credit Project

#### Dependencies Installation
```
pip install numpy opencv-python rerun-sdk gradio packaging pynput pyvrs gradio-rerun flask
```
#### Code files Description
* vrs_annotate.py
  * Annotation code for VRS running in a Linux environment
* vrs_annotate_v2.py
  * Annotation code for VRS running in a Mac environment 
* vrs_annotate_web.py
  * Web version annotation code for VRS 
* app.py
  * Real time stream web flask application code

### Web Live Annotator (Flask)

#### Architecture & Data Flow

```
Android (IP Webcam / RTSP-capable App)
        │ (RTSP or MJPEG)
        ▼
[Flask backend app.py]
  ├─ FrameGrabber thread: pulls frames via OpenCV
  ├─ Recorder thread: writes MP4 locally at target FPS
  ├─ Session: holds t0 and annotations; exports JSON
  └─ /stream.mjpg: serves latest frames as MJPEG for the browser
        │
        ▼
Browser UI (/) → Start / Annotate / Stop → calls /start /annotate /stop /status
```

**Primary endpoints**  
- `GET /` — Web UI  
- `GET /stream.mjpg` — MJPEG stream for the preview  
- `GET /status` — current state (`elapsed`, `count`, `annotations`, `recording` flag)  
- `POST /start` — start session + start recording  
- `POST /annotate` — add text annotation `{"text": "..."}`  
- `POST /stop` — stop, save JSON and MP4, then shut down

---

#### Android Phone Setup (IP Webcam)

1. Install **IP Webcam** from the Play Store.
2. Connect **phone and computer to the same Wi‑Fi** (ideally 5 GHz).
3. Open IP Webcam and allow **camera/microphone** permissions.
4. Optional tuning (`Video preferences`):
   - Resolution: **1280×720** or **1920×1080**
   - FPS: **30**
   - Focus mode: **Continuous**
   - Keep screen awake to prevent sleep
5. Scroll to the bottom and tap **Start server**.
6. Note the base URL shown, e.g. `http://192.168.0.12:8080`.
7. Choose your stream URL (either is fine):
   - **MJPEG**: `http://<phone-ip>:8080/video`
   - **RTSP**: enable *RTSP Server* in IP Webcam, then use something like  
     `rtsp://<phone-ip>:8080/h264_ulaw.sdp

#### Run the server
```bash
# Using RTSP
python app.py --source rtsp://192.168.0.12:8080/h264_ulaw.sdp --fps 25

# Or using MJPEG
python app.py --source http://192.168.0.12:8080/video --fps 25
```
Open your browser at: `http://127.0.0.1:5000` 

---

#### Configuration Flags

- `--source` *(required)*: RTSP/MJPEG/HTTP URL from the phone app.
- `--host` *(optional)*: bind address (default `127.0.0.1`).
- `--port` *(optional)*: port (default `5000`).
- `--width` *(optional)*: browser preview width (keeps aspect; default `960`).
- `--fps` *(optional, v2/v3)*: target recording FPS (default `25`).

---