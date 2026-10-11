"""Live view of both cameras for re-aiming them, next to a reference frame from the
wrist_grasp recordings. Holds the cameras while it runs: stop it before recording.
Local-only; reach it through an SSH tunnel."""
import subprocess, sys, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import av, cv2

REF_ROOT = Path(sys.argv[1] if len(sys.argv) > 1 else "/home/aup/so101_dataset/wrist_grasp_48") / "videos"
REF_NAME = REF_ROOT.parent.name
SERIALS = {"front": "icSpring_icspring_camera", "wrist": "icSpring_icspring_camera_202404160005"}

def node(serial):
    for v in sorted(Path("/dev").glob("video*")):
        if Path(f"/sys/class/video4linux/{v.name}/index").read_text().strip() != "0":
            continue
        props = subprocess.run(["udevadm", "info", "-q", "property", str(v)], capture_output=True, text=True).stdout
        if f"ID_SERIAL={serial}\n" in props:
            return str(v)

def reference(cam, frame_index=30):
    video = sorted((REF_ROOT / f"observation.images.{cam}").glob("**/*.mp4"))[-1]
    c = av.open(str(video))
    for n, fr in enumerate(c.decode(c.streams.video[0])):
        if n == frame_index:
            img = cv2.cvtColor(fr.to_ndarray(format="rgb24"), cv2.COLOR_RGB2BGR); break
    c.close()
    return cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 70])[1].tobytes()

REFS = {cam: reference(cam) for cam in SERIALS}
frames, lock = {}, threading.Lock()

def grab(cam):
    dev = node(SERIALS[cam])
    cap = cv2.VideoCapture(dev, cv2.CAP_V4L2)
    if cam == "wrist":  # both cameras share one USB hub; they cannot both send uncompressed video
        cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640); cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
    while True:
        ok, frame = cap.read()
        if not ok:
            time.sleep(0.2); continue
        ok, jpg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 50])
        if ok:
            with lock: frames[cam] = jpg.tobytes()
        time.sleep(0.05)

PAGE = """<!doctype html><meta charset=utf-8><title>Camera alignment</title>
<body style="background:#111;color:#eee;font-family:sans-serif;margin:12px">
<h2 style="margin:4px 0">Camera alignment (live)</h2>
<p style="color:#bbb;margin:4px 0">Move the overhead camera until the live picture matches the reference from the earlier recordings (REFNAME).
Use the slider to fade between them.</p>
<div style="display:flex;gap:12px;flex-wrap:wrap">
<div><div>overhead: live over reference <input id=mix type=range min=0 max=100 value=50></div>
<div style="position:relative;width:640px;height:480px"><img src="/ref/front" width=640 height=480 style="position:absolute">
<img id=front width=640 height=480 style="position:absolute;opacity:.5"></div></div>
<div><div>overhead: reference (as recorded)</div><img src="/ref/front" width=420><div style="margin-top:8px">wrist: live</div><img id=wrist width=420></div>
</div>
<script>
mix.oninput=()=>front.style.opacity=mix.value/100;
function pump(c){const el=document.getElementById(c);const im=new Image();const next=()=>setTimeout(()=>pump(c),150);
  im.onload=()=>{if(im.naturalWidth)el.src=im.src;next();};im.onerror=next;im.src='/live/'+c+'?'+Date.now();}
pump('front');pump('wrist');
</script></body>""".replace("REFNAME", REF_NAME).encode()

class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def send(self, code, ctype, body):
        self.send_response(code); self.send_header("Content-Type", ctype); self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
    def do_GET(self):
        path = self.path.split("?")[0]
        kind, _, cam = path.strip("/").partition("/")
        if kind == "live" and cam in SERIALS:
            with lock: jpg = frames.get(cam)
            self.send(200, "image/jpeg", jpg) if jpg else self.send(204, "image/jpeg", b"")
        elif kind == "ref" and cam in REFS:
            self.send(200, "image/jpeg", REFS[cam])
        else:
            self.send(200, "text/html", PAGE)

for cam in SERIALS:
    threading.Thread(target=grab, args=(cam,), daemon=True).start()
print("serving on 127.0.0.1:8601", flush=True)
ThreadingHTTPServer(("127.0.0.1", 8601), H).serve_forever()
