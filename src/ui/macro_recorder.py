"""Codis - Macro Recorder.

Grava e reproduz sequências de teclas e ações do editor,
com gerenciamento de múltiplas macros salvas.

Features
--------
- Gravar / Parar / Reproduzir via toolbar flutuante
- Suporte a texto digitado, atalhos, navegação de cursor
- Múltiplas macros nomeadas com persistência em JSON
- Reprodução com delay configurável (para visualizar)
- Importar / exportar macros como JSON
- Atalho padrão: Ctrl+Shift+M (start/stop), Ctrl+Alt+M (play)
"""

from __future__ import annotations

import contextlib
import json
import logging
import threading
import time
import tkinter as tk
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog
from typing import Any

from src.constants import CONFIG_DIR, FONT_MONO, FONT_UI
from src.i18n import _t

logger = logging.getLogger("Codis.Macro")

_MACROS_FILE = CONFIG_DIR / "macros.json"

_EV_INSERT = "insert"
_EV_DELETE = "delete"
_EV_KEY = "key"
_EV_CURSOR = "cursor"
_EV_WAIT = "wait"


@dataclass
class MacroEvent:
    type: str
    data: str = ""
    index: str = ""
    delay: int = 0

    def to_dict(self) -> dict:
        """Serializa o evento de macro em dicionário."""
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> MacroEvent:
        """Desserializa um evento de macro a partir de dicionário."""
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})  # type: ignore[attr-defined]  # pylint: disable=no-member


@dataclass
class Macro:
    name: str
    events: list[MacroEvent] = field(default_factory=list)
    created: str = ""

    def to_dict(self) -> dict:
        """Serializa a macro completa em dicionário."""
        return {
            "name": self.name,
            "created": self.created,
            "events": [e.to_dict() for e in self.events],
        }

    @classmethod
    def from_dict(cls, d: dict) -> Macro:
        """Desserializa uma macro a partir de dicionário."""
        events = [MacroEvent.from_dict(e) for e in d.get("events", [])]
        return cls(
            name=d.get("name", "Macro"), events=events, created=d.get("created", "")
        )


class MacroStore:
    """Persiste macros em macros.json."""

    def __init__(self) -> None:
        """Inicializa a store de macros com persistência em JSON."""
        self._macros: dict[str, Macro] = {}
        self._load()

    def _load(self) -> None:
        if not _MACROS_FILE.exists():
            return
        try:
            raw = json.loads(_MACROS_FILE.read_text(encoding="utf-8"))
            for m in raw.get("macros", []):
                mac = Macro.from_dict(m)
                self._macros[mac.name] = mac
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("macros.json corrompido: %s", exc)

    def save(self) -> None:
        """Persiste as macros no arquivo de configuração."""
        try:
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            data = {"macros": [m.to_dict() for m in self._macros.values()]}
            _MACROS_FILE.write_text(
                json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
            )
        except OSError as exc:
            logger.warning("Falha ao salvar macros.json: %s", exc)

    def add(self, macro: Macro) -> None:
        """Adiciona ou substitui uma macro na store."""
        self._macros[macro.name] = macro
        self.save()

    def remove(self, name: str) -> None:
        """Remove uma macro da store pelo nome."""
        self._macros.pop(name, None)
        self.save()

    def get(self, name: str) -> Macro | None:
        """Retorna a macro com o nome fornecido, ou ``None``."""
        return self._macros.get(name)

    def names(self) -> list[str]:
        """Retorna a lista de nomes de macros disponíveis."""
        return list(self._macros.keys())

    @property
    def macros(self) -> dict[str, Macro]:
        """Retorna a lista de todas as macros armazenadas."""
        return self._macros


