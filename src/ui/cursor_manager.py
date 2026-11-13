"""Codis - Gerenciador de Cursor Customizável.

Permite substituir o ponteiro do mouse do sistema por um cursor de software:
emoji, texto, imagem (PNG/SVG via Pillow) ou combinações.

Como funciona:
  - O cursor do sistema é ocultado (``root.config(cursor="none")``) na janela inteira.
  - Um Toplevel transparente e clicável segue o mouse em tempo real.
  - O Toplevel exibe o cursor escolhido como Label ou Canvas.
  - Cliques atravessam normalmente (WM_EX_TRANSPARENT no Windows,
    ``-type splash`` / ``-alpha 0.01`` no Linux/Mac para passthrough).

Suporte por plataforma:
  - Windows: transparência real via ``wm_attributes("-transparentcolor", ...)``
              + ``SetWindowLong`` para passthrough de eventos.
  - Linux/macOS: Toplevel ultra-fino (1px) + ``wm_attributes("-alpha", 0.99)``.

Limitações:
  - Emojis dependem da fonte disponível no SO.
  - Imagens requerem Pillow (``pip install Pillow``).
  - O cursor de software tem latência de ~1 frame (<=16ms) em relação ao real.

API pública:
    mgr = CursorManager(root, tmgr, cfg)
    mgr.set_cursor(CursorPreset.STAR)          # predefinição
    mgr.set_emoji("🔥")                         # emoji livre
    mgr.set_image(Path("cursor.png"), size=32) # imagem
    mgr.set_text("✦", color="#f9e2af")         # texto customizado
    mgr.set_system()                            # volta ao cursor do sistema
    mgr.enable(True/False)                      # liga/desliga sem trocar preset
"""

from __future__ import annotations

import contextlib
import logging
import sys
import tkinter as tk
from enum import StrEnum
from pathlib import Path
from typing import Any

from src.constants import FONT_UI

logger = logging.getLogger("Codis.CursorManager")

try:
    from PIL import Image, ImageTk  # type: ignore[import]

    _HAS_PIL = True
except ImportError:
    _HAS_PIL = False


class CursorPreset(StrEnum):
    """Cursor predefinidos prontos para uso."""

    SYSTEM = "system"
    DOT = "dot"
    CROSSHAIR = "crosshair"
    STAR = "star"
    HEART = "heart"
    FIRE = "fire"
    LIGHTNING = "lightning"
    DIAMOND = "diamond"
    ARROW_GLOW = "arrow_glow"
    POINTER_HEX = "pointer_hex"
    SKULL = "skull"
    RAINBOW = "rainbow"
    GHOST = "ghost"
    SWORD = "sword"
    MAGIC_WAND = "magic_wand"
    ROBOT = "robot"
    ALIEN = "alien"


_PRESET_DATA: dict[CursorPreset, tuple[str, int, str | None]] = {
    CursorPreset.DOT: ("•", 14, None),
    CursorPreset.CROSSHAIR: ("✛", 20, None),
    CursorPreset.STAR: ("✦", 22, "#f9e2af"),
    CursorPreset.HEART: ("♥", 22, "#f38ba8"),
    CursorPreset.FIRE: ("🔥", 24, None),
    CursorPreset.LIGHTNING: ("⚡", 24, None),
    CursorPreset.DIAMOND: ("◆", 22, "#89dceb"),
    CursorPreset.ARROW_GLOW: ("➤", 20, "#cba6f7"),
    CursorPreset.POINTER_HEX: ("⬡", 22, "#89b4fa"),
    CursorPreset.SKULL: ("💀", 24, None),
    CursorPreset.RAINBOW: ("🌈", 24, None),
    CursorPreset.GHOST: ("👻", 24, None),
    CursorPreset.SWORD: ("⚔", 22, "#a6e3a1"),
    CursorPreset.MAGIC_WAND: ("🪄", 24, None),
    CursorPreset.ROBOT: ("🤖", 24, None),
    CursorPreset.ALIEN: ("👽", 24, None),
}


