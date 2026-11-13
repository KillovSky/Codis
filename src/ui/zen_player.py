"""Codis - Zen Player (player de música com capa, equalizador e dock).

Arquitetura:
    * :class:`ZenPlayerView` - toda a interface. Existe em dois layouts:
      ``full`` (janela flutuante, capa grande, abas Tocando/Fila/Equalizador) e
      ``compact`` (barra encaixada no rodapé da janela principal).
    * :class:`ZenPlayerHost` - decide ONDE a view vive: janela flutuante
      (*detach*) ou encaixada na janela principal (*attach*). Widgets Tk não
      mudam de pai, então a view é reconstruída ao trocar de modo; todo o
      estado (faixa, posição, volume, EQ) mora no motor, então a troca é
      imperceptível e a música não é interrompida.

A reprodução usa :class:`src.ui.zen_media.ZenMediaPlayer` (mpv/VLC).
"""

from __future__ import annotations

import contextlib
import logging
import queue
import threading
import tkinter as tk
from collections import OrderedDict
from collections.abc import Callable
from typing import Any

from src.constants import FONT_UI
from src.theme.utils import alpha_blend_hex
from src.ui import zen_audio as za
from src.ui.zen_media import ZenMediaPlayer, _fmt_time
from src.ui.zen_player_widgets import (
    EqualizerPanel,
    FlatButton,
    Palette,
    QueuePanel,
    SeekBar,
)

logger = logging.getLogger("Codis.ZenPlayer")

MODE_FLOATING = "floating"
MODE_DOCKED = "docked"
DOCK_HEIGHT = 74
FLOAT_SIZE = (800, 470)


def _rounded_photo(img: Any, size: int, radius: int):
    """PIL -> PhotoImage quadrada com cantos arredondados."""
    from PIL import Image, ImageDraw, ImageTk

    scale = 3
    resized = img.convert("RGBA").resize((size, size), Image.LANCZOS)
    mask = Image.new("L", (size * scale, size * scale), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        (0, 0, size * scale - 1, size * scale - 1), radius=radius * scale, fill=255)
    mask = mask.resize((size, size), Image.LANCZOS)
    resized.putalpha(mask)
    return ImageTk.PhotoImage(resized)


class _Info:
    """Dados exibidos da faixa atual (resultado do worker de capa/metadados)."""

    __slots__ = ("url", "title", "artist", "album", "image", "accent", "origin")

    def __init__(self, url: str, title: str = "", artist: str = "", album: str = "",
                 image: Any = None, accent: str | None = None, origin: str = "none") -> None:
        self.url, self.title, self.artist, self.album = url, title, artist, album
        self.image, self.accent, self.origin = image, accent, origin


