"""Renderização rica de mensagens de chat (funções puras, sem Tk)."""

from __future__ import annotations

import re
import time

from src.chat.util import URL_RE

_IRC_FMT = re.compile(r"\x03(?:\d{1,2}(?:,\d{1,2})?)?|[\x02\x0f\x11\x1d\x1e\x1f\x16]")
_CODE_BLOCK = re.compile(r"```(?:[\w+-]*\n)?(.*?)```", re.DOTALL)
_INLINE = re.compile(r"`([^`\n]+)`|\*\*([^*\n]+)\*\*")

Segment = tuple[str, tuple[str, ...]]


def blend(c1: str, c2: str, a: float) -> str:
    """Mistura ``c1`` (fundo) com ``c2`` na proporção ``a`` (0..1). Cores ``#rrggbb``."""
    try:
        r1, g1, b1 = (int(c1[i:i + 2], 16) for i in (1, 3, 5))
        r2, g2, b2 = (int(c2[i:i + 2], 16) for i in (1, 3, 5))
    except (ValueError, IndexError):
        return c1
    return "#{:02x}{:02x}{:02x}".format(*(round(x + (y - x) * a) for x, y in ((r1, r2), (g1, g2), (b1, b2))))


def day_label(ts: float, now: float | None = None) -> str:
    now = now or time.time()
    d, n = time.localtime(ts), time.localtime(now)
    key = lambda t: (t.tm_year, t.tm_yday)  # noqa: E731
    if key(d) == key(n):
        return "Hoje"
    if key(time.localtime(now - 86400)) == key(d):
        return "Ontem"
    return time.strftime("%d/%m/%Y", d)


def same_day(a: float, b: float) -> bool:
    x, y = time.localtime(a), time.localtime(b)
    return (x.tm_year, x.tm_yday) == (y.tm_year, y.tm_yday)


def safe_text(s: str) -> str:
    """Remove o que o Tk não renderiza bem (controles, não-BMP em Tcl antigo)."""
    s = _IRC_FMT.sub("", s)
    return "".join(ch if (ch >= " " or ch in "\n\t") and ord(ch) <= 0xFFFF else ("\ufffd" if ord(ch) > 0xFFFF else "")
                   for ch in s)


def _urls(text: str, tags: tuple[str, ...]) -> list[Segment]:
    out: list[Segment] = []
    pos = 0
    for m in URL_RE.finditer(text):
        if m.start() > pos:
            out.append((text[pos:m.start()], tags))
        out.append((m.group(0), (*tags, "url")))
        pos = m.end()
    if pos < len(text):
        out.append((text[pos:], tags))
    return out


def tokenize(text: str) -> list[Segment]:
    """Divide em segmentos: blocos ``` ```, `código`, **negrito** e URLs."""
    text = safe_text(text)
    segs: list[Segment] = []
    pos = 0
    for blk in _CODE_BLOCK.finditer(text):
        pre = text[pos:blk.start()]
        if pre.endswith("\n"):
            pre = pre[:-1]
        if pre:
            segs.extend(_inline(pre))
        code = blk.group(1).strip("\n")
        segs.append(("\n" + code, ("codeblock",)))
        pos = blk.end()
        if text[pos:pos + 1] == "\n":
            pos += 1
            segs.append(("\n", ()))
    if pos < len(text):
        segs.extend(_inline(text[pos:]))
    return segs or [("", ())]


def _inline(text: str) -> list[Segment]:
    segs: list[Segment] = []
    pos = 0
    for m in _INLINE.finditer(text):
        if m.start() > pos:
            segs.extend(_urls(text[pos:m.start()], ()))
        if m.group(1) is not None:
            segs.append((m.group(1), ("code",)))
        else:
            segs.extend(_urls(m.group(2), ("bold",)))
        pos = m.end()
    if pos < len(text):
        segs.extend(_urls(text[pos:], ()))
    return segs


def normalize_url(raw: str) -> str | None:
    """Só http(s) é aberto (nada de file:, javascript: etc.)."""
    u = raw if re.match(r"https?://", raw, re.IGNORECASE) else "https://" + raw
    return u if re.match(r"https?://[^\s/]+", u, re.IGNORECASE) else None


def ellipsize(s: str, n: int) -> str:
    return s if len(s) <= n else s[: max(1, n - 1)] + "\u2026"


def _lum(c: str) -> float:
    def ch(v: int) -> float:
        x = v / 255
        return x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4
    try:
        r, g, b = (int(c[i:i + 2], 16) for i in (1, 3, 5))
    except (ValueError, IndexError):
        return 0.0
    return 0.2126 * ch(r) + 0.7152 * ch(g) + 0.0722 * ch(b)


def contrast_ratio(a: str, b: str) -> float:
    la, lb = _lum(a), _lum(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def ensure_contrast(color: str, bg: str, minimum: float = 3.6) -> str:
    """Escurece/clareia ``color`` até atingir ``minimum`` de contraste sobre ``bg``."""
    if contrast_ratio(color, bg) >= minimum:
        return color
    target = "#000000" if _lum(bg) > 0.4 else "#ffffff"
    for step in range(1, 11):
        cand = blend(color, target, step / 10)
        if contrast_ratio(cand, bg) >= minimum:
            return cand
    return target
