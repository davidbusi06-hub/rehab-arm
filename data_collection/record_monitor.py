"""Silent recording monitor: shows what lerobot-record is doing and the frames it is saving.

Reads the recording log and the frames LeRobot writes to disk during an episode, so it never
opens the cameras itself. Local-only; reach it through an SSH tunnel.
"""
import json, re, subprocess, sys, time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import cv2

ROOT, LOG = Path(sys.argv[1]), Path(sys.argv[2])
# Manual control: the page's buttons type LeRobot's own recording keys into the screen
# session the recording runs in (n = end this episode / end the reset, r = redo, q = stop).
KEYS = {"next": "n", "redo": "r", "stop": "q"}
# Manual control is off unless the monitor is started with a 4th argument "manual": stray or
# repeated presses (a held space bar auto-repeats) ended episodes with no frames and crashed
# two recordings.
if not (len(sys.argv) > 4 and sys.argv[4] == "manual"):
    KEYS = {}
MIN_GAP_S = 5.0
last_press = 0.0
SCREEN_SESSION = sys.argv[3] if len(sys.argv) > 3 else "record"

EVENT = re.compile(r"(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d).*?(Recording episode (\d+)|Reset the environment|Stop recording|Exiting|Traceback)")

def status():
    state = {"phase": "idle", "episode": None, "since": None}
    try:
        text = LOG.read_text(errors="replace").replace("\r", "\n")
    except OSError:
        return state
    for m in EVENT.finditer(text):
        when = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S").timestamp()
        what = m.group(2)
        if what.startswith("Recording"):
            state = {"phase": "recording", "episode": int(m.group(3)), "since": when}
        elif what.startswith("Reset"):
            state = {"phase": "reset", "episode": state["episode"], "since": when}
        else:
            state = {"phase": "finished", "episode": state["episode"], "since": when}
    if "Traceback" in text[-4000:]:
        state["phase"] = "error"
    if state["since"]:
        state["elapsed"] = round(time.time() - state["since"], 1)
    return state

def latest(cam):
    eps = sorted((ROOT / "images" / f"observation.images.{cam}").glob("episode-*"))
    if not eps:
        return None
    frames = sorted(eps[-1].glob("frame-*.png"))
    return frames[-2] if len(frames) > 1 else None  # the newest may still be being written


def latest_jpeg(cam):
    """The newest saved frame as a small JPEG: the saved PNGs are ~300 KB each, too heavy to
    poll over a slow link (the page stalls and stops updating)."""
    f = latest(cam)
    image = cv2.imread(str(f)) if f else None
    if image is None:
        return None
    ok, jpg = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 55])
    return jpg.tobytes() if ok else None

PAGE = b"""<!doctype html><meta charset=utf-8><title>Recording monitor</title>
<body style="background:#111;color:#eee;font-family:sans-serif;margin:12px">
<div id=s style="font-size:44px;font-weight:700;padding:14px;border-radius:8px;background:#333">connecting</div>
<div style="margin-top:10px;display:flex;gap:10px;flex-wrap:wrap">
<button id=bnext onclick="key('next')" style="font-size:22px;padding:12px 18px">End episode / start next</button>
<button onclick="key('redo')" style="font-size:22px;padding:12px 18px">Redo this episode</button>
<button onclick="if(confirm('Stop recording now?'))key('stop')" style="font-size:22px;padding:12px 18px">Finish recording</button>
<span id=k style="align-self:center;color:#bbb"></span></div>
<div style="display:flex;gap:12px;flex-wrap:wrap;margin-top:12px">
<div><div>front (overhead)</div><img id=front width=560></div>
<div><div>wrist</div><img id=wrist width=560></div></div>
<p style="color:#999">Camera views update while an episode is recording and freeze during resets.
The buttons only work when manual pacing is switched on.</p>
<script>
const col={recording:'#b00020',reset:'#0b6b2f',finished:'#333',idle:'#333',error:'#7a4a00'};
async function tick(){
  try{const r=await (await fetch('/status')).json();const e=r.episode==null?'':' '+(r.episode+1);
    const t=r.elapsed==null?'':'  '+Math.max(0,Math.floor(r.elapsed))+' s';
    const label={recording:'RECORDING episode'+e+t,reset:'RESET - reposition the forearm'+t,finished:'FINISHED',idle:'waiting to start',error:'STOPPED WITH AN ERROR'}[r.phase];
    s.textContent=label;s.style.background=col[r.phase];
    live=r.phase=='recording';
  }catch(e){s.textContent='monitor unreachable';s.style.background='#333';}
}
async function key(name){k.textContent='sending...';
  try{const r=await fetch('/key/'+name,{method:'POST'});k.textContent=r.ok?'sent: '+name:(r.status==425?'ignored: wait 5 seconds between presses':(r.status==409?'no recording is running right now':'manual control is switched off'));}
  catch(e){k.textContent='not sent';}
  setTimeout(()=>k.textContent='',2500);}
let live=false;
// one image request at a time per camera, so a slow link never builds a backlog
function pump(c){const el=document.getElementById(c);const next=()=>setTimeout(()=>pump(c),live?250:600);
  if(!live){next();return;}
  const im=new Image();im.onload=()=>{if(im.naturalWidth)el.src=im.src;next();};im.onerror=next;im.src='/latest/'+c+'?'+Date.now();}
setInterval(tick,500);tick();pump('front');pump('wrist');
</script></body>"""

class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def send(self, code, ctype, body):
        self.send_response(code); self.send_header("Content-Type", ctype); self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
    def do_POST(self):
        name = self.path.strip("/").partition("/")[2]
        if self.path.startswith("/key/") and name in KEYS:
            # LeRobot crashes if an episode is ended before it has a single frame, which a
            # double press does (one press ends the reset, the next ends the new episode).
            # Two guards: never within MIN_GAP_S of the last accepted press (the status below lags
            # the recording by a second or two, so it cannot be trusted right after a press), and
            # never in the first seconds of an episode.
            global last_press
            now = status()
            if name == "next" and (time.time() - last_press < MIN_GAP_S
                                   or (now["phase"] == "recording" and now.get("elapsed", 99) < 3)):
                self.send(425, "text/plain", b"too soon"); return
            if now["phase"] not in ("recording", "reset"):
                self.send(409, "text/plain", b"not recording"); return
            if name == "next":
                last_press = time.time()
            done = subprocess.run(["screen", "-S", SCREEN_SESSION, "-X", "stuff", KEYS[name]], capture_output=True)
            self.send(200 if done.returncode == 0 else 409, "text/plain", b"ok" if done.returncode == 0 else b"no session")
        else:
            self.send(404, "text/plain", b"unknown")

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/status":
            self.send(200, "application/json", json.dumps(status()).encode())
        elif path in ("/latest/front", "/latest/wrist"):
            jpg = latest_jpeg(path.rsplit("/", 1)[1])
            self.send(200, "image/jpeg", jpg) if jpg else self.send(204, "image/jpeg", b"")
        else:
            self.send(200, "text/html", PAGE)

ThreadingHTTPServer(("127.0.0.1", 8600), H).serve_forever()