class ZenPlayerView(tk.Frame):
    """Interface do player (layout ``full`` ou ``compact``)."""

    _COVER_CACHE_MAX = 24
    _cover_cache: OrderedDict[str, _Info] = OrderedDict()
    _cover_lock = threading.Lock()

    def __init__(self, parent: tk.Misc, player: ZenMediaPlayer, cfg: Any, theme: Any,
                 compact: bool, host: ZenPlayerHost) -> None:
        self._theme = theme
        self._base_pal = Palette.from_theme(theme)
        self._pal = self._base_pal
        super().__init__(parent, bg=self._pal.bg)
        self._player = player
        self._cfg = cfg
        self._compact = compact
        self._host = host
        self._shown_url: str | None = None
        self._cover_photo: Any = None
        self._cover_size = 52 if compact else 292
        self._info: _Info | None = None
        self._page = "now"
        self._marquee_job: str | None = None
        self._marquee_x = 0.0
        self._marquee_wait = 0
        self._alive = True
        self._muted_from: int | None = None
        self._popup: tk.Toplevel | None = None
        self._popup_kind: str | None = None
        self._buttons: list[FlatButton] = []
        self._seekbars: list[SeekBar] = []
        self._pages: dict[str, tk.Frame] = {}
        self._tab_labels: dict[str, tk.Label] = {}
        self._eq: EqualizerPanel | None = None
        self._queue: QueuePanel | None = None
        self._results: queue.SimpleQueue[_Info] = queue.SimpleQueue()
        self._poll_job: str | None = None
        self._loading_cover = False

        if compact:
            self._build_compact()
        else:
            self._build_full()

        player.add_listener(self, change=self._on_change, progress=self._on_progress,
                            state=self._on_state)
        self.bind("<Destroy>", self._on_destroy, add="+")
        self._on_change()
        self._on_state(player.is_playing, player.is_paused, player.is_loading)
        self._on_progress(player.position, player.duration)

    def _btn(self, parent: tk.Misc, kind: str, cmd: Callable[[], None], size: int = 32,
             scale: float = 0.36, filled: bool = False, tip: str = "") -> FlatButton:
        b = FlatButton(parent, kind, cmd, self._pal, size=size, icon_scale=scale,
                       filled=filled, tooltip=tip)
        self._buttons.append(b)
        return b

    def _seek(self, parent: tk.Misc) -> SeekBar:
        bar = SeekBar(parent, self._pal, on_seek=self._seek_commit,
                      on_drag=self._seek_preview,
                      hover_text=lambda f: _fmt_time(f * self._player.duration))
        self._seekbars.append(bar)
        return bar

    def _build_transport(self, parent: tk.Misc, big: int, small: int) -> None:
        self._shuffle_btn = self._btn(parent, "shuffle", self._cmd_shuffle, small, tip="Aleatório")
        self._prev_btn = self._btn(parent, "prev", self._player.prev, small, 0.34, tip="Anterior")
        self._play_btn = self._btn(parent, "play", self._player.toggle_pause, big, 0.34,
                                   filled=True, tip="Tocar / pausar")
        self._next_btn = self._btn(parent, "next", self._player.next, small, 0.34, tip="Próxima")
        self._repeat_btn = self._btn(parent, "repeat", self._cmd_repeat, small, tip="Repetir")

    def _build_full(self) -> None:
        pal = self._pal
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        left = tk.Frame(self, bg=pal.bg)
        left.grid(row=0, column=0, sticky="ns", padx=(22, 0), pady=18)
        sz = self._cover_size
        self._cover = tk.Canvas(left, width=sz + 28, height=sz + 28, bg=pal.bg, highlightthickness=0)
        self._cover.pack(expand=True)
        self._time_cover = tk.Label(left, text="", bg=pal.bg, fg=pal.fg_dim, font=(FONT_UI, 8))
        self._time_cover.pack(pady=(2, 0))

        right = tk.Frame(self, bg=pal.bg)
        right.grid(row=0, column=1, sticky="nsew", padx=(18, 22), pady=(14, 18))

        head = tk.Frame(right, bg=pal.bg)
        head.pack(fill="x")
        for key, label in (("now", "Tocando"), ("queue", "Fila"), ("eq", "Equalizador")):
            lbl = tk.Label(head, text=label, bg=pal.bg, fg=pal.fg_dim, font=(FONT_UI, 10, "bold"),
                           cursor="hand2", padx=2, pady=6)
            lbl.pack(side="left", padx=(0, 16))
            lbl.bind("<Button-1>", lambda _e, k=key: self.show_page(k))
            self._tab_labels[key] = lbl
        self._btn(head, "minimize", self._host.minimize, 28, 0.32, tip="Minimizar para a bolinha").pack(side="right")
        self._dock_btn = self._btn(head, "dock", self._host.toggle_dock, 28, 0.34,
                                   tip="Encaixar na janela principal")
        self._dock_btn.pack(side="right")
        self._pin_btn = self._btn(head, "pin", self._host.toggle_pin, 28, 0.32,
                                  tip="Manter no topo")
        self._pin_btn.pack(side="right")
        self._pin_btn.set_active(self._host.pinned)
        self._tab_line = tk.Canvas(right, height=2, bg=pal.bg, highlightthickness=0)
        self._tab_line.pack(fill="x")

        body = tk.Frame(right, bg=pal.bg)
        body.pack(fill="both", expand=True, pady=(12, 0))
        self._pages["now"] = self._build_now_page(body)
        self._pages["queue"] = tk.Frame(body, bg=pal.bg)
        self._queue = QueuePanel(self._pages["queue"], self._player, self._cfg, pal)
        self._queue.pack(fill="both", expand=True)
        self._pages["eq"] = tk.Frame(body, bg=pal.bg)
        self._eq = EqualizerPanel(self._pages["eq"], self._player, self._cfg, pal)
        self._eq.pack(fill="both", expand=True)
        self.show_page("now")

    def _build_now_page(self, parent: tk.Misc) -> tk.Frame:
        pal = self._pal
        page = tk.Frame(parent, bg=pal.bg)
        tk.Frame(page, bg=pal.bg).pack(fill="both", expand=True)

        self._title_cv = tk.Canvas(page, height=34, bg=pal.bg, highlightthickness=0)
        self._title_cv.pack(fill="x")
        self._artist = tk.Label(page, text="", bg=pal.bg, fg=pal.fg_dim, font=(FONT_UI, 11),
                                anchor="w")
        self._artist.pack(fill="x")
        self._status = tk.Label(page, text="", bg=pal.bg, fg=pal.fg_dim, font=(FONT_UI, 8), anchor="w")
        self._status.pack(fill="x", pady=(2, 0))

        tk.Frame(page, bg=pal.bg, height=16).pack()
        seek_row = tk.Frame(page, bg=pal.bg)
        seek_row.pack(fill="x")
        self._pos_lbl = tk.Label(seek_row, text="0:00", bg=pal.bg, fg=pal.fg_dim,
                                 font=(FONT_UI, 8), width=5, anchor="w")
        self._pos_lbl.pack(side="left")
        self._dur_lbl = tk.Label(seek_row, text="0:00", bg=pal.bg, fg=pal.fg_dim,
                                 font=(FONT_UI, 8), width=5, anchor="e")
        self._dur_lbl.pack(side="right")
        self._seek_bar = self._seek(seek_row)
        self._seek_bar.pack(side="left", fill="x", expand=True)

        controls = tk.Frame(page, bg=pal.bg)
        controls.pack(pady=(8, 4))
        self._build_transport(controls, 58, 38)
        for b in (self._shuffle_btn, self._prev_btn, self._play_btn, self._next_btn, self._repeat_btn):
            b.pack(side="left", padx=6)

        vol = tk.Frame(page, bg=pal.bg)
        vol.pack(pady=(4, 0))
        self._vol_icon = self._btn(vol, "volume", self._toggle_mute, 26, 0.34, tip="Mudo")
        self._vol_icon.pack(side="left")
        self._vol_bar = SeekBar(vol, pal, on_seek=self._volume_set, on_drag=self._volume_set)
        self._vol_bar.configure(width=170)
        self._vol_bar.pack(side="left", padx=4)
        self._seekbars.append(self._vol_bar)
        self._vol_bar.set_value(self._player.volume / 100)
        return page

    def _build_compact(self) -> None:
        pal = self._pal
        self.configure(height=DOCK_HEIGHT)
        self.grid_propagate(False)
        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(3, weight=1)
        top = tk.Frame(self, bg=pal.surface_hi, height=1)
        top.place(x=0, y=0, relwidth=1)

        sz = self._cover_size
        self._cover = tk.Canvas(self, width=sz + 8, height=sz + 8, bg=pal.bg, highlightthickness=0)
        self._cover.grid(row=0, column=0, padx=(12, 8), pady=8)

        info = tk.Frame(self, bg=pal.bg, width=210)
        info.grid(row=0, column=1, sticky="ns", pady=8)
        info.pack_propagate(False)
        self._title_cv = tk.Canvas(info, height=22, bg=pal.bg, highlightthickness=0)
        self._title_cv.pack(fill="x", pady=(8, 0))
        self._artist = tk.Label(info, text="", bg=pal.bg, fg=pal.fg_dim, font=(FONT_UI, 9), anchor="w")
        self._artist.pack(fill="x")
        self._status = tk.Label(self, text="")

        transport = tk.Frame(self, bg=pal.bg)
        transport.grid(row=0, column=2, padx=8)
        self._build_transport(transport, 40, 30)
        for b in (self._shuffle_btn, self._prev_btn, self._play_btn, self._next_btn, self._repeat_btn):
            b.pack(side="left", padx=2)

        seek = tk.Frame(self, bg=pal.bg)
        seek.grid(row=0, column=3, sticky="ew", padx=8)
        self._pos_lbl = tk.Label(seek, text="0:00", bg=pal.bg, fg=pal.fg_dim, font=(FONT_UI, 8), width=5)
        self._pos_lbl.pack(side="left")
        self._dur_lbl = tk.Label(seek, text="0:00", bg=pal.bg, fg=pal.fg_dim, font=(FONT_UI, 8), width=5)
        self._dur_lbl.pack(side="right")
        self._seek_bar = self._seek(seek)
        self._seek_bar.pack(side="left", fill="x", expand=True)

        self._vol_group = tk.Frame(self, bg=pal.bg)
        self._vol_group.grid(row=0, column=4, padx=4)
        self._vol_icon = self._btn(self._vol_group, "volume", self._toggle_mute, 26, 0.34, tip="Mudo")
        self._vol_icon.pack(side="left")
        self._vol_bar = SeekBar(self._vol_group, pal, on_seek=self._volume_set, on_drag=self._volume_set)
        self._vol_bar.configure(width=84)
        self._vol_bar.pack(side="left")
        self._seekbars.append(self._vol_bar)
        self._vol_bar.set_value(self._player.volume / 100)

        tools = tk.Frame(self, bg=pal.bg)
        tools.grid(row=0, column=5, padx=(4, 10))
        self._eq_btn = self._btn(tools, "eq", lambda: self._toggle_popup("eq"), 30, 0.36, tip="Equalizador")
        self._eq_btn.pack(side="left")
        self._queue_btn = self._btn(tools, "queue", lambda: self._toggle_popup("queue"), 30, 0.36, tip="Fila")
        self._queue_btn.pack(side="left")
        self._dock_btn = self._btn(tools, "undock", self._host.toggle_dock, 30, 0.34,
                                   tip="Destacar em janela flutuante")
        self._dock_btn.pack(side="left")
        self._btn(tools, "minimize", self._host.minimize, 30, 0.32, tip="Minimizar para a bolinha").pack(side="left")
        self.bind("<Configure>", self._on_compact_resize, add="+")

    def _on_compact_resize(self, e: tk.Event) -> None:
        if e.widget is not self:
            return
        if e.width < 860:
            self._vol_group.grid_remove()
        else:
            self._vol_group.grid()

    def show_page(self, key: str) -> None:
        """Mostra a aba ``now``, ``queue`` ou ``eq`` (layout completo)."""
        if self._compact or key not in self._pages:
            return
        self._page = key
        for k, frame in self._pages.items():
            frame.pack_forget()
        self._pages[key].pack(fill="both", expand=True)
        for k, lbl in self._tab_labels.items():
            lbl.configure(fg=self._pal.fg if k == key else self._pal.fg_dim)
        self._draw_tab_line()
        if key == "eq" and self._eq:
            self._eq.sync_from_player()
        if key == "queue" and self._queue:
            self._queue.refresh()

    def _draw_tab_line(self) -> None:
        if self._compact:
            return
        self.update_idletasks()
        line = self._tab_line
        line.delete("all")
        lbl = self._tab_labels[self._page]
        line.create_rectangle(0, 0, line.winfo_width() or 1, 1, fill=self._pal.track, outline="")
        line.create_rectangle(lbl.winfo_x(), 0, lbl.winfo_x() + lbl.winfo_width(), 2,
                              fill=self._pal.accent, outline="")

    def _toggle_popup(self, kind: str) -> None:
        if self._popup is not None and self._popup_kind == kind:
            self._close_popup()
            return
        self._close_popup()
        pal = self._pal
        pop = tk.Toplevel(self)
        pop.withdraw()
        pop.wm_overrideredirect(True)
        pop.attributes("-topmost", True)
        pop.configure(bg=pal.surface_hi)
        inner = tk.Frame(pop, bg=pal.bg, padx=16, pady=14)
        inner.pack(padx=1, pady=1, fill="both", expand=True)
        if kind == "eq":
            panel: tk.Frame = EqualizerPanel(inner, self._player, self._cfg, pal)
            self._eq = panel  # type: ignore[assignment]
            width = 500
        else:
            panel = QueuePanel(inner, self._player, self._cfg, pal)
            panel.configure(height=300)
            self._queue = panel  # type: ignore[assignment]
            width = 420
        panel.pack(fill="both", expand=True)
        pop.update_idletasks()
        w = max(width, pop.winfo_reqwidth())
        h = pop.winfo_reqheight() if kind == "eq" else 380
        btn = self._eq_btn if kind == "eq" else self._queue_btn
        x = min(max(0, btn.winfo_rootx() + btn.winfo_width() - w),
                self.winfo_screenwidth() - w)
        y = max(0, self.winfo_rooty() - h - 6)
        pop.geometry(f"{w}x{h}+{x}+{y}")
        pop.deiconify()
        self._popup, self._popup_kind = pop, kind
        btn.set_active(True)
        pop.bind("<Escape>", lambda _e: self._close_popup())
        pop.focus_set()
        self.winfo_toplevel().bind("<ButtonPress-1>", self._outside_click, add="+")

    def _outside_click(self, e: tk.Event) -> None:
        if self._popup is None:
            return
        w = e.widget
        with contextlib.suppress(tk.TclError, AttributeError):
            if str(w).startswith(str(self._popup)):
                return
            if w in (self._eq_btn, self._queue_btn):
                return
        self._close_popup()

    def _close_popup(self) -> None:
        if self._popup is not None:
            with contextlib.suppress(tk.TclError):
                self._popup.destroy()
        self._popup = None
        self._popup_kind = None
        if self._compact:
            for b in (getattr(self, "_eq_btn", None), getattr(self, "_queue_btn", None)):
                if b is not None:
                    with contextlib.suppress(tk.TclError):
                        b.set_active(False)

    def _cmd_shuffle(self) -> None:
        self._player.set_shuffle(not self._player.shuffle)
        self._sync_modes()

    def _cmd_repeat(self) -> None:
        order = [ZenMediaPlayer.REPEAT_OFF, ZenMediaPlayer.REPEAT_ALL, ZenMediaPlayer.REPEAT_ONE]
        cur = self._player.repeat
        self._player.set_repeat(order[(order.index(cur) + 1) % 3 if cur in order else 1])
        self._sync_modes()

    def _sync_modes(self) -> None:
        self._shuffle_btn.set_active(self._player.shuffle)
        mode = self._player.repeat
        self._repeat_btn.set_kind("repeat_one" if mode == ZenMediaPlayer.REPEAT_ONE else "repeat")
        self._repeat_btn.set_active(mode != ZenMediaPlayer.REPEAT_OFF)
        with contextlib.suppress(Exception):  # noqa: BLE001
            self._cfg.set("zen_media_shuffle", self._player.shuffle)
            self._cfg.set("zen_media_repeat", mode)

    def _seek_preview(self, frac: float) -> None:
        dur = self._player.duration
        if dur > 0:
            self._pos_lbl.configure(text=_fmt_time(frac * dur))

    def _seek_commit(self, frac: float) -> None:
        dur = self._player.duration
        if dur > 0:
            self._player.seek(frac * dur)

    def _volume_set(self, frac: float) -> None:
        self._muted_from = None
        self._player.set_volume(int(round(frac * 100)))
        self._sync_volume_icon()
        with contextlib.suppress(Exception):  # noqa: BLE001
            self._cfg.set("zen_media_volume", self._player.volume)

    def _toggle_mute(self) -> None:
        if self._muted_from is None:
            self._muted_from = max(self._player.volume, 10)
            self._player.set_volume(0)
            self._vol_bar.set_value(0)
        else:
            self._player.set_volume(self._muted_from)
            self._vol_bar.set_value(self._muted_from / 100)
            self._muted_from = None
        self._sync_volume_icon()

    def _sync_volume_icon(self) -> None:
        self._vol_icon.set_kind("mute" if self._player.volume == 0 else "volume")

    def _on_state(self, playing: bool, paused: bool, loading: bool) -> None:
        if not self._alive:
            return
        with contextlib.suppress(tk.TclError):
            self._play_btn.set_kind("pause" if playing and not paused else "play")
            self._status.configure(
                text="Carregando…" if loading else ("Pausado" if playing and paused else ""))
            self._sync_modes()
            if self._queue and (self._compact is False or self._popup_kind == "queue"):
                self._queue.refresh()

    def _on_progress(self, pos: float, dur: float) -> None:
        if not self._alive or self._seek_bar.dragging:
            return
        with contextlib.suppress(tk.TclError):
            self._pos_lbl.configure(text=_fmt_time(pos))
            self._dur_lbl.configure(text=_fmt_time(dur))
            self._seek_bar.set_value(pos / dur if dur > 0 else 0.0)

    def _on_change(self) -> None:
        if not self._alive:
            return
        with contextlib.suppress(tk.TclError):
            if self._queue:
                self._queue.refresh()
            url = self._player.current_url if self._player.track_count else ""
            name = self._player.current_track
            if url == self._shown_url and self._info is not None:
                return
            self._shown_url = url
            if not url:
                self._apply_info(_Info("", title="Nenhuma faixa",
                                       artist="Adicione músicas na aba Fila"))
                return
            self._apply_info(_Info(url, title=name, artist="Carregando capa…"), final=False)
            self._loading_cover = True
            threading.Thread(target=self._load_info_worker, args=(url, name), daemon=True,
                             name="CodisPlayerCover").start()
            if self._poll_job is None:
                self._poll_job = self.after(40, self._poll_results)

    def _load_info_worker(self, url: str, name: str) -> None:
        with self._cover_lock:
            cached = self._cover_cache.get(url)
        if cached is None:
            meta = za.read_metadata(url, fallback_title=name)
            img, origin = za.load_cover_image(url, 512)
            accent = za.dominant_color(img) if img is not None else None
            cached = _Info(url, meta["title"], meta["artist"], meta["album"], img, accent, origin)
            with self._cover_lock:
                self._cover_cache[url] = cached
                while len(self._cover_cache) > self._COVER_CACHE_MAX:
                    self._cover_cache.popitem(last=False)
        self._results.put(cached)

    def _poll_results(self) -> None:
        """Consome resultados do worker na thread principal (Tk não é thread-safe)."""
        self._poll_job = None
        if not self._alive:
            return
        try:
            while True:
                self._info_ready(self._results.get_nowait())
        except queue.Empty:
            pass
        except tk.TclError:
            return
        if self._loading_cover:
            self._poll_job = self.after(60, self._poll_results)

    def _info_ready(self, info: _Info) -> None:
        if self._alive and info.url == self._shown_url:
            self._loading_cover = False
            self._apply_info(info)

    def _apply_info(self, info: _Info, final: bool = True) -> None:
        self._info = info
        sub = " · ".join(x for x in (info.artist, info.album) if x)
        self._artist.configure(text=sub)
        self._set_title(info.title)
        if info.image is not None:
            try:
                self._cover_photo = _rounded_photo(info.image, self._cover_size,
                                                   12 if not self._compact else 8)
            except Exception as exc:  # noqa: BLE001
                logger.debug("capa: %s", exc)
                self._cover_photo = None
        elif final:
            self._cover_photo = None
        if final and info.accent:
            self._animate_accent(info.accent)
        self._draw_cover()
        if final and not self._compact:
            origin = {"sidecar": "capa da pasta", "embedded": "capa do arquivo",
                      "cache": "capa do arquivo", "ffmpeg": "miniatura do vídeo",
                      "generated": "capa gerada"}.get(info.origin, "")
            if origin:
                self._time_cover.configure(text=origin)

    def _draw_cover(self) -> None:
        c = self._cover
        c.delete("all")
        pad = 14 if not self._compact else 4
        sz = self._cover_size
        pal = self._pal
        if not self._compact:
            for i, k in enumerate((10, 7, 4, 2)):
                c.create_rectangle(pad - k, pad - k + 6, pad + sz + k, pad + sz + k + 6,
                                   fill=alpha_blend_hex(pal.accent, pal.bg, 0.05 + i * 0.03),
                                   outline="")
        if self._cover_photo is not None:
            c.create_image(pad, pad, image=self._cover_photo, anchor="nw")
        else:
            c.create_rectangle(pad, pad, pad + sz, pad + sz, fill=pal.surface_hi, outline="")

    def _set_title(self, text: str) -> None:
        cv = self._title_cv
        cv.delete("all")
        size = 11 if self._compact else 17
        cv.create_text(0, cv.winfo_reqheight() // 2, text=text, anchor="w", fill=self._pal.fg,
                       font=(FONT_UI, size, "bold"), tags="title")
        self._marquee_x = 0.0
        self._marquee_wait = 30
        if self._marquee_job is None:
            self._marquee_job = self.after(60, self._tick_marquee)

    def _tick_marquee(self) -> None:
        self._marquee_job = None
        if not self._alive:
            return
        try:
            cv = self._title_cv
            box = cv.bbox("title")
            if box:
                text_w = box[2] - box[0]
                view_w = max(cv.winfo_width(), 1)
                if text_w > view_w - 4:
                    if self._marquee_wait > 0:
                        self._marquee_wait -= 1
                    else:
                        self._marquee_x -= 1.2
                        if self._marquee_x < -(text_w - view_w + 24):
                            self._marquee_x = 0.0
                            self._marquee_wait = 40
                        cv.coords("title", self._marquee_x, cv.winfo_height() // 2)
                elif self._marquee_x:
                    self._marquee_x = 0.0
                    cv.coords("title", 0, cv.winfo_height() // 2)
            self._marquee_job = self.after(33, self._tick_marquee)
        except tk.TclError:
            self._marquee_job = None

    def _animate_accent(self, target: str, steps: int = 8) -> None:
        start = self._pal.accent

        def _step(i: int) -> None:
            if not self._alive:
                return
            color = alpha_blend_hex(target, start, i / steps)
            self._apply_palette(Palette.from_theme(self._theme, accent=color))
            if i < steps:
                self.after(20, _step, i + 1)

        _step(1)

    def _apply_palette(self, pal: Palette) -> None:
        self._pal = pal
        with contextlib.suppress(tk.TclError):
            for b in self._buttons:
                b.set_palette(pal)
            for s in self._seekbars:
                s.set_palette(pal)
            if self._eq:
                self._eq.set_palette(pal)
            if self._queue:
                self._queue.set_palette(pal)
            for k, lbl in self._tab_labels.items():
                lbl.configure(fg=pal.fg if k == self._page else pal.fg_dim)
            self._draw_cover()
            if not self._compact:
                self._draw_tab_line()

    def set_pinned(self, pinned: bool) -> None:
        """Reflete o estado do "manter no topo" no botão."""
        pin = getattr(self, "_pin_btn", None)
        if pin is not None:
            pin.set_active(pinned)

    def _on_destroy(self, e: tk.Event) -> None:
        if e.widget is not self:
            return
        self._alive = False
        self._player.remove_listener(self)
        self._close_popup()
        for attr in ("_marquee_job", "_poll_job"):
            job = getattr(self, attr)
            if job is not None:
                with contextlib.suppress(tk.TclError, ValueError):
                    self.after_cancel(job)
                setattr(self, attr, None)


class ZenPlayerHost:
    """Gerencia onde o player vive: janela flutuante ou dock na janela principal."""

    def __init__(self, root: tk.Tk, player: ZenMediaPlayer, cfg: Any, tmgr: Any,
                 dock_before: tk.Widget | None = None,
                 on_minimize: Callable[[], None] | None = None) -> None:
        self._root = root
        self._player = player
        self._cfg = cfg
        self._tmgr = tmgr
        self._dock_before = dock_before
        self._on_minimize = on_minimize
        self._win: tk.Toplevel | None = None
        self._view: ZenPlayerView | None = None
        self._visible = False
        mode = cfg.get("zen_player_mode", MODE_FLOATING)
        self._mode = mode if mode in (MODE_FLOATING, MODE_DOCKED) else MODE_FLOATING
        self.pinned = bool(cfg.get("zen_player_topmost", False))
        self._restore_player_prefs()

    def _restore_player_prefs(self) -> None:
        cfg, p = self._cfg, self._player
        gains = cfg.get("zen_eq_gains")
        if gains:
            p.set_equalizer(gains, bool(cfg.get("zen_eq_enabled", True)))
        vol = cfg.get("zen_media_volume")
        if isinstance(vol, int):
            p.set_volume(vol)
        p.set_shuffle(bool(cfg.get("zen_media_shuffle", False)))
        rep = cfg.get("zen_media_repeat", ZenMediaPlayer.REPEAT_OFF)
        if rep in (ZenMediaPlayer.REPEAT_OFF, ZenMediaPlayer.REPEAT_ALL, ZenMediaPlayer.REPEAT_ONE):
            p.set_repeat(rep)

    @property
    def mode(self) -> str:
        """Modo atual: ``floating`` ou ``docked``."""
        return self._mode

    @property
    def visible(self) -> bool:
        """Se o player está visível (em qualquer modo)."""
        return self._visible and self._view is not None

    def winfo_exists(self) -> bool:
        """Indica se a view do player ainda existe."""
        return self._view is not None

    def show(self) -> None:
        """Mostra o player no modo atual."""
        self._mount(self._mode)

    def hide(self) -> None:
        """Esconde o player (a música continua tocando)."""
        self._teardown()
        self._visible = False

    def toggle(self) -> None:
        """Mostra se escondido; esconde se visível."""
        if self.visible:
            self.hide()
        else:
            self.show()

    def toggle_dock(self) -> None:
        """Alterna entre janela flutuante (detach) e encaixado (attach)."""
        self.set_mode(MODE_FLOATING if self._mode == MODE_DOCKED else MODE_DOCKED)

    def set_mode(self, mode: str) -> None:
        """Muda o modo e persiste a preferência."""
        if mode not in (MODE_FLOATING, MODE_DOCKED):
            return
        self._mode = mode
        with contextlib.suppress(Exception):  # noqa: BLE001
            self._cfg.set("zen_player_mode", mode)
        self._mount(mode)

    def toggle_pin(self) -> None:
        """Alterna "manter no topo" da janela flutuante."""
        self.pinned = not self.pinned
        with contextlib.suppress(Exception):  # noqa: BLE001
            self._cfg.set("zen_player_topmost", self.pinned)
        if self._win is not None:
            with contextlib.suppress(tk.TclError):
                self._win.attributes("-topmost", self.pinned)
        if self._view is not None:
            self._view.set_pinned(self.pinned)

    def minimize(self) -> None:
        """Fecha a view e delega para a bolinha flutuante."""
        self.hide()
        if self._on_minimize:
            self._on_minimize()

    def destroy(self) -> None:
        """Libera todos os recursos."""
        self.hide()

    def _teardown(self) -> None:
        if self._win is not None:
            with contextlib.suppress(tk.TclError):
                self._cfg.set("zen_player_geometry", self._win.geometry())
        if self._view is not None:
            with contextlib.suppress(tk.TclError):
                self._view.destroy()
            self._view = None
        if self._win is not None:
            with contextlib.suppress(tk.TclError):
                self._win.destroy()
            self._win = None

    def _mount(self, mode: str) -> None:
        self._teardown()
        theme = self._tmgr.theme
        if mode == MODE_DOCKED and self._dock_before is not None:
            view = ZenPlayerView(self._root, self._player, self._cfg, theme, True, self)
            try:
                view.pack(side="bottom", fill="x", before=self._dock_before)
            except tk.TclError:
                view.pack(side="bottom", fill="x")
            self._view = view
        else:
            self._mode = MODE_FLOATING if mode != MODE_DOCKED else self._mode
            win = tk.Toplevel(self._root)
            win.title("Codis Player")
            win.configure(bg=Palette.from_theme(theme).bg)
            win.minsize(700, 430)
            geo = self._cfg.get("zen_player_geometry")
            if isinstance(geo, str) and "x" in geo:
                with contextlib.suppress(tk.TclError):
                    win.geometry(geo)
            else:
                win.geometry(f"{FLOAT_SIZE[0]}x{FLOAT_SIZE[1]}")
            win.attributes("-topmost", self.pinned)
            win.protocol("WM_DELETE_WINDOW", self.minimize)
            win.bind("<space>", self._space_toggle)
            win.bind("<Left>", lambda _e: self._player.prev())
            win.bind("<Right>", lambda _e: self._player.next())
            view = ZenPlayerView(win, self._player, self._cfg, theme, False, self)
            view.pack(fill="both", expand=True)
            self._win, self._view = win, view
        self._visible = True

    def _space_toggle(self, e: tk.Event) -> None:
        if isinstance(e.widget, (tk.Entry, tk.Text)):
            return
        self._player.toggle_pause()
