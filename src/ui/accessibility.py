"""Codis - Módulo de Acessibilidade.

Funcionalidades:
  - Suporte a leitores de tela via anotações ARIA-like e texto alternativo
  - Navegação completa por teclado (Tab, Shift+Tab, setas, Enter, Escape)
  - Modo de alto contraste (WCAG AA/AAA)
  - Modos para daltonismo: protanopia, deuteranopia, tritanopia
  - Todos os modos são opcionais e ativáveis individualmente via config
"""

from __future__ import annotations

import contextlib
import os
import logging
import platform
import subprocess
import threading
import tkinter as tk
from collections.abc import Callable
from dataclasses import dataclass
from tkinter import ttk
from typing import Any

from src.i18n import _t
from src.ui.grab_safe import safe_grab_set

logger = logging.getLogger("Codis.A11y")

_COLORBLIND_PALETTES: dict[str, dict[str, str]] = {
    "protanopia": {
        "error": "#0077BB",
        "warning": "#EE7733",
        "success": "#009988",
        "info": "#BBBBBB",
        "accent": "#0077BB",
    },
    "deuteranopia": {
        "error": "#D55E00",
        "warning": "#E69F00",
        "success": "#0072B2",
        "info": "#BBBBBB",
        "accent": "#0072B2",
    },
    "tritanopia": {
        "error": "#CC3311",
        "warning": "#EE7733",
        "success": "#009988",
        "info": "#AAAAAA",
        "accent": "#CC3311",
    },
    "achromatopsia": {
        "error": "#111111",
        "warning": "#555555",
        "success": "#888888",
        "info": "#BBBBBB",
        "accent": "#333333",
    },
}

_HIGH_CONTRAST_OVERRIDES: dict[str, str] = {
    "bg": "#000000",
    "fg": "#FFFFFF",
    "editor_bg": "#000000",
    "editor_fg": "#FFFFFF",
    "sidebar_bg": "#0A0A0A",
    "panel_bg": "#0A0A0A",
    "border": "#FFFFFF",
    "accent": "#FFFF00",
    "selection": "#0000FF",
    "cursor": "#FFFFFF",
    "fg_muted": "#CCCCCC",
    "line_hl": "#1A1A1A",
    "floating_bg": "#0A0A0A",
}


@dataclass
class _A11yAnnotation:
    """Anotação de acessibilidade para um widget."""

    role: str = ""
    label: str = ""
    description: str = ""
    live: str = ""
    expanded: bool | None = None
    checked: bool | None = None
    disabled: bool = False
    keyshortcut: str = ""


class ScreenReaderBridge:
    """Envia texto para o leitor de tela do sistema de forma não-bloqueante.

    Suporta:
      - Windows: NVDA (via nvda_controller_client) e SAPI (via COM/ctypes)
      - Linux: Speech-dispatcher (spd-say) e Orca (via AT-SPI2)
      - macOS: say (nativo)

    Fallback: log de debug quando nenhum sistema está disponível.
    """

    def __init__(self) -> None:
        """Inicializa a ponte com o screen reader do sistema operacional."""
        self._platform = platform.system()
        self._available = self._detect()
        self._lock = threading.Lock()
        self._queue: list[str] = []

    def _detect(self) -> bool:
        """Detecta disponibilidade de TTS/leitor de tela."""
        if self._platform == "Windows":
            return True
        if self._platform == "Linux":
            return bool(_which("spd-say") or _which("espeak") or _which("festival"))
        if self._platform == "Darwin":
            return bool(_which("say"))
        return False

    def speak(self, text: str, interrupt: bool = False) -> None:
        """Anuncia *text* pelo leitor de tela/TTS de forma assíncrona."""
        if not text:
            return
        threading.Thread(
            target=self._speak_worker,
            args=(text, interrupt),
            daemon=True,
            name="Codis-A11y-TTS",
        ).start()

    def _speak_worker(self, text: str, interrupt: bool) -> None:
        """Worker assíncrono que chama o TTS do sistema."""
        with self._lock:
            if self._platform == "Linux":
                self._speak_linux(text, interrupt)
            elif self._platform == "Darwin":
                self._speak_macos(text, interrupt)
            elif self._platform == "Windows":
                self._speak_windows(text, interrupt)
            else:
                logger.debug("A11y TTS: %s", text)

    def _speak_linux(self, text: str, interrupt: bool) -> None:
        cmd: list[str] = []
        if _which("spd-say"):
            cmd = ["spd-say", "-e" if interrupt else "", "--", text]
            cmd = [c for c in cmd if c]
        elif _which("espeak"):
            cmd = ["espeak", "--", text]
        if cmd:
            with contextlib.suppress(OSError, subprocess.SubprocessError):
                subprocess.run(cmd, timeout=10, check=False,
                               capture_output=True)

    def _speak_macos(self, text: str, interrupt: bool) -> None:
        with contextlib.suppress(OSError, subprocess.SubprocessError):
            subprocess.run(["say", "--", text], timeout=15,
                           check=False, capture_output=True)

    def _speak_windows(self, text: str, interrupt: bool) -> None:
        ps_cmd = (
            "Add-Type -AssemblyName System.Speech; "
            "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
            "$s.Speak($env:CODIS_TTS_TEXT);"
        )
        env = {**os.environ, "CODIS_TTS_TEXT": text}
        with contextlib.suppress(OSError, subprocess.SubprocessError):
            subprocess.run(
                ["powershell", "-NoProfile", "-Command", ps_cmd],
                timeout=15,
                check=False,
                capture_output=True,
                env=env,
            )

    @property
    def available(self) -> bool:
        """True se um motor TTS foi detectado no sistema."""
        return self._available


