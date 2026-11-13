"""
Codis - Utilitários compartilhados entre pane.py e _pane_lsp.py.

Separados aqui para evitar importação circular:
  pane.py -> _pane_lsp.py -> pane.py (ciclo)

Ambos importam deste módulo sem criar ciclo.
"""

from __future__ import annotations

import sys
import tkinter as tk
import urllib.parse
from pathlib import Path, PurePosixPath

from src.ui.grab_safe import safe_grab_set

__all__ = ["_lsp_uri_to_path", "_BinaryDialog"]


def _lsp_uri_to_path(uri: str) -> Path:
    """
    Converte uma URI LSP para um Path do sistema.

    - URIs com scheme file:// são decodificadas corretamente (incluindo
      URIs do Windows como file:///C:/foo/bar.py).
    - Caminhos sem scheme são retornados como-estão, preservando separadores
      originais (sem normalizar / para \\ no Windows).
    """
    if uri.startswith("file://"):
        parsed = urllib.parse.urlparse(uri)
        path_str = urllib.parse.unquote(parsed.path)
        if (
            sys.platform == "win32"
            and path_str.startswith("/")
            and len(path_str) > 2
            and path_str[2] == ":"
        ):
            path_str = path_str[1:]
        return Path(path_str)

    if "/" in uri and "\\" not in uri:
        return (
            Path(PurePosixPath(uri).as_posix())
            if sys.platform != "win32"
            else _posix_path_on_windows(uri)
        )
    return Path(uri)


def _posix_path_on_windows(posix_str: str) -> Path:
    """
    Retorna um Path que ao ser convertido para str mantém o formato original.
    Usado em testes cross-platform que passam caminhos POSIX no Windows.
    """

    class _PosixPreservingPath(type(Path())):
        def __str__(self):
            return posix_str

    return _PosixPreservingPath(posix_str)


class _BinaryDialog:
    """Diálogo modal de escolha para arquivos binários/compactados.

    Exibe uma mensagem descritiva e um conjunto configurável de botões,
    retornando o valor associado ao botão clicado.
    """

    @staticmethod
    def ask(
        parent: tk.Widget,
        title: str,
        message: str,
        buttons: list[tuple[str, str]],
    ) -> str:
        """Cria e exibe o diálogo. Retorna o valor do botão clicado.

        Args:
            parent:  Widget pai para posicionamento.
            title:   Título da janela.
            message: Texto descritivo exibido no corpo do diálogo.
            buttons: Lista de (label, valor). O último botão é o padrão de fechar.
        """
        result: list[str] = ["cancel"]

        win = tk.Toplevel(parent)
        win.title(title)
        win.resizable(False, False)
        safe_grab_set(win)
        win.focus_set()

        try:
            bg = parent.cget("bg")
        except tk.TclError:
            bg = "#1e1e2e"
        try:
            fg = parent.tk.eval(
                "ttk::style lookup TLabel -foreground") or "#cdd6f4"
        except tk.TclError:
            fg = "#cdd6f4"

        win.configure(bg=bg)

        body = tk.Frame(win, bg=bg, padx=16, pady=14)
        body.pack(fill="both", expand=True)

        tk.Label(
            body,
            text=message,
            bg=bg,
            fg=fg,
            font=("TkDefaultFont", 10),
            justify="left",
            wraplength=440,
            anchor="w",
        ).pack(fill="x", pady=(0, 14))

        btn_frame = tk.Frame(body, bg=bg)
        btn_frame.pack(fill="x")

        accent = "#89b4fa"
        for label, value in buttons:
            is_primary = value not in ("cancel",)
            _v = value

            def _cmd(_val=_v) -> None:
                result[0] = _val
                win.destroy()

            btn = tk.Button(
                btn_frame,
                text=label,
                command=_cmd,
                relief="flat",
                padx=12,
                pady=5,
                font=("TkDefaultFont", 9),
                bg=accent if is_primary else bg,
                fg="#1e1e2e" if is_primary else fg,
                activebackground="#b4d0fc",
                cursor="hand2",
                bd=1,
            )
            btn.pack(side="left", padx=(0, 6))

        win.bind("<Escape>", lambda _e: win.destroy())

        win.update_idletasks()
        try:
            px = (
                parent.winfo_rootx()
                + parent.winfo_width() // 2
                - win.winfo_width() // 2
            )
            py = (
                parent.winfo_rooty()
                + parent.winfo_height() // 2
                - win.winfo_height() // 2
            )
            win.geometry(f"+{px}+{py}")
        except tk.TclError:
            pass

        parent.wait_window(win)
        return result[0]
