"""Funções puras compartilhadas pelo chat (testáveis sem rede nem Tk)."""

from __future__ import annotations

import hashlib
import re
import ssl

_CTRL = re.compile(r"[\x00\r\n]")
URL_RE = re.compile(r"(?:https?://|www\.)[^\s<>\"'`]+[^\s<>\"'`.,;:!?)\]}]", re.IGNORECASE)

NICK_COLORS = [
    "#4EC9B0", "#CE9178", "#DCDCAA", "#9CDCFE", "#C586C0", "#4FC1FF",
    "#F48771", "#B5CEA8", "#98C379", "#E5C07B", "#56B6C2", "#ABB2BF",
]


def nick_color(nick: str) -> str:
    h = int(hashlib.md5(nick.lower().encode(), usedforsecurity=False).hexdigest(), 16)
    return NICK_COLORS[h % len(NICK_COLORS)]


def clean_line(text: str) -> str:
    """Remove CR/LF/NUL: impede injeção de comandos em protocolos orientados a linha."""
    return _CTRL.sub(" ", text)


def split_utf8(text: str, limit: int) -> list[str]:
    """Quebra ``text`` em pedaços de no máximo ``limit`` bytes UTF-8, preferindo espaços."""
    limit = max(8, limit)
    out: list[str] = []
    cur = text
    while len(cur.encode("utf-8")) > limit:
        cut = len(cur)
        while len(cur[:cut].encode("utf-8")) > limit:
            cut -= 1
        window = cur[:cut]
        sp = window.rfind(" ")
        if sp > cut // 2:
            cut = sp
        out.append(cur[:cut].rstrip())
        cur = cur[cut:].lstrip()
    if cur or not out:
        out.append(cur)
    return [c for c in out if c] or [""]


def split_chars(text: str, limit: int) -> list[str]:
    """Como :func:`split_utf8`, mas o limite é em caracteres (Discord/Telegram/Slack)."""
    out: list[str] = []
    cur = text
    while len(cur) > limit:
        cut = cur.rfind("\n", 0, limit)
        if cut < limit // 2:
            cut = cur.rfind(" ", 0, limit)
        if cut < limit // 2:
            cut = limit
        out.append(cur[:cut].rstrip("\n"))
        cur = cur[cut:].lstrip("\n ")
    out.append(cur)
    return [c for c in out if c.strip()] or [""]


def ssl_context(verify: bool = True) -> ssl.SSLContext:
    if verify:
        return ssl.create_default_context()
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def mentions_nick(text: str, nick: str) -> bool:
    """True se ``text`` cita ``nick`` como palavra inteira (não como pedaço de outra)."""
    if not nick:
        return False
    return re.search(rf"(?<![\w]){re.escape(nick)}(?![\w])", text, re.IGNORECASE) is not None


def parse_irc_line(line: str) -> tuple[dict[str, str], str, str, list[str]]:
    """Parser IRC (RFC 1459 + tags IRCv3): ``(tags, prefix, comando, params)``."""
    tags: dict[str, str] = {}
    if line.startswith("@"):
        raw, _, line = line.partition(" ")
        for item in raw[1:].split(";"):
            k, _, v = item.partition("=")
            tags[k] = v
    prefix = ""
    if line.startswith(":"):
        prefix, _, line = line[1:].partition(" ")
    line = line.lstrip(" ")
    trailing: str | None = None
    if line.startswith(":"):
        trailing, line = line[1:], ""
    elif " :" in line:
        line, _, trailing = line.partition(" :")
    parts = line.split()
    cmd = parts[0].upper() if parts else ""
    params = parts[1:]
    if trailing is not None:
        params.append(trailing)
    return tags, prefix, cmd, params


def mqtt_encode_len(n: int) -> bytes:
    out = bytearray()
    while True:
        b, n = n % 128, n // 128
        out.append(b | (0x80 if n else 0))
        if not n:
            return bytes(out)


def mqtt_decode_len(buf: bytes, pos: int) -> tuple[int, int] | None:
    """Decodifica "remaining length" em ``buf[pos:]``; None se incompleto."""
    mult, val = 1, 0
    for i in range(4):
        if pos + i >= len(buf):
            return None
        b = buf[pos + i]
        val += (b & 0x7F) * mult
        if not b & 0x80:
            return val, pos + i + 1
        mult *= 128
    raise ValueError("remaining length inválido")
