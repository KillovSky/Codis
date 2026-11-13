"""Mixin ``_AppToolsMixin`` - ferramentas auxiliares e painéis integrados do Codis.

Agrupa a lógica de abertura e gerenciamento dos painéis opcionais: terminal,
colaboração, gerenciador de pacotes, runner de código, SQL Studio, entre outros.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import re
import subprocess
import sys
import threading
import time
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import TYPE_CHECKING, Any

from src.constants import (
    APP_CODENAME,
    APP_NAME,
    APP_VERSION,
    FONT_MONO,
    FONT_UI,
    LANG_LABEL,
    PLUGINS_DIR,
    SESSIONS_FILE,
)
from src.editor.pane import EditorPane
from src.i18n import _t
from src.theme.base import THEMES
from src.ui.color_picker import ColorPickerPopup, detect_colors_in_line
from src.ui.file_timeline import FileTimeline
from src.ui.grab_safe import safe_grab_set
from src.ui.welcome import WelcomeScreen
from src.updater.dialog import open_update_dialog

if TYPE_CHECKING:
    pass

logger = logging.getLogger("Codis")

__all__ = ["_AppToolsMixin"]


class _AppToolsMixin:
    """Mixin de Tools do CodisApp."""

    def _open_file_timeline(self) -> None:
        """Abre a linha do tempo (backups) do arquivo atual."""
        e = self._e()
        if e is None or e.path is None:
            return
        tw = getattr(self, "_timeline_win", None)
        if tw is not None:
            with contextlib.suppress(tk.TclError):
                if tw.winfo_exists():
                    tw.deiconify()
                    tw.focus_force()
                    return
        content = (
            e.get_content()
            if hasattr(e, "get_content")
            else e.text.get("1.0", "end-1c")
        )
        win = FileTimeline(
            self._root,
            self._tmgr,
            filepath=str(e.path),
            current_content=content,
            on_restore=lambda c: self._restore_from_timeline(e, c),
        )
        self._timeline_win = win

    @staticmethod
    def _restore_from_timeline(editor: EditorPane, content: str) -> None:
        """Restaura o conteúdo do editor a partir de uma versão do timeline."""
        editor.text.delete("1.0", "end")
        editor.text.insert("1.0", content)
        editor.text.edit_modified(True)
        editor.modified = True

    def _setup_autosave(self) -> None:
        """Configura o temporizador de autosave."""
        if self._autosave_job:
            with contextlib.suppress(tk.TclError):
                self._root.after_cancel(self._autosave_job)
            self._autosave_job = None
        if self._cfg.get("autosave", False):
            ms = max(5, self._cfg.get("autosave_delay", 30)) * 1000
            self._autosave_job = self._root.after(ms, self._autosave_tick)

    def _setup_tracker_heartbeat(self) -> None:
        """Inicia o loop de heartbeat do Code Time Tracker (10 s)."""
        try:
            if not hasattr(self, "_tracker_model") or self._tracker_model is None:
                return
            ms = 10_000

            def _tick():
                try:
                    e = (
                        self._active_editor()
                        if hasattr(self, "_active_editor")
                        else None
                    )
                    path = str(e.path) if (e and e.path) else None
                    ws = str(self._workspace) if self._workspace else None
                    self._tracker_model.on_activity(path, ws)
                    self._tracker_model.save()
                    tw = getattr(self, "_tracker_win", None)
                    if tw is not None:
                        try:
                            if tw.winfo_exists():
                                for child in tw.winfo_children():
                                    if hasattr(child, "_refresh"):
                                        child._refresh()
                                        break
                        except Exception:
                            logger.debug(
                                "Falha ao atualizar time tracker widget.", exc_info=True
                            )
                except Exception:
                    logger.debug(
                        "Falha no tick do timer da barra de status.", exc_info=True
                    )
                self._root.after(ms, _tick)

            self._root.after(ms, _tick)
        except Exception as exc:
            logger.debug("tracker heartbeat: %s", exc)

    def _autosave_tick(self) -> None:
        """Executa o autosave a cada intervalo."""
        failed: list[str] = []
        for e in self._editors:
            if e.modified and e.path:
                try:
                    ok = e.save() is not False
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Autosave falhou em '%s': %s", e.path, exc)
                    ok = False
                if not ok:
                    failed.append(e.path.name)
        if failed:
            with contextlib.suppress(Exception):
                self._status.notify(_t("msg.autosave_failed", names=", ".join(failed),
                                       default="Autosave falhou: {names}"), level="warn")
        self._setup_autosave()

    def _toggle_autosave(self) -> None:
        """Alterna a configuração de autosave."""
        self._cfg.set("autosave", self._autosave_var.get())
        self._cfg.save()
        self._setup_autosave()

    def _restore_session_or_welcome(self) -> None:
        """Restaura a sessão anterior ou exibe a tela de boas-vindas."""
        if not self._cfg.get("session_restore", True):
            if self._cfg.get("show_welcome", True):
                self._show_welcome()
            return

        session: dict[str, Any] = {}
        try:
            if SESSIONS_FILE.exists():
                parsed = json.loads(SESSIONS_FILE.read_text(encoding="utf-8"))
                if isinstance(parsed, dict):
                    session = parsed
                else:
                    raise ValueError("session.json com estrutura inválida")
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            logger.debug("Sessão inválida (%s) - iniciando limpo.", exc)
            with contextlib.suppress(OSError):
                if SESSIONS_FILE.exists():
                    SESSIONS_FILE.rename(
                        SESSIONS_FILE.with_suffix(".json.bak"))

        if session.get("workspace"):
            p = Path(session["workspace"])
            if p.is_dir():
                with contextlib.suppress(tk.TclError):
                    self._set_workspace(p)

        restored = False
        for fpath in session.get("files", []):
            p = Path(fpath)
            if p.is_file():
                with contextlib.suppress(tk.TclError):
                    self._open_file(p)
                    restored = True

        active = session.get("active_tab", 0)
        if restored and 0 <= active < len(self._editors):
            with contextlib.suppress(tk.TclError):
                self._select_tab(active)
        elif not restored and self._cfg.get("show_welcome", True):
            self._show_welcome()

    def _save_session(self) -> None:
        """Salva a sessão atual (arquivos abertos, workspace, etc.) no disco."""
        try:
            data = {
                "workspace": str(self._workspace) if self._workspace else None,
                "files": [str(e.path) for e in self._editors if e.path],
                "active_tab": max(0, self._active_idx),
                "codis_ver": APP_VERSION,
            }
            SESSIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
            tmp = SESSIONS_FILE.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
            tmp.replace(SESSIONS_FILE)
        except OSError as exc:
            logger.debug("Erro ao salvar sessão: %s", exc)

    def _load_plugins(self) -> None:
        """Carrega todos os plugins instalados e registra suas contribuições."""
        try:
            PLUGINS_DIR.mkdir(parents=True, exist_ok=True)
            self._plugin_loader.load_all()
            n = len(self._plugin_loader.plugins)
            errs = len(self._plugin_loader.errors)
            if n:
                self._output_panel.write(
                    f"Plugins carregados: {n}", "Extensions", "info", timestamp=True
                )
            if errs:
                self._output_panel.write(
                    f"Plugins com falha: {errs} - verifique o log",
                    "Extensions",
                    "warn",
                    timestamp=True,
                )
                for pid, tb in self._plugin_loader.errors.items():
                    logger.warning("Plugin '%s':\n%s", pid, tb)
        except (OSError, ImportError, RuntimeError) as exc:
            logger.warning("Plugin load error: %s", exc)

    def _install_plugin(self) -> None:
        """Delega a instalação ao PluginPanel (que possui o fluxo de sandbox)."""
        with contextlib.suppress(AttributeError):
            self._plugin_panel._install()
            return

        paths = filedialog.askopenfilenames(
            title=_t("hc.app.instalar_plugin"),
            filetypes=[("Plugins Codis", "*.py *.zip"), ("Todos", "*.*")],
            parent=self._root,
        )
        for p in paths:
            try:
                self._plugin_loader.install_from_path(Path(p))
            except (OSError, ValueError) as exc:
                messagebox.showerror(_t("err.install_plugin"), str(exc))
        with contextlib.suppress(AttributeError, tk.TclError):
            self._plugin_panel.refresh()

    def _fire_hook(self, event: str, data: Any = None) -> None:
        """Dispara um hook de plugin e publica no EventBus RESA.

        Além do ``plugin.hook`` genérico, emite o tópico semântico correto
        (ex.: ``file.save``, ``editor.change``) para que streams reativos
        """
        kui = getattr(self, "_kui", None)
        if kui is not None:
            with contextlib.suppress(Exception):
                kui.on_app_event(event, data)
        for handler in self._plugin_hooks.get(event, []):
            try:
                handler(data)
            except Exception as exc:
                plugin_name = getattr(handler, "__module__", "unknown_plugin")
                msg = f"[Plugin error] {plugin_name} / {event}: {exc}"
                if hasattr(self, "_output_panel"):
                    self._output_panel.write(
                        msg, channel="Codis", level="error")
                else:
                    import traceback

                    traceback.print_exc()
                if self._bus is not None:
                    self._bus.emit(
                        "plugin.error",
                        {"plugin": plugin_name,
                            "event": event, "error": str(exc)},
                        source="CodisApp",
                    )
        if self._bus is not None:
            safe_data = (
                data
                if isinstance(data, (dict, str, int, float, bool, type(None)))
                else str(data)
            )
            payload = {"event": event, "data": safe_data}

            semantic = self._hook_to_kind.get(event)
            if semantic:
                self._bus.emit(semantic, payload, source="CodisApp")

            self._bus.emit("plugin.hook", payload, source="CodisApp")

    def _emit_bus_metrics(self) -> None:
        """Emite métricas do EventBus no canal PERF e reagenda a próxima emissão."""
        if self._bus is None:
            return
        try:
            metrics = self._bus.metrics()
            if metrics:
                self._bus.emit(
                    "perf.metric",
                    {"bus_metrics": metrics},
                    source="CodisApp",
                )
        except Exception:
            logger.debug("Suppressed error", exc_info=True)
        finally:
            self._root.after(30_000, self._emit_bus_metrics)

    def _hide_classic_welcome(self) -> None:
        """Esconde a WelcomeScreen clássica (com o K-UI ativo só a KWelcome existe)."""
        w = getattr(self, "_welcome", None)
        if w is None:
            return
        with contextlib.suppress(tk.TclError):
            if w.winfo_exists() and w.winfo_manager():
                w.pack_forget()

    def _present_start_screen(self) -> None:
        """Mostra a tela inicial do modo atual (K-UI ou clássico) - ponto único."""
        if self._kui and getattr(self._kui, "_enabled", False):
            self._show_welcome()
            return
        with contextlib.suppress(tk.TclError):
            self._welcome.pack(fill="both", expand=True)

    def _show_welcome(self) -> None:
        """Exibe a tela de boas-vindas.

        Se o K-UI estiver ativo, usa a KWelcome cosmica (cosmos pleno).
        Caso contrario, usa a WelcomeScreen classica.
        """
        recent = [str(p) for p in self._cfg.get_recent_files()[:12]]

        if self._kui and getattr(self._kui, "_enabled", False):
            self._hide_classic_welcome()
            for e in self._editors:
                with contextlib.suppress(tk.TclError):
                    e.pack_forget()
            self._active_idx = -1
            self._active_pane = None
            with contextlib.suppress(tk.TclError):
                self._tabbar.set_active(-1)
            self._update_title()

            self._kui.show_welcome(
                on_new=self.new_file,
                on_open=self.open_file_dialog,
                on_folder=self.open_folder_dialog,
                on_recent=self._open_file,
                recent_files=recent,
            )
            return

        if hasattr(self, "_welcome") and self._welcome.winfo_exists():
            self._welcome.destroy()
        self._welcome = WelcomeScreen(
            self._editor_container,
            self._tmgr,
            self._cfg,
            on_new=self.new_file,
            on_open_file=self.open_file_dialog,
            on_open_folder=self.open_folder_dialog,
            on_open_recent=self._open_file,
            recent_files=recent,
            on_clone_success=self._on_clone_success,
            on_log=lambda msg, ch, lv: self._output_panel.write(
                msg, channel=ch, level=lv, timestamp=True
            ),
        )
        for e in self._editors:
            e.pack_forget()
        self._active_idx = -1
        self._active_pane = None
        self._welcome.pack(fill="both", expand=True)
        self._tabbar.set_active(-1)
        self._update_title()

    def _on_clone_success(self, dest: Path) -> None:
        """Chamado quando um clone de repositório é concluído com sucesso."""
        try:
            self._show_panel("output")
            self._output_panel.add_channel("Git Clone")
        except (tk.TclError, AttributeError):
            pass
        if dest.is_dir():
            self._set_workspace(dest)

    def _about_dialog(self) -> None:
        """Exibe o diálogo 'Sobre' com design completo, links e info do sistema."""
        t = self._tmgr.theme
        win = tk.Toplevel(self._root)
        win.title(_t("about.title"))
        win.resizable(True, True)
        win.config(bg=t.floating_bg)
        win.wm_attributes("-topmost", True)
        safe_grab_set(win)
        win.minsize(520, 540)

        hero = tk.Frame(win, bg=t.activity_bg, highlightthickness=0)
        hero.pack(fill="x")

        inner_hero = tk.Frame(hero, bg=t.activity_bg)
        inner_hero.pack(padx=32, pady=(28, 22))

        logo_lbl = tk.Label(
            inner_hero, text="⬡", bg=t.activity_bg, fg=t.btn_primary, font=(FONT_UI, 36)
        )
        logo_lbl.pack()
        tk.Label(
            inner_hero,
            text=APP_NAME,
            bg=t.activity_bg,
            fg=t.fg,
            font=(FONT_UI, 26, "bold"),
        ).pack(pady=(2, 0))

        ver_frame = tk.Frame(inner_hero, bg=t.activity_bg)
        ver_frame.pack(pady=(6, 0))
        tk.Label(
            ver_frame,
            text=f" v{APP_VERSION} ",
            bg=t.btn_primary,
            fg="#000",
            font=(FONT_UI, 8, "bold"),
            padx=6,
            pady=2,
        ).pack(side="left")
        tk.Label(
            ver_frame,
            text=f"  {APP_CODENAME}",
            bg=t.activity_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 9),
        ).pack(side="left")

        tk.Label(
            inner_hero,
            text=_t("about.tagline"),
            bg=t.activity_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 9),
        ).pack(pady=(8, 0))
        tk.Label(
            inner_hero,
            text=f"{_t('about.author')}  -  {_t('about.license')}",
            bg=t.activity_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 8),
        ).pack(pady=(3, 0))

        color_bar = tk.Frame(hero, bg=t.activity_bg, height=4)
        color_bar.pack(fill="x")
        accent_colors = [
            t.btn_primary,
            getattr(t, "ok_fg", "#4ade80"),
            getattr(t, "warn_fg", "#facc15"),
            getattr(t, "err_fg", "#f87171"),
            t.btn_primary,
        ]
        for clr in accent_colors:
            tk.Frame(color_bar, bg=clr, height=4).pack(
                side="left", fill="both", expand=True
            )

        body_canvas = tk.Canvas(win, bg=t.floating_bg,
                                highlightthickness=0, bd=0)
        body_scroll = tk.Scrollbar(
            win, orient="vertical", command=body_canvas.yview)
        body_canvas.configure(yscrollcommand=body_scroll.set)
        body_scroll.pack(side="right", fill="y")
        body_canvas.pack(fill="both", expand=True)

        body = tk.Frame(body_canvas, bg=t.floating_bg)
        body_win = body_canvas.create_window((0, 0), window=body, anchor="nw")

        def _on_body_resize(evt=None):
            body_canvas.configure(scrollregion=body_canvas.bbox("all"))

        def _on_canvas_resize(evt):
            body_canvas.itemconfig(body_win, width=evt.width)

        def _on_mwheel(evt):
            body_canvas.yview_scroll(int(-1 * evt.delta / 120), "units")

        body.bind("<Configure>", _on_body_resize)
        body_canvas.bind("<Configure>", _on_canvas_resize)
        body_canvas.bind("<MouseWheel>", _on_mwheel)

        def _section_hdr(txt: str) -> None:
            frm = tk.Frame(body, bg=t.floating_bg)
            frm.pack(fill="x", padx=24, pady=(18, 6))
            tk.Label(
                frm, text=txt, bg=t.floating_bg, fg=t.fg, font=(
                    FONT_UI, 10, "bold")
            ).pack(side="left")
            tk.Frame(frm, bg=t.border, height=1).pack(
                side="left", fill="x", expand=True, padx=(10, 0), pady=5
            )

        def _link_btn(parent, label: str, url: str, primary: bool = False) -> tk.Label:
            bg = t.btn_primary if primary else t.activity_bg
            fg = "#000" if primary else t.fg
            lbl = tk.Label(
                parent,
                text=label,
                bg=bg,
                fg=fg,
                font=(FONT_UI, 9, "bold" if primary else "normal"),
                padx=16,
                pady=7,
                cursor="hand2",
                highlightthickness=1 if not primary else 0,
                highlightbackground=t.border if not primary else bg,
            )
            lbl.bind("<Button-1>", lambda _: webbrowser.open(url))
            lbl.bind(
                "<Enter>",
                lambda _: lbl.config(
                    bg=(
                        t.btn_primary
                        if primary
                        else t.sel_bg if hasattr(t, "sel_bg") else t.border
                    ),
                    fg="#000" if primary else t.btn_primary,
                ),
            )
            lbl.bind("<Leave>", lambda _: lbl.config(bg=bg, fg=fg))
            return lbl

        _section_hdr(_t("about.links"))

        links_grid = tk.Frame(body, bg=t.floating_bg)
        links_grid.pack(fill="x", padx=24)
        links_grid.columnconfigure(0, weight=1)
        links_grid.columnconfigure(1, weight=1)

        LINKS = [
            (_t("about.github_codis"), "https://github.com/KillovSky/Codis", True),
            (_t("about.github_dev"), "https://github.com/KillovSky", False),
            (_t("about.linktree"), "https://linktr.ee/killovsky", False),
            (_t("about.npm"), "https://www.npmjs.com/~killovsky", False),
            (_t("about.discord"), "https://discord.gg/killovsky", False),
        ]
        for i, (label, url, primary) in enumerate(LINKS):
            r, c = divmod(i, 2)
            btn = _link_btn(links_grid, label, url, primary)
            btn.grid(row=r, column=c, padx=4, pady=4, sticky="ew")

        _section_hdr(_t("about.sysinfo"))

        sysinfo_grid = tk.Frame(body, bg=t.floating_bg)
        sysinfo_grid.pack(fill="x", padx=24, pady=(0, 8))
        sysinfo_grid.columnconfigure(0, weight=1)
        sysinfo_grid.columnconfigure(1, weight=1)

        sysinfo_items = [
            ("🐍  Python", sys.version.split()[0]),
            ("🪟  Tkinter", str(tk.TkVersion)),
            ("🖥  Platform", sys.platform),
            ("Themes", str(len(THEMES))),
            ("🌐  Languages", str(len(LANG_LABEL))),
            ("🔌  Plugins", str(len(self._plugin_loader.plugins))),
        ]
        for i, (k, v) in enumerate(sysinfo_items):
            r, c = divmod(i, 2)
            cell = tk.Frame(
                sysinfo_grid,
                bg=t.activity_bg,
                highlightthickness=1,
                highlightbackground=t.border,
            )
            cell.grid(row=r, column=c, padx=4, pady=3, sticky="ew")
            inn = tk.Frame(cell, bg=t.activity_bg, padx=10, pady=6)
            inn.pack(fill="x")
            tk.Label(
                inn,
                text=k,
                bg=t.activity_bg,
                fg=t.fg_muted,
                font=(FONT_UI, 7),
                anchor="w",
            ).pack(fill="x")
            tk.Label(
                inn,
                text=v,
                bg=t.activity_bg,
                fg=t.fg,
                font=(FONT_UI, 9, "bold"),
                anchor="w",
            ).pack(fill="x")

        tk.Frame(body, bg=t.floating_bg, height=10).pack()

        footer = tk.Frame(win, bg=t.floating_bg, highlightthickness=0)
        footer.pack(fill="x")
        tk.Frame(footer, bg=t.border, height=1).pack(fill="x")
        btn_row = tk.Frame(footer, bg=t.floating_bg)
        btn_row.pack(fill="x", padx=20, pady=10)

        close_btn = tk.Label(
            btn_row,
            text=_t("about.close"),
            bg=t.btn_bg,
            fg=t.fg,
            font=(FONT_UI, 9),
            padx=20,
            pady=6,
            cursor="hand2",
        )
        close_btn.pack(side="right")
        close_btn.bind("<Button-1>", lambda _: win.destroy())

        mit_lbl = tk.Label(
            btn_row,
            text=_t("about.mit_credit"),
            bg=t.floating_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 8),
        )
        mit_lbl.pack(side="left")

        win.update_idletasks()
        sw = win.winfo_screenwidth()
        sh = win.winfo_screenheight()
        w = min(620, int(sw * 0.45))
        h = min(660, int(sh * 0.80))
        win.minsize(480, 520)
        rx = self._root.winfo_rootx() + self._root.winfo_width() // 2
        ry = self._root.winfo_rooty() + self._root.winfo_height() // 2
        win.geometry(f"{w}x{h}+{rx - w // 2}+{ry - h // 2}")

    def _check_for_updates(self) -> None:
        """Abre o diálogo de atualização do Codis."""
        token = self._cfg.get("github_token", "")
        open_update_dialog(self._root, APP_VERSION,
                           github_token=token, tmgr=self._tmgr)

    def _keybindings_dialog(self) -> None:
        """Exibe o diálogo de atalhos de teclado."""
        t = self._tmgr.theme
        win = tk.Toplevel(self._root)
        win.title(_t("ui.keybindings_title"))
        win.geometry("560x520")
        win.config(bg=t.floating_bg)
        safe_grab_set(win)

        tk.Label(
            win,
            text=_t("ui.keybindings_title"),
            bg=t.floating_bg,
            fg=t.fg,
            font=(FONT_UI, 13, "bold"),
        ).pack(pady=8)

        txt = tk.Text(
            win,
            bg=t.floating_bg,
            fg=t.fg,
            font=(FONT_MONO, 9),
            relief="flat",
            bd=0,
            state="disabled",
            padx=16,
            pady=8,
        )
        vsb = ttk.Scrollbar(
            win, orient="vertical", command=txt.yview, style="Thin.Vertical.TScrollbar"
        )
        vsb.pack(side="right", fill="y")
        txt.config(yscrollcommand=vsb.set)
        txt.pack(fill="both", expand=True)

        sections = [
            (
                _t("keybind.sec.general"),
                [
                    ("Ctrl+Shift+P", _t("keybind.cmd.palette")),
                    ("Ctrl+P", _t("keybind.quick_open")),
                    ("Ctrl+,", _t("keybind.settings")),
                    ("F11", _t("keybind.fullscreen")),
                    ("Ctrl+`", _t("keybind.terminal")),
                ],
            ),
            (
                _t("keybind.sec.file"),
                [
                    ("Ctrl+N", _t("keybind.new_file")),
                    ("Ctrl+O", _t("keybind.open_file")),
                    ("Ctrl+S", _t("keybind.save")),
                    ("Ctrl+Shift+S", _t("keybind.save_as")),
                    ("Ctrl+W", _t("keybind.close_tab")),
                    ("Ctrl+Tab", _t("keybind.next_tab")),
                ],
            ),
            (
                _t("keybind.sec.edit"),
                [
                    ("Ctrl+Z", _t("keybind.undo")),
                    ("Ctrl+Y", _t("keybind.redo")),
                    ("Ctrl+/", _t("keybind.comment")),
                    ("Ctrl+D", _t("keybind.duplicate")),
                    ("Alt+Up/Down", _t("keybind.move_line")),
                    ("Ctrl+F", _t("keybind.find")),
                    ("Ctrl+H", _t("keybind.replace")),
                    ("Ctrl+Shift+F", _t("keybind.find_files")),
                    ("Shift+Alt+F", _t("keybind.format")),
                ],
            ),
            (
                _t("keybind.sec.nav"),
                [
                    ("Ctrl+G", _t("keybind.go_line")),
                    ("F8", _t("keybind.next_problem")),
                    ("Shift+F8", _t("keybind.prev_problem")),
                    ("F7", _t("keybind.next_diff")),
                    ("F3", _t("keybind.next_match")),
                ],
            ),
            (
                _t("keybind.sec.view"),
                [
                    ("Ctrl++", _t("keybind.zoom_in")),
                    ("Ctrl+-", _t("keybind.zoom_out")),
                    ("Ctrl+0", _t("keybind.zoom_reset")),
                    ("Ctrl+\\", _t("keybind.split_editor")),
                    ("Ctrl+J", _t("keybind.toggle_panel")),
                ],
            ),
            (
                _t("keybind.sec.debug"),
                [
                    ("F5", _t("keybind.start_debug")),
                    ("Shift+F5", _t("keybind.stop_debug")),
                    ("F10", _t("keybind.step_over")),
                ],
            ),
        ]

        txt.config(state="normal")
        for title, items in sections:
            txt.insert("end", f"\n  {title}\n")
            start = txt.index("end - 2 lines")
            end = txt.index("end - 1 char")
            tag = f"sec_{title}"
            txt.tag_configure(tag, foreground=t.t_keyword,
                              font=(FONT_UI, 9, "bold"))
            txt.tag_add(tag, start, end)
            for key, desc in items:
                txt.insert("end", f"    {key:<24}  {desc}\n")
        txt.config(state="disabled")

    @staticmethod
    def _open_extern(path: Path) -> None:
        """Abre uma pasta no explorador de arquivos do sistema."""
        path.mkdir(parents=True, exist_ok=True)
        try:
            if sys.platform == "win32":
                os.startfile(str(path))
            elif sys.platform == "darwin":
                with subprocess.Popen(["open", str(path)]):
                    pass
            else:
                with subprocess.Popen(["xdg-open", str(path)]):
                    pass
        except OSError:
            pass

    @staticmethod
    def _new_window() -> None:
        """Abre uma nova janela do Codis."""
        try:
            from src.pyexec import child_env, new_window_command

            with subprocess.Popen(new_window_command(), env=child_env()):
                pass
        except OSError as exc:
            messagebox.showerror(_t("err.new_window"), str(exc))

    def _purge_active_tab_memory(self) -> None:
        """Libera a memória não-essencial da aba ativa (highlight, undo, caches)."""
        e = self._e()
        if e:
            e.purge_memory()
        else:
            messagebox.showinfo("Liberar memória", _t("ui.no_open_tab"))

    def _global_gc(self) -> None:
        """Executa coleta de lixo global e atualiza a barra de memória na status bar."""
        import gc

        from src.syntax.highlighter import _OM_CACHE

        _OM_CACHE.clear()
        gc.collect()
        collected = gc.collect()
        messagebox.showinfo(
            "GC completo",
            f"Cache global limpo.\n{collected} objeto(s) coletado(s) na segunda passagem.\n\n"
            "Nota: a redução de memória do processo só é visível se o OS já reclamou "
            "as páginas liberadas (depende do allocator do Python).",
        )

    def _word_count_dialog(self) -> None:
        """Exibe a contagem de palavras, linhas e caracteres."""
        e = self._e()
        if not e:
            return
        t = self._tmgr.theme
        try:
            text = e.text.get(tk.SEL_FIRST, tk.SEL_LAST)
            scope = "selection"
        except tk.TclError:
            text = e.get_content()
            scope = "file"

        words = len(re.findall(r"\S+", text))
        lines = text.count("\n") + (1 if text else 0)
        chars = len(text)
        chars_ns = len(text.replace(" ", "").replace(
            "\n", "").replace("\t", ""))

        dlg = tk.Toplevel(self._root)
        dlg.title(_t("ui.word_count_title"))
        dlg.configure(bg=t.floating_bg)
        dlg.resizable(False, False)
        dlg.transient(self._root)
        safe_grab_set(dlg)

        tk.Label(
            dlg,
            text=f"📊  {_t('ui.word_count_title')} ({scope})",
            bg=t.floating_bg,
            fg=t.fg,
            font=(FONT_UI, 12, "bold"),
        ).pack(padx=20, pady=(14, 6))

        body = _t("ui.word_count_body", w=words, l=lines, c=chars, cs=chars_ns)
        tk.Label(
            dlg,
            text=body,
            bg=t.editor_bg,
            fg=t.fg,
            font=(FONT_MONO, 10),
            justify="left",
            padx=16,
            pady=10,
        ).pack(fill="x", padx=12, pady=4)

        tk.Label(
            dlg,
            text=_t("ui.ok"),
            bg=t.btn_primary,
            fg="#000",
            font=(FONT_UI, 9, "bold"),
            padx=20,
            pady=4,
            cursor="hand2",
        ).pack(pady=(4, 14))
        dlg.bind("<Return>", lambda _: dlg.destroy())
        dlg.bind("<Escape>", lambda _: dlg.destroy())
        dlg.winfo_children()[-1].bind("<Button-1>", lambda _: dlg.destroy())
        self._center_dialog(dlg)

    def _remove_duplicate_lines(self) -> None:
        """Remove linhas duplicadas do arquivo ou seleção."""
        e = self._e()
        if not e:
            return
        content = e.get_content()
        lines = content.splitlines()
        seen: set = set()
        unique = []
        for line in lines:
            if line not in seen:
                seen.add(line)
                unique.append(line)
        removed = len(lines) - len(unique)
        e.text.delete("1.0", "end")
        e.text.insert("1.0", "\n".join(unique))
        self._status.notify(_t("msg.dupes_removed", n=removed))

    def _apply_codec(self, mode: str) -> None:
        """Aplica encode/decode (base64 ou url) à seleção do editor ativo.

        Args:
            mode: ``"b64_enc"``, ``"b64_dec"``, ``"url_enc"`` ou ``"url_dec"``.
        """
        import base64
        from urllib.parse import quote, unquote

        e = self._e()
        if not e:
            return
        try:
            sel = e.text.get(tk.SEL_FIRST, tk.SEL_LAST)
        except tk.TclError:
            self._status.notify(_t("msg.no_selection"), level="warn")
            return

        try:
            if mode == "b64_enc":
                result = base64.b64encode(sel.encode()).decode()
                label = "encode"
            elif mode == "b64_dec":
                result = base64.b64decode(sel.encode()).decode()
                label = "decode"
            elif mode == "url_enc":
                result = quote(sel, safe="")
                label = "URL-encode"
            else:
                result = unquote(sel)
                label = "URL-decode"
        except (ValueError, UnicodeDecodeError):
            key = "err.invalid_base64" if mode.startswith(
                "b64") else "err.invalid_url"
            self._status.notify(_t(key), level="error")
            return

        e.text.replace(tk.SEL_FIRST, tk.SEL_LAST, result)
        self._status.notify(_t("msg.base64_done", op=label))

    def _base64_encode(self) -> None:
        self._apply_codec("b64_enc")

    def _base64_decode(self) -> None:
        self._apply_codec("b64_dec")

    def _url_encode(self) -> None:
        self._apply_codec("url_enc")

    def _url_decode(self) -> None:
        self._apply_codec("url_dec")

    def _hash_selection(self, algo: str) -> None:
        """Calcula o hash (MD5, SHA256) da seleção e copia para a área de transferência."""
        import hashlib

        e = self._e()
        if not e:
            return
        try:
            sel = e.text.get(tk.SEL_FIRST, tk.SEL_LAST)
        except tk.TclError:
            sel = e.get_content()
        h = hashlib.new(algo, sel.encode(), usedforsecurity=False).hexdigest()
        self._root.clipboard_clear()
        self._root.clipboard_append(h)

        t = self._tmgr.theme
        dlg = tk.Toplevel(self._root)
        dlg.title(f"{algo.upper()} Hash")
        dlg.configure(bg=t.floating_bg)
        dlg.resizable(False, False)
        dlg.transient(self._root)
        safe_grab_set(dlg)
        tk.Label(
            dlg,
            text=f"  {algo.upper()} Hash  ",
            bg=t.floating_bg,
            fg=t.fg,
            font=(FONT_UI, 11, "bold"),
        ).pack(padx=16, pady=(12, 4))
        tk.Label(
            dlg,
            text=h,
            bg=t.editor_bg,
            fg=t.t_string,
            font=(FONT_MONO, 9),
            padx=12,
            pady=8,
            wraplength=440,
        ).pack(fill="x", padx=12, pady=4)
        tk.Label(
            dlg,
            text=_t("msg.hash_done", algo=algo.upper()),
            bg=t.floating_bg,
            fg=t.ok_fg,
            font=(FONT_UI, 8),
        ).pack(pady=(0, 8))
        ok = tk.Label(
            dlg,
            text=_t("ui.ok"),
            bg=t.btn_primary,
            fg="#000",
            font=(FONT_UI, 9, "bold"),
            padx=20,
            pady=4,
            cursor="hand2",
        )
        ok.pack(pady=(0, 12))
        ok.bind("<Button-1>", lambda _: dlg.destroy())
        dlg.bind("<Return>", lambda _: dlg.destroy())
        dlg.bind("<Escape>", lambda _: dlg.destroy())
        self._center_dialog(dlg)

    def _insert_sequence_dialog(self) -> None:
        """Insere uma sequência numérica no local do cursor."""
        t = self._tmgr.theme
        e = self._e()
        if not e:
            return

        dlg = tk.Toplevel(self._root)
        dlg.title(_t("tools.insert_seq"))
        dlg.configure(bg=t.floating_bg)
        dlg.resizable(False, False)
        dlg.transient(self._root)
        safe_grab_set(dlg)

        tk.Label(
            dlg,
            text=f"  🔢  {_t('tools.insert_seq')}",
            bg=t.floating_bg,
            fg=t.fg,
            font=(FONT_UI, 12, "bold"),
        ).pack(padx=16, pady=(12, 6))

        ew = {
            "bg": t.input_bg,
            "fg": t.fg,
            "insertbackground": t.cursor,
            "relief": "flat",
            "bd": 0,
            "highlightthickness": 1,
            "highlightbackground": t.border,
            "font": (FONT_MONO, 10),
            "width": 12,
        }

        start_var = tk.StringVar(value="1")
        step_var = tk.StringVar(value="1")
        count_var = tk.StringVar(value="10")
        pad_var = tk.BooleanVar(value=False)

        for label_key, var in (
            (_t("ui.seq_start"), start_var),
            (_t("ui.seq_step"), step_var),
            ("Count", count_var),
        ):
            row = tk.Frame(dlg, bg=t.floating_bg)
            row.pack(fill="x", padx=16, pady=3)
            tk.Label(
                row,
                text=label_key + ":",
                bg=t.floating_bg,
                fg=t.fg_muted,
                font=(FONT_UI, 9),
                width=8,
                anchor="w",
            ).pack(side="left")
            tk.Entry(row, textvariable=var, **ew).pack(side="left")

        pad_row = tk.Frame(dlg, bg=t.floating_bg)
        pad_row.pack(fill="x", padx=16, pady=3)
        tk.Checkbutton(
            pad_row,
            text=_t("ui.seq_pad"),
            variable=pad_var,
            bg=t.floating_bg,
            fg=t.fg,
            activebackground=t.floating_bg,
            selectcolor=t.sel_bg,
            font=(FONT_UI, 9),
        ).pack(side="left")

        def _apply():
            try:
                start = int(start_var.get())
                step = int(step_var.get())
                count = int(count_var.get())
                nums = [start + i * step for i in range(count)]
                if pad_var.get():
                    w_max = len(str(max(abs(n) for n in nums)))
                    strs = [str(n).zfill(w_max) for n in nums]
                else:
                    strs = [str(n) for n in nums]

                try:
                    e.text.replace(tk.SEL_FIRST, tk.SEL_LAST, "\n".join(strs))
                except tk.TclError:
                    e.text.insert(tk.INSERT, "\n".join(strs))
                self._status.notify(_t("msg.seq_inserted", n=count))
            except ValueError:
                pass
            dlg.destroy()

        btn_row = tk.Frame(dlg, bg=t.floating_bg)
        btn_row.pack(pady=12)
        for text, cmd in (("Insert", _apply), ("Cancel", dlg.destroy)):
            tk.Label(
                btn_row,
                text=text,
                bg=t.btn_primary if text == "Insert" else t.btn_bg,
                fg="#000" if text == "Insert" else t.fg,
                font=(FONT_UI, 9, "bold"),
                padx=14,
                pady=4,
                cursor="hand2",
            ).pack(side="left", padx=4)
            btn_row.winfo_children(
            )[-1].bind("<Button-1>", lambda _, c=cmd: c())
        self._center_dialog(dlg)

    def _open_color_picker(self) -> None:
        """Abre o seletor de cores com a cor sob o cursor (se houver)."""
        e = self._e()
        if not e:
            return

        try:
            line_no = int(e.text.index(tk.INSERT).split(".")[0])
            line = e.text.get(f"{line_no}.0", f"{line_no}.end")
            col = int(e.text.index(tk.INSERT).split(".")[1])
            initial = "#4a9eff"
            for sc, ec, hex_c in detect_colors_in_line(line):
                if sc <= col <= ec:
                    initial = hex_c
                    break
        except (tk.TclError, ValueError):
            initial = "#4a9eff"

        def _on_pick(hex_c: str) -> None:
            with contextlib.suppress(tk.TclError):
                e.text.insert(tk.INSERT, hex_c)
            self._root.clipboard_clear()
            self._root.clipboard_append(hex_c)
            self._status.notify(_t("msg.copied"))

        ColorPickerPopup(self._root, self._tmgr, initial, _on_pick)

    def _toggle_zen_mode(self) -> None:
        """Alterna o modo Zen (tela cheia sem distrações)."""
        self._zen_mode = not self._zen_mode
        t = self._tmgr.theme

        if self._zen_mode:
            self._hide_sidebar()
            self._hide_bottom_panel()
            with contextlib.suppress(tk.TclError):
                if hasattr(self, "_sidebar_toggle_btn"):
                    self._sidebar_toggle_btn.place_forget()
                if hasattr(self, "_panel_toggle_btn"):
                    self._panel_toggle_btn.place_forget()

            with contextlib.suppress(tk.TclError):
                self._h_paned.paneconfigure(self._actbar, minsize=0, width=0)
                self._h_paned.sash_place(0, 0, 0)

            with contextlib.suppress(tk.TclError):
                self._root.attributes("-fullscreen", True)

            self._zen_lbl = tk.Label(
                self._root,
                text=_t("ui.zen_mode") + "  [Esc to exit]",
                bg=t.editor_bg,
                fg=t.fg_muted,
                font=(FONT_UI, 8),
            )

            self._zen_lbl.place(relx=0.5, rely=1.0, anchor="s", y=-4)
            self._zen_lbl.lift()
            self._status.notify(_t("msg.zen_on"))
            self._root.bind("<Escape>", lambda _: self._toggle_zen_mode())
            with contextlib.suppress(tk.TclError):
                self._zen_media_autoplay()
        else:
            with contextlib.suppress(tk.TclError):
                self._root.attributes("-fullscreen", False)
            self._show_sidebar()
            self._show_bottom_panel()

            with contextlib.suppress(tk.TclError):
                self._h_paned.paneconfigure(self._actbar, minsize=48, width=48)
                self._h_paned.sash_place(0, 48, 0)

            with contextlib.suppress(tk.TclError):
                if hasattr(self, "_sidebar_toggle_btn"):
                    self._sidebar_toggle_btn.place(x=48, rely=0.5, anchor="w")
                    self._sidebar_toggle_btn.lift()
                if hasattr(self, "_panel_toggle_btn"):
                    self._panel_toggle_btn.place(
                        relx=1.0, rely=1.0, anchor="se", x=-2, y=-26
                    )
                    self._panel_toggle_btn.lift()
            if hasattr(self, "_zen_lbl") and self._zen_lbl.winfo_exists():
                self._zen_lbl.destroy()
            self._status.notify(_t("msg.zen_off"))
            self._root.bind("<Escape>", self._on_escape)
            with contextlib.suppress(tk.TclError):
                self._zen_media_stop_hud()

    def _toggle_focus_mode(self) -> None:
        """Alterna o modo foco (escurece abas inativas)."""
        self._focus_mode = not self._focus_mode
        self._apply_focus_mode()
        msg = _t("msg.focus_on") if self._focus_mode else _t("msg.focus_off")
        self._status.notify(msg)

    def _apply_focus_mode(self) -> None:
        """Aplica o efeito visual do modo foco."""
        if not hasattr(self, "_editors"):
            return
        t = self._tmgr.theme
        for i, ed in enumerate(self._editors):
            active = i == self._active_idx
            with contextlib.suppress(tk.TclError):
                bg = t.editor_bg if active or not self._focus_mode else t.bg
                ed.text.config(bg=bg)

    def _toggle_read_only(self) -> None:
        """Alterna o modo somente leitura no editor ativo."""
        e = self._e()
        if not e:
            return
        current = str(e.text.cget("state")) == "disabled"
        if current:
            e.text.config(state="normal")
            self._status.notify(_t("msg.read_only_off"))
        else:
            e.text.config(state="disabled")
            self._status.notify(_t("msg.read_only_on"))
        self._update_status_bar()

    def _toggle_overwrite_mode(self) -> None:
        """Alterna o modo sobrescrita (overwrite) no editor ativo."""
        e = self._e()
        if not e:
            return

        ovr = not getattr(e, "_overwrite", False)
        e._overwrite = ovr

        if ovr:

            def _ovr_key(event):
                if event.char and event.char.isprintable():
                    with contextlib.suppress(tk.TclError):
                        e.text.delete(tk.INSERT, f"{tk.INSERT}+1c")

            e.text.bind("<KeyPress>", _ovr_key, add="+")
        else:
            e.text.unbind("<KeyPress>")
        self._update_status_bar()

    def _add_bookmark(self) -> None:
        """Adiciona um marcador na linha atual."""
        e = self._e()
        if not e or not e.path:
            return
        t = self._tmgr.theme
        try:
            line_no = int(e.text.index(tk.INSERT).split(".")[0])
            col_no = int(e.text.index(tk.INSERT).split(".")[1])
            snippet = e.text.get(f"{line_no}.0", f"{line_no}.end").strip()
        except (tk.TclError, ValueError):
            return

        dlg = tk.Toplevel(self._root)
        dlg.title(_t("ui.bookmark_title"))
        dlg.configure(bg=t.floating_bg)
        dlg.resizable(False, False)
        dlg.transient(self._root)
        safe_grab_set(dlg)

        tk.Label(
            dlg,
            text=f"  🔖  {_t('ui.bookmark_title')}  -  line {line_no}",
            bg=t.floating_bg,
            fg=t.fg,
            font=(FONT_UI, 11, "bold"),
        ).pack(padx=16, pady=(12, 4))
        tk.Label(
            dlg,
            text=snippet[:60],
            bg=t.editor_bg,
            fg=t.fg_muted,
            font=(FONT_MONO, 9),
            padx=12,
            pady=4,
        ).pack(fill="x", padx=12, pady=2)
        tk.Label(
            dlg,
            text=_t("ui.bookmark_note") + ":",
            bg=t.floating_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 9),
        ).pack(anchor="w", padx=16, pady=(6, 0))

        note_var = tk.StringVar()
        note_e = tk.Entry(
            dlg,
            textvariable=note_var,
            bg=t.input_bg,
            fg=t.fg,
            insertbackground=t.cursor,
            relief="flat",
            bd=0,
            highlightthickness=1,
            highlightbackground=t.border,
            font=(FONT_UI, 10),
        )
        note_e.pack(fill="x", padx=16, pady=4)
        note_e.focus_set()

        def _save():
            self._bookmark_mgr.add(
                str(e.path), line_no, col_no, snippet, note_var.get()
            )

            with contextlib.suppress(tk.TclError):
                self._bookmarks_panel.refresh()
            self._status.notify(_t("msg.bookmark_added"))
            dlg.destroy()

        btn_row = tk.Frame(dlg, bg=t.floating_bg)
        btn_row.pack(pady=10)
        for text, cmd in ((_t("ui.bookmark_add"), _save), ("Cancel", dlg.destroy)):
            btn = tk.Label(
                btn_row,
                text=text,
                bg=t.btn_primary if text != "Cancel" else t.btn_bg,
                fg="#000" if text != "Cancel" else t.fg,
                font=(FONT_UI, 9, "bold"),
                padx=14,
                pady=4,
                cursor="hand2",
            )
            btn.pack(side="left", padx=4)
            btn.bind("<Button-1>", lambda _, c=cmd: c())
        note_e.bind("<Return>", lambda _: _save())
        dlg.bind("<Escape>", lambda _: dlg.destroy())
        self._center_dialog(dlg)

    def _next_bookmark(self) -> None:
        """Vai para o próximo marcador no arquivo atual."""
        e = self._e()
        if not e or not e.path:
            return
        try:
            cur = int(e.text.index(tk.INSERT).split(".")[0])
        except (tk.TclError, ValueError):
            cur = 0
        bm = self._bookmark_mgr.next_bookmark(str(e.path), cur)
        if bm:
            e.goto_line(bm["line"], bm["col"])
            self._status.notify(
                f"🔖 {bm.get('note', '') or bm['snippet'][:40]}")

    def _prev_bookmark(self) -> None:
        """Vai para o marcador anterior no arquivo atual."""
        e = self._e()
        if not e or not e.path:
            return
        try:
            cur = int(e.text.index(tk.INSERT).split(".")[0])
        except (tk.TclError, ValueError):
            cur = 99999
        bm = self._bookmark_mgr.prev_bookmark(str(e.path), cur)
        if bm:
            e.goto_line(bm["line"], bm["col"])
            self._status.notify(
                f"🔖 {bm.get('note', '') or bm['snippet'][:40]}")

    def _open_session_notes(self) -> None:
        """Abre uma janela flutuante de notas persistente por workspace."""
        import hashlib as _hl

        existing = getattr(self, "_notes_win", None)
        if existing is not None:
            with contextlib.suppress(tk.TclError):
                if existing.winfo_exists():
                    existing.deiconify()
                    existing.focus_force()
                    return

        t = self._tmgr.theme
        ws_key = _hl.md5(
            str(self._workspace or "global").encode(), usedforsecurity=False
        ).hexdigest()[:8]
        notes_file = Path.home() / ".codis" / f"notes_{ws_key}.txt"

        dlg = tk.Toplevel(self._root)
        self._notes_win = dlg
        dlg.title(_t("ui.notes_title"))
        dlg.configure(bg=t.floating_bg)
        dlg.geometry("360x280")
        dlg.resizable(True, True)

        tk.Label(
            dlg,
            text=f"  📝  {_t('ui.notes_title')}",
            bg=t.activity_bg,
            fg=t.fg,
            font=(FONT_UI, 9, "bold"),
            anchor="w",
            height=2,
        ).pack(fill="x")

        saved_lbl = tk.Label(
            dlg, text="", bg=t.floating_bg, fg=t.ok_fg, font=(FONT_UI, 7)
        )
        saved_lbl.pack(anchor="e", padx=8)

        txt = tk.Text(
            dlg,
            bg=t.editor_bg,
            fg=t.fg,
            insertbackground=t.cursor,
            font=(FONT_MONO, 10),
            relief="flat",
            bd=0,
            padx=8,
            pady=6,
            wrap="word",
            undo=True,
        )
        txt.pack(fill="both", expand=True, padx=4, pady=4)

        with contextlib.suppress(tk.TclError):
            if notes_file.exists():
                txt.insert("1.0", notes_file.read_text(encoding="utf-8"))

        def _do_save():
            with contextlib.suppress(OSError, tk.TclError):
                notes_file.parent.mkdir(parents=True, exist_ok=True)
                notes_file.write_text(
                    txt.get("1.0", "end-1c"), encoding="utf-8")
                saved_lbl.config(text=_t("ui.notes_saved"))

        from src.ui._perf_utils import debounce_fn

        _autosave = debounce_fn(dlg, _do_save, ms=600)

        def _on_key(*_):
            saved_lbl.config(text="...")
            _autosave()

        txt.bind("<KeyRelease>", _on_key)

    def _regex_replace(self, pattern: str, replacement: str, flags_str: str) -> None:
        """Aplica substituição por expressão regular no conteúdo do editor."""
        e = self._e()
        if not e:
            return
        flags = 0
        if "i" in flags_str.lower():
            flags |= re.IGNORECASE
        if "m" in flags_str.lower():
            flags |= re.MULTILINE
        if "s" in flags_str.lower():
            flags |= re.DOTALL
        try:
            rx = re.compile(pattern, flags)
            content = e.get_content()
            new_content, n = rx.subn(replacement, content)
            if n:
                e.text.delete("1.0", "end")
                e.text.insert("1.0", new_content)
                self._status.notify(f"{n} substituição(ões) realizada(s)")
        except re.error as err:
            self._status.notify(
                _t("err.invalid_regex", err=err), level="error")

    def _paste_history_dialog(self) -> None:
        """Abre o diálogo de histórico de área de transferência com busca e inserção."""
        import time as _time

        t = self._tmgr.theme
        e = self._e()
        history: list = getattr(self, "_copy_history", [])

        dlg = tk.Toplevel(self._root)
        dlg.title(_t("ui.copy_history_title"))
        dlg.configure(bg=t.floating_bg)
        dlg.geometry("560x380")
        dlg.transient(self._root)
        safe_grab_set(dlg)

        tk.Label(
            dlg,
            text=f"  📋  {_t('ui.copy_history_title')}",
            bg=t.activity_bg,
            fg=t.fg,
            font=(FONT_UI, 10, "bold"),
            height=2,
        ).pack(fill="x")

        frame = tk.Frame(dlg, bg=t.editor_bg)
        frame.pack(fill="both", expand=True, padx=8, pady=(8, 0))

        scrollbar = tk.Scrollbar(frame, orient="vertical")
        listbox = tk.Listbox(
            frame,
            bg=t.editor_bg,
            fg=t.fg,
            selectbackground=t.sel_bg,
            selectforeground=t.sel_fg,
            font=(FONT_MONO, 9),
            relief="flat",
            activestyle="none",
            highlightthickness=0,
            yscrollcommand=scrollbar.set,
        )
        scrollbar.config(command=listbox.yview)
        scrollbar.pack(side="right", fill="y")
        listbox.pack(side="left", fill="both", expand=True)

        if not history:
            listbox.insert("end", f"  {_t('ui.paste_history_empty')}")
        else:
            for entry in history[:50]:
                ts = _time.strftime(
                    "%H:%M:%S", _time.localtime(entry.timestamp))
                icon = "✂" if entry.kind == "cut" else "⎘"
                preview = entry.text.replace("\n", "↵")[:72]
                listbox.insert("end", f"  {icon} {ts}  {preview}")

        def _paste_selected(_ev=None):
            sel = listbox.curselection()
            if not sel or not history:
                return
            idx = sel[0]
            if 0 <= idx < len(history) and e:
                with contextlib.suppress(tk.TclError):
                    if e.text.tag_ranges(tk.SEL):
                        e.text.delete(tk.SEL_FIRST, tk.SEL_LAST)
                    e.text.insert(tk.INSERT, history[idx].text)
            dlg.destroy()

        def _clear_history(_ev=None):
            self._copy_history.clear()
            dlg.destroy()

        btn_row = tk.Frame(dlg, bg=t.floating_bg)
        btn_row.pack(pady=6)
        for label, cmd, primary in (
            (_t("ui.copy_history_paste"), _paste_selected, True),
            (_t("ui.copy_history_clear"), _clear_history, False),
            (_t("ui.copy_history_cancel"), dlg.destroy, False),
        ):
            btn = tk.Label(
                btn_row,
                text=label,
                bg=t.btn_primary if primary else t.btn_bg,
                fg="#000" if primary else t.fg,
                font=(FONT_UI, 9, "bold"),
                padx=14,
                pady=4,
                cursor="hand2",
            )
            btn.pack(side="left", padx=4)
            btn.bind("<Button-1>", lambda _, c=cmd: c())
        listbox.bind("<Double-Button-1>", _paste_selected)
        listbox.bind("<Return>", _paste_selected)
        dlg.bind("<Escape>", lambda _: dlg.destroy())
        self._center_dialog(dlg)

    def _center_dialog(self, dlg: tk.Toplevel) -> None:
        """Centraliza um diálogo em relação à janela principal."""
        dlg.update_idletasks()
        w, h = dlg.winfo_reqwidth(), dlg.winfo_reqheight()
        x = self._root.winfo_x() + (self._root.winfo_width() - w) // 2
        y = self._root.winfo_y() + (self._root.winfo_height() - h) // 2
        dlg.geometry(f"+{x}+{y}")

    def _save_layout(self) -> None:
        """Persiste largura da sidebar e altura do painel na configuração."""
        with contextlib.suppress(tk.TclError, AttributeError):
            w = self._sidebar.winfo_width()
            if w > 60 and (self._sidebar_visible or not self._dock.floating):
                self._cfg.set("sidebar_width", w)
        with contextlib.suppress(tk.TclError, AttributeError):
            self._cfg.set("panel_height", self._bottom_frame.winfo_height())

    def _restart_process(self) -> None:
        """Reinicia o processo do Codis completamente.

        o executável Python com os mesmos argumentos usando ``os.execv``.
        No Windows, onde ``os.execv`` não substitui o processo, lança um
        novo processo via ``subprocess.Popen`` e encerra o atual.

        O workspace e a sessão são salvos antes do reinício para que o Codis
        reabra no mesmo estado.
        """
        has_unsaved = any(e.modified for e in self._editors)
        if not has_unsaved and not messagebox.askyesno(
            _t("file.restart"),
            _t("ui.confirm_restart") if _t("ui.confirm_restart") != "ui.confirm_restart"
            else "Reiniciar o Codis agora?",
            parent=self._root,
        ):
            return
        if not self._resolve_unsaved():
            return

        self._save_layout()
        self._save_session()
        self._cfg.save()

        try:
            from src.pyexec import restart_command

            cmd = restart_command()
            if sys.platform == "win32":
                subprocess.Popen(cmd)
                self._root.after(200, self._root.destroy)
            else:
                os.execv(cmd[0], cmd)
        except Exception as exc:
            messagebox.showerror(_t("file.restart"),
                                 str(exc), parent=self._root)

    @staticmethod
    def _arm_exit_watchdog(seconds: float = 5.0) -> None:
        """Garante que o processo termine mesmo se algo segurar o encerramento.

        Uma thread daemon dorme por ``seconds`` e então força a saída com
        ``os._exit``. Se o interpretador finalizar antes, ela morre junto, sem
        efeito. Cobre threads de terceiros, pools de workers e subprocessos
        que impediriam o Python de sair sozinho.
        """

        def _force_exit() -> None:
            time.sleep(seconds)
            logger.warning("Encerramento passou de %.0fs; forçando saída.", seconds)
            with contextlib.suppress(Exception):
                logging.shutdown()
            os._exit(0)

        threading.Thread(target=_force_exit, daemon=True, name="codis-exit-watchdog").start()

    def _shutdown_services(self) -> None:
        """Para todos os serviços em background antes de encerrar a aplicação."""
        with contextlib.suppress(Exception):
            from src.events import bus as event_bus

            event_bus.shutdown()
        with contextlib.suppress(Exception):
            from src.ui.k_ui._perf import shutdown_physics_executor

            shutdown_physics_executor()
        with contextlib.suppress(Exception):
            if getattr(self, "_panel_detach", None):
                self._panel_detach.close_all()
        with contextlib.suppress(Exception):
            if self._lsp_mgr:
                self._lsp_mgr.stop_all()
        with contextlib.suppress(Exception):
            if self._autosave_job:
                self._root.after_cancel(self._autosave_job)
        with contextlib.suppress(Exception):
            self._lint_mgr.cancel()

        with contextlib.suppress(Exception):
            if self._kui and getattr(self._kui, "_enabled", False):
                kui = self._kui
                for attr in ("_topbar", "_tabbar_w", "_stbar", "_engine"):
                    w = getattr(kui, attr, None)
                    if w and hasattr(w, "stop"):
                        with contextlib.suppress(Exception):
                            w.stop()

        with contextlib.suppress(Exception):
            if hasattr(self, "_terminal") and self._terminal:
                self._terminal.destroy()

        with contextlib.suppress(Exception):
            if hasattr(self, "_debug_panel") and self._debug_panel:
                if hasattr(self._debug_panel, "_proc") and self._debug_panel._proc:
                    self._debug_panel._proc.terminate()

    def _resolve_unsaved(self) -> bool:
        """Aplica ``exit_save_mode`` aos editores com alterações pendentes.

        * ``ask``     - pergunta (Sim = salvar, Não = descartar, Cancelar = ficar);
        * ``save``    - salva tudo sem perguntar;
        * ``discard`` - não salva nada e não pergunta.

        Se algum salvamento falhar (ou o diálogo "Salvar como" de um arquivo sem
        nome for cancelado) pergunta antes de sair, em vez de perder dados em
        silêncio.  Retorna ``True`` quando é seguro prosseguir.
        """
        unsaved = [e for e in self._editors if e.modified]
        if not unsaved:
            return True
        mode = self._cfg.get("exit_save_mode", "ask")
        if mode == "ask":
            names = ", ".join(e.path.name if e.path else _t("ui.tab_untitled") for e in unsaved)
            ans = messagebox.askyesnocancel(
                _t("ui.unsaved_changes_title"),
                _t("ui.confirm_exit") + f"\n\n{names}",
                parent=self._root,
            )
            if ans is None:
                return False
            mode = "save" if ans else "discard"
        if mode == "discard":
            return True
        failed: list[str] = []
        for e in unsaved:
            fname = e.path.name if e.path else _t("ui.tab_untitled")
            try:
                ok = e.save() is not False
            except Exception as exc:  # noqa: BLE001
                logger.warning("Falha ao salvar '%s' no encerramento: %s", fname, exc)
                ok = False
            if not ok:
                failed.append(fname)
        if failed:
            return messagebox.askyesno(
                _t("ui.unsaved_changes_title"),
                _t("ui.exit_save_failed", names=", ".join(failed),
                   default="Não foi possível salvar:\n{names}\n\nSair mesmo assim e perder essas alterações?"),
                icon="warning",
                parent=self._root,
            )
        return True

    def _persist_session_on_exit(self) -> None:
        """Grava (ou apaga) a sessão conforme ``session_save_on_exit``."""
        if self._cfg.get("session_save_on_exit", True):
            self._save_session()
        else:
            with contextlib.suppress(OSError):
                SESSIONS_FILE.unlink(missing_ok=True)

    def _on_exit(self) -> None:
        """Fecha a aplicação respeitando ``exit_save_mode`` e ``session_save_on_exit``."""
        if not self._resolve_unsaved():
            return

        if self._dual_mode:
            with contextlib.suppress(Exception):
                self._close_dual_mode()

        with contextlib.suppress(Exception):
            self._save_layout()
        with contextlib.suppress(Exception):
            self._persist_session_on_exit()
        with contextlib.suppress(Exception):
            self._cfg.save()

        self._arm_exit_watchdog()
        self._shutdown_services()

        try:
            self._root.destroy()
        except Exception as exc:
            logger.debug("Erro ao destruir root: %s", exc)
        try:
            sys.exit(0)
        except SystemExit:  # pylint: disable=try-except-raise
            raise
        except Exception:
            os._exit(0)

    def _open_remote_ssh(self) -> None:
        """Abre o Explorador de Arquivos Remotos (SSH/SFTP/FTP/HTTP/WebDAV)."""
        try:
            from src.ui.remote_browser import RemoteBrowserPanel

            if hasattr(self, "_rbrowser_win") and self._rbrowser_win.winfo_exists():
                self._rbrowser_win.deiconify()
                self._rbrowser_win.focus_force()
                return
            self._rbrowser_win = tk.Toplevel(self._root)
            self._rbrowser_win.title(
                _t("rbrowser.window_title", "Codis - Arquivos Remotos")
            )
            from src.ui.remote_browser import fit_toplevel

            fit_toplevel(self._rbrowser_win, 960, 660)
            self._rbrowser_win.config(bg=self._tmgr.theme.sidebar_bg)
            self._rbrowser_panel = RemoteBrowserPanel(
                self._rbrowser_win,
                self._tmgr,
                self._cfg,
                open_file_cb=lambda p: self._open_file(p, adopt_workspace=False),
            )
            self._rbrowser_panel.pack(fill="both", expand=True)
            self._rbrowser_win.focus_force()
        except Exception as exc:
            logger.warning("RemoteBrowserPanel não disponível: %s", exc)
            messagebox.showerror(
                _t("rbrowser.title", "Arquivos Remotos"),
                f"Painel não pôde ser aberto:\n{exc}",
            )

    def _open_chat(self) -> None:
        """Abre o Painel de Chat Universal (IRC, WebSocket, Codis)."""
        try:
            from src.ui.chat_panel import ChatPanel

            if hasattr(self, "_chat_win") and self._chat_win.winfo_exists():
                self._chat_win.deiconify()
                self._chat_win.focus_force()
                return
            self._chat_win = tk.Toplevel(self._root)
            self._chat_win.title(_t("chat.window_title", "Codis - Chat"))
            self._chat_win.geometry("1000x660")
            self._chat_win.minsize(640, 420)
            self._chat_win.config(bg=self._tmgr.theme.sidebar_bg)
            self._chat_panel = ChatPanel(self._chat_win, self._tmgr, self._cfg)
            self._chat_panel.pack(fill="both", expand=True)
            self._chat_win.protocol("WM_DELETE_WINDOW", lambda: (self._chat_panel.shutdown(), self._chat_win.destroy()))
            self._chat_win.focus_force()
        except Exception as exc:
            logger.warning("ChatPanel não disponível: %s", exc)
            messagebox.showerror(
                _t("chat.title", "Chat"), f"Painel não pôde ser aberto:\n{exc}"
            )

    def _open_collab(self) -> None:
        """Abre o Painel de Colaboração em Tempo Real."""
        try:
            from src.ui.collab_panel import CollabPanel

            if hasattr(self, "_collab_win") and self._collab_win.winfo_exists():
                self._collab_win.deiconify()
                self._collab_win.focus_force()
                return
            self._collab_win = tk.Toplevel(self._root)
            self._collab_win.title(
                _t("collab.window_title", "Codis - Colaboração"))
            self._collab_win.geometry("480x760")
            self._collab_win.config(bg=self._tmgr.theme.sidebar_bg)

            def _get_editor_widget() -> tk.Text | None:
                """``tk.Text`` da aba ativa (o EditorPane não expõe get/insert)."""
                pane = self._e()
                return pane.text if pane is not None else None

            def _new_editor() -> tk.Text | None:
                """Abre uma aba nova (sem sobrescrever o arquivo aberto) ao entrar numa sala."""
                pane = self.new_file()
                return pane.text if pane is not None else None

            def _close_collab() -> None:
                with contextlib.suppress(Exception):
                    self._collab_panel.disconnect()
                with contextlib.suppress(tk.TclError):
                    self._collab_win.destroy()

            self._collab_win.protocol("WM_DELETE_WINDOW", _close_collab)
            self._collab_panel = CollabPanel(
                self._collab_win,
                self._tmgr,
                self._cfg,
                get_editor_widget=_get_editor_widget,
                new_editor=_new_editor,
            )
            self._collab_panel.pack(fill="both", expand=True)
            self._collab_win.focus_force()
        except Exception as exc:
            logger.warning("CollabPanel não disponível: %s", exc)
            messagebox.showerror(
                _t("collab.title",
                   "Colaboração"), f"Painel não pôde ser aberto:\n{exc}"
            )

    def _open_accessibility(self) -> None:
        """Abre o painel de configuração de Acessibilidade."""
        try:
            from src.ui.accessibility import AccessibilityManager, AccessibilityPanel

            existing = getattr(self, "_a11y_panel", None)
            if existing is not None:
                with contextlib.suppress(tk.TclError):
                    if existing.winfo_exists():
                        existing.deiconify()
                        existing.focus_force()
                        return

            if not hasattr(self, "_a11y_mgr"):
                self._a11y_mgr = AccessibilityManager(
                    self, self._cfg, self._tmgr)
            panel = AccessibilityPanel(self._root, self._a11y_mgr, self._tmgr)
            self._a11y_panel = panel
            panel.focus_force()
        except Exception as exc:
            logger.warning("Painel de acessibilidade não disponível: %s", exc)

    def _init_accessibility(self) -> None:
        """Inicializa AccessibilityManager na startup se configurado."""
        try:
            from src.ui.accessibility import AccessibilityManager

            self._a11y_mgr = AccessibilityManager(self, self._cfg, self._tmgr)
        except Exception as exc:
            logger.debug("A11y init: %s", exc)

    def _init_performance_monitor(self) -> None:
        """Monitora travamentos da UI e aplica modo performance automaticamente.

        Usa um thread de background com ping-pong no main thread via after().
        Se o main thread travar, o ping para de chegar e o monitor detecta o lag.

        - Ping a cada 150ms
        - Período de graça de 20s na inicialização
        - Back-off exponencial: após detectar travamento, a espera cresce
          até 60s antes de aplicar de novo (evita modo perf piscando)
        - Quando o sistema se recupera, reduz o back-off
        - Usa threading.Event para parada limpa no shutdown
        """
        if not self._cfg.get("perf_auto_mode", True):
            return
        threshold_ms = int(self._cfg.get("perf_freeze_threshold_ms", 500))
        import threading
        import time

        _stop_event = threading.Event()
        if not hasattr(self, "_perf_mon_stop"):
            self._perf_mon_stop = _stop_event

        def _monitor() -> None:
            last_ping = time.monotonic()
            last_apply = 0.0
            apply_cooldown = 30.0
            grace_until = time.monotonic() + 20.0
            ping_interval_ms = 150
            check_interval = max(threshold_ms / 1000.0, 0.4)

            def _ping() -> None:
                nonlocal last_ping
                last_ping = time.monotonic()
                if not _stop_event.is_set():
                    try:
                        self._root.after(ping_interval_ms, _ping)
                    except Exception:
                        logger.debug(
                            "Falha ao reagendar ping de watchdog.", exc_info=True
                        )

            try:
                self._root.after(ping_interval_ms, _ping)
            except Exception:
                return

            while not _stop_event.wait(timeout=check_interval):
                now = time.monotonic()
                if now < grace_until:
                    last_ping = now
                    continue
                lag = (now - last_ping) * 1000.0
                if lag > threshold_ms:
                    if now - last_apply > apply_cooldown:
                        logger.warning(
                            "Travamento detectado (%.0fms). Ativando modo performance.",
                            lag,
                        )
                        last_apply = now
                        apply_cooldown = min(apply_cooldown * 2.0, 120.0)
                        with contextlib.suppress(Exception):
                            self._root.after(0, self._apply_perf_mode)
                    last_ping = now
                else:
                    if apply_cooldown > 30.0:
                        apply_cooldown = max(30.0, apply_cooldown * 0.8)

        t = threading.Thread(target=_monitor, daemon=True,
                             name="Codis-PerfMon")
        t.start()

    def _apply_perf_mode(self) -> None:
        """Aplica configurações de performance usando preferências customizadas do usuário."""
        try:
            from src.ui.performance_panel import apply_perf_custom

            editors = list(getattr(self, "_editors", []))
            apply_perf_custom(self._cfg, editors)
            with contextlib.suppress(Exception):
                if hasattr(self, "_kui") and self._kui:
                    profile = str(self._cfg.get("perf_kui_fallback", "low"))
                    keep_kui = bool(self._cfg.get("perf_keep_kui_anim", True))
                    if not keep_kui:
                        self._kui.set_perf_profile(profile)
        except Exception as exc:
            logger.warning("_apply_perf_mode falhou: %s", exc)
        logger.info("Modo performance aplicado via configuração customizada.")

    def _open_performance_panel(self) -> None:
        """Abre o painel de configuração do modo performance customizável."""
        try:
            from src.ui.performance_panel import open_performance_panel

            if hasattr(self, "_perf_panel_win"):
                with contextlib.suppress(Exception):
                    if self._perf_panel_win.winfo_exists():
                        self._perf_panel_win.deiconify()
                        self._perf_panel_win.focus_force()
                        return
            self._perf_panel_win = open_performance_panel(
                self._root,
                self._cfg,
                self._tmgr,
                on_apply_now=self._apply_perf_mode,
            )
        except Exception as exc:
            logger.warning("PerformancePanel falhou: %s", exc)
            messagebox.showerror(_t("err.title", "Erro"),
                                 str(exc), parent=self._root)

    def _open_paint_panel(self) -> None:
        """Abre o editor de imagens (Paint integrado) em janela flutuante."""
        try:
            from src.ui.paint_panel import PaintPanel

            existing = getattr(self, "_paint_win", None)
            if existing is not None:
                with contextlib.suppress(tk.TclError):
                    if existing.winfo_exists():
                        existing.deiconify()
                        existing.focus_force()
                        return

            win = tk.Toplevel(self._root)
            win.title(_t("paint.window_title", "Paint - Codis"))
            win.geometry("1000x700")
            self._paint_win = win
            panel = PaintPanel(win, self._tmgr)
            panel.pack(fill="both", expand=True)
            win.focus_set()
        except Exception as exc:
            logger.warning("PaintPanel falhou: %s", exc)
            messagebox.showerror(_t("err.title", "Erro"),
                                 str(exc), parent=self._root)

    def _open_pip_manager(self) -> None:
        """Abre o gerenciador de pacotes pip."""
        try:
            from src.ui.pip_manager import PipManagerPanel

            if hasattr(self, "_pip_win") and self._pip_win.winfo_exists():
                self._pip_win.deiconify()
                self._pip_win.focus_force()
                return
            win = tk.Toplevel(self._root)
            win.title(_t("pip.window_title", "📦  Gerenciador Pip - Codis"))
            win.geometry("1000x680")
            win.minsize(760, 500)
            self._pip_win = win
            panel = PipManagerPanel(win, self._tmgr, self._cfg)
            panel.pack(fill="both", expand=True)
            win.focus_set()
        except Exception as exc:
            logger.warning("PipManager falhou: %s", exc)
            messagebox.showerror(_t("err.title", "Erro"),
                                 str(exc), parent=self._root)

    def _open_task_manager(self) -> None:
        """Abre o gerenciador de tarefas Kanban."""
        try:
            from src.ui.task_manager import TaskManagerPanel

            if hasattr(self, "_tasks_win") and self._tasks_win.winfo_exists():
                self._tasks_win.deiconify()
                self._tasks_win.focus_force()
                return
            win = tk.Toplevel(self._root)
            win.title(_t("tasks.window_title", "✅  Tarefas - Codis"))
            win.geometry("1100x700")
            win.minsize(800, 500)
            self._tasks_win = win
            panel = TaskManagerPanel(win, self._tmgr, self._cfg)
            panel.pack(fill="both", expand=True)
            win.focus_set()
        except Exception as exc:
            logger.warning("TaskManager falhou: %s", exc)
            messagebox.showerror(_t("err.title", "Erro"),
                                 str(exc), parent=self._root)

    def _open_process_manager(self) -> None:
        """Abre o gerenciador de processos."""
        try:
            from src.ui.process_manager import ProcessManagerPanel

            if hasattr(self, "_procmgr_win") and self._procmgr_win.winfo_exists():
                self._procmgr_win.deiconify()
                self._procmgr_win.focus_force()
                return
            win = tk.Toplevel(self._root)
            win.title(_t("procmgr.window_title",
                      "Gerenciador de Processos - Codis"))
            win.geometry("1200x720")
            win.minsize(800, 500)
            self._procmgr_win = win
            panel = ProcessManagerPanel(
                win, self._tmgr, self._cfg, app_ref=self)
            panel.pack(fill="both", expand=True)
            win.focus_set()
        except Exception as exc:
            logger.warning("ProcessManager falhou: %s", exc)
            messagebox.showerror(_t("err.title", "Erro"),
                                 str(exc), parent=self._root)

    def _open_diff(self) -> None:
        """Abre o visualizador de diff."""
        try:
            from src.ui.diff_panel import DiffPanel

            if hasattr(self, "_diff_win") and self._diff_win.winfo_exists():
                self._diff_win.deiconify()
                self._diff_win.focus_force()
                return
            win = tk.Toplevel(self._root)
            win.title(_t("diff.window_title", "⇄  Diff Viewer - Codis"))
            win.geometry("1100x680")
            win.minsize(700, 400)
            self._diff_win = win
            panel = DiffPanel(win, self._tmgr, self._cfg)
            panel.pack(fill="both", expand=True)
            win.focus_set()
        except Exception as exc:
            logger.warning("DiffPanel falhou: %s", exc)
            messagebox.showerror(_t("err.title", "Erro"),
                                 str(exc), parent=self._root)

    def _open_formatter(self) -> None:
        """Abre o formatador JSON/YAML/TOML/XML."""
        try:
            from src.ui.formatter_panel import FormatterPanel

            if hasattr(self, "_fmt_win") and self._fmt_win.winfo_exists():
                self._fmt_win.deiconify()
                self._fmt_win.focus_force()
                return
            win = tk.Toplevel(self._root)
            win.title(_t("fmt.window_title", "{ }  Formatador - Codis"))
            win.geometry("1000x660")
            win.minsize(700, 400)
            self._fmt_win = win
            panel = FormatterPanel(win, self._tmgr, self._cfg)
            panel.pack(fill="both", expand=True)
            win.focus_set()
        except Exception as exc:
            logger.warning("FormatterPanel falhou: %s", exc)
            messagebox.showerror(_t("err.title", "Erro"),
                                 str(exc), parent=self._root)

    def _open_env_manager(self) -> None:
        """Abre o gerenciador de variáveis de ambiente."""
        try:
            from src.ui.env_manager import EnvManagerPanel

            if hasattr(self, "_env_win") and self._env_win.winfo_exists():
                self._env_win.deiconify()
                self._env_win.focus_force()
                return
            win = tk.Toplevel(self._root)
            win.title(_t("env.window_title", "⚙  Variáveis de Ambiente - Codis"))
            win.geometry("900x620")
            win.minsize(700, 400)
            self._env_win = win
            panel = EnvManagerPanel(win, self._tmgr, self._cfg)
            panel.pack(fill="both", expand=True)
            win.focus_set()
        except Exception as exc:
            logger.warning("EnvManager falhou: %s", exc)
            messagebox.showerror(_t("err.title", "Erro"),
                                 str(exc), parent=self._root)

    def _open_calculator(self) -> None:
        """Abre a calculadora integrada com histórico."""
        try:
            from src.ui.calculator_panel import CalculatorPanel

            existing = getattr(self, "_calc_win", None)
            if existing is not None:
                with contextlib.suppress(tk.TclError):
                    if existing.winfo_exists():
                        existing.deiconify()
                        existing.focus_force()
                        return

            win = tk.Toplevel(self._root)
            win.title(_t("calc.window_title", "Calculadora - Codis"))
            win.geometry("400x600")
            win.resizable(True, True)
            self._calc_win = win
            panel = CalculatorPanel(win, self._tmgr, self._cfg)
            panel.pack(fill="both", expand=True)
            win.focus_set()
        except Exception as exc:
            logger.warning("Calculator falhou: %s", exc)
            messagebox.showerror(_t("err.title", "Erro"),
                                 str(exc), parent=self._root)

    def _open_calendar(self) -> None:
        """Abre o calendário integrado com lembretes e anotações."""
        try:
            from src.ui.calendar_panel import CalendarPanel

            existing = getattr(self, "_calendar_win", None)
            if existing is not None:
                with contextlib.suppress(tk.TclError):
                    if existing.winfo_exists():
                        existing.deiconify()
                        existing.focus_force()
                        return

            win = tk.Toplevel(self._root)
            win.title(_t("calendar.window_title", "📅  Calendário - Codis"))
            win.geometry("900x640")
            win.resizable(True, True)
            self._calendar_win = win
            panel = CalendarPanel(win, self._tmgr, self._cfg)
            panel.pack(fill="both", expand=True)
            win.focus_set()
        except Exception as exc:
            logger.warning("Calendar falhou: %s", exc)
            messagebox.showerror(_t("err.title", "Erro"),
                                 str(exc), parent=self._root)

    def _open_clock(self) -> None:
        """Abre o relógio integrado com alarmes e cronômetro."""
        try:
            from src.ui.clock_panel import ClockPanel

            if hasattr(self, "_clock_win") and self._clock_win.winfo_exists():
                self._clock_win.deiconify()
                self._clock_win.focus_force()
                return
            win = tk.Toplevel(self._root)
            win.title(_t("clock.window_title", "Relogio - Codis"))
            win.geometry("340x560")
            win.resizable(False, True)
            self._clock_win = win
            panel = ClockPanel(win, self._tmgr, self._cfg)
            panel.pack(fill="both", expand=True)
            win.focus_set()
        except Exception as exc:
            logger.warning("Clock falhou: %s", exc)
            messagebox.showerror(_t("err.title", "Erro"),
                                 str(exc), parent=self._root)

    def _init_screen_reader_global(self) -> None:
        """Configura leitura de tela global (tipo TalkBack) para todos os widgets."""
        try:
            a11y = getattr(self, "_a11y_mgr", None)
            if a11y and self._cfg.get("a11y_screen_reader", False):
                a11y.setup_widget_reading(self._root)
                logger.debug("Leitor de tela global ativado.")
        except Exception as exc:
            logger.debug("Screen reader global setup: %s", exc)

    def _open_time_tracker(self) -> None:
        """Abre o rastreador de tempo de codificação (offline, privado)."""
        try:
            from src.ui.time_tracker import TimeTrackerModel, TimeTrackerPanel

            tw = getattr(self, "_tracker_win", None)
            if tw is not None and tw.winfo_exists():
                tw.deiconify()
                tw.focus_force()
                return
            if not hasattr(self, "_tracker_model"):
                self._tracker_model = TimeTrackerModel()
            win = tk.Toplevel(self._root)
            win.title(_t("tracker.window_title",
                      "⏱  Code Time Tracker - Codis"))
            win.geometry("560x640")
            win.minsize(440, 480)
            self._tracker_win = win
            panel = TimeTrackerPanel(win, self._tmgr, self._tracker_model)
            panel.pack(fill="both", expand=True)
            win.focus_set()
        except Exception as exc:
            logger.warning("TimeTracker falhou: %s", exc)
            messagebox.showerror(_t("err.title", "Erro"),
                                 str(exc), parent=self._root)

    def _open_dependency_graph(self) -> None:
        """Abre o visualizador de grafo de dependências Python."""
        try:
            from src.ui.dependency_graph import DependencyGraphPanel

            dw = getattr(self, "_depgraph_win", None)
            if dw is not None and dw.winfo_exists():
                dw.deiconify()
                dw.focus_force()
                return
            win = tk.Toplevel(self._root)
            win.title(_t("depgraph.window_title",
                      "Grafo de Dependencias - Codis"))
            win.geometry("900x680")
            win.minsize(600, 480)
            self._depgraph_win = win
            ws = getattr(self, "_workspace", None)
            panel = DependencyGraphPanel(
                win,
                self._tmgr,
                workspace=ws,
                on_open_file=self._open_file,
            )
            panel.pack(fill="both", expand=True)
            win.focus_set()
        except Exception as exc:
            logger.warning("DependencyGraph falhou: %s", exc)
            messagebox.showerror(_t("err.title", "Erro"),
                                 str(exc), parent=self._root)

    def set_workspace(self, path: Path) -> None:
        """API pública - define o workspace. Chamado por main._open_cli_args."""
        self._set_workspace(path)

    def open_file(self, path: Path) -> None:
        """API pública - abre um arquivo. Chamado por main._open_cli_args."""
        self._open_file(path)

    def run(self) -> None:
        """
        Inicia o event loop.
        Chamá-lo quando já há um mainloop ativo é seguro (re-entrância segura
        no Tk: a chamada interna retorna quando a janela é destruída, não antes).
        """
        self._root.mainloop()