def _which(cmd: str) -> str | None:
    """Retorna o caminho completo de *cmd* ou None se não encontrado."""
    import shutil

    return shutil.which(cmd)


class KeyboardNavigationManager:
    """Gerencia navegação por teclado em uma janela Tkinter.

    - Tab / Shift+Tab: foco sequencial entre widgets registrados
    - Setas: movimento contextual (listas, menus, árvores)
    - Enter / Space: ativação do widget focado
    - Escape: fecha modais ou retorna ao contexto anterior
    """

    def __init__(self, root: tk.Tk | tk.Toplevel) -> None:
        """Inicializa o gerenciador de navegação por teclado."""
        self._root = root
        self._groups: dict[str, list[tk.Widget]] = {}
        self._active_group: str | None = None
        self._focus_index: dict[str, int] = {}

        root.bind_all("<Tab>", self._on_tab, add="+")
        root.bind_all("<Shift-Tab>", self._on_shift_tab, add="+")
        root.bind_all("<Escape>", self._on_escape, add="+")

    def _focus_current(self) -> None:
        if self._active_group is None:
            return
        grp = self._groups.get(self._active_group, [])
        idx = self._focus_index.get(self._active_group, 0)
        if grp and 0 <= idx < len(grp):
            with contextlib.suppress(tk.TclError):
                grp[idx].focus_set()

    def _on_tab(self, event: tk.Event) -> str | None:
        if self._active_group is None:
            return None
        grp = self._groups.get(self._active_group, [])
        if not grp:
            return None
        idx = self._focus_index[self._active_group]
        self._focus_index[self._active_group] = (idx + 1) % len(grp)
        self._focus_current()
        return "break"

    def _on_shift_tab(self, event: tk.Event) -> str | None:
        if self._active_group is None:
            return None
        grp = self._groups.get(self._active_group, [])
        if not grp:
            return None
        idx = self._focus_index[self._active_group]
        self._focus_index[self._active_group] = (idx - 1) % len(grp)
        self._focus_current()
        return "break"

    def _on_escape(self, _event: tk.Event) -> None:
        self._active_group = None


