#!/usr/bin/env python3
"""
Codis - Modo Vim CLI
====================
Reimplementação do modo Vim como experiência completamente imersiva e CLI.

Quando ativado:
    * Uma janela ``tk.Toplevel`` separada é criada com aparência de terminal.
    * O conteúdo do arquivo ativo é copiado para o editor interno da janela.
    * A interface principal do Codis fica em segundo plano (não é destruída).
    * A janela CLI exibe modo atual, número de linha e coluna no rodapé.
    * Só é possível sair via ``:q``, ``:q!``, ``:wq``, ou ``Ctrl+Alt+V``.

Modos implementados:
    * ``NORMAL``  - navegação e comandos (hjkl, w, b, e, gg, G, dd, yy ...)
    * ``INSERT``  - entrada de texto livre (``i``, ``a``, ``o``, ``I``, ``A``)
    * ``VISUAL``  - seleção char a char (``v``)
    * ``V-LINE``  - seleção por linha (``V``)
    * ``REPLACE`` - substituição de um caractere (``r``)
    * ``COMMAND`` - linha de comando ex (``:``, Enter executa)

Design da janela CLI
--------------------
A janela ocupa 80 % da tela por padrão e é redimensionável.
O editor de texto usa fonte monoespaçada, cursor em bloco.
O rodapé exibe: modo | arquivo | linha:coluna | modificado.
Uma linha de status separada mostra o buffer de comando ``:``.
"""

from __future__ import annotations

import contextlib
import re
import tkinter as tk
from collections.abc import Callable
from tkinter import font as tkfont

from src.ui.grab_safe import safe_grab_set

NORMAL = "NORMAL"
INSERT = "INSERT"
VISUAL = "VISUAL"
VLINE = "V-LINE"
REPLACE = "REPLACE"
COMMAND = "COMMAND"

_SAIDA_ATALHO = "Ctrl+Alt+V"


