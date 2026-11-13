"""Codis - Utilitários de performance para UI Tkinter.

Módulo centralizado com helpers usados por todos os painéis:

  debounce_canvas(canvas, win_id, *, ms=40)
      Conecta os eventos <Configure> do canvas/frame-filho com debounce,
      evitando bbox()+itemconfig() síncronos a cada pixel de resize.

  BatchPacker
      Agrupa pack()/pack_forget() pendentes e os aplica num único
      after(0) - elimina N reflows causados por show/hide sequencial de rows.

  ThrottledRedraw
      Envolve qualquer Canvas e garante que redraw() seja chamado no máximo
      1× por intervalo, mesmo que múltiplos eventos disparem juntos.

  bg_recursive(widget, color)
      Versão iterativa (sem recursão Python) de _set_bg_recursive;
      usa uma fila explícita e suprime TclError silenciosamente.

  StickyScrollregion
      Mixin para Canvas que mantém scrollregion sempre atualizado via
      debounce, sem precisar bindar manualmente.
"""

from __future__ import annotations

import contextlib
import tkinter as tk
from collections import deque as _deque
from typing import Any


def debounce_canvas(
    canvas: tk.Canvas,
    window_id: int | str,
    *,
    ms: int = 40,
    extra_widget: tk.Widget | None = None,
) -> None:
    """Conecta eventos <Configure> ao canvas com debounce de ``ms`` ms.

    Os handlers só executam depois de ``ms`` ms de silêncio, evitando
    centenas de reflows durante o resize de janela, ao contrário de::

        content.bind("<Configure>", lambda _: canvas.config(scrollregion=...))
        canvas.bind("<Configure>", lambda _: canvas.itemconfig(win_id, ...))

    Args:
        canvas:      Canvas Tkinter a gerenciar.
        window_id:   ID retornado por canvas.create_window().
        ms:          Intervalo de debounce em milissegundos (padrão 40ms ≈ 25fps).
        extra_widget: Widget adicional que dispara <Configure> (ex.: frame filho).
    """
    _job: list[str | None] = [None]

    def _apply() -> None:
        _job[0] = None
        try:
            if not canvas.winfo_exists():
                return
            canvas.config(scrollregion=canvas.bbox("all"))
            canvas.itemconfig(window_id, width=canvas.winfo_width())
        except tk.TclError:
            pass

    def _trigger(_event: Any = None) -> None:
        if _job[0]:
            with contextlib.suppress(tk.TclError):
                canvas.after_cancel(_job[0])
        try:
            if not canvas.winfo_exists():
                return
            _job[0] = canvas.after(ms, _apply)
        except tk.TclError:
            pass

    canvas.bind("<Configure>", _trigger, add="+")
    if extra_widget is not None:
        extra_widget.bind("<Configure>", _trigger, add="+")


class BatchPacker:
    """Acumula operações pack/pack_forget e as aplica em lote via after(0).

    Uso::

        batcher = BatchPacker(root_widget)
        for row in rows:
            if visible:
                batcher.show(row, fill="x")
            else:
                batcher.hide(row)
        batcher.flush()   # ou deixa o after(0) fazer automaticamente

    Isso reduz N reflows de layout para 1, eliminando o flickering e
    o custo de cada pack_forget/pack individual que força relayout.
    """

    def __init__(self, master: tk.Widget) -> None:
        self._master = master
        self._ops: list[tuple[tk.Widget, str, dict]] = []
        self._job: str | None = None

    def show(self, widget: tk.Widget, **pack_kw: Any) -> None:
        """Agenda pack() para o widget."""
        self._ops.append((widget, "show", pack_kw))
        self._schedule()

    def hide(self, widget: tk.Widget) -> None:
        """Agenda pack_forget() para o widget."""
        self._ops.append((widget, "hide", {}))
        self._schedule()

    def _schedule(self) -> None:
        if self._job:
            return
        try:
            if not self._master.winfo_exists():
                return
            self._job = self._master.after(0, self.flush)
        except tk.TclError:
            pass

    def flush(self) -> None:
        """Aplica todas as operações pendentes imediatamente."""
        self._job = None
        ops = self._ops
        self._ops = []
        try:
            master_alive = self._master.winfo_exists()
        except tk.TclError:
            master_alive = False
        if not master_alive:
            return
        for widget, op, kw in ops:
            with contextlib.suppress(tk.TclError):
                if op == "show":
                    widget.pack(**kw)
                else:
                    widget.pack_forget()


