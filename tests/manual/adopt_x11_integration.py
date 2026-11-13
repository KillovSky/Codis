"""Integração MANUAL do modo "adotar" em X11 real (Xvfb + xterm).

    xvfb-run -a python tests/manual/adopt_x11_integration.py
Valida: listar janelas, adotar por NOME do exe (sem handle), reparentar,
devolver ao desktop mantendo o processo vivo, e fechar a janela externa.
"""
import os, subprocess, sys, time, tkinter as tk
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from src.ui import app_embed as ae
from src.ui import window_embed as we

def pump(root, s):
    end = time.time() + s
    while time.time() < end:
        root.update(); time.sleep(0.01)

root = tk.Tk(); root.geometry("1000x700+0+0")
box = tk.Frame(root); box.pack(fill="both", expand=True); root.update()
backend = we.get_backend(); assert backend and backend.name == "x11"

ext = subprocess.Popen(["xterm", "-T", "ADOTE-ME", "-geometry", "80x24", "-e", "sleep", "120"])
pump(root, 1.5)
wins = ae.list_candidate_windows(backend)
print("janelas:", [(w.exe, w.title) for w in wins])
assert any(w.title == "ADOTE-ME" and w.exe == "xterm" for w in wins), "xterm não listado"

closed = []; status = []
panel = ae.start_adopt(box, exe_name="xterm", backend=backend,
                       on_status=status.append, on_close=lambda: closed.append(1))
t0 = time.time()
while panel.phase != "embedded" and time.time() - t0 < 8:
    pump(root, 0.05)
print("fase:", panel.phase, status[-1:])
assert panel.phase == "embedded"
h = panel._hwnd
assert backend.is_embedded(h, panel._host.winfo_id()), "não reparentou"

panel.close(); pump(root, 0.5)
assert closed, "on_close não chamado"
backend = we.get_backend()
assert ext.poll() is None, "o programa adotado foi MORTO ao devolver (bug)"
assert backend.is_alive(h), "janela sumiu"
assert not backend.is_embedded(h, box.winfo_id()), "ainda filha do Codis"
print("devolvida; processo vivo; janela no desktop")

w = next(w for w in ae.list_candidate_windows(backend) if w.title == "ADOTE-ME")
closed.clear()
panel = ae.start_adopt(box, window=w, backend=backend, on_close=lambda: closed.append(1))
t0 = time.time()
while panel.phase != "embedded" and time.time() - t0 < 8:
    pump(root, 0.05)
assert panel.phase == "embedded"
ext.kill(); ext.wait(); pump(root, 1.0)
assert closed, "painel não fechou quando o programa adotado morreu"

backend = we.get_backend()
status.clear()
panel = ae.start_adopt(box, exe_name="nao-existe-xyz", backend=backend, on_status=status.append)
panel._timeout = 1.0
pump(root, 2.5)
print("inexistente:", panel.phase, status[-1:])
assert panel.phase == "failed"
print("OK")