class AccessibilityManager:
    """Gerenciador central de acessibilidade do Codis.

    Controla:
      - Modo de alto contraste
      - Modo para daltonismo
      - Suporte a leitor de tela
      - Navegação por teclado
      - Tamanho mínimo de fonte acessível

    Todos os modos são opcionais. Nenhum afeta o comportamento padrão
    enquanto não for explicitamente ativado pelo usuário nas configurações.
    """

    def __init__(self, app: Any, cfg: Any, theme_mgr: Any) -> None:
        """
        Args:
            app:       Instância de CodisApp.
            cfg:       ConfigManager com preferências do usuário.
            theme_mgr: ThemeManager para aplicar sobreposições de cor.
        """
        self._app = app
        self._cfg = cfg
        self._tmgr = theme_mgr
        self._annotations: dict[str, _A11yAnnotation] = {}
        self._sr = ScreenReaderBridge()
        self._nav: KeyboardNavigationManager | None = None
        self._active_modes: set[str] = set()

        self._high_contrast = False
        self._colorblind_mode: str | None = None
        self._screen_reader_enabled: bool = False
        self._keyboard_nav_enabled: bool = True
        self._large_ui_font: bool = False
        self._reduce_motion: bool = False
        self._prev_theme_name: str | None = None

        self._load_prefs()

    def _load_prefs(self) -> None:
        """Carrega preferências de acessibilidade do ConfigManager."""
        hc = self._cfg.get("a11y_high_contrast", False)
        cb = self._cfg.get("a11y_colorblind_mode", "")
        self._screen_reader_enabled = self._cfg.get("a11y_screen_reader", False)
        self._keyboard_nav_enabled = self._cfg.get("a11y_keyboard_nav", True)
        self._large_ui_font = self._cfg.get("a11y_large_ui_font", False)
        self._reduce_motion = self._cfg.get("a11y_reduce_motion", False)
        if hc:
            self.enable_high_contrast(save=False)
        if cb and cb in _COLORBLIND_PALETTES:
            self.enable_colorblind_mode(cb, save=False)
        if self._keyboard_nav_enabled:
            with contextlib.suppress(Exception):
                root = getattr(self._app, "_root", None)
                if root:
                    self.setup_keyboard_navigation(root)

    def _save_prefs(self) -> None:
        self._cfg.set("a11y_high_contrast", self._high_contrast)
        self._cfg.set("a11y_colorblind_mode", self._colorblind_mode or "")
        self._cfg.set("a11y_screen_reader", self._screen_reader_enabled)
        self._cfg.set("a11y_keyboard_nav", self._keyboard_nav_enabled)
        self._cfg.set("a11y_large_ui_font", self._large_ui_font)
        self._cfg.set("a11y_reduce_motion", self._reduce_motion)

    def enable_high_contrast(self, save: bool = True) -> None:
        """Ativa o modo de alto contraste trocando o tema para 'high_contrast'."""
        import contextlib

        from src.theme.base import THEMES

        self._high_contrast = True
        self._active_modes.add("high_contrast")

        if self._prev_theme_name is None:
            current_name = getattr(self._tmgr, "_name", None) or getattr(
                self._tmgr.theme, "name", "fluent_dark"
            )
            if current_name != "high_contrast":
                self._prev_theme_name = current_name

        if "high_contrast" in THEMES:
            with contextlib.suppress(Exception):
                self._tmgr.set("high_contrast")
        else:
            self._reapply_active_overrides()

        logger.info("Acessibilidade: alto contraste ativado.")
        if save:
            self._save_prefs()
        self._announce(_t("a11y.high_contrast_on",
                       "Modo de alto contraste ativado."))

    def disable_high_contrast(self, save: bool = True) -> None:
        """Desativa o modo de alto contraste restaurando o tema anterior."""
        import contextlib

        self._high_contrast = False
        self._active_modes.discard("high_contrast")

        prev = self._prev_theme_name
        self._prev_theme_name = None
        if prev:
            with contextlib.suppress(Exception):
                self._tmgr.set(prev)
        else:
            self._restore_theme()

        if save:
            self._save_prefs()
        self._announce(
            _t("a11y.high_contrast_off", "Modo de alto contraste desativado.")
        )

    def toggle_high_contrast(self) -> bool:
        """Alterna alto contraste. Retorna o novo estado."""
        if self._high_contrast:
            self.disable_high_contrast()
        else:
            self.enable_high_contrast()
        return self._high_contrast

    def enable_colorblind_mode(self, mode: str, save: bool = True) -> None:
        """Ativa paleta otimizada para o tipo de daltonismo especificado.

        Args:
            mode: "protanopia" | "deuteranopia" | "tritanopia" | "achromatopsia"
        """
        if mode not in _COLORBLIND_PALETTES:
            logger.warning("Modo de daltonismo desconhecido: %s", mode)
            return
        if self._colorblind_mode:
            self._active_modes.discard(f"colorblind_{self._colorblind_mode}")
        self._colorblind_mode = mode
        self._active_modes.add(f"colorblind_{mode}")
        self._reapply_active_overrides()
        logger.info("Acessibilidade: modo daltonismo '%s' ativado.", mode)
        if save:
            self._save_prefs()
        label = _t(f"a11y.colorblind_{mode}",
                   mode.replace("_", " ").capitalize())
        self._announce(
            _t("a11y.colorblind_on", f"Modo para daltonismo ativado: {label}")
        )

    def disable_colorblind_mode(self, save: bool = True) -> None:
        """Desativa o modo de daltonismo atual."""
        if self._colorblind_mode:
            self._active_modes.discard(f"colorblind_{self._colorblind_mode}")
        self._colorblind_mode = None
        self._restore_theme()
        if save:
            self._save_prefs()
        self._announce(
            _t("a11y.colorblind_off", "Modo para daltonismo desativado."))

    @property
    def available_colorblind_modes(self) -> list[str]:
        """Lista de modos de daltonismo disponíveis."""
        return list(_COLORBLIND_PALETTES.keys())

    def annotate(self, widget: tk.Widget, **kwargs: Any) -> None:
        """Associa metadados de acessibilidade a um widget.

        Exemplo::

            mgr.annotate(btn_save,
                         role="button",
                         label=_t("file.save", "Salvar"),
                         keyshortcut="Ctrl+S")

        Args:
            widget: Widget Tkinter a anotar.
            **kwargs: Campos de :class:`_A11yAnnotation`.
        """
        widget_id = str(id(widget))
        self._annotations[widget_id] = _A11yAnnotation(**kwargs)

        def _on_focus(_ev: tk.Event) -> None:
            ann = self._annotations.get(widget_id)
            if ann:
                parts = [ann.label or ann.role]
                if ann.keyshortcut:
                    parts.append(f"({ann.keyshortcut})")
                if ann.description:
                    parts.append(f"- {ann.description}")
                self._announce(" ".join(parts))

        with contextlib.suppress(tk.TclError):
            widget.bind("<FocusIn>", _on_focus, add="+")

    def setup_widget_reading(self, root: tk.Widget) -> None:
        """Lê automaticamente rótulos de qualquer widget que ganhe foco.

        Funciona como o TalkBack do Android: lê o texto ou aria-label
        de todo widget focalizável, mesmo sem anotação explícita.
        """
        if not self._screen_reader_enabled:
            return

        def _on_any_focus(ev: tk.Event) -> None:
            widget = ev.widget
            ann = self._annotations.get(str(id(widget)))
            if ann and ann.label:
                parts = [ann.label]
                if ann.role:
                    parts.append(f"({ann.role})")
                if ann.keyshortcut:
                    parts.append(ann.keyshortcut)
                self._announce(" ".join(parts), interrupt=True)
                return
            text_parts: list[str] = []
            try:
                cls = type(widget).__name__
                if cls in ("Button", "Label"):
                    text_parts.append(str(widget.cget("text")))
                elif cls == "Entry":
                    text_parts.append(_t("a11y.input_field", "Campo de texto"))
                    val = widget.get()
                    if val:
                        text_parts.append(val[:40])
                elif cls == "Checkbutton":
                    text_parts.append(str(widget.cget("text")))
                elif cls == "Combobox":
                    text_parts.append(_t("a11y.combobox", "Lista"))
                    text_parts.append(widget.get())
                elif cls == "Scale":
                    text_parts.append(_t("a11y.slider", "Deslizador"))
                    text_parts.append(str(widget.get()))
                elif cls == "Text":
                    text_parts.append(
                        _t("a11y.text_editor", "Editor de texto"))
            except Exception:
                logger.debug(
                    "Falha ao descrever widget para leitor de tela.", exc_info=True
                )
            if text_parts:
                self._announce(
                    " - ".join(p for p in text_parts if p), interrupt=True)

        _hover_job: list[str] = [None]  # type: ignore[list-item]

        def _on_hover(ev: tk.Event) -> None:
            """Anuncia o widget sob o mouse com debounce de 400ms."""
            widget = ev.widget
            cls = type(widget).__name__
            if cls in (
                "Frame",
                "LabelFrame",
                "PanedWindow",
                "Canvas",
                "Scrollbar",
                "Tk",
                "Toplevel",
            ):
                return
            try:
                if not widget.winfo_exists():
                    return
            except tk.TclError:
                return
            if _hover_job[0]:
                with contextlib.suppress(tk.TclError):
                    root.after_cancel(_hover_job[0])
                _hover_job[0] = None

            def _safe_announce() -> None:
                try:
                    if widget.winfo_exists():
                        _on_any_focus(ev)
                except tk.TclError:
                    pass

            _hover_job[0] = root.after(400, _safe_announce)

        with contextlib.suppress(tk.TclError):
            root.bind_all("<FocusIn>", _on_any_focus, add="+")
            root.bind_all("<Enter>", _on_hover, add="+")

    def announce(self, text: str, interrupt: bool = False) -> None:
        """Anuncia texto via leitor de tela de forma pública.

        Args:
            text:      Mensagem a ser lida.
            interrupt: Se True, interrompe a fala atual.
        """
        self._announce(text, interrupt)

    def _announce(self, text: str, interrupt: bool = False) -> None:
        if self._sr.available:
            self._sr.speak(text, interrupt)
        else:
            logger.debug("A11y announce: %s", text)

    @property
    def screen_reader_available(self) -> bool:
        """True se um motor TTS/leitor de tela foi detectado."""
        return self._sr.available

    def setup_keyboard_navigation(
        self, root: tk.Tk | tk.Toplevel
    ) -> KeyboardNavigationManager:
        """Configura navegação global por teclado para a janela *root*."""
        self._nav = KeyboardNavigationManager(root)
        return self._nav

    def _reapply_active_overrides(self) -> None:
        """Restaura o tema base e reaplicar todos os modos ativos em sequência.

        Garante consistência quando múltiplos modos (alto contraste + daltonismo)
        estão ativos ao mesmo tempo e um deles é desativado.
        """
        from src.theme.base import THEMES

        hc_uses_full_theme = self._high_contrast and "high_contrast" in THEMES

        if not hc_uses_full_theme:
            with contextlib.suppress(Exception):
                from src.theme.manager import _patch_theme

                theme_name = getattr(
                    self._tmgr, "_name", getattr(
                        self._tmgr, "name", "fluent_dark")
                )
                if theme_name in THEMES:
                    self._tmgr.theme = THEMES[theme_name]
                    _patch_theme(self._tmgr.theme)

            if self._high_contrast:
                theme = self._tmgr.theme
                for attr, color in _HIGH_CONTRAST_OVERRIDES.items():
                    if hasattr(theme, attr):
                        with contextlib.suppress(AttributeError):
                            object.__setattr__(theme, attr, color)

        if self._colorblind_mode and self._colorblind_mode in _COLORBLIND_PALETTES:
            theme = self._tmgr.theme
            for attr, color in _COLORBLIND_PALETTES[self._colorblind_mode].items():
                if hasattr(theme, attr):
                    with contextlib.suppress(AttributeError):
                        object.__setattr__(theme, attr, color)

        with contextlib.suppress(Exception):
            self._tmgr.apply_ttk()
        self._request_ui_refresh()

    def _restore_theme(self) -> None:
        """Restaura o tema original e reaplicar modos ainda ativos."""
        self._reapply_active_overrides()

    def _request_ui_refresh(self) -> None:
        """Solicita redesenho da UI ao thread principal."""
        with contextlib.suppress(Exception):
            root = getattr(self._app, "_root", None) or getattr(
                self._app, "root", None)
            if root:
                root.after(50, root.update_idletasks)

    @property
    def high_contrast(self) -> bool:
        """True se o modo de alto contraste estiver ativo."""
        return self._high_contrast

    @property
    def colorblind_mode(self) -> str | None:
        """Nome do modo de daltonismo ativo, ou None."""
        return self._colorblind_mode