class ThrottledRedraw:
    """Envolve um Canvas e garante redraw() no máximo 1× a cada ``interval_ms``.

    Uso::

        throttle = ThrottledRedraw(canvas, redraw_fn, interval_ms=16)
        # Em vez de chamar redraw_fn() diretamente:
        throttle.request()

    Se ``request()`` for chamado múltiplas vezes antes do intervalo expirar,
    apenas o último é executado - sem frames "empilhados".
    """

    def __init__(
        self,
        master: tk.Widget,
        redraw_fn: Any,
        interval_ms: int = 16,
    ) -> None:
        self._master = master
        self._fn = redraw_fn
        self._interval = interval_ms
        self._job: str | None = None
        self._dirty = False

    def request(self) -> None:
        """Solicita um redraw; executado no máximo 1× por intervalo."""
        self._dirty = True
        if self._job:
            return
        try:
            if not self._master.winfo_exists():
                return
            self._job = self._master.after(self._interval, self._fire)
        except tk.TclError:
            pass

    def _fire(self) -> None:
        self._job = None
        if not self._dirty:
            return
        self._dirty = False
        try:
            if not self._master.winfo_exists():
                return
        except tk.TclError:
            return
        with contextlib.suppress(Exception):
            self._fn()

    def cancel(self) -> None:
        """Cancela qualquer redraw pendente."""
        if self._job:
            with contextlib.suppress(tk.TclError):
                self._master.after_cancel(self._job)
            self._job = None
        self._dirty = False


def bg_recursive(widget: tk.Widget, color: str) -> None:
    """Define bg= em ``widget`` e todos os seus descendentes (iterativo).

    Iterativo, para evitar stack overflow em árvores de widgets profundas.
    """
    queue: _deque[tk.Widget] = _deque([widget])
    while queue:
        w = queue.popleft()
        with contextlib.suppress(tk.TclError):
            w.configure(bg=color)  # type: ignore[arg-type]
        try:
            children = w.winfo_children()
        except tk.TclError:
            children = []
        queue.extend(children)


def make_hover_highlight(
    widget: tk.Widget,
    children: list[tk.Widget],
    hover_bg: str,
    normal_bg: str,
    *,
    add: str = "+",
) -> None:
    """Registra <Enter>/<Leave> em ``widget`` que alteram bg de ``children``.

    Mais eficiente que a variante que chama ``winfo_children()`` a cada
    evento, pois a lista de widgets afetados é capturada uma única vez
    no momento da criação do handler.

    Args:
        widget:    Widget raiz que recebe os eventos.
        children:  Lista de widgets cujo bg deve mudar (pré-computada).
        hover_bg:  Cor de fundo no hover.
        normal_bg: Cor de fundo normal.
        add:       Passado para widget.bind(..., add=add).
    """
    targets = [widget] + children

    def _on_enter(_e: Any = None) -> None:
        for w in targets:
            with contextlib.suppress(tk.TclError):
                w.configure(bg=hover_bg)

    def _on_leave(_e: Any = None) -> None:
        for w in targets:
            with contextlib.suppress(tk.TclError):
                w.configure(bg=normal_bg)

    widget.bind("<Enter>", _on_enter, add=add)
    widget.bind("<Leave>", _on_leave, add=add)


def debounce_fn(master: tk.Widget, fn: Any, ms: int) -> Any:
    """Retorna uma versão debounced de ``fn`` usando ``master.after()``.

    Cada chamada ao wrapper cancela o agendamento anterior e reagenda
    ``fn`` para daqui a ``ms`` ms. Útil para search bars, resize handlers, etc.

    Exemplo::

        _schedule_filter = debounce_fn(self, self._apply_filter, ms=80)
        self._filter_var.trace_add("write", lambda *_: _schedule_filter())
    """
    _state: list[str | None] = [None]

    def _wrapper(*args: Any, **kwargs: Any) -> None:
        if _state[0]:
            with contextlib.suppress(tk.TclError):
                master.after_cancel(_state[0])
        try:
            if not master.winfo_exists():
                return
        except tk.TclError:
            return

        def _safe_call() -> None:
            _state[0] = None
            try:
                if not master.winfo_exists():
                    return
            except tk.TclError:
                return
            with contextlib.suppress(tk.TclError, Exception):
                fn(*args, **kwargs)

        with contextlib.suppress(tk.TclError):
            _state[0] = master.after(ms, _safe_call)

    return _wrapper
