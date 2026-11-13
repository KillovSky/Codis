"""Codis - DropDown: Entry + Listbox substituto do ttk.Combobox.

Implementação 100% tk puro - sem ttk - para evitar problemas com temas
TTK customizados que quebram a abertura do dropdown nativo do Combobox.

API compatível com ttk.Combobox:
    widget = DropDown(parent, textvariable=var, values=[...], width=20,
                      font=(...), state="readonly")
    widget.bind("<<ComboboxSelected>>", callback)
    widget.config(values=[...])
    widget["values"] = [...]
    widget.pack / grid / place   (passa para o frame interno)
"""

from __future__ import annotations

import contextlib
import tkinter as tk
import tkinter.font as _tkfont
from collections.abc import Callable, Sequence
from typing import Any


class DropDown(tk.Frame):
    """Entry + popup Listbox que imita ttk.Combobox sem usar ttk."""

    def __init__(
        self,
        parent: tk.Widget,
        textvariable: tk.StringVar | None = None,
        values: Sequence[str] = (),
        width: int = 16,
        font: Any = None,
        state: str = "readonly",
        bg: str | None = None,
        fg: str | None = None,
        sel_bg: str | None = None,
        sel_fg: str | None = None,
        border_color: str | None = None,
        arrow_fg: str | None = None,
        **kw: Any,
    ) -> None:
        """Inicializa o widget de dropdown customizado."""
        bg = bg or self._resolve("input_bg", parent, "#3c3c52")
        fg = fg or self._resolve("fg", parent, "#cdd6f4")
        sel_bg = sel_bg or self._resolve("sel_bg", parent, "#45475a")
        sel_fg = sel_fg or self._resolve("sel_fg", parent, "#cdd6f4")
        border_color = border_color or self._resolve(
            "border", parent, "#555577")
        arrow_fg = arrow_fg or self._resolve("fg_muted", parent, "#888aaa")
        list_bg = self._resolve("floating_bg", parent, "#2a2b3d")

        super().__init__(
            parent,
            bg=border_color,
            highlightthickness=0,
            bd=0,
            **{
                k: v
                for k, v in kw.items()
                if k not in ("highlightbackground", "highlightcolor")
            },
        )

        self._var = textvariable or tk.StringVar()
        self._values: list[str] = list(values)
        self._state = state
        self._font = font or ("sans-serif", 9)
        self._fg = fg
        self._bg = bg
        self._sel_bg = sel_bg
        self._sel_fg = sel_fg
        self._list_bg = list_bg
        self._border_color = border_color
        self._popup: tk.Toplevel | None = None
        self._listbox: tk.Listbox | None = None
        self._callbacks: list[Callable] = []

        inner = tk.Frame(self, bg=bg, bd=0)
        inner.pack(fill="both", expand=True, padx=1, pady=1)

        self._entry = tk.Entry(
            inner,
            textvariable=self._var,
            font=self._font,
            width=width,
            bg=bg,
            fg=fg,
            insertbackground=fg,
            relief="flat",
            bd=0,
            highlightthickness=0,
            readonlybackground=bg,
            state="readonly" if state == "readonly" else "normal",
            cursor="arrow",
        )
        self._entry.pack(side="left", fill="both", expand=True, padx=(4, 0))

        self._arrow = tk.Label(
            inner,
            text="▾",
            bg=bg,
            fg=arrow_fg,
            font=(
                self._font[0] if isinstance(
                    self._font, (list, tuple)) else self._font,
                9,
            ),
            cursor="hand2",
            padx=4,
            pady=0,
        )
        self._arrow.pack(side="right")

        for widget in (self._entry, self._arrow, inner, self):
            widget.bind("<ButtonPress-1>", self._toggle, add="+")
        self._entry.bind("<FocusOut>", self._on_focus_out, add="+")

    @staticmethod
    def _resolve(attr: str, widget: tk.Widget, fallback: str) -> str:
        """Walk up widget parents to find a ThemeManager theme colour."""
        try:
            w = widget
            for _ in range(12):
                tmgr = getattr(w, "_tmgr", None)
                if tmgr is not None:
                    return getattr(tmgr.theme, attr, fallback)
                app = getattr(w, "_app", None)
                if app is not None:
                    tmgr2 = getattr(app, "_tmgr", None)
                    if tmgr2 is not None:
                        return getattr(tmgr2.theme, attr, fallback)
                w = w.master
                if w is None:
                    break
        except (tk.TclError, AttributeError):
            pass
        return fallback

    def bind(
        self, sequence: str = "", func: Callable | None = None, add: Any = None
    ) -> str:
        """Delega o bind para o widget interno, repassando sequence e func."""
        if sequence == "<<ComboboxSelected>>":
            if func:
                self._callbacks.append(func)
            return ""
        return super().bind(sequence, func, add)

    def config(self, **kw: Any) -> None:
        """Aplica configurações de estilo ao widget interno do dropdown."""

        if "values" in kw:
            self._values = list(kw.pop("values"))
            if self._listbox:
                self._populate_listbox()
        if kw:
            super().config(**kw)

    configure = config

    def __setitem__(self, key: str, value: Any) -> None:
        """Define atributo via indexação; 'values' atualiza a lista interna."""
        if key == "values":
            self.config(values=value)
        else:
            super().__setitem__(key, value)

    def __getitem__(self, key: str) -> Any:
        """Retorna atributo via indexação; 'values' retorna a lista interna."""
        if key == "values":
            return self._values
        return super().__getitem__(key)

    def get(self) -> str:
        """Retorna o valor atualmente selecionado no dropdown."""
        return self._var.get()

    def set(self, value: str) -> None:
        """Define o valor exibido no campo de entrada."""
        self._var.set(value)

    def _toggle(self, _event: Any = None) -> None:
        """Alterna o estado do componente (ativo/inativo ou visível/oculto).

        Args:
            _event: Parâmetro da operação.
        """
        if self._popup and self._popup.winfo_exists():
            self._close_popup()
        else:
            self._open_popup()

    def _open_popup(self) -> None:
        """Cria e exibe a janela popup com a lista de opções."""
        if not self._values:
            return
        self._close_popup()

        popup = tk.Toplevel(self)
        popup.overrideredirect(True)
        popup.attributes("-topmost", True)
        self._popup = popup

        self.update_idletasks()
        x = self.winfo_rootx()
        y = self.winfo_rooty() + self.winfo_height()
        w = max(self.winfo_width(), 80)
        rows = min(len(self._values), 10)

        try:
            fnt = _tkfont.Font(font=self._font)
            row_h = fnt.metrics("linespace") + 6
        except tk.TclError:
            row_h = 20
        h = rows * row_h + 4

        sw = self.winfo_screenwidth()
        sh = self.winfo_screenheight()
        if x + w > sw:
            x = max(0, sw - w)
        if y + h > sh:
            y = max(0, self.winfo_rooty() - h)

        popup.geometry(f"{w}x{h}+{x}+{y}")
        popup.config(bg=self._border_color)

        frame = tk.Frame(popup, bg=self._list_bg, bd=0)
        frame.pack(fill="both", expand=True, padx=1, pady=1)

        sb = tk.Scrollbar(
            frame,
            orient="vertical",
            bg=self._list_bg,
            troughcolor=self._list_bg,
            activebackground=self._sel_bg,
            highlightthickness=0,
            bd=0,
            width=8,
        )
        sb.pack(side="right", fill="y")

        lb = tk.Listbox(
            frame,
            font=self._font,
            bg=self._list_bg,
            fg=self._fg,
            selectbackground=self._sel_bg,
            selectforeground=self._sel_fg,
            activestyle="none",
            relief="flat",
            bd=0,
            highlightthickness=0,
            yscrollcommand=sb.set,
            exportselection=False,
            cursor="hand2",
        )
        lb.pack(side="left", fill="both", expand=True)
        sb.config(command=lb.yview)
        self._listbox = lb

        self._populate_listbox()

        cur = self._var.get()
        if cur in self._values:
            idx = self._values.index(cur)
            lb.selection_set(idx)
            lb.see(idx)

        lb.bind("<ButtonRelease-1>", self._on_select)
        lb.bind("<Return>", self._on_select)
        lb.bind("<Escape>", lambda _: self._close_popup())
        popup.bind("<FocusOut>", self._on_popup_focus_out)
        popup.bind("<Escape>", lambda _: self._close_popup())

        popup.update_idletasks()
        popup.deiconify()
        popup.lift()
        popup.focus_force()
        lb.focus_set()

    def _populate_listbox(self) -> None:
        """Preenche a listbox do popup com as opções disponíveis."""
        if not self._listbox:
            return
        self._listbox.delete(0, "end")
        for v in self._values:
            self._listbox.insert("end", v)

    def _on_select(self, _event: Any = None) -> None:
        """Trata o evento 'select'.

        Args:
            _event: Parâmetro da operação.
        """
        lb = self._listbox
        if lb is None:
            return
        sel = lb.curselection()
        if sel:
            value = self._values[sel[0]]
            self._var.set(value)
            self._close_popup()

            for cb in self._callbacks:
                with contextlib.suppress(TypeError, RuntimeError):
                    cb(None)

    def _close_popup(self) -> None:
        """Fecha e destrói a janela popup do dropdown."""
        self._listbox = None
        if self._popup and self._popup.winfo_exists():
            self._popup.destroy()
        self._popup = None

    def _on_focus_out(self, _event: Any = None) -> None:
        """Trata o evento 'focus out'.

        Args:
            _event: Parâmetro da operação.
        """
        self.after(50, self._check_focus)

    def _on_popup_focus_out(self, _event: Any = None) -> None:
        """Trata o evento 'popup focus out'.

        Args:
            _event: Parâmetro da operação.
        """
        self.after(50, self._check_focus)

    def _check_focus(self) -> None:
        """Fecha o popup se o foco saiu de seus widgets."""
        try:
            focused = self.focus_get()
        except tk.TclError:
            focused = None
        if self._popup and self._popup.winfo_exists():
            try:
                popup_path = str(self._popup)
                if focused and str(focused).startswith(popup_path):
                    return
            except tk.TclError:
                pass
            self._close_popup()