class MacroRecorder:
    """Componente de gravação e reprodução de macros.

    Conecte-o ao widget Text ativo via `attach(text_widget)`.
    """

    def __init__(self, store: MacroStore) -> None:
        """Inicializa o gravador de macros."""
        self._store = store
        self._recording = False
        self._playing = False
        self._events: list[MacroEvent] = []
        self._text: tk.Text | None = None
        self._last_ts: float = 0.0
        self._bindings: list[tuple[tk.Widget, str, str]] = []
        self._on_state_change: Callable[[], None] | None = None

    def attach(self, text_widget: tk.Text) -> None:
        """Conecta o gravador a um widget Text. Chame ao trocar de aba."""
        self._detach()
        self._text = text_widget
        if self._recording:
            self._bind_text(text_widget)

    def _detach(self) -> None:
        for widget, seq, fid in self._bindings:
            with contextlib.suppress(tk.TclError):
                widget.unbind(seq, fid)
        self._bindings.clear()
        self._text = None

    def start_recording(self) -> None:
        """Inicia a gravação de uma nova macro com o nome fornecido."""
        if self._recording:
            return
        self._recording = True
        self._events = []
        self._last_ts = time.monotonic()
        if self._text:
            self._bind_text(self._text)
        self._notify()
        logger.info("Macro: gravação iniciada")

    def stop_recording(self, name: str | None = None) -> Macro | None:
        """Encerra a gravação e salva a macro na store."""
        if not self._recording:
            return None
        self._recording = False
        self._detach()
        self._notify()

        if not self._events:
            return None

        import datetime

        macro = Macro(
            name=name or f"Macro {len(self._store.names()) + 1}",
            events=self._events[:],
            created=datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
        )
        self._store.add(macro)
        logger.info("Macro: %d eventos gravados → '%s'",
                    len(self._events), macro.name)
        return macro

    def play(
        self,
        name: str,
        speed: float = 1.0,
        on_done: Callable[[], None] | None = None,
    ) -> None:
        """Reproduz a macro em thread de background."""
        macro = self._store.get(name)
        if not macro or self._playing:
            return
        self._playing = True
        self._notify()

        def _run() -> None:
            try:
                self._replay(macro, speed)
            finally:
                self._playing = False
                self._notify()
                if on_done and self._text:
                    self._text.after(0, on_done)

        threading.Thread(target=_run, daemon=True).start()

    @property
    def is_recording(self) -> bool:
        """Retorna ``True`` se uma macro está sendo gravada."""
        return self._recording

    @property
    def is_playing(self) -> bool:
        """Retorna ``True`` se uma macro está sendo reproduzida."""
        return self._playing

    def on_state_change(self, cb: Callable[[], None]) -> None:
        """Registra callback chamado quando o estado de gravação/reprodução muda."""
        self._on_state_change = cb

    def _notify(self) -> None:
        if self._on_state_change and self._text:
            self._text.after(0, self._on_state_change)

    def _bind_text(self, w: tk.Text) -> None:
        def _bind(seq: str, cb: Callable) -> None:
            fid = w.bind(seq, cb, "+")
            self._bindings.append((w, seq, fid))

        _bind("<Key>", self._on_key)

    def _on_key(self, event: tk.Event) -> None:
        if not self._recording:
            return
        now = time.monotonic()
        delay = int((now - self._last_ts) * 1000)
        self._last_ts = now

        char = event.char
        if char and char.isprintable():
            idx = self._text.index("insert") if self._text else "insert"
            self._events.append(
                MacroEvent(_EV_INSERT, data=char, index=idx, delay=delay)
            )
            return

        sym = event.keysym
        if sym in (
            "Control_L",
            "Control_R",
            "Shift_L",
            "Shift_R",
            "Alt_L",
            "Alt_R",
            "Super_L",
            "Super_R",
        ):
            return

        parts = []
        if event.state & 0x4:
            parts.append("Control")
        if event.state & 0x1:
            parts.append("Shift")
        if event.state & 0x8:
            parts.append("Alt")
        parts.append(sym)
        key_str = "<" + "-".join(parts) + ">"

        idx = self._text.index("insert") if self._text else "insert"
        self._events.append(MacroEvent(
            _EV_KEY, data=key_str, index=idx, delay=delay))

    def _replay(self, macro: Macro, speed: float) -> None:
        if not self._text:
            return
        for ev in macro.events:
            if not self._playing:
                break
            raw_delay = ev.delay
            delay_s = (raw_delay / 1000.0) / max(speed, 0.1)
            if delay_s > 0.005:
                time.sleep(min(delay_s, 2.0))

            if ev.type == _EV_INSERT:
                self._text.after(0, lambda c=ev.data: self._do_insert(c))
            elif ev.type == _EV_KEY:
                self._text.after(0, lambda k=ev.data: self._do_key(k))

    def _do_insert(self, char: str) -> None:
        if self._text:
            with contextlib.suppress(tk.TclError):
                self._text.insert("insert", char)

    def _do_key(self, key_str: str) -> None:
        if self._text:
            with contextlib.suppress(tk.TclError):
                self._text.event_generate(key_str)


