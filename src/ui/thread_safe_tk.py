"""Chamadas de Tk vindas de threads de trabalho, executadas na thread principal.

O Tkinter só é seguro na thread que criou o interpretador. Dezenas de pontos do
Codis usam o padrão ``widget.after(0, callback)`` a partir de threads de rede,
de Git, de varredura de arquivos e do monitor de desempenho. Em builds de Tcl/Tk
sem suporte a threads isso corrompe memória (segfault, geralmente só em máquinas
com vários núcleos), e nos builds com threads falha com ``RuntimeError``.

``install`` troca ``Misc.after``, ``Misc.after_idle`` e ``Misc.event_generate``
por versões que, ao serem chamadas de fora da thread principal, só enfileiram o
pedido; um laço na thread principal executa a fila. Na thread principal o
comportamento é o original, sem custo adicional.
"""

from __future__ import annotations

import contextlib
import itertools
import logging
import queue
import threading
import time
import tkinter as tk
from typing import Any

logger = logging.getLogger("Codis")

_POLL_FAST_MS = 4
_POLL_IDLE_MS = 20
_ACTIVE_WINDOW_S = 0.25

_state: dict[str, Any] = {"installed": False, "root": None, "main": None}
_queue: queue.SimpleQueue = queue.SimpleQueue()
_ids = itertools.count(1)
_original: dict[str, Any] = {}


def _on_main() -> bool:
    return threading.current_thread() is _state["main"]


def _safe_after(self: tk.Misc, ms: int, func: Any = None, *args: Any) -> str | None:
    if _on_main():
        return _original["after"](self, ms, func, *args)
    if func is None:
        time.sleep(max(0, ms) / 1000)
        return None
    token = f"codis-thread-after-{next(_ids)}"
    _queue.put(("after", self, (ms, func, args), {}))
    return token


def _safe_after_idle(self: tk.Misc, func: Any, *args: Any) -> str:
    if _on_main():
        return _original["after_idle"](self, func, *args)
    token = f"codis-thread-idle-{next(_ids)}"
    _queue.put(("after_idle", self, (func, args), {}))
    return token


def _safe_event_generate(self: tk.Misc, sequence: str, **kw: Any) -> None:
    if _on_main():
        return _original["event_generate"](self, sequence, **kw)
    _queue.put(("event_generate", self, (sequence,), kw))
    return None


def _run(item: tuple[str, tk.Misc, tuple, dict]) -> None:
    kind, widget, args, kw = item
    try:
        if kind == "after":
            ms, func, extra = args
            _original["after"](widget, ms, func, *extra)
        elif kind == "after_idle":
            func, extra = args
            _original["after_idle"](widget, func, *extra)
        else:
            _original["event_generate"](widget, args[0], **kw)
    except tk.TclError:
        pass
    except Exception:
        logger.debug("Falha ao executar chamada de Tk enfileirada", exc_info=True)


def drain() -> int:
    """Executa os pedidos enfileirados por outras threads. Devolve quantos rodou."""
    count = 0
    while True:
        try:
            item = _queue.get_nowait()
        except queue.Empty:
            return count
        _run(item)
        count += 1


def _pump(root: tk.Misc, last_active: float) -> None:
    ran = drain()
    now = time.monotonic()
    if ran:
        last_active = now
    delay = _POLL_FAST_MS if now - last_active < _ACTIVE_WINDOW_S else _POLL_IDLE_MS
    with contextlib.suppress(tk.TclError):
        _original["after"](root, delay, _pump, root, last_active)


def install(root: tk.Misc) -> None:
    """Ativa o encaminhamento para a thread principal. Deve ser chamado nela, uma vez."""
    if _state["installed"]:
        return
    _state["installed"] = True
    _state["root"] = root
    _state["main"] = threading.current_thread()
    _original["after"] = tk.Misc.after
    _original["after_idle"] = tk.Misc.after_idle
    _original["event_generate"] = tk.Misc.event_generate
    tk.Misc.after = _safe_after
    tk.Misc.after_idle = _safe_after_idle
    tk.Misc.event_generate = _safe_event_generate
    _original["after"](root, _POLL_FAST_MS, _pump, root, time.monotonic())


def uninstall() -> None:
    """Restaura o comportamento original (usado em testes)."""
    if not _state["installed"]:
        return
    tk.Misc.after = _original["after"]
    tk.Misc.after_idle = _original["after_idle"]
    tk.Misc.event_generate = _original["event_generate"]
    _state.update(installed=False, root=None, main=None)
    drain()