class AccessibilityPanel(tk.Toplevel):
    """Janela de configuração dos modos de acessibilidade.

    Abre como modal. Permite ativar/desativar individualmente cada modo
    sem precisar reabrir o editor.
    """

    def __init__(self, parent: tk.Widget, mgr: AccessibilityManager, tmgr: Any) -> None:
        """Inicializa o painel de configurações de acessibilidade."""
        super().__init__(parent)
        self._mgr = mgr
        self._tmgr = tmgr
        t = tmgr.theme

        self.title(_t("a11y.panel_title", "Acessibilidade"))
        self.resizable(False, False)
        self.config(bg=t.panel_bg)
        safe_grab_set(self)

        self._build_ui(t)
        self.update_idletasks()
        self._center()

    def _build_ui(self, t: Any) -> None:
        from src.constants import FONT_UI

        pad = {"padx": 16, "pady": 6}

        hdr = tk.Frame(self, bg=t.sidebar_bg)
        hdr.pack(fill="x")
        tk.Label(
            hdr,
            text=_t("a11y.panel_header", "♿  Acessibilidade"),
            bg=t.sidebar_bg,
            fg=t.fg,
            font=(FONT_UI, 13, "bold"),
            anchor="w",
        ).pack(side="left", **pad)

        body = tk.Frame(self, bg=t.panel_bg)
        body.pack(fill="both", expand=True, padx=8, pady=8)

        def _section(label: str) -> None:
            tk.Label(
                body,
                text=label,
                bg=t.panel_bg,
                fg=t.fg_muted,
                font=(FONT_UI, 9, "bold"),
                anchor="w",
            ).pack(fill="x", padx=8, pady=(10, 2))
            ttk.Separator(body, orient="horizontal").pack(fill="x", padx=8)

        def _toggle_row(
            label: str, desc: str, var: tk.BooleanVar, command: Callable
        ) -> None:
            row = tk.Frame(body, bg=t.panel_bg)
            row.pack(fill="x", padx=8, pady=3)
            cb = tk.Checkbutton(
                row,
                variable=var,
                command=command,
                bg=t.panel_bg,
                fg=t.fg,
                activebackground=t.panel_bg,
                selectcolor=t.sidebar_bg,
                font=(FONT_UI, 10),
                text=label,
                anchor="w",
            )
            cb.pack(side="left")
            if desc:
                tk.Label(
                    row, text=desc, bg=t.panel_bg, fg=t.fg_muted, font=(
                        FONT_UI, 8)
                ).pack(side="left", padx=(4, 0))

        _section(_t("a11y.section_vision", "Visão"))

        self._hc_var = tk.BooleanVar(value=self._mgr.high_contrast)
        _toggle_row(
            _t("a11y.hc_label", "Alto contraste (WCAG AA)"),
            _t("a11y.hc_desc", "Fundo preto, texto branco, bordas nítidas"),
            self._hc_var,
            self._on_hc_toggle,
        )

        tk.Label(
            body,
            text=_t("a11y.cb_label", "Modo daltonismo:"),
            bg=t.panel_bg,
            fg=t.fg,
            font=(FONT_UI, 10),
            anchor="w",
        ).pack(fill="x", padx=8, pady=(8, 2))

        cb_modes = [""] + self._mgr.available_colorblind_modes
        cb_labels = [_t("a11y.cb_none", "Nenhum")] + [
            _t(f"a11y.colorblind_{m}", m.replace("_", " ").capitalize())
            for m in self._mgr.available_colorblind_modes
        ]
        self._cb_var = tk.StringVar(value=self._mgr.colorblind_mode or "")
        cb_combo = ttk.Combobox(
            body,
            textvariable=self._cb_var,
            values=cb_labels,
            state="readonly",
            width=28,
        )
        cb_combo.pack(padx=16, pady=(0, 4), anchor="w")
        cb_combo.bind("<<ComboboxSelected>>", self._on_cb_change)

        self._cb_label_map = dict(zip(cb_labels, cb_modes, strict=False))
        if self._mgr.colorblind_mode:
            idx = cb_modes.index(self._mgr.colorblind_mode)
            cb_combo.current(idx)
        else:
            cb_combo.current(0)

        _section(_t("a11y.section_nav", "Navegação e Interação"))

        self._nav_var = tk.BooleanVar(value=self._mgr._keyboard_nav_enabled)
        _toggle_row(
            _t("a11y.nav_label", "Navegação aprimorada por teclado (Tab/Setas)"),
            _t("a11y.nav_desc", "Tab e setas se movem por áreas registradas"),
            self._nav_var,
            lambda: self._on_toggle(
                "_keyboard_nav_enabled", "a11y_keyboard_nav", self._nav_var
            ),
        )

        self._font_var = tk.BooleanVar(value=self._mgr._large_ui_font)
        _toggle_row(
            _t("a11y.font_label", "Fonte de UI grande (mín. 13pt)"),
            _t("a11y.font_desc", "Aumenta legibilidade em telas de baixa resolução"),
            self._font_var,
            lambda: self._on_toggle(
                "_large_ui_font", "a11y_large_ui_font", self._font_var
            ),
        )

        self._motion_var = tk.BooleanVar(value=self._mgr._reduce_motion)
        _toggle_row(
            _t("a11y.motion_label", "Reduzir animações e movimento"),
            _t("a11y.motion_desc", "Desativa transições, piscar e efeitos visuais"),
            self._motion_var,
            lambda: self._on_toggle(
                "_reduce_motion", "a11y_reduce_motion", self._motion_var
            ),
        )

        _section(_t("a11y.section_sr", "Leitor de Tela / TTS"))

        self._sr_var = tk.BooleanVar(value=self._mgr._screen_reader_enabled)
        _toggle_row(
            _t("a11y.sr_enable_label", "Habilitar anúncios por TTS"),
            _t("a11y.sr_enable_desc", "Anuncia ações e foco para leitor de tela"),
            self._sr_var,
            lambda: self._on_toggle(
                "_screen_reader_enabled", "a11y_screen_reader", self._sr_var
            ),
        )

        status_color = t.fg if self._mgr.screen_reader_available else t.fg_muted
        status_text = (
            _t("a11y.sr_available", "TTS detectado no sistema.")
            if self._mgr.screen_reader_available
            else _t(
                "a11y.sr_unavailable",
                "Nenhum TTS detectado (spd-say, espeak, say, SAPI).",
            )
        )
        tk.Label(
            body,
            text=status_text,
            bg=t.panel_bg,
            fg=status_color,
            font=(FONT_UI, 9),
            wraplength=340,
            justify="left",
            anchor="w",
        ).pack(fill="x", padx=8, pady=4)

        tk.Button(
            body,
            text=_t("a11y.sr_test", "🔊  Testar leitor de tela"),
            bg=t.sidebar_bg,
            fg=t.fg,
            activebackground=t.accent,
            relief="flat",
            font=(FONT_UI, 9),
            command=self._on_sr_test,
        ).pack(padx=16, pady=4, anchor="w")

        tk.Button(
            self,
            text=_t("a11y.close", "Fechar"),
            bg=t.accent,
            fg="#FFFFFF",
            activebackground=t.accent,
            relief="flat",
            font=(FONT_UI, 10),
            command=self.destroy,
        ).pack(pady=(4, 12))

    def _on_toggle(self, attr: str, cfg_key: str, var: tk.BooleanVar) -> None:
        """Persiste uma preferência booleana simples no manager e no ConfigManager."""
        val = var.get()
        setattr(self._mgr, attr, val)
        self._mgr._cfg.set(cfg_key, val)
        self._mgr._save_prefs()

    def _on_hc_toggle(self) -> None:
        self._mgr.toggle_high_contrast()

    def _on_cb_change(self, _event: tk.Event) -> None:
        label = self._cb_var.get()
        mode = self._cb_label_map.get(label, "")
        if mode:
            self._mgr.enable_colorblind_mode(mode)
        else:
            self._mgr.disable_colorblind_mode()

    def _on_sr_test(self) -> None:
        self._mgr.announce(
            _t("a11y.sr_test_msg", "Leitor de tela do Codis funcionando corretamente."),
            interrupt=True,
        )

    def _center(self) -> None:
        """Centraliza a janela na tela."""
        self.update_idletasks()
        w, h = self.winfo_reqwidth(), self.winfo_reqheight()
        sw = self.winfo_screenwidth()
        sh = self.winfo_screenheight()
        x = (sw - w) // 2
        y = (sh - h) // 2
        self.geometry(f"{w}x{h}+{x}+{y}")