class MacroToolbar(tk.Toplevel):
    """Toolbar flutuante sempre-no-topo para controlar gravação/reprodução."""

    def __init__(
        self,
        parent: tk.Widget,
        tmgr: Any,
        recorder: MacroRecorder,
        store: MacroStore,
        get_text: Callable[[], tk.Text | None],
    ) -> None:
        """Inicializa a barra de ferramentas do gravador de macros."""
        t = tmgr.theme
        super().__init__(parent)
        self.title(_t("macro.toolbar_title"))
        self.configure(bg=t.floating_bg)
        self.resizable(False, False)
        self.attributes("-topmost", True)
        self.transient(parent)

        self._tmgr = tmgr
        self._rec = recorder
        self._store = store
        self._get_txt = get_text
        self._speed = tk.DoubleVar(value=1.0)

        self._build(t)
        recorder.on_state_change(self._update_ui)
        self._update_ui()
        self._place_near_parent(parent)

    def _build(self, t: Any) -> None:
        tk.Frame(self, bg=t.accent, height=2).pack(fill="x")

        top = tk.Frame(self, bg=t.floating_bg)
        top.pack(fill="x", padx=6, pady=4)

        tk.Label(
            top,
            text=_t("macro.title"),
            bg=t.floating_bg,
            fg=t.t_func,
            font=(FONT_UI, 9, "bold"),
        ).pack(side="left")

        tk.Label(
            self,
            text=_t("macro.saved_list"),
            bg=t.floating_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 8),
        ).pack(anchor="w", padx=6)

        list_frame = tk.Frame(self, bg=t.editor_bg)
        list_frame.pack(fill="both", expand=True, padx=6, pady=2)

        vsb = tk.Scrollbar(list_frame, orient="vertical")
        self._listbox = tk.Listbox(
            list_frame,
            bg=t.editor_bg,
            fg=t.fg,
            selectbackground=t.sel_bg,
            selectforeground=t.sel_fg,
            font=(FONT_MONO, 8),
            relief="flat",
            bd=0,
            activestyle="none",
            height=6,
            highlightthickness=0,
            yscrollcommand=vsb.set,
        )
        vsb.config(command=self._listbox.yview)
        vsb.pack(side="right", fill="y")
        self._listbox.pack(fill="both", expand=True)
        self._populate()

        sp_row = tk.Frame(self, bg=t.floating_bg)
        sp_row.pack(fill="x", padx=6, pady=2)
        tk.Label(
            sp_row,
            text=_t("macro.speed_label"),
            bg=t.floating_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 8),
        ).pack(side="left")
        tk.Scale(
            sp_row,
            from_=0.25,
            to=4.0,
            resolution=0.25,
            orient="horizontal",
            variable=self._speed,
            bg=t.floating_bg,
            fg=t.fg,
            troughcolor=t.editor_bg,
            highlightthickness=0,
            showvalue=True,
            length=120,
            font=(FONT_UI, 7),
        ).pack(side="left", padx=4)

        btn_row = tk.Frame(self, bg=t.floating_bg)
        btn_row.pack(fill="x", padx=6, pady=(4, 6))

        buttons = [
            (_t("macro.btn_record"), self._btn_record, "#c62828", "white"),
            (_t("macro.btn_stop"), self._btn_stop, t.btn_bg, t.fg),
            (_t("macro.btn_play"), self._btn_play, t.btn_primary, t.btn_primary_fg),
            (_t("macro.btn_remove"), self._btn_remove, t.btn_bg, t.fg),
        ]
        self._btns: dict[str, tk.Label] = {}
        for text, cmd, bg, fg in buttons:
            key = text.split()[0]
            btn = tk.Label(
                btn_row,
                text=text,
                bg=bg,
                fg=fg,
                font=(FONT_UI, 8),
                cursor="hand2",
                padx=6,
                pady=3,
                relief="flat",
            )
            btn.pack(side="left", padx=2)
            btn.bind("<Button-1>", lambda _, c=cmd: c())
            self._btns[key] = btn

        io_row = tk.Frame(self, bg=t.floating_bg)
        io_row.pack(fill="x", padx=6, pady=(0, 6))
        for text, cmd in (
            (_t("macro.btn_export"), self._btn_export),
            (_t("macro.btn_import"), self._btn_import),
        ):
            tk.Label(
                io_row,
                text=text,
                bg=t.btn_bg,
                fg=t.fg,
                font=(FONT_UI, 7),
                cursor="hand2",
                padx=4,
                pady=2,
                relief="flat",
            ).pack(side="left", padx=2)
            io_row.winfo_children()[-1].bind("<Button-1>",
                                             lambda _, c=cmd: c())

        self._status = tk.Label(
            self,
            text="",
            bg=t.floating_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 7),
        )
        self._status.pack(anchor="w", padx=6, pady=(0, 4))

    def _populate(self) -> None:
        self._listbox.delete(0, "end")
        for name in self._store.names():
            m = self._store.get(name)
            ev_count = len(m.events) if m else 0
            self._listbox.insert(
                "end", f"  {name}  ({_t('macro.ev_count', n=ev_count)})"
            )

    def _update_ui(self) -> None:
        t = self._tmgr.theme
        rec = self._rec.is_recording
        play = self._rec.is_playing

        rec_btn = self._btns.get("⏺")
        if rec_btn:
            rec_btn.config(
                text=_t("macro.btn_stop_rec") if rec else "⏺ Gravar",
                bg="#c62828" if rec else t.btn_bg,
                fg="white" if rec else t.fg,
            )

        play_btn = self._btns.get("▶")
        if play_btn:
            play_btn.config(state="disabled" if play else "normal")

        status = ""
        if rec:
            status = _t("macro.recording")
        elif play:
            status = _t("macro.playing")
        self._status.config(text=status)

    def _btn_record(self) -> None:
        if self._rec.is_recording:
            name = simpledialog.askstring(
                _t("macro.save_title"),
                _t("macro.save_prompt"),
                parent=self,
                initialvalue=_t("macro.save_default",
                                n=len(self._store.names()) + 1),
            )
            if name is None:
                return
            macro = self._rec.stop_recording(name)
            if macro:
                self._populate()
                self._status.config(text=f"Salva: {macro.name}")
        else:
            txt = self._get_txt()
            if txt:
                self._rec.attach(txt)
            self._rec.start_recording()

    def _btn_stop(self) -> None:
        if self._rec.is_recording:
            self._btn_record()
        elif self._rec.is_playing:
            self._rec._playing = False

    def _btn_play(self) -> None:
        sel = self._listbox.curselection()
        names = self._store.names()
        if not sel or sel[0] >= len(names):
            self._status.config(text=_t("macro.no_select"))
            return
        name = names[sel[0]]
        txt = self._get_txt()
        if txt:
            self._rec.attach(txt)
        self._rec.play(
            name,
            speed=self._speed.get(),
            on_done=lambda: self._status.config(text=_t("macro.play_done")),
        )

    def _btn_remove(self) -> None:
        sel = self._listbox.curselection()
        names = self._store.names()
        if not sel or sel[0] >= len(names):
            return
        name = names[sel[0]]
        if messagebox.askyesno(
            _t("macro.remove_title"), f"Remover '{name}'?", parent=self
        ):
            self._store.remove(name)
            self._populate()

    def _btn_export(self) -> None:
        path = filedialog.asksaveasfilename(
            parent=self,
            title=_t("macro.export_title"),
            defaultextension=".json",
            filetypes=[("JSON", "*.json")],
            initialfile=_t("macro.export_file"),
        )
        if not path:
            return
        try:
            data = {"macros": [m.to_dict()
                               for m in self._store.macros.values()]}
            Path(path).write_text(
                json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
            )
            self._status.config(text=_t("macro.exported"))
        except OSError as exc:
            messagebox.showerror(_t("macro.error_title"),
                                 str(exc), parent=self)

    def _btn_import(self) -> None:
        path = filedialog.askopenfilename(
            parent=self,
            title=_t("macro.import_title"),
            filetypes=[("JSON", "*.json")],
        )
        if not path:
            return
        try:
            raw = json.loads(Path(path).read_text(encoding="utf-8"))
            count = 0
            for m in raw.get("macros", []):
                mac = Macro.from_dict(m)
                self._store.add(mac)
                count += 1
            self._populate()
            self._status.config(text=_t("macro.import_done", n=count))
        except (OSError, json.JSONDecodeError) as exc:
            messagebox.showerror(_t("macro.import_err"), str(exc), parent=self)

    def _place_near_parent(self, parent: tk.Widget) -> None:
        self.update_idletasks()
        with contextlib.suppress(tk.TclError):
            x = parent.winfo_rootx() + parent.winfo_width() - self.winfo_width() - 20
            y = parent.winfo_rooty() + 60
            self.geometry(f"+{x}+{y}")
