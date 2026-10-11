"""Silent recording monitor: shows what lerobot-record is doing and the frames it is saving.

Reads the recording log and the frames LeRobot writes to disk during an episode, so it never
opens the cameras itself. Local-only; reach it through an SSH tunnel.
"""
import json, re, sys, time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT, LOG = Path(sys.argv[1]), Path(sys.argv[2])
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

PAGE = b"""<!doctype html><meta charset=utf-8><title>Recording monitor</title>
<body style="background:#111;color:#eee;font-family:sans-serif;margin:12px">
<div id=s style="font-size:44px;font-weight:700;padding:14px;border-radius:8px;background:#333">connecting</div>
<div style="display:flex;gap:12px;flex-wrap:wrap;margin-top:12px">
<div><div>front (overhead)</div><img id=front width=560></div>
<div><div>wrist</div><img id=wrist width=560></div></div>
<p style="color:#999">Camera views update while an episode is recording and freeze during resets.</p>
<script>
const col={recording:'#b00020',reset:'#0b6b2f',finished:'#333',idle:'#333',error:'#7a4a00'};
async function tick(){
  try{const r=await (await fetch('/status')).json();const e=r.episode==null?'':' '+(r.episode+1);
    const t=r.elapsed==null?'':'  '+Math.max(0,Math.floor(r.elapsed))+' s';
    const label={recording:'RECORDING episode'+e+t,reset:'RESET - reposition the forearm'+t,finished:'FINISHED',idle:'waiting to start',error:'STOPPED WITH AN ERROR'}[r.phase];
    s.textContent=label;s.style.background=col[r.phase];
    if(r.phase=='recording'){for(const c of ['front','wrist'])document.getElementById(c).src='/latest/'+c+'?'+Date.now();}
  }catch(e){s.textContent='monitor unreachable';s.style.background='#333';}
}
setInterval(tick,250);tick();
</script></body>"""

class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def send(self, code, ctype, body):
        self.send_response(code); self.send_header("Content-Type", ctype); self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/status":
            self.send(200, "application/json", json.dumps(status()).encode())
        elif path in ("/latest/front", "/latest/wrist"):
            f = latest(path.rsplit("/", 1)[1])
            try:
                self.send(200, "image/png", f.read_bytes()) if f else self.send(204, "image/png", b"")
            except OSError:
                self.send(204, "image/png", b"")
        else:
            self.send(200, "text/html", PAGE)

ThreadingHTTPServer(("127.0.0.1", 8600), H).serve_forever()
