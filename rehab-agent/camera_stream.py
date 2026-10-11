"""Standalone multi-camera MJPEG streamer (demo utility).

Serves every openable camera on this machine as an MJPEG stream over HTTP,
so the dashboard (or any browser) can show a live view with a plain <img>
tag. Deliberately separate from app.py: the dashboard embeds the stream,
it does not open the cameras itself. Person B embeds it (see the snippet
printed at startup); this file does not modify app.py.

Run:
    python camera_stream.py            # auto-detect cameras, serve on :8503
    CAM_PORT=8600 python camera_stream.py
    CAM_INDICES=0,2 python camera_stream.py   # force specific devices

Prereqs:
    - opencv installed (opencv-python-headless)
    - the running user must be in the 'video' group, else every camera
      fails to open (sudo usermod -aG video <user>, then re-login).

Endpoints:
    GET /                 index page with one <img> per camera
    GET /stream/<idx>     multipart/x-mixed-replace MJPEG stream
    GET /snapshot/<idx>   single JPEG frame
"""

import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import cv2

PORT = int(os.environ.get("CAM_PORT", "8503"))
WIDTH = int(os.environ.get("CAM_WIDTH", "640"))
HEIGHT = int(os.environ.get("CAM_HEIGHT", "480"))
JPEG_QUALITY = int(os.environ.get("CAM_JPEG_QUALITY", "70"))
FPS = float(os.environ.get("CAM_FPS", "15"))


class Camera:
    """Continuously grabs frames from one device into a shared latest-JPEG buffer."""

    def __init__(self, index):
        self.index = index
        self.cap = cv2.VideoCapture(index, cv2.CAP_V4L2)
        # Request MJPG so each camera compresses on-board. Two UVC cameras on
        # one USB bus cannot both stream raw (YUYV); MJPG fits the bandwidth.
        self.cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
        if WIDTH:
            self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, WIDTH)
        if HEIGHT:
            self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, HEIGHT)
        self.latest = None
        self.lock = threading.Lock()
        self.running = False

    def opened(self):
        return self.cap is not None and self.cap.isOpened()

    def start(self):
        self.running = True
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self):
        interval = 1.0 / FPS if FPS > 0 else 0
        enc = [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY]
        while self.running:
            ok, frame = self.cap.read()
            if ok:
                ok2, buf = cv2.imencode(".jpg", frame, enc)
                if ok2:
                    with self.lock:
                        self.latest = buf.tobytes()
            if interval:
                time.sleep(interval)

    def get(self):
        with self.lock:
            return self.latest


def detect_cameras():
    """Return {index: Camera} for every device that opens and yields a frame."""
    forced = os.environ.get("CAM_INDICES")
    indices = [int(x) for x in forced.split(",")] if forced else range(0, 10)
    cams = {}
    for i in indices:
        cam = Camera(i)
        if cam.opened() and cam.cap.read()[0]:
            cams[i] = cam
        else:
            cam.cap.release()
    return cams


CAMERAS = {}


def _index_html():
    if not CAMERAS:
        return (
            "<h2>No cameras available</h2>"
            "<p>Either no device is attached, or this user is not in the "
            "'video' group (sudo usermod -aG video &lt;user&gt;, then re-login).</p>"
        )
    imgs = "".join(
        f'<figure style="margin:8px;display:inline-block">'
        f'<img src="/stream/{i}" style="width:480px;border:1px solid #ccc">'
        f"<figcaption>camera {i}</figcaption></figure>"
        for i in CAMERAS
    )
    return f"<html><body><h2>Cameras</h2>{imgs}</body></html>"


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass  # quiet

    def do_GET(self):
        path = self.path.rstrip("/")
        if path in ("", "/"):
            body = _index_html().encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif path.startswith("/snapshot/"):
            self._snapshot(path.rsplit("/", 1)[-1])
        elif path.startswith("/stream/"):
            self._stream(path.rsplit("/", 1)[-1])
        else:
            self.send_error(404)

    def _cam(self, idx_str):
        try:
            return CAMERAS.get(int(idx_str))
        except ValueError:
            return None

    def _snapshot(self, idx_str):
        cam = self._cam(idx_str)
        frame = cam.get() if cam else None
        if frame is None:
            self.send_error(404, "no such camera / no frame yet")
            return
        self.send_response(200)
        self.send_header("Content-Type", "image/jpeg")
        self.send_header("Content-Length", str(len(frame)))
        self.end_headers()
        self.wfile.write(frame)

    def _stream(self, idx_str):
        cam = self._cam(idx_str)
        if cam is None:
            self.send_error(404, "no such camera")
            return
        self.send_response(200)
        self.send_header(
            "Content-Type", "multipart/x-mixed-replace; boundary=frame"
        )
        self.end_headers()
        try:
            while True:
                frame = cam.get()
                if frame is not None:
                    self.wfile.write(b"--frame\r\n")
                    self.wfile.write(b"Content-Type: image/jpeg\r\n")
                    self.wfile.write(
                        f"Content-Length: {len(frame)}\r\n\r\n".encode()
                    )
                    self.wfile.write(frame)
                    self.wfile.write(b"\r\n")
                time.sleep(1.0 / FPS if FPS > 0 else 0.03)
        except (BrokenPipeError, ConnectionResetError):
            pass  # client disconnected


def main():
    global CAMERAS
    CAMERAS = detect_cameras()
    for cam in CAMERAS.values():
        cam.start()
    if CAMERAS:
        print(f"Serving cameras {list(CAMERAS)} on http://127.0.0.1:{PORT}")
        print(f"  stream:   http://127.0.0.1:{PORT}/stream/<index>")
        print(f"  snapshot: http://127.0.0.1:{PORT}/snapshot/<index>")
    else:
        print("WARNING: no cameras opened (permission? none attached?). "
              "Serving an info page anyway.")
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
