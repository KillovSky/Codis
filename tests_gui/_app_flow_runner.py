"""Executado em subprocesso por test_kui_app_flows.py (HOME isolado)."""

import json
import os
import pathlib
import sys
import tempfile
import time
import tkinter as tk

home = tempfile.mkdtemp()
os.environ["HOME"] = os.environ["USERPROFILE"] = home
sys.path.insert(0, sys.argv[1])
from src.app import CodisApp  # noqa: E402
from src.config import ConfigManager  # noqa: E402
from src.ui.workspace_trust import WorkspaceTrust  # noqa: E402

WorkspaceTrust.prompt_if_needed = lambda self, *a, **k: True
cfg = ConfigManager()
cfg.set("first_run_done", True)
cfg.save()
root = tk.Tk()
root.geometry("1200x760+0+0")
app = CodisApp(root)
errors = []
root.report_callback_exception = lambda *a: errors.append(repr(a[1]))


def pump(ms):
    end = time.monotonic() + ms / 1000
    while time.monotonic() < end:
        root.update()
        time.sleep(0.004)


def mapped(w):
    try:
        return bool(w is not None and w.winfo_exists() and w.winfo_ismapped())
    except tk.TclError:
        return False


def state():
    k = app._kui
    return {
        "classic": mapped(app._welcome),
        "kwelcome": mapped(k._welcome) if k else False,
        "kui_on": bool(k and k._enabled),
        "sidebars": [str(p) for p in app._h_paned.panes()].count(str(app._sidebar))
        + (1 if app._dock.floating and app._sidebar.winfo_manager() == "place" else 0),
    }


out = {}
pump(800)
app._enable_kui()
pump(900)
out["enabled"] = state()
first = app._kui
app._enable_kui()
pump(300)
out["double_enable_same_instance"] = app._kui is first
app._kui.minimize_to_planet()
pump(500)
out["cosmos"] = state()
app._kui.restore_from_planet()
pump(600)
out["restored"] = state()
app._show_welcome()
pump(600)
out["command"] = state()
ws = pathlib.Path(home) / "p"
ws.mkdir()
f = ws / "a.py"
f.write_text("x=1\n")
app._open_file(f)
pump(700)
out["file_open"] = state()
app._close_active_tab()
pump(700)
out["closed_last"] = state()
app._disable_kui()
pump(800)
out["disabled"] = state()
out["errors"] = errors[:3]
print("RESULT:" + json.dumps(out))
os._exit(0)