class _SoftCursor:
    """Toplevel que segue o mouse exibindo o cursor escolhido."""

    _POLL_MS = 8

    def __init__(self, root: tk.Tk) -> None:
        self._root = root
        self._win: tk.Toplevel | None = None
        self._label: tk.Label | None = None
        self._canvas: tk.Canvas | None = None
        self._photo: Any = None
        self._job: str | None = None
        self._active = False
        self._offset_x = 0
        self._offset_y = 0

    def show(
        self,
        content: str | None,
        size: int,
        color: str,
        image: Path | None,
        offset: tuple[int, int],
    ) -> None:
        """Cria / atualiza o cursor de software."""
        self._offset_x, self._offset_y = offset
        self._destroy_win()
        self._active = True
        self._build_win(content, size, color, image)
        if self._win is None:
            self._active = False
            return
        self._poll()

    def hide(self) -> None:
        self._active = False
        if self._job:
            with contextlib.suppress(tk.TclError):
                self._root.after_cancel(self._job)
            self._job = None
        self._destroy_win()

    def _destroy_win(self) -> None:
        if self._win:
            with contextlib.suppress(tk.TclError):
                self._win.destroy()
            self._win = None
            self._label = None
            self._canvas = None
            self._photo = None

    def _build_win(
        self, content: str | None, size: int, color: str, image: Path | None
    ) -> None:
        win = tk.Toplevel(self._root)
        win.wm_overrideredirect(True)
        win.wm_attributes("-topmost", True)
        win.wm_geometry("+9999+9999")

        bg_key = "#010101"
        win.config(bg=bg_key)

        if image and _HAS_PIL and image.exists():
            self._build_image(win, bg_key, image, size)
        else:
            self._build_text(win, bg_key, content or "✦", size, color)

        self._win = win

        if sys.platform == "win32":
            with contextlib.suppress(tk.TclError):
                win.wm_attributes("-transparentcolor", bg_key)
                win.update_idletasks()

            win.update_idletasks()
            self._apply_click_passthrough(win)
        else:
            with contextlib.suppress(tk.TclError):
                win.wm_attributes("-alpha", 0.99)
            with contextlib.suppress(tk.TclError):
                win.wm_attributes("-type", "splash")

    def _apply_click_passthrough(self, win: tk.Toplevel) -> None:
        """Adiciona WS_EX_TRANSPARENT ao HWND real da janela (aditivo).

        Lê o estilo estendido atual e apenas liga o bit WS_EX_TRANSPARENT,
        sem reescrever WS_EX_LAYERED - esse já foi configurado pelo próprio
        Tk ao aplicar -transparentcolor, e sobrescrevê-lo apagaria a
        color-key (causa do "quadrado preto" sólido). Se a aplicação falhar,
        a janela é destruída em vez de permanecer um overlay -topmost opaco
        a cliques cobrindo a tela inteira - preferimos "sem cursor
        customizado" a "app travado".
        """
        try:
            import ctypes

            hwnd = int(win.winfo_id())
            if not hwnd:
                raise OSError("HWND inválido (0) após mapeamento da janela.")
            GWL_EXSTYLE = -20
            WS_EX_TRANSPARENT = 0x00000020
            user32 = ctypes.WinDLL("user32", use_last_error=True)
            old = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            if old & WS_EX_TRANSPARENT:
                return
            new = old | WS_EX_TRANSPARENT
            ctypes.set_last_error(0)
            user32.SetWindowLongW(hwnd, GWL_EXSTYLE, new)
            confirm = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            if not confirm & WS_EX_TRANSPARENT:
                raise OSError("WS_EX_TRANSPARENT não foi aplicado.")
        except Exception as exc:
            logger.warning(
                "Cursor de software: passthrough de cliques falhou (%s). "
                "Desativando overlay para evitar travamento da interface.",
                exc,
            )
            self._destroy_win()
            self._active = False

    def _build_text(
        self, win: tk.Toplevel, bg: str, text: str, size: int, color: str
    ) -> None:
        lbl = tk.Label(
            win,
            text=text,
            bg=bg,
            fg=color,
            font=(FONT_UI, size),
            padx=0,
            pady=0,
        )
        lbl.pack()
        self._label = lbl

    def _build_image(self, win: tk.Toplevel, bg: str, path: Path, size: int) -> None:
        try:
            img = Image.open(path).convert("RGBA")
            resample = getattr(
                Image, "LANCZOS", getattr(Image, "ANTIALIAS", 1))
            img = img.resize((size, size), resample)
            photo = ImageTk.PhotoImage(img)
            self._photo = photo
            lbl = tk.Label(win, image=photo, bg=bg, padx=0, pady=0)
            lbl.pack()
            self._label = lbl
        except Exception as exc:
            logger.warning("Imagem de cursor falhou: %s", exc)
            self._build_text(win, bg, "✦", size, "#ffffff")

    def _poll(self) -> None:
        if not self._active:
            return
        if not self._win:
            return
        try:
            x = self._root.winfo_pointerx() + self._offset_x
            y = self._root.winfo_pointery() + self._offset_y
            self._win.wm_geometry(f"+{x}+{y}")
        except tk.TclError:
            self._active = False
            self._job = None
            self._win = None
            return
        self._job = self._root.after(self._POLL_MS, self._poll)


