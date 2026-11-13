"""Codis - Preview Web avançado (navegador real embutido, sem API key).

Abre a página (HTML/CSS/JS, Markdown ou SVG) num **Chromium de verdade**
(Edge/Chrome/Brave já instalados) cuja janela é incorporada ao editor, e
liga a ele:

* servidor local com *live reload* e buffers **não salvos** (:mod:`webdev`);
* barra de navegação (voltar/avançar/recarregar/URL) e emulação de
  dispositivo (desktop/tablet/mobile) via DevTools Protocol;
* **console do navegador dentro do Codis** - erros de JS, ``console.log``,
  recursos que falharam, com clique para abrir ``arquivo:linha`` no editor;
* divisão do editor (35/50/70/100 %) e F12 do próprio navegador (DevTools).

Sem Chromium (ou sem suporte a incorporar, como macOS/Wayland) o painel serve
a página e abre no navegador padrão, mantendo o live reload.
"""

from __future__ import annotations

import contextlib
import logging
import os
import queue
import shutil
import sys
import tkinter as tk
import webbrowser
from collections.abc import Callable
from pathlib import Path
from typing import Any

from src.constants import CONFIG_DIR, FONT_MONO, FONT_UI
from src.i18n import _t
from src.ui import webdev
from src.ui import window_embed as we

logger = logging.getLogger("Codis.WebPreview")

__all__ = ["WebPreviewPanel", "markdown_page"]

_PROFILE_ROOT = CONFIG_DIR / "webpreview"


def markdown_page(text: str, title: str, tmgr: Any) -> str:
    """Markdown -> página HTML completa com o tema do Codis."""
    from src.ui import render_panel as rp

    try:
        import markdown as mdlib  # type: ignore[import-untyped]

        body = mdlib.markdown(
            text, extensions=["fenced_code", "tables", "toc", "attr_list", "def_list", "footnotes"]
        )
    except ImportError:
        body = rp.markdown_to_html(text)
    return rp._TEMPLATE.format(  # noqa: SLF001
        title=title, body=body, extra_head="", extra_scripts="", **rp._theme_vars(tmgr)
    )


