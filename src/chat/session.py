"""Sessão de chat: estado dos canais, comandos e ligação adaptador <-> interface.

Thread-safety: os adaptadores chamam os handlers de ``_on_*`` de threads de rede;
a interface lê o estado sob ``lock``. Cada mudança chama ``notify`` (que só
marca uma flag - quem desenha é a thread principal).
"""

from __future__ import annotations

import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field

from src.chat.adapters import SERVER_TAB, Adapter, Events, make_adapter
from src.chat.history import HistoryStore
from src.chat.models import CHAT_HISTORY_DIR, ChatProfile
from src.chat.util import mentions_nick

MAX_MESSAGES = 3000
TRIM_TO = 2500
_PFX = "~&@%+!"
_PFX_RANK = {c: i for i, c in enumerate(_PFX)}

HELP_LINES = [
    "Comandos do Codis Chat:",
    "/join canal [chave] · /part [canal] · /close  fecha a aba",
    "/query nick [msg]  abre conversa privada · /msg nick texto",
    "/me ação · /nick novo · /topic [texto] · /away [motivo]",
    "/whois nick · /who · /names · /list · /mode ... · /kick nick [motivo]",
    "/ignore nick · /unignore nick · /clear · /clearhistory",
    "/reconnect · /disconnect · //texto  envia '/texto' literalmente",
]


@dataclass
class Msg:
    ts: float
    nick: str
    text: str
    kind: str = "msg"
    mention: bool = False
    history: bool = False


@dataclass
class Channel:
    name: str
    kind: str = "channel"
    nicks: dict[str, str] = field(default_factory=dict)
    topic: str = ""
    unread: int = 0
    mentions: int = 0
    joined: bool = True
    messages: list[Msg] = field(default_factory=list)
    base: int = 0

    @property
    def total(self) -> int:
        return self.base + len(self.messages)


def split_prefix(raw: str) -> tuple[str, str]:
    i = 0
    while i < len(raw) and raw[i] in _PFX:
        i += 1
    return raw[:i], raw[i:]


