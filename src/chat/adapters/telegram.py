"""Telegram Bot API (long-polling)."""

from __future__ import annotations

import time

from src.chat.adapters.base import Adapter, AdapterFatal
from src.chat.adapters.http import HTTPFailure, request_json
from src.chat.util import split_chars

API = "https://api.telegram.org"


class TelegramAdapter(Adapter):
    learn_nicks = True
    max_bytes = 0

    def __init__(self, *a, **k) -> None:
        super().__init__(*a, **k)
        self._offset = 0
        self._chats: dict[str, str] = {}
        self._bot = "bot"

    def _call(self, method: str, params: dict | None = None, timeout: float = 15) -> dict:
        base = self.p.x("api_base", API).rstrip("/")
        status, data = request_json("POST", f"{base}/bot{self.p.decoded_token()}/{method}",
                                    body=params or {}, timeout=timeout, verify=self.p.verify_tls)
        if not isinstance(data, dict) or not data.get("ok"):
            desc = (data or {}).get("description", f"HTTP {status}") if isinstance(data, dict) else f"HTTP {status}"
            if status in (401, 404):
                raise AdapterFatal(f"Telegram: token inválido ({desc})")
            if status == 409:
                raise AdapterFatal("Telegram: outro cliente/webhook está usando este bot (409).")
            raise HTTPFailure(status, desc)
        return data

    @staticmethod
    def _name_of(chat: dict) -> str:
        if chat.get("username"):
            return "@" + chat["username"]
        return (chat.get("title") or " ".join(x for x in (chat.get("first_name"), chat.get("last_name")) if x)
                or str(chat.get("id", "?")))

    def _register(self, chat: dict) -> str:
        name = self._name_of(chat)
        cid = str(chat["id"])
        if self._chats.get(name, cid) != cid:
            name = f"{name} ({cid})"
        self._chats[name] = cid
        return name

    def _session_once(self) -> None:
        me = self._call("getMe")
        self._bot = me["result"].get("username", "bot")
        self._chats.clear()
        self._mark_up(f"Telegram: conectado como @{self._bot}")
        for ch in self.p.channels:
            self.join(ch)
        while not self._stop.is_set():
            try:
                data = self._call("getUpdates", {"offset": self._offset, "timeout": 25,
                                                 "allowed_updates": ["message", "channel_post"]}, timeout=40)
            except HTTPFailure as exc:
                if exc.status == 429:
                    time.sleep(2)
                    continue
                raise
            for upd in data.get("result", []):
                self._offset = upd["update_id"] + 1
                msg = upd.get("message") or upd.get("channel_post")
                if msg:
                    self._on_message(msg)

    def _on_message(self, msg: dict) -> None:
        chat = msg.get("chat", {})
        if "id" not in chat:
            return
        frm = msg.get("from") or {}
        sender = (("@" + frm["username"]) if frm.get("username") else
                  " ".join(x for x in (frm.get("first_name"), frm.get("last_name")) if x)
                  or msg.get("author_signature") or chat.get("title") or "?")
        sender = sender.lstrip("@")
        text = msg.get("text") or msg.get("caption") or ""
        for key, label in (("photo", "[foto]"), ("document", "[arquivo]"), ("voice", "[áudio]"),
                           ("video", "[vídeo]"), ("sticker", "[figurinha]"), ("location", "[localização]")):
            if key in msg:
                extra = msg[key].get("file_name", "") if isinstance(msg[key], dict) else ""
                text = f"{label} {extra} {text}".strip()
                break
        if not text:
            return
        ch = self._register(chat)
        kind = "msg"
        ts = float(msg["date"]) if "date" in msg else None
        self.ev.message(ch, sender, text, kind, ts)

    def join(self, channel: str, key: str = "") -> None:
        cid = channel if channel.lstrip("-").isdigit() else ("@" + channel.lstrip("@"))
        try:
            chat = self._call("getChat", {"chat_id": cid})["result"]
        except HTTPFailure as exc:
            self.ev.error(f"Telegram: não achei {channel} ({exc})")
            return
        name = self._register(chat)
        self.ev.join(name, self._bot)

    def _deliver(self, target: str, text: str) -> None:
        cid = self._chats.get(target) or (target if target.lstrip("-").isdigit() else "@" + target.lstrip("@"))
        for part in split_chars(text, 4096):
            self._call("sendMessage", {"chat_id": cid, "text": part, "disable_web_page_preview": False})

    @property
    def current_nick(self) -> str:
        return self._bot

    def part(self, channel: str, reason: str = "") -> None:
        self.ev.part(channel, self._bot, "")
