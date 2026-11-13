"""Integração MANUAL do embutir-janelas em X11 real (requer Xvfb/xterm).

Uso::

    Xvfb :99 -screen 0 1600x900x24 &
    DISPLAY=:99 python tests/manual/embed_x11_integration.py

Usa o ``xterm`` como "motor falso" e valida: reparentamento (pai = frame do
Tk), redimensionamento, falha com log, timeout e ausência de órfãos.
Não é coletado pelo pytest (nome sem prefixo ``test_``).
"""
import sys, os, time, subprocess, tkinter as tk
sys.path.insert(0, "/home/claude/proj")
from src.ui import window_embed as we

def pump(root, secs):
    end = time.time() + secs
    while time.time() < end:
        root.update(); time.sleep(0.01)

status = []
root = tk.Tk(); root.geometry("1000x700+0+0")
container = tk.Frame(root, bg="#222"); container.pack(fill="both", expand=True)
root.update()
backend = we.get_backend()
assert backend is not None and backend.name == "x11", backend
labels = we.EmbedLabels(hint="hint", back="Back", focus="Focus", loading="loading", embedded="embedded",
    closed="closed", err_exited="exited {code} {log}", err_timeout="timeout {seconds}",
    err_launch="launch {reason}", embed_failed="embed failed {reason}")
closed = []
panel = we.EmbeddedAppPanel(container, ["xterm", "-geometry", "80x24", "-e", "sleep", "120"], backend=backend,
    labels=labels, on_status=status.append, on_close=lambda: closed.append(1), log_path=None, window_timeout=10)
panel.place(x=0, y=0, relwidth=1, relheight=1)
root.update()
panel.start()
pid = panel._proc.pid
t0 = time.time()
while panel.phase != "embedded" and time.time() - t0 < 8:
    pump(root, 0.05)
print("phase:", panel.phase, "status:", status)
assert panel.phase == "embedded"
host_id = panel._host.winfo_id()
tree = subprocess.run(["xwininfo", "-id", str(panel._hwnd), "-tree"], capture_output=True, text=True).stdout
print("xterm win", hex(panel._hwnd), "host", hex(host_id))
out = subprocess.run(["xwininfo", "-id", str(panel._hwnd), "-children"], capture_output=True, text=True).stdout
parent_line = [l for l in out.splitlines() if "Parent window id" in l]
print(parent_line)
assert hex(host_id) in parent_line[0]
pump(root, 0.5)
w, h = backend.size_of(panel._hwnd); hw, hh = panel._host_size()
print("size", (w, h), "host", (hw, hh)); assert (w, h) == (hw, hh)
root.geometry("1200x800+0+0"); pump(root, 0.8)
w2, h2 = backend.size_of(panel._hwnd); hw2, hh2 = panel._host_size()
print("after resize", (w2, h2), "host", (hw2, hh2)); assert (w2, h2) == (hw2, hh2) and w2 > w
try:
    from PIL import ImageGrab
    ImageGrab.grab(xdisplay=":99").save("/tmp/embed_x11.png"); print("screenshot ok")
except Exception as e: print("shot fail", e)
panel.close(); pump(root, 0.5)
t0 = time.time()
while time.time() - t0 < 4:
    pump(root, 0.1)
    try: os.kill(pid, 0)
    except ProcessLookupError: break
    else:
        st = open(f"/proc/{pid}/stat").read().split()[2] if os.path.exists(f"/proc/{pid}/stat") else "X"
        if st in ("Z","X"): break
alive = os.path.exists(f"/proc/{pid}") and open(f"/proc/{pid}/stat").read().split()[2] not in ("Z","X")
print("closed cb:", closed, "proc alive:", alive, "status tail:", status[-1])
assert closed == [1] and not alive and not panel.winfo_exists()

status.clear()
p2 = we.EmbeddedAppPanel(container, ["sh", "-c", "echo boom >&2; exit 3"], backend=we.get_backend(), labels=labels,
    on_status=status.append, log_path=__import__("pathlib").Path("/tmp/doom_test.log"), window_timeout=5)
p2.place(x=0, y=0, relwidth=1, relheight=1); root.update(); p2.start()
t0 = time.time()
while p2.phase != "failed" and time.time() - t0 < 4: pump(root, 0.05)
print("fail phase:", p2.phase, status); assert p2.phase == "failed" and "exited 3" in status[-1] and "boom" in status[-1]
p2.close(); pump(root, 0.3)

status.clear()
p3 = we.EmbeddedAppPanel(container, ["sleep", "30"], backend=we.get_backend(), labels=labels, on_status=status.append, window_timeout=1)
p3.place(x=0, y=0, relwidth=1, relheight=1); root.update(); p3.start()
t0 = time.time()
while p3.phase != "failed" and time.time() - t0 < 4: pump(root, 0.05)
print("timeout phase:", p3.phase, status); assert p3.phase == "failed" and "timeout" in status[-1]
pid3 = p3._proc.pid; p3.close(); pump(root, 0.5)

status.clear()
p4 = we.EmbeddedAppPanel(container, ["xterm", "-e", "sleep", "120"], backend=we.get_backend(), labels=labels, on_status=status.append)
p4.place(x=0, y=0, relwidth=1, relheight=1); root.update(); p4.start()
t0 = time.time()
while p4.phase != "embedded" and time.time() - t0 < 8: pump(root, 0.05)
pid4 = p4._proc.pid; assert p4.phase == "embedded"
root.destroy(); time.sleep(0.5)
st = open(f"/proc/{pid4}/stat").read().split()[2] if os.path.exists(f"/proc/{pid4}") else "X"
print("orphan after root destroy:", st); assert st in ("Z","X")
print("ALL X11 INTEGRATION OK")