class VimMode:
    """Modo de edição Vim imersivo em janela CLI separada.

    Ao ser ativado, cria um ``tk.Toplevel`` com aparência de terminal e
    copia o conteúdo do editor ativo para dentro dele.  A janela principal
    do Codis continua rodando normalmente em segundo plano.

    O usuário só consegue retornar ao Codis normal via comando ``:q``,
    ``:q!``, ``:wq`` ou pelo atalho ``Ctrl+Alt+V``.

    Args:
        editor_pane:     Painel de editor do Codis com atributo ``.text``
                         (``tk.Text``) e opcionalmente ``.current_file``.
        on_mode_change:  Callback ``(modo: str, cmd: str = "")`` chamado a
                         cada transição de modo; usado pela K-UI.
        quit_cb:         Chamado quando o usuário executa ``:q`` / ``:q!``;
                         deve chamar ``_disable_vim_mode()`` do app.
        save_cb:         Chamado quando o usuário executa ``:w`` / ``:wq``;
                         deve salvar o arquivo ativo.
    """

    def __init__(
        self,
        editor_pane,
        on_mode_change: Callable,
        quit_cb: Callable | None = None,
        save_cb: Callable | None = None,
    ) -> None:
        """Inicializa o modo Vim no widget de texto ``txt``."""
        self.editor_pane = editor_pane
        self._source_text: tk.Text = editor_pane.text
        self._on_mode_change = on_mode_change
        self._quit_cb = quit_cb
        self._save_cb = save_cb
        self._enabled = False

        self._mode = NORMAL
        self._cmd_buffer = ""
        self._pending = ""
        self._count_buf = ""
        self._register = ""
        self._visual_start = ""
        self._modified = False

        self._win: tk.Toplevel | None = None
        self._txt: tk.Text | None = None
        self._status_lbl: tk.Label | None = None
        self._cmd_lbl: tk.Label | None = None
        self._bindings: list = []

    def enable(self) -> None:
        """Abre a janela CLI e transfere o conteúdo do editor ativo."""
        if self._enabled:
            return
        self._enabled = True
        self._modified = False
        self._criar_janela_cli()

    def disable(self) -> None:
        """Fecha a janela CLI e restaura o foco para o Codis."""
        if not self._enabled:
            return
        self._enabled = False
        self._fechar_janela()
        with contextlib.suppress(tk.TclError):
            self._on_mode_change("")

    def _criar_janela_cli(self) -> None:
        """Cria a janela Toplevel com aparência de terminal."""
        root = self._source_text.winfo_toplevel()

        self._win = tk.Toplevel(root)
        self._win.title("[ VIM ]")
        self._win.protocol("WM_DELETE_WINDOW", self._abortar)

        sw = root.winfo_screenwidth()
        sh = root.winfo_screenheight()
        w = max(900, int(sw * 0.80))
        h = max(600, int(sh * 0.80))
        x = (sw - w) // 2
        y = (sh - h) // 2
        self._win.geometry(f"{w}x{h}+{x}+{y}")
        self._win.resizable(True, True)
        self._win.configure(bg="#0a0a0f")

        self._win.bind("<Control-Alt-v>", lambda _: self._abortar())
        self._win.bind("<Control-Alt-V>", lambda _: self._abortar())

        self._construir_layout()
        self._carregar_conteudo()
        self._set_mode(NORMAL)

        self._txt.focus_set()
        safe_grab_set(self._win)

    def _construir_layout(self) -> None:
        """Constrói os widgets internos da janela CLI."""
        BG = "#0a0a0f"
        FG = "#c8d3f5"
        FG_DIM = "#545c7e"
        CURSOR_CLR = "#c8d3f5"
        SEL_BG = "#2d3f76"
        LINE_HL = "#13131f"
        FONT_NOME = "Consolas"
        FONT_SZ = 13

        mono = (FONT_NOME, FONT_SZ)

        with contextlib.suppress(tk.TclError):
            f = tkfont.Font(family=FONT_NOME, size=FONT_SZ)
            if f.measure("M") == 0:
                FONT_NOME = "Courier New"

        corpo = tk.Frame(self._win, bg=BG)
        corpo.pack(fill="both", expand=True)

        nums_frame = tk.Frame(corpo, bg="#0d0d18")
        nums_frame.pack(side="left", fill="y")

        self._numeros = tk.Text(
            nums_frame,
            width=5,
            bg="#0d0d18",
            fg=FG_DIM,
            font=mono,
            state="disabled",
            relief="flat",
            bd=0,
            highlightthickness=0,
            padx=6,
            pady=2,
            cursor="arrow",
        )
        self._numeros.pack(fill="y", expand=True)

        self._txt = tk.Text(
            corpo,
            bg=BG,
            fg=FG,
            insertbackground=CURSOR_CLR,
            insertwidth=10,
            selectbackground=SEL_BG,
            font=mono,
            relief="flat",
            bd=0,
            highlightthickness=0,
            padx=8,
            pady=2,
            undo=True,
            wrap="none",
        )
        self._txt.tag_configure("current_line", background=LINE_HL)
        self._txt.pack(side="left", fill="both", expand=True)

        scrolly = tk.Scrollbar(
            corpo, command=self._txt.yview, bg="#0d0d18", troughcolor="#0d0d18"
        )
        scrolly.pack(side="right", fill="y")
        self._txt.config(yscrollcommand=scrolly.set)

        scrollx = tk.Scrollbar(
            self._win,
            orient="horizontal",
            command=self._txt.xview,
            bg="#0d0d18",
            troughcolor="#0d0d18",
        )
        scrollx.pack(side="bottom", fill="x")
        self._txt.config(xscrollcommand=scrollx.set)

        footer = tk.Frame(self._win, bg="#1a1a2e", height=24)
        footer.pack(side="bottom", fill="x")
        footer.pack_propagate(False)

        self._modo_lbl = tk.Label(
            footer,
            text="",
            bg="#7c4dff",
            fg="#ffffff",
            font=(FONT_NOME, 10, "bold"),
            padx=8,
        )
        self._modo_lbl.pack(side="left")

        self._arquivo_lbl = tk.Label(
            footer,
            text="",
            bg="#1a1a2e",
            fg=FG_DIM,
            font=(FONT_NOME, 10),
            padx=8,
        )
        self._arquivo_lbl.pack(side="left")

        self._pos_lbl = tk.Label(
            footer,
            text="",
            bg="#1a1a2e",
            fg=FG_DIM,
            font=(FONT_NOME, 10),
            padx=8,
        )
        self._pos_lbl.pack(side="right")

        self._mod_lbl = tk.Label(
            footer,
            text="",
            bg="#1a1a2e",
            fg="#ff5370",
            font=(FONT_NOME, 10, "bold"),
            padx=8,
        )
        self._mod_lbl.pack(side="right")

        self._cmd_lbl = tk.Label(
            self._win,
            text="",
            bg="#0a0a0f",
            fg="#c8d3f5",
            font=(FONT_NOME, 11),
            anchor="w",
            padx=8,
        )
        self._cmd_lbl.pack(side="bottom", fill="x")

        self._txt.bind("<Key>", self._handle_key, add=True)
        self._txt.bind("<Return>", self._handle_return, add=True)
        self._txt.bind("<KeyRelease>", lambda _: self._atualizar_pos())

    def _carregar_conteudo(self) -> None:
        """Copia o conteúdo do editor ativo para o editor interno da janela."""
        content = ""
        with contextlib.suppress(tk.TclError):
            content = self._source_text.get("1.0", "end-1c")
        self._txt.delete("1.0", "end")
        self._txt.insert("1.0", content)
        self._txt.edit_reset()
        self._atualizar_numeros()

        with contextlib.suppress(tk.TclError):
            cursor_src = self._source_text.index("insert")
            self._txt.mark_set("insert", cursor_src)
            self._txt.see("insert")

        name = ""
        with contextlib.suppress(tk.TclError):
            name = getattr(self.editor_pane, "current_file", "") or ""
            if name:
                import os

                name = os.path.basename(name)
        with contextlib.suppress(tk.TclError):
            self._arquivo_lbl.config(text=f"  {name or '[sem name]'}")

    def _fechar_janela(self) -> None:
        """Fecha a janela CLI e libera o grab."""
        with contextlib.suppress(tk.TclError):
            if self._win:
                self._win.grab_release()
                self._win.destroy()
                self._win = None
                self._txt = None

    def _abortar(self) -> None:
        """Sai do modo Vim sem salvar (equivalente a ``:q!``)."""
        self._fechar_janela()
        self._enabled = False
        with contextlib.suppress(tk.TclError):
            self._on_mode_change("")
        if self._quit_cb:
            with contextlib.suppress(tk.TclError):
                self._quit_cb()

    def _set_mode(self, mode: str, cmd: str = "") -> None:
        """Atualiza o modo atual e sincroniza a UI da janela CLI.

        Args:
            mode: Novo modo (``NORMAL``, ``INSERT``, etc.).
            cmd:  Buffer de comando ex atual (usado em ``COMMAND``).
        """
        self._mode = mode
        self._pending = ""
        self._count_buf = ""
        if mode == COMMAND:
            self._cmd_buffer = ""

        with contextlib.suppress(tk.TclError):
            if self._txt:
                if mode == INSERT:
                    self._txt.config(insertwidth=2)
                else:
                    self._txt.config(insertwidth=10)

        self._atualizar_rodape(mode, cmd)
        with contextlib.suppress(tk.TclError):
            self._on_mode_change(mode, cmd)

    def _atualizar_rodape(self, mode: str, cmd: str = "") -> None:
        """Atualiza os rótulos do rodapé com o modo e o buffer de comando.

        Args:
            mode: Modo atual.
            cmd:  Buffer de comando ex (exibido na linha de cmd abaixo).
        """
        if not self._win:
            return
        cores_modo = {
            NORMAL: ("#7c4dff", "#ffffff"),
            INSERT: ("#00bcd4", "#000000"),
            VISUAL: ("#ff9800", "#000000"),
            VLINE: ("#ff9800", "#000000"),
            REPLACE: ("#f44336", "#ffffff"),
            COMMAND: ("#1a1a2e", "#c8d3f5"),
        }
        bg_m, fg_m = cores_modo.get(mode, ("#1a1a2e", "#c8d3f5"))
        with contextlib.suppress(tk.TclError):
            self._modo_lbl.config(text=f" {mode} ", bg=bg_m, fg=fg_m)

        if mode == COMMAND:
            with contextlib.suppress(tk.TclError):
                self._cmd_lbl.config(text=f":{cmd}")
        else:
            with contextlib.suppress(tk.TclError):
                self._cmd_lbl.config(text="")

        self._atualizar_pos()
        with contextlib.suppress(tk.TclError):
            self._mod_lbl.config(text="[+]" if self._modified else "")

    def _atualizar_pos(self) -> None:
        """Atualiza o rótulo de posição (linha:coluna) no rodapé."""
        if not self._txt or not self._win:
            return
        with contextlib.suppress(tk.TclError):
            idx = self._txt.index("insert")
            ln, col = idx.split(".")
            self._pos_lbl.config(text=f"Ln {ln}  Col {int(col) + 1}  ")
        self._atualizar_numeros()
        self._hl_linha_atual()

    def _atualizar_numeros(self) -> None:
        """Sincroniza o painel de números de linha."""
        if not self._txt or not self._numeros:
            return
        with contextlib.suppress(tk.TclError):
            total = int(self._txt.index("end-1c").split(".", 1)[0])
            lines = "\n".join(str(i) for i in range(1, total + 1))
            self._numeros.config(state="normal")
            self._numeros.delete("1.0", "end")
            self._numeros.insert("1.0", lines)
            self._numeros.config(state="disabled")

    def _hl_linha_atual(self) -> None:
        """Realça a linha onde o cursor está."""
        if not self._txt:
            return
        with contextlib.suppress(tk.TclError):
            self._txt.tag_remove("current_line", "1.0", "end")
            ln = self._txt.index("insert").split(".", 1)[0]
            self._txt.tag_add("current_line", f"{ln}.0", f"{ln}.end+1c")

    def _handle_return(self, _event: tk.Event) -> str | None:
        """Handler dedicado para Enter - executa o buffer COMMAND ou move linha.

        Args:
            event: Evento Tkinter de teclado.

        Returns:
            ``"break"`` para consumir o evento; ``None`` para passar adiante.
        """
        if not self._enabled:
            return None
        if self._mode == COMMAND:
            self._execute_command(self._cmd_buffer)
            return "break"
        if self._mode == INSERT:
            return None
        with contextlib.suppress(tk.TclError):
            self._txt.mark_set("insert", "insert+1l")
        return "break"

    def _handle_key(self, event: tk.Event) -> str | None:
        """
        Args:
            event: Evento Tkinter de teclado.

        Returns:
            ``"break"`` para consumir; ``None`` para passar ao Tkinter.
        """
        if not self._enabled:
            return None
        sym = event.keysym
        char = event.char

        if self._mode == INSERT:
            if sym == "Escape":
                self._set_mode(NORMAL)
                with contextlib.suppress(tk.TclError):
                    idx = self._txt.index("insert")
                    ln, col = idx.split(".")
                    if int(col) > 0:
                        self._txt.mark_set("insert", f"{ln}.{int(col) - 1}")
                return "break"
            self._modified = True
            self._atualizar_pos()
            return None

        if self._mode == REPLACE:
            if sym == "Escape":
                self._set_mode(NORMAL)
                return "break"
            if char and char.isprintable():
                with contextlib.suppress(tk.TclError):
                    idx = self._txt.index("insert")
                    self._txt.delete(idx)
                    self._txt.insert(idx, char)
                    self._txt.mark_set("insert", idx)
                self._modified = True
                self._set_mode(NORMAL)
                return "break"
            return "break"

        if self._mode == COMMAND:
            return self._command_key(sym, char)

        if self._mode in (VISUAL, VLINE):
            return self._visual_key(sym, char)

        return self._normal_key(event, sym, char)

    def _normal_motion(self, sym: str, char: str, count: int) -> str | None:
        """Trata teclas de movimento no modo NORMAL (h/j/k/l, w/b/e, 0/$, G, gg)."""
        txt = self._txt
        if sym in ("h", "Left"):
            for _ in range(count):
                with contextlib.suppress(tk.TclError):
                    txt.mark_set("insert", "insert-1c")
            return "break"
        if sym in ("l", "Right"):
            for _ in range(count):
                with contextlib.suppress(tk.TclError):
                    txt.mark_set("insert", "insert+1c")
            return "break"
        if sym in ("j", "Down"):
            for _ in range(count):
                with contextlib.suppress(tk.TclError):
                    txt.mark_set("insert", "insert+1l")
            self._atualizar_pos()
            return "break"
        if sym in ("k", "Up"):
            for _ in range(count):
                with contextlib.suppress(tk.TclError):
                    txt.mark_set("insert", "insert-1l")
            self._atualizar_pos()
            return "break"
        if char == "w":
            for _ in range(count):
                with contextlib.suppress(tk.TclError):
                    txt.mark_set("insert", "insert wordend")
                    txt.mark_set("insert", "insert+1c wordstart")
            return "break"
        if char == "b":
            for _ in range(count):
                with contextlib.suppress(tk.TclError):
                    txt.mark_set("insert", "insert-1c wordstart")
            return "break"
        if char == "e":
            for _ in range(count):
                with contextlib.suppress(tk.TclError):
                    txt.mark_set("insert", "insert wordend")
            return "break"
        if char == "0" or sym == "Home":
            with contextlib.suppress(tk.TclError):
                ln = txt.index("insert").split(".", 1)[0]
                txt.mark_set("insert", f"{ln}.0")
            return "break"
        if char == "$" or sym == "End":
            with contextlib.suppress(tk.TclError):
                txt.mark_set("insert", "insert lineend")
            return "break"
        if char == "^":
            with contextlib.suppress(tk.TclError):
                ln = txt.index("insert").split(".", 1)[0]
                line = txt.get(f"{ln}.0", f"{ln}.end")
                col = len(line) - len(line.lstrip())
                txt.mark_set("insert", f"{ln}.{col}")
            return "break"
        if char == "G":
            target = (
                count if self._count_buf else int(
                    txt.index("end-1c").split(".", 1)[0])
            )
            with contextlib.suppress(tk.TclError):
                txt.mark_set("insert", f"{target}.0")
                txt.see("insert")
            self._count_buf = ""
            return "break"
        if self._pending == "g" and char == "g":
            with contextlib.suppress(tk.TclError):
                txt.mark_set("insert", "1.0")
                txt.see("insert")
            self._pending = ""
            return "break"
        return None

    def _normal_scroll(self, event: tk.Event, sym: str, count: int) -> str | None:
        """Trata teclas de scroll no modo NORMAL (Ctrl-f/b/d/u)."""
        txt = self._txt
        ctrl = event.state & 4
        if sym == "control_f" or (ctrl and sym == "f"):
            with contextlib.suppress(tk.TclError):
                txt.yview_scroll(count, "pages")
            return "break"
        if sym == "control_b" or (ctrl and sym == "b"):
            with contextlib.suppress(tk.TclError):
                txt.yview_scroll(-count, "pages")
            return "break"
        if sym == "control_d" or (ctrl and sym == "d"):
            with contextlib.suppress(tk.TclError):
                txt.yview_scroll(count, "units")
            return "break"
        if sym == "control_u" or (ctrl and sym == "u"):
            with contextlib.suppress(tk.TclError):
                txt.yview_scroll(-count, "units")
            return "break"
        return None

    def _normal_insert_enter(self, char: str) -> str | None:
        """Trata teclas de entrada em modo INSERT a partir do NORMAL (i/I/a/A/o/O/r/R)."""
        txt = self._txt
        if char == "i":
            self._set_mode(INSERT)
            return "break"
        if char == "I":
            with contextlib.suppress(tk.TclError):
                ln = txt.index("insert").split(".", 1)[0]
                line = txt.get(f"{ln}.0", f"{ln}.end")
                col = len(line) - len(line.lstrip())
                txt.mark_set("insert", f"{ln}.{col}")
            self._set_mode(INSERT)
            return "break"
        if char == "a":
            with contextlib.suppress(tk.TclError):
                txt.mark_set("insert", "insert+1c")
            self._set_mode(INSERT)
            return "break"
        if char == "A":
            with contextlib.suppress(tk.TclError):
                txt.mark_set("insert", "insert lineend")
            self._set_mode(INSERT)
            return "break"
        if char == "o":
            with contextlib.suppress(tk.TclError):
                txt.mark_set("insert", "insert lineend")
                txt.insert("insert", "\n")
            self._modified = True
            self._set_mode(INSERT)
            return "break"
        if char == "O":
            with contextlib.suppress(tk.TclError):
                ln = txt.index("insert").split(".", 1)[0]
                txt.mark_set("insert", f"{ln}.0")
                txt.insert("insert", "\n")
                txt.mark_set("insert", f"{ln}.0")
            self._modified = True
            self._set_mode(INSERT)
            return "break"
        if char in ("r", "R"):
            self._set_mode(REPLACE)
            return "break"
        return None

    def _normal_pending(self, char: str, count: int) -> str | None:
        """Resolve operadores pendentes (dd, dw, yy, cc, cw, zz) no modo NORMAL."""
        txt = self._txt
        pending = self._pending
        if pending == "d":
            if char == "d":
                for _ in range(count):
                    with contextlib.suppress(tk.TclError):
                        ln = txt.index("insert").split(".", 1)[0]
                        self._register = txt.get(f"{ln}.0", f"{ln}.end+1c")
                        txt.delete(f"{ln}.0", f"{ln}.end+1c")
                self._modified = True
            elif char == "w":
                with contextlib.suppress(tk.TclError):
                    start = txt.index("insert")
                    txt.mark_set("insert", "insert wordend")
                    self._register = txt.get(start, "insert")
                    txt.delete(start, "insert")
                self._modified = True
            self._pending = ""
            return "break"
        if pending == "y":
            if char == "y":
                with contextlib.suppress(tk.TclError):
                    ln = txt.index("insert").split(".", 1)[0]
                    self._register = txt.get(f"{ln}.0", f"{ln}.end+1c")
            self._pending = ""
            return "break"
        if pending == "c":
            if char == "c":
                with contextlib.suppress(tk.TclError):
                    ln = txt.index("insert").split(".", 1)[0]
                    self._register = txt.get(f"{ln}.0", f"{ln}.end")
                    txt.delete(f"{ln}.0", f"{ln}.end")
                self._modified = True
                self._set_mode(INSERT)
            elif char == "w":
                with contextlib.suppress(tk.TclError):
                    start = txt.index("insert")
                    txt.mark_set("insert", "insert wordend")
                    self._register = txt.get(start, "insert")
                    txt.delete(start, "insert")
                    txt.mark_set("insert", start)
                self._modified = True
                self._set_mode(INSERT)
            self._pending = ""
            return "break"
        if pending == "z":
            if char == "z":
                with contextlib.suppress(tk.TclError):
                    txt.see("insert")
            self._pending = ""
            return "break"
        return None

    def _normal_key(self, event: tk.Event, sym: str, char: str) -> str | None:
        """Processa teclas no modo NORMAL.

        Args:
            event: Evento original do Tk.
            sym:   Nome do keysym.
            char:  Caractere imprimível (pode ser vazio).

        Returns:
            ``"break"`` para suprimir o evento padrão, ou ``None``.
        """
        txt = self._txt
        pending = self._pending

        if sym == "Escape":
            self._pending = ""
            self._count_buf = ""
            self._set_mode(NORMAL)
            return "break"

        if char.isdigit() and char != "0" and not pending:
            self._count_buf += char
            return "break"

        count = max(1, int(self._count_buf)) if self._count_buf else 1

        if pending in ("d", "y", "c", "z"):
            result = self._normal_pending(char, count)
            if result is not None:
                return result

        result = self._normal_motion(sym, char, count)
        if result is not None:
            return result

        result = self._normal_scroll(event, sym, count)
        if result is not None:
            return result

        result = self._normal_insert_enter(char)
        if result is not None:
            return result

        if char == "v":
            self._visual_start = txt.index("insert")
            self._set_mode(VISUAL)
            return "break"
        if char == "V":
            self._visual_start = txt.index("insert")
            self._set_mode(VLINE)
            return "break"

        if char == "u":
            with contextlib.suppress(tk.TclError):
                txt.edit_undo()
            return "break"
        if sym == "control_r":
            with contextlib.suppress(tk.TclError):
                txt.edit_redo()
            return "break"

        if char == ":":
            self._set_mode(COMMAND)
            return "break"

        if char == "g":
            self._pending = "g"
            return "break"
        if char == "d":
            self._pending = "d"
            self._count_buf = ""
            return "break"
        if char == "y":
            self._pending = "y"
            self._count_buf = ""
            return "break"
        if char == "c":
            self._pending = "c"
            self._count_buf = ""
            return "break"
        if char == "z":
            self._pending = "z"
            return "break"

        if char == "p":
            if self._register:
                with contextlib.suppress(tk.TclError):
                    txt.insert("insert+1c", self._register)
                self._modified = True
            return "break"
        if char == "P":
            if self._register:
                with contextlib.suppress(tk.TclError):
                    txt.insert("insert", self._register)
                self._modified = True
            return "break"

        if char == "x":
            for _ in range(count):
                with contextlib.suppress(tk.TclError):
                    self._register = txt.get("insert")
                    txt.delete("insert")
            self._modified = True
            return "break"
        if char == "X":
            for _ in range(count):
                with contextlib.suppress(tk.TclError):
                    self._register = txt.get("insert-1c")
                    txt.delete("insert-1c")
            self._modified = True
            return "break"

        if char == "/":
            self._cmd_buffer = "/"
            self._set_mode(COMMAND)
            with contextlib.suppress(tk.TclError):
                self._cmd_lbl.config(text="/")
            return "break"
        if char == "n":
            self._buscar_proximo(direction=1)
            return "break"
        if char == "N":
            self._buscar_proximo(direction=-1)
            return "break"

        if char == "J":
            with contextlib.suppress(tk.TclError):
                ln = txt.index("insert").split(".", 1)[0]
                txt.delete(f"{ln}.end", f"{ln}.end+1c")
                txt.insert(f"{ln}.end", " ")
            self._modified = True
            return "break"

        return "break"

    def _command_key(self, sym: str, char: str) -> str | None:
        """Processa teclas no modo COMMAND (linha de comando ex).

        Args:
            sym:  Keysym da tecla.
            char: Caractere imprimível.

        Returns:
            Sempre ``"break"``.
        """
        if sym == "Escape":
            self._set_mode(NORMAL)
            return "break"

        if sym in ("Return", "KP_Enter"):
            self._execute_command(self._cmd_buffer)
            return "break"

        if sym == "BackSpace":
            if self._cmd_buffer:
                self._cmd_buffer = self._cmd_buffer[:-1]
                with contextlib.suppress(tk.TclError):
                    self._on_mode_change(COMMAND, self._cmd_buffer)
                with contextlib.suppress(tk.TclError):
                    self._cmd_lbl.config(text=f":{self._cmd_buffer}")
            else:
                self._set_mode(NORMAL)
            return "break"

        if char and (char.isprintable() or char in ("!", "%", "/", "\\", " ")):
            self._cmd_buffer += char
            with contextlib.suppress(tk.TclError):
                self._on_mode_change(COMMAND, self._cmd_buffer)
            with contextlib.suppress(tk.TclError):
                self._cmd_lbl.config(text=f":{self._cmd_buffer}")

        return "break"

    def _execute_command(self, cmd: str) -> None:
        """Interpreta e executa o buffer de comando ex.

        Comandos suportados: ``:q``, ``:q!``, ``:qa``, ``:w``, ``:wq``,
        ``:x``, ``:%s/.../.../``, ``:set nu``, ``:set nonu``, ``:N`` (ir para linha).

        Args:
            cmd: Conteúdo do buffer sem o ``:``.
        """
        cmd = cmd.strip()

        if cmd in ("q", "q!", "qa", "qa!"):
            self._do_quit(salvar=False)
            return

        if cmd in ("w", "w!"):
            self._do_save()
            self._set_mode(NORMAL)
            return

        if cmd in ("wq", "wq!", "x", "x!"):
            self._do_save()
            self._do_quit(salvar=False)
            return

        if cmd.startswith("%s/") or cmd.startswith("s/"):
            self._do_substitute(cmd)
            self._set_mode(NORMAL)
            return

        if cmd in ("set nu", "set number"):
            with contextlib.suppress(tk.TclError):
                self.editor_pane.set_line_numbers(True)
            self._set_mode(NORMAL)
            return
        if cmd in ("set nonu", "set nonumber"):
            with contextlib.suppress(tk.TclError):
                self.editor_pane.set_line_numbers(False)
            self._set_mode(NORMAL)
            return

        if cmd.startswith("/") and len(cmd) > 1:
            self._ultimo_padrao_busca = cmd[1:]
            self._buscar_proximo(direction=1)
            self._set_mode(NORMAL)
            return

        if cmd.isdigit():
            target = int(cmd)
            with contextlib.suppress(tk.TclError):
                self._txt.mark_set("insert", f"{target}.0")
                self._txt.see("insert")
            self._set_mode(NORMAL)
            return

        self._set_mode(NORMAL)

    def _do_save(self) -> None:
        """Copia o conteúdo de volta para o editor do Codis e salva."""
        with contextlib.suppress(tk.TclError):
            content = self._txt.get("1.0", "end-1c")
            self._source_text.delete("1.0", "end")
            self._source_text.insert("1.0", content)
        if self._save_cb:
            with contextlib.suppress(tk.TclError):
                self._save_cb()
        self._modified = False
        with contextlib.suppress(tk.TclError):
            self._mod_lbl.config(text="")

    def _do_quit(self, salvar: bool = False) -> None:
        """Fecha a janela CLI e retorna ao Codis.

        Opcionalmente salva antes de fechar.

        Args:
            salvar: Se ``True``, chama :meth:`_do_save` antes de fechar.
        """
        if salvar:
            self._do_save()
        self._fechar_janela()
        self._enabled = False
        with contextlib.suppress(tk.TclError):
            self._on_mode_change("")
        if self._quit_cb:
            with contextlib.suppress(tk.TclError):
                self._quit_cb()

    def _do_substitute(self, cmd: str) -> None:
        """Implementa substituição ``:%s/padrão/substituto/flags``.

        Args:
            cmd: Comando ex completo (com ou sem o ``%`` inicial).
        """
        try:
            partes = cmd.lstrip("%").lstrip("s").lstrip("/").split("/")
            if len(partes) < 2:
                return
            pattern, subst = partes[0], partes[1]
            flags_str = partes[2] if len(partes) > 2 else ""
            global_flag = "g" in flags_str
            re_flags = re.MULTILINE
            if "i" in flags_str:
                re_flags |= re.IGNORECASE
            text = self._txt.get("1.0", "end-1c")
            if global_flag:
                new_val = re.sub(pattern, subst, text, flags=re_flags)
            else:
                new_val = re.sub(pattern, subst, text, count=1, flags=re_flags)
            self._txt.delete("1.0", "end")
            self._txt.insert("1.0", new_val)
            self._modified = True
        except (re.error, tk.TclError, ValueError):
            pass

    def _buscar_proximo(self, direction: int = 1) -> None:
        """Busca a próxima ocorrência do padrão de busca atual.

        Args:
            direcao: ``1`` para frente, ``-1`` para trás.
        """
        pattern = getattr(self, "_ultimo_padrao_busca", "")
        if not pattern or not self._txt:
            return
        with contextlib.suppress(tk.TclError):
            pos_atual = self._txt.index("insert")
            if direction == 1:
                pos = self._txt.search(
                    pattern, f"{pos_atual}+1c", stopindex="end", regexp=True
                )
                if not pos:
                    pos = self._txt.search(
                        pattern, "1.0", stopindex="end", regexp=True)
            else:
                pos = self._txt.search(
                    pattern, pos_atual, stopindex="1.0", backwards=True, regexp=True
                )
                if not pos:
                    pos = self._txt.search(
                        pattern, "end", stopindex="1.0", backwards=True, regexp=True
                    )
            if pos:
                self._txt.mark_set("insert", pos)
                self._txt.see("insert")

    def _visual_key(self, sym: str, char: str) -> str | None:
        """Processa teclas no modo VISUAL / V-LINE.

        Args:
            sym:  Keysym da tecla.
            char: Caractere imprimível.

        Returns:
            Sempre ``"break"``.
        """
        txt = self._txt

        if sym == "Escape":
            with contextlib.suppress(tk.TclError):
                txt.tag_remove("sel", "1.0", "end")
            self._set_mode(NORMAL)
            return "break"

        movimentos = {
            ("h", "Left"): "insert-1c",
            ("l", "Right"): "insert+1c",
            ("j", "Down"): "insert+1l",
            ("k", "Up"): "insert-1l",
        }
        for teclas, dest in movimentos.items():
            if sym in teclas or char in teclas:
                with contextlib.suppress(tk.TclError):
                    txt.mark_set("insert", dest)
                self._update_visual_selection()
                return "break"

        if char == "y":
            with contextlib.suppress(tk.TclError):
                sel = txt.tag_ranges("sel")
                if sel:
                    self._register = txt.get(sel[0], sel[1])
                txt.tag_remove("sel", "1.0", "end")
            self._set_mode(NORMAL)
            return "break"

        if char == "d":
            with contextlib.suppress(tk.TclError):
                sel = txt.tag_ranges("sel")
                if sel:
                    self._register = txt.get(sel[0], sel[1])
                    txt.delete(sel[0], sel[1])
                txt.tag_remove("sel", "1.0", "end")
            self._modified = True
            self._set_mode(NORMAL)
            return "break"

        if char == "c":
            with contextlib.suppress(tk.TclError):
                sel = txt.tag_ranges("sel")
                if sel:
                    self._register = txt.get(sel[0], sel[1])
                    txt.delete(sel[0], sel[1])
                txt.tag_remove("sel", "1.0", "end")
            self._modified = True
            self._set_mode(INSERT)
            return "break"

        return "break"

    def _update_visual_selection(self) -> None:
        """Atualiza a tag ``sel`` entre o início da seleção visual e o cursor."""
        txt = self._txt
        if not txt:
            return
        with contextlib.suppress(tk.TclError):
            cur = txt.index("insert")
            start = self._visual_start
            if txt.compare(start, "<=", cur):
                a, b = start, cur + "+1c"
            else:
                a, b = cur, start + "+1c"
            if self._mode == VLINE:
                a_ln = txt.index(a).split(".", 1)[0]
                b_ln = txt.index(b).split(".", 1)[0]
                a = f"{a_ln}.0"
                b = f"{b_ln}.end+1c"
            txt.tag_remove("sel", "1.0", "end")
            txt.tag_add("sel", a, b)