class CursorManager:
    """Gerencia o cursor customizável para toda a janela do Codis."""

    def __init__(self, root: tk.Tk, tmgr: Any, cfg: Any) -> None:
        self._root = root
        self._tmgr = tmgr
        self._cfg = cfg
        self._soft = _SoftCursor(root)
        self._enabled = False
        self._current_preset: str = CursorPreset.SYSTEM
        self._current_emoji: str = "✦"
        self._current_size: int = 22
        self._current_color: str = "#cdd6f4"
        self._current_image: Path | None = None
        self._hotspot: tuple[int, int] = (0, 0)

        self._load_from_cfg()

    def set_system(self) -> None:
        """Restaura o cursor nativo do sistema operacional."""
        self._enabled = False
        self._current_preset = CursorPreset.SYSTEM
        self._soft.hide()
        self._restore_system_cursor()
        self._save_cfg()

    def set_preset(self, preset: CursorPreset | str) -> None:
        """Aplica uma das predefinições de cursor."""
        preset = CursorPreset(preset) if isinstance(preset, str) else preset
        self._current_preset = preset
        if preset == CursorPreset.SYSTEM:
            self.set_system()
            return
        emoji, size, color = _PRESET_DATA.get(preset, ("✦", 22, None))
        color = color or self._tmgr.theme.fg
        self._current_emoji = emoji
        self._current_size = size
        self._current_color = color
        self._current_image = None
        self._hotspot = (0, 0)
        self._apply()
        self._save_cfg()

    def set_emoji(self, emoji: str, size: int = 22, color: str | None = None) -> None:
        """Define um emoji livre como cursor."""
        self._current_preset = "__custom__"
        self._current_emoji = emoji
        self._current_size = size
        self._current_color = color or self._tmgr.theme.fg
        self._current_image = None
        self._apply()
        self._save_cfg()

    def set_text(self, text: str, size: int = 20, color: str | None = None) -> None:
        """Define um caractere / símbolo Unicode como cursor."""
        self.set_emoji(text, size, color)

    def set_image(
        self, path: Path | str, size: int = 32, hotspot: tuple[int, int] = (0, 0)
    ) -> None:
        """Define uma imagem (PNG, etc.) como cursor. Requer Pillow."""
        path = Path(path)
        if not _HAS_PIL:
            logger.warning(
                "Pillow não instalado - cursor de imagem indisponível.")
            return
        if not path.exists():
            logger.warning("Imagem de cursor não encontrada: %s", path)
            return
        self._current_preset = "__image__"
        self._current_image = path
        self._current_size = size
        self._hotspot = hotspot
        self._apply()
        self._save_cfg()

    def enable(self, on: bool) -> None:
        """Liga ou desliga o cursor customizado sem alterar o preset."""
        if on:
            if self._current_preset in (CursorPreset.SYSTEM, "__none__"):
                return
            self._apply()
        else:
            self._soft.hide()
            self._restore_system_cursor()
        self._enabled = on

    @property
    def current_preset(self) -> str:
        return self._current_preset

    def _apply(self) -> None:
        self._hide_system_cursor()
        self._soft.show(
            content=self._current_emoji,
            size=self._current_size,
            color=self._current_color,
            image=self._current_image,
            offset=self._hotspot,
        )
        self._enabled = True

    def _hide_system_cursor(self) -> None:
        """Oculta o cursor em todos os widgets da janela (inclusive filhos).

        root.config(cursor="none") não propaga para widgets que definem seu
        próprio cursor.  Percorremos a árvore inteira e forçamos "none" em
        cada widget - guardando o valor original para restauração posterior.
        """
        self._original_cursors: dict[str, str] = {}
        with contextlib.suppress(tk.TclError):
            self._set_cursor_recursive(self._root, "none")

    def _restore_system_cursor(self) -> None:
        saved = getattr(self, "_original_cursors", {})
        try:
            if saved:
                for widget_path, orig in saved.items():
                    try:
                        w = self._root.nametowidget(widget_path)
                        w.config(cursor=orig)
                    except (tk.TclError, KeyError):
                        pass
                self._original_cursors = {}
            else:
                self._root.config(cursor="")
        except tk.TclError:
            pass

    def _set_cursor_recursive(self, widget: tk.Widget, cursor: str) -> None:
        """Aplica *cursor* em *widget* e em todos os seus descendentes."""
        with contextlib.suppress(tk.TclError):
            try:
                orig = widget.cget("cursor")
            except tk.TclError:
                orig = ""
            path = str(widget)
            if path not in self._original_cursors:
                self._original_cursors[path] = orig
            widget.config(cursor=cursor)
        for child in widget.winfo_children():
            self._set_cursor_recursive(child, cursor)

    def _save_cfg(self) -> None:
        with contextlib.suppress(Exception):
            self._cfg.set("cursor_preset", self._current_preset)
            self._cfg.set("cursor_emoji", self._current_emoji)
            self._cfg.set("cursor_size", self._current_size)
            self._cfg.set("cursor_color", self._current_color)

    def _load_from_cfg(self) -> None:
        preset = self._cfg.get("cursor_preset", CursorPreset.SYSTEM)
        if preset == CursorPreset.SYSTEM:
            return
        self._current_emoji = self._cfg.get("cursor_emoji", "✦")
        self._current_size = self._cfg.get("cursor_size", 22)
        self._current_color = self._cfg.get("cursor_color", "#cdd6f4")
        with contextlib.suppress(Exception):
            self._apply()