class WebPreviewPanel(tk.Frame):
    """Painel de preview com navegador embutido; coloque-o com ``place``.

    Args:
        parent: container do editor.
        tmgr: gerenciador de temas.
        cfg: configuração (``web_browser_path``, ``web_extra_args``).
        root_dir: pasta servida (workspace ou pasta do arquivo).
        open_at: ``(caminho, linha)`` chamado ao clicar num erro do console.
        on_status: mensagens para a status bar.
        on_close: chamado uma vez ao encerrar.
    """

    _LEVEL_FG = {"error": "#f38ba8", "warn": "#f9e2af", "info": "#89b4fa", "debug": "#9399b2", "log": ""}

    def __init__(
        self,
        parent: tk.Widget,
        tmgr: Any,
        cfg: Any,
        *,
        root_dir: Path,
        open_at: Callable[[str, int], None] | None = None,
        on_status: Callable[[str], None] | None = None,
        on_close: Callable[[], None] | None = None,
    ) -> None:
        """Monta a interface; chame :meth:`open_path` para carregar uma página."""
        self._t = tmgr.theme
        super().__init__(parent, bg=self._t.panel_bg, highlightthickness=1,
                         highlightbackground=self._t.border)
        self._tmgr, self._cfg = tmgr, cfg
        self._open_at, self._status, self._on_close = open_at, on_status, on_close
        self._server = webdev.DevServer(
            root_dir, md_renderer=lambda text, title: markdown_page(text, title, self._tmgr))
        self._embed: we.EmbeddedAppPanel | None = None
        self._cdp: webdev.CdpClient | None = None
        self._profile: Path | None = None
        self._events: queue.Queue[Any] = queue.Queue()
        self._pump_job: str | None = None
        self._closed = False
        self._counts = {"error": 0, "warn": 0}
        self._console_open = False
        self._url_var = tk.StringVar()
        self._current: Path | None = None
        self._build()
        self.bind("<Destroy>", self._on_destroy, add="+")

    def _btn(self, parent: tk.Widget, text: str, cmd: Callable[[], None], tip: str = "") -> tk.Label:
        t = self._t
        b = tk.Label(parent, text=text, bg=t.input_bg, fg=t.fg, font=(FONT_UI, 10), cursor="hand2", padx=7, pady=2)
        b.pack(side="left", padx=1)
        b.bind("<Button-1>", lambda _e: cmd())
        b.bind("<Enter>", lambda _e: b.config(bg=t.sel_bg))
        b.bind("<Leave>", lambda _e: b.config(bg=t.input_bg))
        return b

    def _build(self) -> None:
        t = self._t
        bar = tk.Frame(self, bg=t.panel_bg)
        bar.pack(fill="x", padx=4, pady=4)
        self._btn(bar, "◀", lambda: self._cdp_do("back"))
        self._btn(bar, "▶", lambda: self._cdp_do("forward"))
        self._btn(bar, "↻", self.reload)
        url = tk.Entry(bar, textvariable=self._url_var, bg=t.input_bg, fg=t.fg, relief="flat",
                       insertbackground=t.fg, font=(FONT_UI, 9))
        url.pack(side="left", fill="x", expand=True, padx=6, ipady=3)
        url.bind("<Return>", lambda _e: self._go(self._url_var.get()))

        self._device_btn = tk.Menubutton(bar, text="📱", bg=t.input_bg, fg=t.fg, relief="flat",
                                         font=(FONT_UI, 10), padx=6)
        menu = tk.Menu(self._device_btn, tearoff=0, bg=t.floating_bg, fg=t.fg)
        for name, preset in webdev.DEVICE_PRESETS.items():
            menu.add_command(label=name, command=lambda p=preset, n=name: self._emulate(p, n))
        self._device_btn.config(menu=menu)
        self._device_btn.pack(side="left", padx=1)

        self._console_btn = self._btn(bar, "⌨ 0", self.toggle_console)
        self._btn(bar, "🌍", self.open_external)
        for frac, label in ((0.35, "⅓"), (0.5, "½"), (0.7, "⅔"), (1.0, "⛶")):
            self._btn(bar, label, lambda f=frac: self.set_split(f))
        self._btn(bar, "✕", self.close)

        self._stage = tk.Frame(self, bg=t.editor_bg)
        self._stage.pack(fill="both", expand=True)
        self._msg = tk.Label(self._stage, bg=t.editor_bg, fg=t.fg_muted, font=(FONT_UI, 10),
                             wraplength=420, justify="center")

        self._console_frame = tk.Frame(self, bg=t.panel_bg, height=150)
        self._console_frame.pack_propagate(False)
        head = tk.Frame(self._console_frame, bg=t.panel_bg)
        head.pack(fill="x")
        tk.Label(head, text=_t("webpreview.console", "Console do navegador"), bg=t.panel_bg,
                 fg=t.fg_muted, font=(FONT_UI, 8, "bold")).pack(side="left", padx=6)
        clear = tk.Label(head, text=_t("webpreview.clear", "limpar"), bg=t.panel_bg, fg=t.accent,
                         font=(FONT_UI, 8), cursor="hand2")
        clear.pack(side="right", padx=6)
        clear.bind("<Button-1>", lambda _e: self._clear_console())
        self._console = tk.Text(self._console_frame, bg=t.editor_bg, fg=t.fg, font=(FONT_MONO, 9),
                                relief="flat", wrap="word", state="disabled", height=6)
        sb = tk.Scrollbar(self._console_frame, command=self._console.yview)
        self._console.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self._console.pack(fill="both", expand=True)
        for lvl, fg in self._LEVEL_FG.items():
            self._console.tag_configure(lvl, foreground=fg or t.fg)
        self._console.tag_configure("link", underline=True)
        self._console.tag_bind("link", "<Button-1>", self._click_console)
        self._console.tag_bind("link", "<Enter>", lambda _e: self._console.config(cursor="hand2"))
        self._console.tag_bind("link", "<Leave>", lambda _e: self._console.config(cursor=""))
        self._links: dict[str, tuple[str, int]] = {}

    def open_path(self, path: Path) -> bool:
        """Mostra ``path`` (HTML/MD/SVG). Inicia o navegador na primeira chamada.

        Returns:
            ``True`` se há navegador embutido; ``False`` se caiu para o navegador externo.
        """
        self._current = path
        self._server.start()
        url = self._server.url_for(path)
        if url is None:
            self._notify(_t("webpreview.outside", "Arquivo fora da pasta servida."))
            return False
        self._url_var.set(url)
        if self._embed is not None and self._cdp is not None and self._cdp.connected:
            self._cdp.navigate(url)
            return True
        if self._embed is not None:
            return True
        return self._launch(url)

    def set_buffer(self, path: Path, text: str | None) -> None:
        """Entrega o texto não salvo do editor ao servidor (live reload sem salvar)."""
        if webdev.is_previewable(path) or path.suffix.lower() in (".css", ".js", ".mjs", ".json"):
            self._server.set_overlay(path, text)

    def reload(self) -> None:
        """Recarrega a página (ignora cache)."""
        if self._cdp is not None and self._cdp.connected:
            self._cdp.reload()
        elif self._current is not None:
            self.open_path(self._current)

    def open_external(self) -> None:
        """Abre a mesma URL no navegador padrão do sistema."""
        url = self._url_var.get().strip() or (self._server.url_for(self._current) if self._current else None)
        if url:
            webbrowser.open(url)

    def _go(self, text: str) -> None:
        text = text.strip()
        if not text:
            return
        p = Path(text.strip('"')).expanduser()
        if "://" not in text and p.is_file():
            self.open_path(p)
            return
        if "://" not in text:
            text = "http://" + text
        if self._cdp is not None and self._cdp.connected:
            self._cdp.navigate(text)

    def _cdp_do(self, what: str) -> None:
        if self._cdp is not None and self._cdp.connected:
            getattr(self._cdp, what)()

    def _emulate(self, preset: tuple[int, int, float, bool] | None, name: str) -> None:
        if self._cdp is not None and self._cdp.connected:
            self._cdp.emulate(preset)
            self._device_btn.config(text="📱" if preset is None else "📱•")
            self._notify(name)

    def _launch(self, url: str) -> bool:
        browser = webdev.find_browser(self._cfg.get("web_browser_path", None) if self._cfg else None)
        backend = we.get_backend() if browser else None
        if browser is None or backend is None:
            reason = (_t("webpreview.no_browser", "Nenhum navegador Chromium (Edge/Chrome/Brave) encontrado.")
                      if browser is None else
                      _t("webpreview.no_embed", "Este ambiente não permite incorporar janelas."))
            self._show_message(reason + "\n" + _t("webpreview.external_fallback", "Abrindo no navegador padrão (com live reload)."))
            webbrowser.open(url)
            return False

        self._profile = _PROFILE_ROOT / f"p{os.getpid()}"
        shutil.rmtree(self._profile, ignore_errors=True)
        self._profile.mkdir(parents=True, exist_ok=True)
        argv = [
            str(browser), f"--app={url}", f"--user-data-dir={self._profile}",
            "--remote-debugging-port=0", "--no-first-run", "--no-default-browser-check",
            "--disable-sync", "--disable-session-crashed-bubble", "--hide-crash-restore-bubble",
            "--disable-features=Translate", "--window-size=900,700",
        ]
        if sys.platform.startswith("linux") and hasattr(os, "geteuid") and os.geteuid() == 0:
            argv.append("--no-sandbox")
        extra = (self._cfg.get("web_extra_args", "") if self._cfg else "") or ""
        if extra:
            import shlex

            with contextlib.suppress(ValueError):
                argv += shlex.split(extra, posix=sys.platform != "win32")

        self._embed = we.EmbeddedAppPanel(
            self._stage, argv, backend=backend,
            labels=we.EmbedLabels(
                hint="", back="", focus="",
                loading=_t("webpreview.loading", "Abrindo o navegador..."),
                embedded=_t("webpreview.embedded", "Preview pronto. F12 abre o DevTools."),
                closed=_t("webpreview.closed", "Preview fechado."),
                err_exited=_t("webpreview.err_exited", "O navegador encerrou ao iniciar (código {code}). {log}"),
                err_timeout=_t("webpreview.err_timeout", "O navegador não abriu a janela em {seconds} s."),
                err_launch=_t("webpreview.err_launch", "Falha ao iniciar o navegador: {reason}"),
                embed_failed=_t("webpreview.err_embed", "Não foi possível incorporar o navegador: {reason}"),
            ),
            theme=we.EmbedTheme(bg=self._t.editor_bg, boot_fg=self._t.fg_muted),
            cwd=str(self._profile), env=os.environ.copy(), window_timeout=30.0, show_bar=False,
            on_status=self._notify, on_close=self.close,
        )
        self._embed.pack(fill="both", expand=True)
        self._embed.start()
        self._cdp = webdev.CdpClient(self._profile, self._events_put_console, self._events_put_nav)
        self._cdp.connect_async()
        self._pump()
        return True

    def _events_put_console(self, entry: webdev.ConsoleEntry) -> None:
        self._events.put(("console", entry))

    def _events_put_nav(self, url: str) -> None:
        self._events.put(("nav", url))

    def _pump(self) -> None:
        self._pump_job = None
        if self._closed:
            return
        try:
            while True:
                kind, payload = self._events.get_nowait()
                if kind == "console":
                    self._add_console(payload)
                elif kind == "nav" and payload and not payload.startswith(("about:", "chrome-error:")):
                    self._url_var.set(payload)
        except queue.Empty:
            pass
        with contextlib.suppress(tk.TclError):
            self._pump_job = self.after(150, self._pump)

    def toggle_console(self) -> None:
        """Mostra/esconde o console do navegador."""
        self._console_open = not self._console_open
        if self._console_open:
            self._console_frame.pack(fill="x", side="bottom", before=self._stage)
        else:
            self._console_frame.pack_forget()

    def _add_console(self, e: webdev.ConsoleEntry) -> None:
        if e.level in self._counts:
            self._counts[e.level] += 1
            self._console_btn.config(text=f"⌨ ✖{self._counts['error']} ⚠{self._counts['warn']}")
        c = self._console
        c.config(state="normal")
        c.insert("end", f"[{e.level}] ", e.level)
        c.insert("end", e.text + "\n", e.level)
        local = self._server.path_for_url(e.url) if e.url else None
        if local is not None and e.line:
            tag = f"lnk{len(self._links)}"
            self._links[tag] = (str(local), e.line)
            c.insert("end", f"    {local.name}:{e.line}\n", ("link", tag))
        c.see("end")
        c.config(state="disabled")
        if e.level == "error" and not self._console_open:
            self.toggle_console()

    def _click_console(self, event: tk.Event) -> None:
        idx = self._console.index(f"@{event.x},{event.y}")
        for tag in self._console.tag_names(idx):
            if tag in self._links and self._open_at:
                self._open_at(*self._links[tag])
                return

    def _clear_console(self) -> None:
        self._console.config(state="normal")
        self._console.delete("1.0", "end")
        self._console.config(state="disabled")
        self._counts = {"error": 0, "warn": 0}
        self._links.clear()
        self._console_btn.config(text="⌨ 0")

    def set_split(self, frac: float) -> None:
        """Ocupa ``frac`` da largura do editor (a partir da direita)."""
        frac = max(0.2, min(1.0, frac))
        self.place(relx=1.0 - frac, rely=0.0, relwidth=frac, relheight=1.0)
        self.lift()

    def _show_message(self, text: str) -> None:
        self._msg.config(text=text)
        self._msg.place(relx=0.5, rely=0.5, anchor="center")

    def _notify(self, msg: str) -> None:
        if self._status is not None:
            with contextlib.suppress(Exception):
                self._status(msg)

    def close(self) -> None:
        """Fecha navegador, servidor e painel (idempotente)."""
        if self._closed:
            return
        self._closed = True
        if self._pump_job is not None:
            with contextlib.suppress(tk.TclError, ValueError):
                self.after_cancel(self._pump_job)
        if self._cdp is not None:
            self._cdp.close()
        embed, self._embed = self._embed, None
        if embed is not None:
            with contextlib.suppress(Exception):
                embed.close()
        self._server.stop()
        top: tk.Misc | None = None
        with contextlib.suppress(tk.TclError):
            top = self.winfo_toplevel()
        if self._profile is not None:
            prof = self._profile
            shutil.rmtree(prof, ignore_errors=True)
            if top is not None:
                with contextlib.suppress(tk.TclError):
                    top.after(3000, lambda: shutil.rmtree(prof, ignore_errors=True))
        cb, self._on_close = self._on_close, None
        with contextlib.suppress(tk.TclError):
            self.destroy()
        if cb is not None:
            with contextlib.suppress(Exception):
                cb()

    def _on_destroy(self, event: tk.Event) -> None:
        if event.widget is self and not self._closed:
            self._closed = True
            if self._cdp is not None:
                self._cdp.close()
            self._server.stop()
