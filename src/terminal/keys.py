"""Tradução de teclas e mouse do Tk para as sequências que um terminal envia.

Funções puras, sem Tkinter: recebem o ``keysym`` e o caractere do evento mais
os modificadores já interpretados e devolvem os bytes a escrever no PTY.
"""

from __future__ import annotations

_CURSOR_KEYS = {"Up": "A", "Down": "B", "Right": "C", "Left": "D", "Home": "H", "End": "F"}
_TILDE_KEYS = {
    "Insert": 2, "Delete": 3, "Prior": 5, "Next": 6,
    "F5": 15, "F6": 17, "F7": 18, "F8": 19, "F9": 20, "F10": 21, "F11": 23, "F12": 24,
}
_SS3_KEYS = {"F1": "P", "F2": "Q", "F3": "R", "F4": "S"}
_CTRL_PUNCT = {
    "bracketleft": "\x1b", "backslash": "\x1c", "bracketright": "\x1d",
    "asciicircum": "\x1e", "underscore": "\x1f", "minus": "\x1f", "space": "\x00",
    "at": "\x00", "2": "\x00", "6": "\x1e", "slash": "\x1f",
}
_IGNORED = frozenset(
    {
        "Shift_L", "Shift_R", "Control_L", "Control_R", "Alt_L", "Alt_R", "Meta_L", "Meta_R",
        "Super_L", "Super_R", "Caps_Lock", "Num_Lock", "Scroll_Lock", "ISO_Level3_Shift",
        "Mode_switch", "Win_L", "Win_R", "Menu", "Multi_key", "Hyper_L", "Hyper_R",
    }
)


def _modifier_code(shift: bool, alt: bool, ctrl: bool) -> int:
    """Código xterm de modificador: 1 + shift + 2*alt + 4*ctrl (1 = sem modificador)."""
    return 1 + int(shift) + 2 * int(alt) + 4 * int(ctrl)


def encode_key(
    keysym: str,
    char: str,
    *,
    shift: bool = False,
    alt: bool = False,
    ctrl: bool = False,
    app_cursor: bool = False,
) -> bytes | None:
    """Converte um evento de tecla em bytes para o PTY.

    Args:
        keysym: nome da tecla do Tk (``Up``, ``F5``, ``a``...).
        char: caractere produzido pelo evento (pode ser vazio).
        shift, alt, ctrl: estado dos modificadores.
        app_cursor: modo de cursor de aplicação (DECCKM) ativo.

    Returns:
        Os bytes a enviar, ou ``None`` se a tecla não gera entrada (modificadores soltos).
    """
    if keysym in _IGNORED:
        return None
    mod = _modifier_code(shift, alt, ctrl)

    if keysym in _CURSOR_KEYS:
        final = _CURSOR_KEYS[keysym]
        if mod > 1:
            return f"\x1b[1;{mod}{final}".encode()
        return (f"\x1bO{final}" if app_cursor else f"\x1b[{final}").encode()
    if keysym in _TILDE_KEYS:
        code = _TILDE_KEYS[keysym]
        return (f"\x1b[{code};{mod}~" if mod > 1 else f"\x1b[{code}~").encode()
    if keysym in _SS3_KEYS:
        final = _SS3_KEYS[keysym]
        return (f"\x1b[1;{mod}{final}" if mod > 1 else f"\x1bO{final}").encode()

    if keysym in ("Return", "KP_Enter"):
        return b"\x1b\r" if alt else b"\r"
    if keysym == "BackSpace":
        data = b"\x08" if ctrl else b"\x7f"
        return b"\x1b" + data if alt else data
    if keysym in ("Tab", "ISO_Left_Tab"):
        if shift or keysym == "ISO_Left_Tab":
            return b"\x1b[Z"
        return b"\x1b\t" if alt else b"\t"
    if keysym == "Escape":
        return b"\x1b\x1b" if alt else b"\x1b"

    if ctrl:
        if len(keysym) == 1 and keysym.isalpha() and keysym.isascii():
            data = bytes([ord(keysym.lower()) - 96])
            return b"\x1b" + data if alt else data
        if keysym in _CTRL_PUNCT:
            data = _CTRL_PUNCT[keysym].encode()
            return b"\x1b" + data if alt else data
        if char and ord(char[0]) < 32:
            return char.encode()
        return None

    if char and (char >= " " and char != "\x7f"):
        data = char.encode("utf-8", errors="replace")
        return b"\x1b" + data if alt else data
    if keysym == "space":
        return b"\x1b " if alt else b" "
    return None


def encode_paste(text: str, bracketed: bool) -> bytes:
    """Texto colado como bytes, com colchetes de colagem quando o programa pediu."""
    text = text.replace("\r\n", "\r").replace("\n", "\r")
    if bracketed:
        text = text.replace("\x1b[201~", "")
        return b"\x1b[200~" + text.encode("utf-8", errors="replace") + b"\x1b[201~"
    return text.encode("utf-8", errors="replace")


def encode_mouse(
    button: int,
    col: int,
    row: int,
    *,
    pressed: bool,
    motion: bool = False,
    shift: bool = False,
    alt: bool = False,
    ctrl: bool = False,
    sgr: bool = True,
) -> bytes:
    """Relatório de mouse xterm.

    Args:
        button: 0 esquerdo, 1 meio, 2 direito, 64 rolagem para cima, 65 para baixo.
        col, row: posição da célula, contando a partir de 1.
        pressed: ``True`` ao pressionar, ``False`` ao soltar.
        motion: ``True`` para movimento com botão pressionado.
        sgr: usa a codificação SGR (1006); do contrário, a legada (X10).
    """
    code = button + (4 if shift else 0) + (8 if alt else 0) + (16 if ctrl else 0) + (32 if motion else 0)
    if sgr:
        return f"\x1b[<{code};{col};{row}{'M' if pressed else 'm'}".encode()
    if not pressed:
        code = 3 + (4 if shift else 0) + (8 if alt else 0) + (16 if ctrl else 0)
    if col > 223 or row > 223:
        return b""
    return b"\x1b[M" + bytes([32 + code, 32 + col, 32 + row])
