"""Modelos e tabelas de protocolos do Chat."""

from __future__ import annotations

import base64
from dataclasses import dataclass, field
from enum import StrEnum

from src.constants import CONFIG_DIR

CHAT_PROFILES_FILE = CONFIG_DIR / "chat_profiles.json"
CHAT_HISTORY_DIR = CONFIG_DIR / "chat_history"


class ChatProtocol(StrEnum):
    IRC = "irc"
    IRCS = "ircs"
    XMPP = "xmpp"
    XMPPS = "xmpps"
    MATRIX = "matrix"
    SLACK = "slack"
    DISCORD = "discord"
    TELEGRAM = "telegram"
    MATTERMOST = "mattermost"
    MQTT = "mqtt"
    WS = "ws"
    WSS = "wss"
    CODIS = "codis"

    @classmethod
    def _missing_(cls, value: object) -> ChatProtocol:
        return cls.IRC


PROTO_DEFAULTS: dict[str, int] = {
    "irc": 6667, "ircs": 6697, "xmpp": 5222, "xmpps": 5223, "matrix": 443,
    "slack": 443, "discord": 443, "telegram": 443, "mattermost": 443,
    "mqtt": 1883, "ws": 80, "wss": 443, "codis": 9743,
}

PROTO_LABELS: dict[str, str] = {
    "irc": "IRC", "ircs": "IRC + TLS", "xmpp": "XMPP / Jabber", "xmpps": "XMPP + TLS (direto)",
    "matrix": "Matrix (Element)", "slack": "Slack", "discord": "Discord",
    "telegram": "Telegram Bot", "mattermost": "Mattermost", "mqtt": "MQTT",
    "ws": "WebSocket", "wss": "WebSocket + TLS", "codis": "Codis Collab",
}

PROTO_BADGE: dict[str, tuple[str, str]] = {
    "irc": ("IRC", "#6C8EBF"), "ircs": ("IRC", "#6C8EBF"),
    "xmpp": ("XMP", "#D9822B"), "xmpps": ("XMP", "#D9822B"),
    "matrix": ("MTX", "#0DBD8B"), "slack": ("SLK", "#9B59B6"),
    "discord": ("DSC", "#5865F2"), "telegram": ("TG", "#2AABEE"),
    "mattermost": ("MM", "#1E6FD9"), "mqtt": ("MQ", "#B5651D"),
    "ws": ("WS", "#7F8C8D"), "wss": ("WS", "#7F8C8D"), "codis": ("CDS", "#E0568A"),
}


@dataclass(frozen=True)
class FieldSpec:
    key: str
    label_key: str
    default: str = ""
    secret: bool = False
    tip: str = ""
    kind: str = "text"
    choices: tuple[str, ...] = ()
    extra: bool = False


def _f(key, label_key, default="", secret=False, tip="", kind="text", choices=(), extra=False):
    return FieldSpec(key, label_key, default, secret, tip, kind, tuple(choices), extra)


_HOST = lambda d="", tip="": _f("host", "chat.f_host", d, tip=tip)  # noqa: E731
_PORT = lambda d: _f("port", "chat.f_port", d)  # noqa: E731
_NICK = lambda: _f("nick", "chat.f_nick", "CodisUser")  # noqa: E731
_PASS = lambda tip="": _f("password", "chat.f_password", "", True, tip)  # noqa: E731
_CHAN = lambda d="", tip="": _f("channels", "chat.f_channels", d, tip=tip)  # noqa: E731
_TLS = lambda d="false", tip="": _f("tls", "chat.f_tls", d, tip=tip, kind="bool")  # noqa: E731

_IRC = lambda port, tls: [  # noqa: E731
    _HOST("irc.libera.chat"), _PORT(port), _NICK(),
    _f("user", "chat.f_user", "codis"), _f("realname", "chat.f_realname", "Codis Editor"),
    _PASS("Senha NickServ / SASL"), _CHAN("#codis", "separe por vírgula"),
    *([_TLS()] if tls else []), _f("sasl", "chat.f_sasl", "false", tip="SASL PLAIN", kind="bool"),
]
_XMPP = lambda port, tls: [  # noqa: E731
    _HOST("", "vazio = domínio do JID"), _PORT(port),
    _f("jid", "chat.f_jid", "", tip="usuario@dominio"), _PASS(),
    _CHAN("", "sala@conference.dominio"),
    *([_TLS("true", "STARTTLS obrigatório")] if tls else []),
    _f("muc_nick", "chat.f_muc_nick", "", tip="apelido nas salas (opcional)", extra=True),
]