class ChatSession:
    def __init__(
        self,
        profile: ChatProfile,
        notify: Callable[[], None] | None = None,
        adapter_factory: Callable[[ChatProfile, Events], Adapter] = make_adapter,
        history_root=None,
    ) -> None:
        self.profile = profile
        self.lock = threading.RLock()
        self._notify = notify or (lambda: None)
        self._factory = adapter_factory
        self.channels: dict[str, Channel] = {SERVER_TAB: Channel(SERVER_TAB, "server")}
        self.active = SERVER_TAB
        self.adapter: Adapter | None = None
        self.status = "offline"
        self.status_detail = ""
        self.retry_at = 0.0
        self.ignored: set[str] = set()
        self.version = 0
        self._wanted: set[str] = {c.casefold() for c in profile.channels}
        self._echo: deque[tuple[str, str, float]] = deque(maxlen=50)
        self._history = HistoryStore(history_root or CHAT_HISTORY_DIR, profile.name) if profile.keep_history else None
        self._events = Events(
            message=self._on_message, own=self._on_own, join=self._on_join, part=self._on_part,
            nick=self._on_nick, topic=self._on_topic, names=self._on_names, connect=self._on_connect,
            error=self._on_error, info=self._on_info, disconnect=self._on_disconnect,
            reconnecting=self._on_reconnecting,
        )

    def _bump(self) -> None:
        self.version += 1
        self._notify()

    def _is_me(self, nick: str) -> bool:
        return nick.casefold() == self.nick.casefold()

    def _ch(self, name: str) -> Channel:
        key = name
        ch = self.channels.get(key)
        if ch is None:
            for k, v in self.channels.items():
                if k.casefold() == name.casefold():
                    return v
            dm = bool(self.adapter and self.adapter.is_dm(name))
            ch = Channel(name, "dm" if dm else "channel")
            self.channels[name] = ch
            if self._history:
                for ts, nick, text, kind in self._history.load(name):
                    ch.messages.append(Msg(ts, nick, text, kind, history=True))
        return ch

    def _post(self, channel: str, nick: str, text: str, kind: str = "msg", *,
              ts: float | None = None, mention: bool = False, count: bool = True) -> None:
        with self.lock:
            ch = self._ch(channel)
            msg = Msg(ts or time.time(), nick, text, kind, mention)
            ch.messages.append(msg)
            if len(ch.messages) > MAX_MESSAGES:
                drop = len(ch.messages) - TRIM_TO
                del ch.messages[:drop]
                ch.base += drop
            if count and ch.name != self.active and kind in ("msg", "action", "notice"):
                ch.unread += 1
                if mention or ch.kind == "dm":
                    ch.mentions += 1
            if self._history and ch.kind != "server" and kind in ("msg", "own", "action", "notice"):
                self._history.append(ch.name, msg.ts, nick, text, kind)
        self._bump()

    def _on_message(self, channel: str, nick: str, text: str, kind: str = "msg", ts: float | None = None) -> None:
        if nick.casefold() in self.ignored:
            return
        ad = self.adapter
        with self.lock:
            if ad and ad.echoes_self:
                key = channel.casefold()
                now = time.time()
                for item in list(self._echo):
                    if item[0] == key and item[1] == text.strip() and now - item[2] < 15:
                        self._echo.remove(item)
                        return
            if ad and ad.learn_nicks and channel != SERVER_TAB:
                ch = self._ch(channel)
                if nick and nick not in ch.nicks:
                    ch.nicks[nick] = ""
            mention = (not self._is_me(nick)) and mentions_nick(text, self.nick)
        if self._is_me(nick) and kind == "msg":
            kind = "own"
        self._post(channel, nick, text, kind, ts=ts, mention=mention)

    def _on_own(self, channel: str, text: str, kind: str = "own") -> None:
        self._post(channel, self.nick, text, kind)

    def _on_join(self, channel: str, nick: str) -> None:
        with self.lock:
            ch = self._ch(channel)
            if self._is_me(nick):
                ch.joined = True
                self._wanted.add(channel.casefold())
                if self.active == SERVER_TAB:
                    self.active = ch.name
                    ch.unread = ch.mentions = 0
            else:
                ch.nicks.setdefault(nick, "")
        self._post(channel, nick, "entrou", "join", count=False)

    def _on_part(self, channel: str, nick: str, reason: str = "") -> None:
        with self.lock:
            targets = ([c for c in self.channels.values() if nick in c.nicks]
                       if channel == "*" else [self._ch(channel)])
            for ch in targets:
                if self._is_me(nick):
                    ch.joined = False
                    ch.nicks.clear()
                    self._wanted.discard(ch.name.casefold())
                else:
                    ch.nicks.pop(nick, None)
        for ch in targets:
            who = "Você" if self._is_me(nick) else nick
            self._post(ch.name, who, f"saiu{': ' + reason if reason else ''}", "part", count=False)

    def _on_nick(self, old: str, new: str) -> None:
        with self.lock:
            hit = [c.name for c in self.channels.values() if old in c.nicks]
            for name in hit:
                c = self.channels[name]
                c.nicks[new] = c.nicks.pop(old, "")
        for name in hit:
            self._post(name, old, f"agora é {new}", "nick", count=False)

    def _on_topic(self, channel: str, topic: str) -> None:
        with self.lock:
            self._ch(channel).topic = topic
        if topic:
            self._post(channel, "", f"Tópico: {topic}", "topic", count=False)
        else:
            self._bump()

    def _on_names(self, channel: str, nicks: list[str]) -> None:
        d: dict[str, str] = {}
        for raw in nicks:
            pfx, bare = split_prefix(raw)
            if bare:
                d[bare] = pfx
        with self.lock:
            self._ch(channel).nicks = d
        self._bump()

    def _on_connect(self, msg: str) -> None:
        with self.lock:
            self.status, self.status_detail, self.retry_at = "online", "", 0.0
            extra = [c for c in self._wanted if c not in {x.casefold() for x in self.profile.channels}]
        self._post(SERVER_TAB, "", msg or "Conectado.", "sys", count=False)
        ad = self.adapter
        for name in extra:
            if ad:
                ad.join(name)

    def _on_error(self, msg: str) -> None:
        self.status_detail = msg
        self._post(SERVER_TAB, "", msg, "err", count=False)
        with self.lock:
            act = self.channels.get(self.active)
        if act and act.name != SERVER_TAB:
            self._post(act.name, "", msg, "err", count=False)

    def _on_info(self, channel: str, text: str) -> None:
        self._post(channel, "", text, "sys", count=False)

    def _on_disconnect(self, reason: str) -> None:
        with self.lock:
            self.status = "reconnecting" if self.profile.auto_reconnect else "offline"
            self.status_detail = reason
            open_ch = [c.name for c in self.channels.values() if c.kind != "server" and c.joined]
        self._post(SERVER_TAB, "", f"Desconectado: {reason}", "err", count=False)
        for name in open_ch:
            self._post(name, "", "Conexão perdida.", "err", count=False)

    def _on_reconnecting(self, delay: float) -> None:
        with self.lock:
            self.status = "reconnecting"
            self.retry_at = time.time() + delay
        self._post(SERVER_TAB, "", f"Reconectando em {int(delay + 0.5)}s...", "sys", count=False)

    def connect(self) -> None:
        with self.lock:
            if self.adapter and (self.adapter._thread and self.adapter._thread.is_alive()):
                return
            self.status = "connecting"
            self.adapter = self._factory(self.profile, self._events)
        self._post(SERVER_TAB, "", f"Conectando a {self.profile.host or self.profile.protocol}...", "sys", count=False)
        self.adapter.connect()

    def disconnect(self) -> None:
        ad = self.adapter
        if ad:
            ad.quit()
        with self.lock:
            self.status, self.retry_at = "offline", 0.0
        self._bump()

    def reconnect(self) -> None:
        ad = self.adapter
        if ad:
            ad.quit()
            t = ad._thread
            if t:
                t.join(timeout=2)
        self.adapter = None
        self.connect()

    @property
    def connected(self) -> bool:
        return bool(self.adapter and self.adapter.connected)

    @property
    def nick(self) -> str:
        return self.adapter.current_nick if self.adapter else self.profile.nick

    def select(self, name: str) -> None:
        with self.lock:
            if name in self.channels:
                self.active = name
                c = self.channels[name]
                c.unread = c.mentions = 0
        self._bump()

    def members(self, name: str) -> list[tuple[str, str]]:
        with self.lock:
            ch = self.channels.get(name)
            items = list(ch.nicks.items()) if ch else []
        items.sort(key=lambda kv: (min((_PFX_RANK.get(c, 9) for c in kv[1]), default=9), kv[0].casefold()))
        return items

    def snapshot(self, name: str, from_abs: int = 0) -> tuple[int, list[Msg]]:
        """(índice absoluto da 1ª msg devolvida, mensagens a partir de ``from_abs``)."""
        with self.lock:
            ch = self.channels.get(name)
            if not ch:
                return 0, []
            start = max(from_abs, ch.base)
            return start, ch.messages[start - ch.base:]

    def unread_totals(self) -> tuple[int, int]:
        with self.lock:
            return (sum(c.unread for c in self.channels.values()),
                    sum(c.mentions for c in self.channels.values()))

    def close_channel(self, name: str) -> None:
        with self.lock:
            ch = self.channels.get(name)
            if not ch or ch.kind == "server":
                return
            joined_chan = ch.kind == "channel" and ch.joined
        if joined_chan and self.adapter:
            self.adapter.part(name)
        with self.lock:
            self.channels.pop(name, None)
            self._wanted.discard(name.casefold())
            if self.active == name:
                self.active = SERVER_TAB
        self._bump()

    def clear_channel(self, name: str) -> None:
        with self.lock:
            ch = self.channels.get(name)
            if ch:
                ch.base += len(ch.messages)
                ch.messages.clear()
        self._bump()

    def join_channel(self, channel: str, key: str = "") -> None:
        if self.adapter:
            self.adapter.join(channel, key)
        with self.lock:
            self._wanted.add(channel.casefold())

    def open_query(self, nick: str) -> None:
        with self.lock:
            self._ch(nick)
            self.active = nick
        self._bump()

    def _sys(self, text: str, kind: str = "sys") -> None:
        self._post(self.active, "", text, kind, count=False)

    def send(self, text: str) -> bool:
        text = text.rstrip()
        if not text:
            return False
        if text.startswith("/") and not text.startswith("//"):
            return self._command(text[1:])
        if text.startswith("//"):
            text = text[1:]
        target = self.active
        ad = self.adapter
        ch = self.channels.get(target)
        if target == SERVER_TAB or ch is None:
            self._sys("Abra um canal para conversar (ou use /join). Digite /help.")
            return False
        if not ad or not ad.connected:
            self._sys("Sem conexão: mensagem não enviada. Use /reconnect.", "err")
            return False
        if ch.kind == "channel" and not ch.joined:
            self._sys("Você não está neste canal. Use /join.", "err")
            return False
        with self.lock:
            self._echo.append((target.casefold(), text.strip(), time.time()))
        ad.send(target, text)
        self._post(target, self.nick, text, "own")
        return True

    def _command(self, line: str) -> bool:
        cmd, _, rest = line.partition(" ")
        cmd, rest = cmd.upper(), rest.strip()
        ad = self.adapter
        target = self.active
        if cmd == "HELP":
            for ln in HELP_LINES + ([f"Do protocolo: {' '.join('/' + c for c in ad.commands())}"]
                                    if ad and ad.commands() else []):
                self._sys(ln)
        elif cmd == "CLEAR":
            self.clear_channel(target)
        elif cmd == "CLEARHISTORY":
            if self._history:
                self._history.clear()
            self._sys("Histórico salvo apagado.")
        elif cmd in ("CLOSE", "WC"):
            self.close_channel(target)
        elif cmd in ("QUERY", "Q"):
            nick, _, msg = rest.partition(" ")
            if not nick:
                self._sys("Uso: /query nick [mensagem]", "err")
                return False
            self.open_query(nick)
            if msg.strip():
                return self.send(msg.strip())
        elif cmd in ("ME", "ACTION"):
            if target == SERVER_TAB or not rest:
                self._sys("Uso: /me ação (dentro de um canal).", "err")
                return False
            if not ad or not ad.connected:
                self._sys("Sem conexão.", "err")
                return False
            with self.lock:
                self._echo.append((target.casefold(), rest.strip(), time.time()))
            ad.send_action(target, rest)
            self._post(target, self.nick, rest, "action")
        elif cmd == "IGNORE":
            if rest:
                self.ignored.add(rest.casefold())
                self._sys(f"Ignorando {rest}.")
            else:
                self._sys("Ignorados: " + (", ".join(sorted(self.ignored)) or "ninguém"))
        elif cmd == "UNIGNORE":
            self.ignored.discard(rest.casefold())
            self._sys(f"{rest} não é mais ignorado.")
        elif cmd == "RECONNECT":
            threading.Thread(target=self.reconnect, daemon=True).start()
        elif cmd in ("DISCONNECT", "QUIT", "EXIT"):
            self.disconnect()
        elif ad and ad.connected and ad.handle_command(cmd, rest, target):
            pass
        elif cmd == "JOIN" and rest:
            name, _, key = rest.partition(" ")
            self.join_channel(name, key.strip())
        elif cmd == "PART":
            self.close_channel(rest.split()[0] if rest else target)
        elif ad and not ad.connected:
            self._sys("Sem conexão. Use /reconnect.", "err")
            return False
        else:
            self._sys(f"Comando desconhecido: /{cmd.lower()}  (veja /help)", "err")
            return False
        return True