class CursorPickerPanel(tk.Frame):
    """Painel visual para escolher e personalizar o cursor."""

    def __init__(self, parent: tk.Widget, mgr: CursorManager, tmgr: Any) -> None:
        t = tmgr.theme
        super().__init__(parent, bg=t.panel_bg)
        self._mgr = mgr
        self._tmgr = tmgr
        self._build(t)

    def _build(self, t) -> None:  # noqa: PLR0912
        for child in self.winfo_children():
            with contextlib.suppress(tk.TclError):
                child.destroy()

        hdr = tk.Frame(self, bg=t.panel_bg)
        hdr.pack(fill="x", padx=16, pady=(14, 4))
        tk.Label(
            hdr,
            text="🖱  Cursor Customizável",
            bg=t.panel_bg,
            fg=t.fg,
            font=(FONT_UI, 11, "bold"),
            anchor="w",
        ).pack(side="left")

        self._status_var = tk.StringVar(value=self._status_text())
        tk.Label(
            hdr,
            textvariable=self._status_var,
            bg=t.panel_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 8),
            anchor="e",
        ).pack(side="right")

        tk.Frame(self, bg=t.border, height=1).pack(fill="x", padx=16)

        tk.Label(
            self,
            text="  Predefinições",
            bg=t.panel_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 8, "bold"),
            anchor="w",
        ).pack(fill="x", padx=16, pady=(10, 2))

        grid_frame = tk.Frame(self, bg=t.panel_bg)
        grid_frame.pack(fill="x", padx=12, pady=(0, 8))

        presets = [
            (CursorPreset.SYSTEM, "⬚  Sistema"),
            (CursorPreset.DOT, "•  Ponto"),
            (CursorPreset.CROSSHAIR, "✛  Mira"),
            (CursorPreset.STAR, "✦  Estrela"),
            (CursorPreset.HEART, "♥  Coração"),
            (CursorPreset.FIRE, "🔥  Fogo"),
            (CursorPreset.LIGHTNING, "⚡  Raio"),
            (CursorPreset.DIAMOND, "◆  Diamante"),
            (CursorPreset.ARROW_GLOW, "➤  Seta"),
            (CursorPreset.POINTER_HEX, "⬡  Hex"),
            (CursorPreset.SKULL, "💀  Caveira"),
            (CursorPreset.GHOST, "👻  Fantasma"),
            (CursorPreset.SWORD, "⚔  Espada"),
            (CursorPreset.MAGIC_WAND, "🪄  Varinha"),
            (CursorPreset.ROBOT, "🤖  Robô"),
            (CursorPreset.ALIEN, "👽  Alien"),
        ]

        cols = 4
        for i, (preset, label) in enumerate(presets):
            row, col = divmod(i, cols)
            is_active = self._mgr.current_preset == preset
            btn = tk.Frame(
                grid_frame,
                bg=t.sel_bg if is_active else t.sidebar_bg,
                cursor="hand2",
                highlightthickness=1,
                highlightbackground=(
                    getattr(t, "accent", t.border) if is_active else t.border
                ),
            )
            btn.grid(row=row, column=col, padx=4, pady=4, sticky="ew")
            grid_frame.columnconfigure(col, weight=1)

            tk.Label(
                btn,
                text=label,
                bg=t.sel_bg if is_active else t.sidebar_bg,
                fg=t.sel_fg if is_active else t.fg,
                font=(FONT_UI, 9),
                anchor="w",
                padx=8,
                pady=6,
                cursor="hand2",
            ).pack(fill="x")

            def _pick(p=preset, b=btn):
                self._mgr.set_preset(p)
                self._status_var.set(self._status_text())
                self.after_idle(lambda: self._build(self._tmgr.theme))

            btn.bind("<Button-1>", lambda _e, fn=_pick: fn())
            for child in btn.winfo_children():
                child.bind("<Button-1>", lambda _e, fn=_pick: fn())

        tk.Frame(self, bg=t.border, height=1).pack(
            fill="x", padx=16, pady=(4, 8))

        tk.Label(
            self,
            text="  Emoji ou símbolo customizado",
            bg=t.panel_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 8, "bold"),
            anchor="w",
        ).pack(fill="x", padx=16, pady=(0, 4))

        custom_row = tk.Frame(self, bg=t.panel_bg)
        custom_row.pack(fill="x", padx=16, pady=(0, 8))

        self._emoji_var = tk.StringVar(value=self._mgr._current_emoji)
        tk.Entry(
            custom_row,
            textvariable=self._emoji_var,
            bg=t.editor_bg,
            fg=t.fg,
            insertbackground=t.cursor,
            font=(FONT_UI, 16),
            width=4,
            relief="flat",
            highlightthickness=1,
            highlightbackground=t.border,
        ).pack(side="left", padx=(0, 8))

        tk.Label(
            custom_row, text="Tamanho:", bg=t.panel_bg, fg=t.fg_muted, font=(FONT_UI, 9)
        ).pack(side="left")
        self._size_var = tk.IntVar(value=self._mgr._current_size)
        tk.Spinbox(
            custom_row,
            from_=10,
            to=64,
            textvariable=self._size_var,
            bg=t.editor_bg,
            fg=t.fg,
            buttonbackground=t.sidebar_bg,
            width=4,
            font=(FONT_UI, 9),
            relief="flat",
        ).pack(side="left", padx=(4, 12))

        tk.Label(
            custom_row, text="Cor:", bg=t.panel_bg, fg=t.fg_muted, font=(FONT_UI, 9)
        ).pack(side="left")
        self._color_var = tk.StringVar(value=self._mgr._current_color)
        color_entry = tk.Entry(
            custom_row,
            textvariable=self._color_var,
            bg=t.editor_bg,
            fg=t.fg,
            insertbackground=t.cursor,
            font=(FONT_UI, 9),
            width=9,
            relief="flat",
            highlightthickness=1,
            highlightbackground=t.border,
        )
        color_entry.pack(side="left", padx=(4, 8))

        self._preview_box = tk.Label(
            custom_row,
            text="  ",
            bg=self._mgr._current_color,
            width=2,
            relief="flat",
            cursor="hand2",
        )
        self._preview_box.pack(side="left", padx=(0, 8))
        self._preview_box.bind("<Button-1>", self._pick_color)
        self._color_var.trace_add("write", self._on_color_change)

        tk.Button(
            custom_row,
            text="Aplicar",
            bg=getattr(t, "accent", t.sel_bg),
            fg=t.sel_fg if hasattr(t, "sel_fg") else "#ffffff",
            font=(FONT_UI, 9),
            relief="flat",
            cursor="hand2",
            command=self._apply_custom,
            padx=10,
        ).pack(side="left")

        if _HAS_PIL:
            tk.Frame(self, bg=t.border, height=1).pack(
                fill="x", padx=16, pady=(4, 8))
            tk.Label(
                self,
                text="  Cursor de imagem (PNG / WebP / GIF)",
                bg=t.panel_bg,
                fg=t.fg_muted,
                font=(FONT_UI, 8, "bold"),
                anchor="w",
            ).pack(fill="x", padx=16, pady=(0, 4))

            img_row = tk.Frame(self, bg=t.panel_bg)
            img_row.pack(fill="x", padx=16, pady=(0, 8))

            self._img_var = tk.StringVar(
                value=str(
                    self._mgr._current_image) if self._mgr._current_image else ""
            )
            tk.Entry(
                img_row,
                textvariable=self._img_var,
                bg=t.editor_bg,
                fg=t.fg,
                insertbackground=t.cursor,
                font=(FONT_UI, 9),
                relief="flat",
                highlightthickness=1,
                highlightbackground=t.border,
            ).pack(side="left", fill="x", expand=True, padx=(0, 8))

            tk.Button(
                img_row,
                text="Procurar…",
                bg=t.sidebar_bg,
                fg=t.fg,
                font=(FONT_UI, 9),
                relief="flat",
                cursor="hand2",
                command=self._browse_image,
                padx=8,
            ).pack(side="left", padx=(0, 4))

            tk.Button(
                img_row,
                text="Usar imagem",
                bg=getattr(t, "accent", t.sel_bg),
                fg=t.sel_fg if hasattr(t, "sel_fg") else "#ffffff",
                font=(FONT_UI, 9),
                relief="flat",
                cursor="hand2",
                command=self._apply_image,
                padx=8,
            ).pack(side="left")
        else:
            tk.Label(
                self,
                text="  ℹ  Instale Pillow para usar cursores de imagem:  pip install Pillow",
                bg=t.panel_bg,
                fg=t.fg_muted,
                font=(FONT_UI, 8),
                anchor="w",
            ).pack(fill="x", padx=16, pady=4)

        tk.Frame(self, bg=t.border, height=1).pack(
            fill="x", padx=16, pady=(4, 8))
        footer = tk.Frame(self, bg=t.panel_bg)
        footer.pack(fill="x", padx=16, pady=(0, 12))
        tk.Button(
            footer,
            text="↩  Restaurar cursor do sistema",
            bg=t.sidebar_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 9),
            relief="flat",
            cursor="hand2",
            command=self._reset,
            padx=10,
        ).pack(side="left")

    def _status_text(self) -> str:
        p = self._mgr.current_preset
        if p == CursorPreset.SYSTEM:
            return "Sistema (padrão)"
        if p == "__image__":
            img = self._mgr._current_image
            return f"Imagem: {img.name if img else '?'}"
        emoji = self._mgr._current_emoji
        return f"Cursor: {emoji}  ·  {self._mgr._current_size}px"

    def _on_color_change(self, *_) -> None:
        color = self._color_var.get()
        with contextlib.suppress(tk.TclError):
            self._preview_box.config(bg=color)

    def _pick_color(self, _e=None) -> None:
        from tkinter import colorchooser

        result = colorchooser.askcolor(
            title="Cor do cursor",
            color=self._color_var.get(),
            parent=self,
        )
        if result and result[1]:
            self._color_var.set(result[1])

    def _apply_custom(self) -> None:
        emoji = self._emoji_var.get().strip() or "✦"
        size = max(10, min(64, self._size_var.get()))
        color = self._color_var.get().strip() or "#cdd6f4"
        self._mgr.set_emoji(emoji, size, color)
        self._status_var.set(self._status_text())

    def _browse_image(self) -> None:
        from tkinter import filedialog

        p = filedialog.askopenfilename(
            title="Selecionar imagem de cursor",
            filetypes=[
                ("Imagens", "*.png *.webp *.gif *.bmp *.jpg *.jpeg *.ico"),
                ("Todos", "*.*"),
            ],
        )
        if p:
            self._img_var.set(p)

    def _apply_image(self) -> None:
        path = self._img_var.get().strip()
        if path:
            self._mgr.set_image(Path(path), size=32)
            self._status_var.set(self._status_text())

    def _reset(self) -> None:
        self._mgr.set_system()
        self._status_var.set(self._status_text())
        self.after_idle(lambda: self._build(self._tmgr.theme))