PROTO_FIELDS: dict[str, list[FieldSpec]] = {
    "irc": _IRC("6667", True), "ircs": _IRC("6697", False),
    "xmpp": _XMPP("5222", True), "xmpps": _XMPP("5223", False),
    "matrix": [
        _HOST("matrix.org", "homeserver"), _PORT("443"),
        _f("jid", "chat.f_mat_user", "", tip="@user:server ou user"),
        _PASS("ou use o access_token"), _f("token", "chat.f_token", "", True, "access_token (opcional)"),
        _CHAN("", "#alias:server ou !roomid"),
    ],
    "slack": [
        _f("token", "chat.f_token", "", True, "Bot Token xoxb-..."),
        _f("app_token", "chat.f_app_token", "", True, "xapp-... (Socket Mode, recomendado)", extra=True),
        _CHAN("general", "nomes ou IDs"),
    ],
    "discord": [
        _f("token", "chat.f_token", "", True, "Token do Bot"),
        _f("token_kind", "chat.f_token_kind", "bot", kind="choice", choices=("bot", "user"),
           tip="'user' (self-bot) viola os ToS do Discord", extra=True),
        _CHAN("", "IDs/nomes; vazio = todos"),
    ],
    "telegram": [
        _f("token", "chat.f_token", "", True, "Token do @BotFather"),
        _CHAN("", "@canal ou chat_id; vazio = todos"),
    ],
    "mattermost": [
        _HOST("", "servidor.exemplo.com"), _PORT("443"),
        _f("jid", "chat.f_mm_user", "", tip="login/e-mail"), _PASS(),
        _f("token", "chat.f_token", "", True, "Personal Access Token"),
        _f("team", "chat.f_team", "", tip="vazio = primeira equipe", extra=True),
        _CHAN("town-square"), _TLS("true"),
    ],
    "mqtt": [
        _HOST("localhost", "broker MQTT"), _PORT("1883"), _NICK(),
        _f("jid", "chat.f_mqtt_user", "", tip="usuário (opcional)"), _PASS(),
        _CHAN("chat/geral", "tópicos (aceita + e #)"), _TLS(),
        _f("mqtt_format", "chat.f_mqtt_format", "plain", kind="choice", choices=("plain", "json"), extra=True),
    ],
    "ws": [
        _HOST("localhost"), _PORT("80"), _f("room_id", "chat.f_room_id", "", tip="caminho/sala"),
        _PASS(), _NICK(),
        _f("ws_format", "chat.f_ws_format", "json", kind="choice", choices=("json", "texto"), extra=True),
    ],
    "wss": [
        _HOST("localhost"), _PORT("443"), _f("room_id", "chat.f_room_id", "", tip="caminho/sala"),
        _PASS(), _NICK(),
        _f("ws_format", "chat.f_ws_format", "json", kind="choice", choices=("json", "texto"), extra=True),
    ],
    "codis": [
        _HOST("localhost"), _PORT("9743"), _f("room_id", "chat.f_room_id", "", tip="ID da sala"),
        _PASS("senha da sala"), _NICK(), _TLS("false", "wss://"),
    ],
}

PROTO_GROUPS: dict[str, list[str]] = {
    "IRC": ["irc", "ircs"], "XMPP / Jabber": ["xmpp", "xmpps"], "Matrix": ["matrix"],
    "Mensageiros": ["slack", "discord", "telegram", "mattermost"],
    "IoT / Web": ["mqtt", "ws", "wss"], "Codis": ["codis"],
}


@dataclass
class ChatProfile:
    name: str
    protocol: str = "irc"
    host: str = ""
    port: int = 6667
    nick: str = "CodisUser"
    user: str = "codis"
    realname: str = "Codis Editor"
    jid: str = ""
    password: str = ""
    token: str = ""
    channels: list[str] = field(default_factory=list)
    tls: bool = False
    verify_tls: bool = True
    sasl: bool = False
    encoding: str = "utf-8"
    room_id: str = ""
    extra: dict[str, str] = field(default_factory=dict)
    auto_reconnect: bool = True
    auto_connect: bool = False
    keep_history: bool = True

    @property
    def proto(self) -> ChatProtocol:
        return ChatProtocol(self.protocol)

    @staticmethod
    def _decode(raw: str) -> str:
        if not raw:
            return ""
        try:
            return base64.b64decode(raw.encode(), validate=True).decode()
        except Exception:
            return raw

    def decoded_password(self) -> str:
        return self._decode(self.password)

    def decoded_token(self) -> str:
        return self._decode(self.token)

    def x(self, key: str, default: str = "") -> str:
        """Lê um parâmetro específico do protocolo (``extra``); tokens ficam em base64."""
        val = (self.extra or {}).get(key, default)
        return self._decode(val) if key in SECRET_EXTRA else val

    @staticmethod
    def encode(raw: str) -> str:
        return base64.b64encode(raw.encode()).decode() if raw else ""


SECRET_EXTRA = {"app_token"}
