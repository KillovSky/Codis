"""Codis - incorporação (reparentamento) de janelas nativas de outros processos.

Este módulo permite hospedar, dentro de um ``tk.Frame``, a janela *nativa* de
um aplicativo externo (ex.: um motor de DOOM), como se ela fosse um widget do
editor:

* **Windows** - a janela top-level do processo filho é localizada por PID
  (``EnumWindows``), convertida em ``WS_CHILD`` (sem moldura/legenda) e
  reparentada com ``SetParent`` ao HWND do frame hospedeiro.
* **Linux/BSD (X11)** - a janela do cliente é localizada por ``_NET_WM_PID`` e
  reparentada com ``XReparentWindow`` ao XID do frame hospedeiro.
* **Outras plataformas / Wayland puro** - :func:`get_backend` retorna ``None``
  e o chamador deve cair para uma janela externa.

Arquitetura::

    EmbeddedAppPanel (tk.Frame)  ── usa ──▶  WindowBackend (Protocol)
        │  ciclo de vida do processo            ├─ Win32Backend  ──▶ Win32Calls (ctypes)
        │  polling não-bloqueante (after)        └─ X11Backend    ──▶ libX11   (ctypes)
        └─ barra de ferramentas + console de boot

Toda a interação com Tk acontece na thread principal (nenhuma thread auxiliar
chama ``after``); o acompanhamento do processo é feito por *polling* via
``after``.
"""

from __future__ import annotations

import contextlib
import ctypes
import ctypes.util
import logging
import os
import subprocess
import sys
import time
import tkinter as tk
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Any, Protocol

logger = logging.getLogger("Codis.Embed")

__all__ = [
    "WindowInfo",
    "EmbedError",
    "EmbedLabels",
    "EmbedTheme",
    "EmbeddedAppPanel",
    "WindowBackend",
    "Win32Backend",
    "X11Backend",
    "get_backend",
    "EXIT_HOTKEY_TEXT",
]

EXIT_HOTKEY_TEXT = "Ctrl+Shift+F12"

_MIN_WINDOW_W = 200
_MIN_WINDOW_H = 150

_TICK_MS = 80


class EmbedError(RuntimeError):
    """Falha ao localizar, reparentar ou controlar a janela de um processo."""


@dataclass(frozen=True)
class WindowInfo:
    """Janela top-level de outro programa, candidata a ser incorporada."""

    handle: int
    pid: int
    title: str
    exe: str
    size: tuple[int, int] = (0, 0)


class WindowBackend(Protocol):
    """Operações de janela nativa necessárias ao :class:`EmbeddedAppPanel`.

    ``handle`` é um HWND (Windows) ou XID (X11); ``host`` é o identificador
    nativo do widget Tk que receberá a janela (``widget.winfo_id()``).
    """

    name: str

    def find_window(self, pid: int) -> int | None:
        """Retorna a janela principal do processo ``pid`` (ou ``None``)."""

    def find_window_in(self, pids: Sequence[int]) -> int | None:
        """Como :meth:`find_window`, mas aceita vários PIDs (árvore de processos)."""

    def list_windows(self) -> list[WindowInfo]:
        """Lista janelas top-level visíveis e incorporáveis (para o seletor)."""

    def is_alive(self, handle: int) -> bool:
        """Indica se a janela ainda existe."""

    def is_embedded(self, handle: int, host: int) -> bool:
        """Indica se a janela ainda é filha de ``host``."""

    def embed(self, handle: int, host: int, width: int, height: int) -> None:
        """Reparenta a janela em ``host`` com o tamanho dado."""

    def resize(self, handle: int, width: int, height: int) -> None:
        """Redimensiona a janela embutida."""

    def size_of(self, handle: int) -> tuple[int, int] | None:
        """Tamanho atual (largura, altura) da janela embutida."""

    def focus(self, handle: int) -> None:
        """Dá o foco de teclado à janela embutida."""

    def detach(self, handle: int, *, restore: bool = False) -> None:
        """Devolve a janela ao desktop (desfaz o reparentamento).

        ``restore=True`` (programas adotados, que continuam rodando) mantém a
        janela visível e na posição/tamanho originais; sem isso ela é escondida.
        """

    def request_close(self, handle: int) -> None:
        """Pede educadamente que o aplicativo feche (WM_CLOSE)."""

    def release_pointer(self) -> None:
        """Libera qualquer captura/confinamento do ponteiro do mouse."""

    def exit_hotkey_down(self, pids: Sequence[int]) -> bool:
        """Indica se o atalho global de saída está pressionado."""

    def close(self) -> None:
        """Libera recursos do backend."""


_GWL_STYLE = -16
_GWL_EXSTYLE = -20
_WS_POPUP = 0x80000000
_WS_CHILD = 0x40000000
_WS_MINIMIZE = 0x20000000
_WS_VISIBLE = 0x10000000
_WS_CLIPSIBLINGS = 0x04000000
_WS_CLIPCHILDREN = 0x02000000
_WS_MAXIMIZE = 0x01000000
_WS_BORDER = 0x00800000
_WS_DLGFRAME = 0x00400000
_WS_SYSMENU = 0x00080000
_WS_THICKFRAME = 0x00040000
_WS_MINIMIZEBOX = 0x00020000
_WS_MAXIMIZEBOX = 0x00010000

_WS_EX_DLGMODALFRAME = 0x00000001
_WS_EX_TOPMOST = 0x00000008
_WS_EX_TOOLWINDOW = 0x00000080
_WS_EX_WINDOWEDGE = 0x00000100
_WS_EX_CLIENTEDGE = 0x00000200
_WS_EX_STATICEDGE = 0x00020000
_WS_EX_APPWINDOW = 0x00040000

_STRIP_STYLE = (
    _WS_POPUP
    | _WS_MINIMIZE
    | _WS_MAXIMIZE
    | _WS_BORDER
    | _WS_DLGFRAME
    | _WS_SYSMENU
    | _WS_THICKFRAME
    | _WS_MINIMIZEBOX
    | _WS_MAXIMIZEBOX
)
_ADD_STYLE = _WS_CHILD | _WS_VISIBLE | _WS_CLIPSIBLINGS | _WS_CLIPCHILDREN
_STRIP_EXSTYLE = (
    _WS_EX_DLGMODALFRAME
    | _WS_EX_TOPMOST
    | _WS_EX_TOOLWINDOW
    | _WS_EX_WINDOWEDGE
    | _WS_EX_CLIENTEDGE
    | _WS_EX_STATICEDGE
    | _WS_EX_APPWINDOW
)

_SWP_NOZORDER = 0x0004
_SWP_NOACTIVATE = 0x0010
_SWP_FRAMECHANGED = 0x0020
_SWP_SHOWWINDOW = 0x0040
_SWP_HIDEWINDOW = 0x0080

_VK_SHIFT = 0x10
_VK_CONTROL = 0x11
_VK_F12 = 0x7B

_IGNORED_CLASSES = frozenset(
    {
        "IME",
        "MSCTFIME UI",
        "ConsoleWindowClass",
        "CASCADIA_HOSTING_WINDOW_CLASS",
        "GDI+ Hook Window Class",
        "tooltips_class32",
        "#32770_hidden",
    }
)
_IGNORED_CLASS_PREFIXES = ("SDL_Helper", "SDL_HelperWindow")


def _to_long_ptr(value: int) -> int:
    """Converte um inteiro sem sinal de 32 bits para LONG_PTR (com sinal) válido."""
    bits = ctypes.sizeof(ctypes.c_void_p) * 8
    value &= (1 << bits) - 1
    if value >= 1 << (bits - 1):
        value -= 1 << bits
    return value


class Win32Calls:
    """Camada fina sobre ``user32``/``kernel32`` com protótipos ctypes corretos.

    Definir ``argtypes``/``restype`` é essencial: sem isso o ctypes assume
    ``int`` de 32 bits como retorno e **trunca HWNDs de 64 bits**.
    Instanciar apenas no Windows.
    """

    _GW_OWNER = 4
    _WM_CLOSE = 0x0010
    _DPI_HOSTING_BEHAVIOR_MIXED = 1

    def __init__(self) -> None:
        """Carrega user32/kernel32 e declara os protótipos das funções usadas."""
        if os.name != "nt":  # pragma: no cover
            raise EmbedError("Win32Calls só está disponível no Windows")
        from ctypes import wintypes as wt

        self._wt = wt
        win_dll: Any = ctypes.WinDLL  # type: ignore[attr-defined,unused-ignore]
        u = win_dll("user32", use_last_error=True)
        k = win_dll("kernel32", use_last_error=True)
        self._u, self._k = u, k
        hwnd, lp, wp = ctypes.c_void_p, ctypes.c_ssize_t, ctypes.c_size_t

        winfunctype: Any = ctypes.WINFUNCTYPE  # type: ignore[attr-defined,unused-ignore]
        self._enum_proc = winfunctype(wt.BOOL, hwnd, ctypes.c_ssize_t)
        u.EnumWindows.argtypes = [self._enum_proc, ctypes.c_ssize_t]
        u.EnumWindows.restype = wt.BOOL
        u.IsWindow.argtypes = [hwnd]
        u.IsWindow.restype = wt.BOOL
        u.IsWindowVisible.argtypes = [hwnd]
        u.IsWindowVisible.restype = wt.BOOL
        u.GetWindow.argtypes = [hwnd, wt.UINT]
        u.GetWindow.restype = hwnd
        u.GetParent.argtypes = [hwnd]
        u.GetParent.restype = hwnd
        u.GetWindowThreadProcessId.argtypes = [hwnd, ctypes.POINTER(wt.DWORD)]
        u.GetWindowThreadProcessId.restype = wt.DWORD
        u.GetClassNameW.argtypes = [hwnd, wt.LPWSTR, ctypes.c_int]
        u.GetClassNameW.restype = ctypes.c_int
        u.GetWindowRect.argtypes = [hwnd, ctypes.POINTER(wt.RECT)]
        u.GetWindowRect.restype = wt.BOOL
        u.GetClientRect.argtypes = [hwnd, ctypes.POINTER(wt.RECT)]
        u.GetClientRect.restype = wt.BOOL
        u.GetWindowTextLengthW.argtypes = [hwnd]
        u.GetWindowTextLengthW.restype = ctypes.c_int
        u.GetWindowTextW.argtypes = [hwnd, wt.LPWSTR, ctypes.c_int]
        u.GetWindowTextW.restype = ctypes.c_int
        k.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
        k.OpenProcess.restype = ctypes.c_void_p
        k.CloseHandle.argtypes = [ctypes.c_void_p]
        k.CloseHandle.restype = wt.BOOL
        k.QueryFullProcessImageNameW.argtypes = [
            ctypes.c_void_p, wt.DWORD, wt.LPWSTR, ctypes.POINTER(wt.DWORD),
        ]
        k.QueryFullProcessImageNameW.restype = wt.BOOL
        getter = getattr(u, "GetWindowLongPtrW", None) or u.GetWindowLongW
        setter = getattr(u, "SetWindowLongPtrW", None) or u.SetWindowLongW
        getter.argtypes = [hwnd, ctypes.c_int]
        getter.restype = lp
        setter.argtypes = [hwnd, ctypes.c_int, lp]
        setter.restype = lp
        self._get_long, self._set_long = getter, setter
        u.SetParent.argtypes = [hwnd, hwnd]
        u.SetParent.restype = hwnd
        u.SetWindowPos.argtypes = [
            hwnd, hwnd, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wt.UINT,
        ]
        u.SetWindowPos.restype = wt.BOOL
        u.SetFocus.argtypes = [hwnd]
        u.SetFocus.restype = hwnd
        u.AttachThreadInput.argtypes = [wt.DWORD, wt.DWORD, wt.BOOL]
        u.AttachThreadInput.restype = wt.BOOL
        u.PostMessageW.argtypes = [hwnd, wt.UINT, wp, lp]
        u.PostMessageW.restype = wt.BOOL
        u.ClipCursor.argtypes = [ctypes.POINTER(wt.RECT)]
        u.ClipCursor.restype = wt.BOOL
        u.GetAsyncKeyState.argtypes = [ctypes.c_int]
        u.GetAsyncKeyState.restype = ctypes.c_short
        u.GetForegroundWindow.argtypes = []
        u.GetForegroundWindow.restype = hwnd
        k.GetCurrentThreadId.argtypes = []
        k.GetCurrentThreadId.restype = wt.DWORD
        k.SetLastError.argtypes = [wt.DWORD]
        k.SetLastError.restype = None

    def top_level_windows(self) -> list[int]:
        """Lista os HWNDs das janelas top-level da área de trabalho."""
        found: list[int] = []

        def _cb(hwnd: int | None, _lparam: int) -> bool:
            if hwnd:
                found.append(int(hwnd))
            return True

        cb = self._enum_proc(_cb)
        self._u.EnumWindows(cb, 0)
        return found

    def is_window(self, hwnd: int) -> bool:
        """True se ``hwnd`` ainda é uma janela válida."""
        return bool(self._u.IsWindow(hwnd))

    def is_visible(self, hwnd: int) -> bool:
        """True se a janela tem o estilo WS_VISIBLE."""
        return bool(self._u.IsWindowVisible(hwnd))

    def owner(self, hwnd: int) -> int:
        """HWND do proprietário (0 se não houver)."""
        return int(self._u.GetWindow(hwnd, self._GW_OWNER) or 0)

    def parent(self, hwnd: int) -> int:
        """HWND do pai (0 para janelas top-level)."""
        return int(self._u.GetParent(hwnd) or 0)

    def pid_of(self, hwnd: int) -> int:
        """PID do processo dono da janela."""
        pid = self._wt.DWORD(0)
        self._u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return int(pid.value)

    def class_name(self, hwnd: int) -> str:
        """Nome da classe da janela."""
        buf = ctypes.create_unicode_buffer(256)
        self._u.GetClassNameW(hwnd, buf, 256)
        return buf.value

    def rect_size(self, hwnd: int) -> tuple[int, int]:
        """(largura, altura) do retângulo externo da janela."""
        r = self._wt.RECT()
        self._u.GetWindowRect(hwnd, ctypes.byref(r))
        return int(r.right - r.left), int(r.bottom - r.top)

    def window_rect(self, hwnd: int) -> tuple[int, int, int, int]:
        """(esquerda, topo, direita, base) em coordenadas de tela."""
        r = self._wt.RECT()
        self._u.GetWindowRect(hwnd, ctypes.byref(r))
        return int(r.left), int(r.top), int(r.right), int(r.bottom)

    def window_title(self, hwnd: int) -> str:
        """Título (legenda) da janela."""
        n = int(self._u.GetWindowTextLengthW(hwnd))
        if n <= 0:
            return ""
        buf = ctypes.create_unicode_buffer(n + 1)
        self._u.GetWindowTextW(hwnd, buf, n + 1)
        return buf.value

    def process_image(self, pid: int) -> str:
        """Caminho completo do executável do processo (vazio se sem acesso)."""
        h = self._k.OpenProcess(0x1000, False, pid)
        if not h:
            return ""
        try:
            size = self._wt.DWORD(1024)
            buf = ctypes.create_unicode_buffer(1024)
            ok = self._k.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size))
            return buf.value if ok else ""
        finally:
            self._k.CloseHandle(h)

    def is_cloaked(self, hwnd: int) -> bool:
        """True para janelas UWP/virtual-desktop "escondidas" (DWM cloaked)."""
        try:
            dwm = ctypes.WinDLL("dwmapi")  # type: ignore[attr-defined]
            val = ctypes.c_int(0)
            dwm.DwmGetWindowAttribute(
                ctypes.c_void_p(hwnd), 14, ctypes.byref(val), ctypes.sizeof(val)
            )
            return bool(val.value)
        except (OSError, AttributeError):
            return False

    def get_style(self, hwnd: int) -> int:
        """Estilo (WS_*) da janela, como inteiro sem sinal de 32 bits."""
        return int(self._get_long(hwnd, _GWL_STYLE)) & 0xFFFFFFFF

    def set_style(self, hwnd: int, value: int) -> None:
        """Define o estilo (WS_*) da janela."""
        self._set_long(hwnd, _GWL_STYLE, _to_long_ptr(value))

    def get_exstyle(self, hwnd: int) -> int:
        """Estilo estendido (WS_EX_*) da janela."""
        return int(self._get_long(hwnd, _GWL_EXSTYLE)) & 0xFFFFFFFF

    def set_exstyle(self, hwnd: int, value: int) -> None:
        """Define o estilo estendido (WS_EX_*) da janela."""
        self._set_long(hwnd, _GWL_EXSTYLE, _to_long_ptr(value))

    def set_parent(self, hwnd: int, parent: int) -> None:
        """Chama ``SetParent`` zerando o último erro antes (checar via ``last_error``)."""
        self._k.SetLastError(0)
        self._u.SetParent(hwnd, parent or None)

    def last_error(self) -> int:
        """Último código de erro Win32 da thread."""
        return int(getattr(ctypes, "get_last_error", lambda: 0)())

    @staticmethod
    def format_error(code: int) -> str:
        """Mensagem legível para um código de erro Win32."""
        fmt: Any = ctypes.FormatError  # type: ignore[attr-defined,unused-ignore]
        return str(fmt(code)).strip()

    def set_pos(self, hwnd: int, x: int, y: int, w: int, h: int, flags: int) -> None:
        """``SetWindowPos`` sem alterar a ordem Z."""
        self._u.SetWindowPos(hwnd, None, x, y, w, h, flags)

    def set_focus(self, hwnd: int) -> bool:
        """Dá foco à janela; usa ``AttachThreadInput`` como último recurso."""
        if self._u.SetFocus(hwnd):
            return True
        target = self._u.GetWindowThreadProcessId(hwnd, None)
        me = self._k.GetCurrentThreadId()
        if not target or target == me:
            return False
        self._u.AttachThreadInput(me, target, True)
        try:
            return bool(self._u.SetFocus(hwnd))
        finally:
            self._u.AttachThreadInput(me, target, False)

    def post_close(self, hwnd: int) -> None:
        """Posta ``WM_CLOSE`` (fechamento gracioso)."""
        self._u.PostMessageW(hwnd, self._WM_CLOSE, 0, 0)

    def release_cursor_clip(self) -> None:
        """Remove qualquer ``ClipCursor`` ativo (jogos costumam confinar o mouse)."""
        self._u.ClipCursor(None)

    def key_down(self, vk: int) -> bool:
        """True se a tecla virtual ``vk`` está pressionada agora (global)."""
        return bool(self._u.GetAsyncKeyState(vk) & 0x8000)

    def foreground_pid(self) -> int:
        """PID do processo dono da janela em primeiro plano."""
        fg = self._u.GetForegroundWindow()
        return self.pid_of(int(fg)) if fg else 0

    def enable_mixed_dpi_hosting(self) -> None:
        """Permite hospedar janelas de outro DPI-awareness (Windows 10 1803+)."""
        fn = getattr(self._u, "SetThreadDpiHostingBehavior", None)
        if fn is None:
            return
        fn.argtypes = [ctypes.c_int]
        fn.restype = ctypes.c_int
        fn(self._DPI_HOSTING_BEHAVIOR_MIXED)


@dataclass
class _SavedWin32State:
    """Estado original de uma janela, para restaurar ao desanexar."""

    style: int
    exstyle: int
    rect: tuple[int, int, int, int] | None = None


class Win32Backend:
    """Backend de reparentamento para Windows (``SetParent`` + ``WS_CHILD``).

    Args:
        calls: Implementação de :class:`Win32Calls` (injetável em testes).
    """

    name = "win32"

    def __init__(self, calls: Any | None = None) -> None:
        """Cria o backend; ``calls`` default usa ``user32`` real."""
        self._c = calls if calls is not None else Win32Calls()
        self._saved: dict[int, _SavedWin32State] = {}

    def find_window(self, pid: int) -> int | None:
        """Escolhe a melhor janela top-level do processo ``pid``.

        Critérios: mesmo PID, sem proprietário, não ``TOOLWINDOW``, classe fora
        da lista de auxiliares e tamanho mínimo; prefere janelas visíveis e,
        entre elas, a de maior área.
        """
        c = self._c
        best: int | None = None
        best_score = (-1, -1)
        for hwnd in c.top_level_windows():
            if c.pid_of(hwnd) != pid or c.owner(hwnd):
                continue
            cls = c.class_name(hwnd)
            if cls in _IGNORED_CLASSES or cls.startswith(_IGNORED_CLASS_PREFIXES):
                continue
            if c.get_exstyle(hwnd) & _WS_EX_TOOLWINDOW:
                continue
            w, h = c.rect_size(hwnd)
            if w < _MIN_WINDOW_W or h < _MIN_WINDOW_H:
                continue
            score = (1 if c.is_visible(hwnd) else 0, w * h)
            if score > best_score:
                best, best_score = hwnd, score
        return best

    def _usable(self, hwnd: int) -> tuple[int, int] | None:
        """Tamanho da janela se ela é "principal" e incorporável; senão ``None``."""
        c = self._c
        if c.owner(hwnd):
            return None
        cls = c.class_name(hwnd)
        if cls in _IGNORED_CLASSES or cls.startswith(_IGNORED_CLASS_PREFIXES):
            return None
        if c.get_exstyle(hwnd) & _WS_EX_TOOLWINDOW:
            return None
        w, h = c.rect_size(hwnd)
        if w < _MIN_WINDOW_W or h < _MIN_WINDOW_H:
            return None
        return w, h

    def find_window_in(self, pids: Sequence[int]) -> int | None:
        """Melhor janela entre as de qualquer um dos ``pids`` (visível, maior área)."""
        wanted = set(pids)
        best: int | None = None
        best_score = (-1, -1)
        for hwnd in self._c.top_level_windows():
            if self._c.pid_of(hwnd) not in wanted:
                continue
            size = self._usable(hwnd)
            if size is None:
                continue
            score = (1 if self._c.is_visible(hwnd) else 0, size[0] * size[1])
            if score > best_score:
                best, best_score = hwnd, score
        return best

    def list_windows(self) -> list[WindowInfo]:
        """Janelas visíveis com título, para o seletor "adotar janela"."""
        c, out = self._c, []
        for hwnd in c.top_level_windows():
            if not c.is_visible(hwnd):
                continue
            size = self._usable(hwnd)
            if size is None:
                continue
            title = c.window_title(hwnd) if hasattr(c, "window_title") else ""
            if not title:
                continue
            if hasattr(c, "is_cloaked") and c.is_cloaked(hwnd):
                continue
            pid = c.pid_of(hwnd)
            image = c.process_image(pid) if hasattr(c, "process_image") else ""
            out.append(WindowInfo(hwnd, pid, title, image.replace("\\", "/").rsplit("/", 1)[-1], size))
        return out

    def is_alive(self, handle: int) -> bool:
        """Indica se o HWND ainda existe."""
        return bool(self._c.is_window(handle))

    def is_embedded(self, handle: int, host: int) -> bool:
        """Indica se ``handle`` ainda é filho direto de ``host``."""
        return self._c.parent(handle) == host

    def embed(self, handle: int, host: int, width: int, height: int) -> None:
        """Transforma ``handle`` em filho de ``host``.

        Ordem exigida pela documentação do ``SetParent``: primeiro limpar
        ``WS_POPUP``/moldura e definir ``WS_CHILD``, **depois** reparentar.
        O sucesso é verificado com ``GetParent`` (o valor de retorno do
        ``SetParent`` é ambíguo para janelas top-level).
        """
        c = self._c
        style = c.get_style(handle)
        exstyle = c.get_exstyle(handle)
        rect_fn = getattr(c, "window_rect", None)
        rect = rect_fn(handle) if rect_fn is not None else None
        self._saved.setdefault(handle, _SavedWin32State(style, exstyle, rect))

        c.set_style(handle, (style & ~_STRIP_STYLE) | _ADD_STYLE)
        c.set_exstyle(handle, exstyle & ~_STRIP_EXSTYLE)

        with contextlib.suppress(AttributeError, OSError):
            c.enable_mixed_dpi_hosting()

        c.set_parent(handle, host)
        if c.parent(handle) != host:
            code = c.last_error()
            c.set_style(handle, style)
            c.set_exstyle(handle, exstyle)
            self._saved.pop(handle, None)
            detail = c.format_error(code) if code else "sem código de erro"
            raise EmbedError(f"SetParent falhou ({detail}, erro {code})")

        c.set_pos(
            handle, 0, 0, max(1, width), max(1, height),
            _SWP_NOZORDER | _SWP_NOACTIVATE | _SWP_FRAMECHANGED | _SWP_SHOWWINDOW,
        )

    def resize(self, handle: int, width: int, height: int) -> None:
        """Ajusta a janela filha ao tamanho do hospedeiro."""
        self._c.set_pos(
            handle, 0, 0, max(1, width), max(1, height),
            _SWP_NOZORDER | _SWP_NOACTIVATE,
        )

    def size_of(self, handle: int) -> tuple[int, int] | None:
        """Tamanho atual da janela (ou ``None`` se ela já não existe)."""
        if not self._c.is_window(handle):
            return None
        return self._c.rect_size(handle)

    def focus(self, handle: int) -> None:
        """Foca a janela embutida (melhor esforço)."""
        with contextlib.suppress(OSError):
            self._c.set_focus(handle)

    def detach(self, handle: int, *, restore: bool = False) -> None:
        """Devolve a janela ao desktop e restaura os estilos originais.

        Sem ``restore`` a janela é escondida (o processo será encerrado). Com
        ``restore=True`` (programa adotado) ela volta visível ao tamanho e à
        posição de antes. Deve ser chamado antes de destruir o widget
        hospedeiro: destruir o pai derrubaria a janela de outro processo.
        """
        c = self._c
        saved = self._saved.pop(handle, None)
        if not c.is_window(handle):
            return
        if restore:
            c.set_parent(handle, 0)
            if saved is not None:
                c.set_style(handle, saved.style)
                c.set_exstyle(handle, saved.exstyle)
            l, t, r, b = saved.rect if saved and saved.rect else (100, 100, 900, 700)
            c.set_pos(
                handle, l, t, max(_MIN_WINDOW_W, r - l), max(_MIN_WINDOW_H, b - t),
                _SWP_FRAMECHANGED | _SWP_NOACTIVATE | _SWP_NOZORDER | _SWP_SHOWWINDOW,
            )
            return
        c.set_pos(handle, 0, 0, 0, 0, _SWP_HIDEWINDOW | _SWP_NOACTIVATE | _SWP_NOZORDER)
        c.set_parent(handle, 0)
        if saved is not None:
            c.set_style(handle, saved.style)
            c.set_exstyle(handle, saved.exstyle)
            c.set_pos(
                handle, 0, 0, 0, 0,
                _SWP_FRAMECHANGED | _SWP_NOACTIVATE | _SWP_NOZORDER | _SWP_HIDEWINDOW,
            )

    def request_close(self, handle: int) -> None:
        """Posta ``WM_CLOSE`` para encerramento gracioso."""
        if self._c.is_window(handle):
            self._c.post_close(handle)

    def release_pointer(self) -> None:
        """Libera ``ClipCursor`` deixado por jogos que confinam o mouse."""
        with contextlib.suppress(OSError):
            self._c.release_cursor_clip()

    def exit_hotkey_down(self, pids: Sequence[int]) -> bool:
        """Ctrl+Shift+F12, apenas se o Codis ou o processo embutido está em foco."""
        c = self._c
        if not (c.key_down(_VK_CONTROL) and c.key_down(_VK_SHIFT) and c.key_down(_VK_F12)):
            return False
        return c.foreground_pid() in set(pids)

    def close(self) -> None:
        """Sem recursos persistentes a liberar."""
        self._saved.clear()


_XA_CARDINAL = 6
_XK_SHIFT_L, _XK_SHIFT_R = 0xFFE1, 0xFFE2
_XK_CONTROL_L, _XK_CONTROL_R = 0xFFE3, 0xFFE4
_XK_F12 = 0xFFC9


class X11Backend:
    """Backend de reparentamento para X11 (``XReparentWindow``).

    Abre uma conexão própria ao display (``DISPLAY``). O handler de erros do
    Xlib é **global ao processo**; por isso ele só é substituído durante cada
    operação (e restaurado em seguida), preservando o handler do próprio Tk.
    """

    name = "x11"

    def __init__(self) -> None:
        """Abre o display e declara os protótipos Xlib usados."""
        lib_name = ctypes.util.find_library("X11")
        if not lib_name:
            raise EmbedError("libX11 não encontrada")
        x = ctypes.CDLL(lib_name)
        c_ulong, c_int, c_uint, c_void = ctypes.c_ulong, ctypes.c_int, ctypes.c_uint, ctypes.c_void_p
        x.XOpenDisplay.argtypes = [ctypes.c_char_p]
        x.XOpenDisplay.restype = c_void
        x.XCloseDisplay.argtypes = [c_void]
        x.XDefaultRootWindow.argtypes = [c_void]
        x.XDefaultRootWindow.restype = c_ulong
        x.XInternAtom.argtypes = [c_void, ctypes.c_char_p, c_int]
        x.XInternAtom.restype = c_ulong
        x.XQueryTree.argtypes = [
            c_void, c_ulong, ctypes.POINTER(c_ulong), ctypes.POINTER(c_ulong),
            ctypes.POINTER(ctypes.POINTER(c_ulong)), ctypes.POINTER(c_uint),
        ]
        x.XQueryTree.restype = c_int
        x.XFree.argtypes = [c_void]
        x.XGetWindowProperty.argtypes = [
            c_void, c_ulong, c_ulong, ctypes.c_long, ctypes.c_long, c_int, c_ulong,
            ctypes.POINTER(c_ulong), ctypes.POINTER(c_int), ctypes.POINTER(c_ulong),
            ctypes.POINTER(c_ulong), ctypes.POINTER(ctypes.POINTER(ctypes.c_ubyte)),
        ]
        x.XGetWindowProperty.restype = c_int
        x.XReparentWindow.argtypes = [c_void, c_ulong, c_ulong, c_int, c_int]
        x.XMoveResizeWindow.argtypes = [c_void, c_ulong, c_int, c_int, c_uint, c_uint]
        x.XMapWindow.argtypes = [c_void, c_ulong]
        x.XUnmapWindow.argtypes = [c_void, c_ulong]
        x.XSync.argtypes = [c_void, c_int]
        x.XFlush.argtypes = [c_void]
        x.XGetGeometry.argtypes = [
            c_void, c_ulong, ctypes.POINTER(c_ulong), ctypes.POINTER(c_int),
            ctypes.POINTER(c_int), ctypes.POINTER(c_uint), ctypes.POINTER(c_uint),
            ctypes.POINTER(c_uint), ctypes.POINTER(c_uint),
        ]
        x.XGetGeometry.restype = c_int
        x.XSetInputFocus.argtypes = [c_void, c_ulong, c_int, c_ulong]
        x.XQueryKeymap.argtypes = [c_void, ctypes.c_char * 32]
        x.XKeysymToKeycode.argtypes = [c_void, c_ulong]
        x.XKeysymToKeycode.restype = ctypes.c_ubyte
        self._err_t = ctypes.CFUNCTYPE(c_int, c_void, c_void)
        x.XSetErrorHandler.argtypes = [self._err_t]
        x.XSetErrorHandler.restype = self._err_t
        self._x = x

        self._dpy = x.XOpenDisplay(None)
        if not self._dpy:
            raise EmbedError("não foi possível abrir o display X11")
        self._root = x.XDefaultRootWindow(self._dpy)
        self._pid_atom = x.XInternAtom(self._dpy, b"_NET_WM_PID", 0)
        self._name_atom = x.XInternAtom(self._dpy, b"_NET_WM_NAME", 0)
        self._utf8_atom = x.XInternAtom(self._dpy, b"UTF8_STRING", 0)
        self._x_error = False

        def _on_error(_dpy: int, _ev: int) -> int:
            self._x_error = True
            return 0

        self._on_error = self._err_t(_on_error)

    @contextlib.contextmanager
    def _guard(self) -> Iterator[None]:
        """Instala o handler de erros só durante a operação e sincroniza o servidor."""
        self._x_error = False
        previous = self._x.XSetErrorHandler(self._on_error)
        try:
            yield
            self._x.XSync(self._dpy, 0)
        finally:
            self._x.XSetErrorHandler(previous)

    def _geometry(self, win: int) -> tuple[int, int] | None:
        if not getattr(self, '_dpy', None):
            return None
        root, x, y = ctypes.c_ulong(), ctypes.c_int(), ctypes.c_int()
        w, h, bw, depth = (ctypes.c_uint() for _ in range(4))
        with self._guard():
            ok = self._x.XGetGeometry(
                self._dpy, win, ctypes.byref(root), ctypes.byref(x), ctypes.byref(y),
                ctypes.byref(w), ctypes.byref(h), ctypes.byref(bw), ctypes.byref(depth),
            )
        if not ok or self._x_error:
            return None
        return int(w.value), int(h.value)

    def _tree(self, win: int) -> tuple[int, list[int]] | None:
        """Retorna (pai, filhos) de ``win`` ou ``None`` se a janela sumiu."""
        if not getattr(self, '_dpy', None):
            return None
        root, parent = ctypes.c_ulong(), ctypes.c_ulong()
        kids = ctypes.POINTER(ctypes.c_ulong)()
        n = ctypes.c_uint()
        with self._guard():
            ok = self._x.XQueryTree(
                self._dpy, win, ctypes.byref(root), ctypes.byref(parent),
                ctypes.byref(kids), ctypes.byref(n),
            )
        if not ok or self._x_error:
            return None
        children = [int(kids[i]) for i in range(n.value)] if kids else []
        if kids:
            self._x.XFree(ctypes.cast(kids, ctypes.c_void_p))
        return int(parent.value), children

    def _pid_of(self, win: int) -> int | None:
        if not getattr(self, '_dpy', None):
            return None
        atype, nitems, after = ctypes.c_ulong(), ctypes.c_ulong(), ctypes.c_ulong()
        fmt = ctypes.c_int()
        data = ctypes.POINTER(ctypes.c_ubyte)()
        with self._guard():
            status = self._x.XGetWindowProperty(
                self._dpy, win, self._pid_atom, 0, 1, 0, _XA_CARDINAL,
                ctypes.byref(atype), ctypes.byref(fmt), ctypes.byref(nitems),
                ctypes.byref(after), ctypes.byref(data),
            )
        pid: int | None = None
        if status == 0 and not self._x_error and data and nitems.value >= 1 and fmt.value == 32:
            pid = int(ctypes.cast(data, ctypes.POINTER(ctypes.c_ulong))[0])
        if data:
            self._x.XFree(ctypes.cast(data, ctypes.c_void_p))
        return pid

    def _title_of(self, win: int) -> str:
        """Título via ``_NET_WM_NAME`` (UTF-8); vazio se ausente."""
        if not getattr(self, '_dpy', None):
            return ''
        atype, nitems, after = ctypes.c_ulong(), ctypes.c_ulong(), ctypes.c_ulong()
        fmt = ctypes.c_int()
        data = ctypes.POINTER(ctypes.c_ubyte)()
        with self._guard():
            status = self._x.XGetWindowProperty(
                self._dpy, win, self._name_atom, 0, 256, 0, self._utf8_atom,
                ctypes.byref(atype), ctypes.byref(fmt), ctypes.byref(nitems),
                ctypes.byref(after), ctypes.byref(data),
            )
        text = ""
        if status == 0 and not self._x_error and data and fmt.value == 8:
            text = bytes(data[: nitems.value]).decode("utf-8", "replace")
        if data:
            self._x.XFree(ctypes.cast(data, ctypes.c_void_p))
        return text or self._wm_name_of(win)

    def _wm_name_of(self, win: int) -> str:
        """Fallback: ``WM_NAME`` (STRING/Latin-1), usado por apps sem ``_NET_WM_NAME``."""
        if not getattr(self, '_dpy', None):
            return ''
        atype, nitems, after = ctypes.c_ulong(), ctypes.c_ulong(), ctypes.c_ulong()
        fmt = ctypes.c_int()
        data = ctypes.POINTER(ctypes.c_ubyte)()
        with self._guard():
            status = self._x.XGetWindowProperty(
                self._dpy, win, 39, 0, 256, 0, 0,
                ctypes.byref(atype), ctypes.byref(fmt), ctypes.byref(nitems),
                ctypes.byref(after), ctypes.byref(data),
            )
        text = ""
        if status == 0 and not self._x_error and data and fmt.value == 8:
            text = bytes(data[: nitems.value]).decode("latin-1", "replace")
        if data:
            self._x.XFree(ctypes.cast(data, ctypes.c_void_p))
        return text

    @staticmethod
    def _exe_of(pid: int) -> str:
        try:
            return os.path.basename(os.readlink(f"/proc/{pid}/exe"))
        except OSError:
            try:
                with open(f"/proc/{pid}/comm", encoding="utf-8") as fh:
                    return fh.read().strip()
            except OSError:
                return ""

    def find_window_in(self, pids: Sequence[int]) -> int | None:
        """Maior janela (até 3 níveis) pertencente a qualquer um dos ``pids``."""
        wanted = set(pids)
        best, best_area = None, 0
        for win in self._walk(self._root, 3):
            if self._pid_of(win) not in wanted:
                continue
            geo = self._geometry(win)
            if geo is None or geo[0] < _MIN_WINDOW_W or geo[1] < _MIN_WINDOW_H:
                continue
            if geo[0] * geo[1] > best_area:
                best, best_area = win, geo[0] * geo[1]
        return best

    def list_windows(self) -> list[WindowInfo]:
        """Janelas de clientes X11 com ``_NET_WM_PID`` e título."""
        out: list[WindowInfo] = []
        for win in self._walk(self._root, 3):
            pid = self._pid_of(win)
            if not pid:
                continue
            geo = self._geometry(win)
            if geo is None or geo[0] < _MIN_WINDOW_W or geo[1] < _MIN_WINDOW_H:
                continue
            title = self._title_of(win)
            if title:
                out.append(WindowInfo(win, pid, title, self._exe_of(pid), geo))
        return out

    def _walk(self, win: int, depth: int) -> Iterator[int]:
        info = self._tree(win)
        if info is None:
            return
        for child in info[1]:
            yield child
            if depth > 1:
                yield from self._walk(child, depth - 1)

    def find_window(self, pid: int) -> int | None:
        """Procura (até 3 níveis, por causa de molduras do WM) a maior janela do PID."""
        best, best_area = None, 0
        for win in self._walk(self._root, 3):
            if self._pid_of(win) != pid:
                continue
            geo = self._geometry(win)
            if geo is None or geo[0] < _MIN_WINDOW_W or geo[1] < _MIN_WINDOW_H:
                continue
            if geo[0] * geo[1] > best_area:
                best, best_area = win, geo[0] * geo[1]
        return best

    def is_alive(self, handle: int) -> bool:
        """True se a janela ainda existe no servidor X."""
        return self._geometry(handle) is not None

    def is_embedded(self, handle: int, host: int) -> bool:
        """True se o pai atual de ``handle`` é ``host``."""
        info = self._tree(handle)
        return info is not None and info[0] == host

    def embed(self, handle: int, host: int, width: int, height: int) -> None:
        """Reparenta, dimensiona e mapeia a janela dentro de ``host``."""
        with self._guard():
            self._x.XReparentWindow(self._dpy, handle, host, 0, 0)
            self._x.XMoveResizeWindow(
                self._dpy, handle, 0, 0, max(1, width), max(1, height)
            )
            self._x.XMapWindow(self._dpy, handle)
        if self._x_error:
            raise EmbedError("XReparentWindow falhou (BadWindow/BadMatch)")

    def resize(self, handle: int, width: int, height: int) -> None:
        """Redimensiona a janela embutida."""
        with self._guard():
            self._x.XMoveResizeWindow(
                self._dpy, handle, 0, 0, max(1, width), max(1, height)
            )

    def size_of(self, handle: int) -> tuple[int, int] | None:
        """Tamanho atual da janela (ou ``None`` se ela já não existe)."""
        return self._geometry(handle)

    def focus(self, handle: int) -> None:
        """Foca a janela embutida (RevertToParent, CurrentTime)."""
        with self._guard():
            self._x.XSetInputFocus(self._dpy, handle, 2, 0)

    def detach(self, handle: int, *, restore: bool = False) -> None:
        """Devolve a janela à raiz; ``restore`` a mantém visível (o WM a reenquadra)."""
        with self._guard():
            self._x.XUnmapWindow(self._dpy, handle)
            self._x.XReparentWindow(self._dpy, handle, self._root, 0, 0)
            if restore:
                self._x.XMapWindow(self._dpy, handle)

    def request_close(self, handle: int) -> None:
        """No X11 o encerramento é feito por sinal ao processo (ver o painel)."""

    def release_pointer(self) -> None:
        """O servidor X libera grabs automaticamente quando o cliente termina."""

    def exit_hotkey_down(self, pids: Sequence[int]) -> bool:
        """Ctrl+Shift+F12 via ``XQueryKeymap`` (independe do foco)."""
        keymap = (ctypes.c_char * 32)()
        self._x.XQueryKeymap(self._dpy, keymap)
        raw = bytes(keymap)

        def pressed(*keysyms: int) -> bool:
            for sym in keysyms:
                kc = int(self._x.XKeysymToKeycode(self._dpy, sym))
                if kc and raw[kc // 8] & (1 << (kc % 8)):
                    return True
            return False

        return (
            pressed(_XK_CONTROL_L, _XK_CONTROL_R)
            and pressed(_XK_SHIFT_L, _XK_SHIFT_R)
            and pressed(_XK_F12)
        )

    def close(self) -> None:
        """Fecha a conexão com o display."""
        if getattr(self, "_dpy", None):
            self._x.XCloseDisplay(self._dpy)
            self._dpy = None


def get_backend() -> WindowBackend | None:
    """Cria o backend adequado à plataforma, ou ``None`` se não houver suporte.

    ``None`` indica que o chamador deve executar o aplicativo em janela externa.
    """
    try:
        if sys.platform == "win32":
            return Win32Backend()
        if sys.platform.startswith(("linux", "freebsd", "openbsd", "netbsd")):
            if not os.environ.get("DISPLAY"):
                return None
            return X11Backend()
    except (EmbedError, OSError, AttributeError) as exc:
        logger.warning("Backend de incorporação indisponível: %s", exc)
    return None


_LIVE_PROCS: set[subprocess.Popen] = set()


def _spawn(
    argv: Sequence[str],
    cwd: str | None,
    env: dict[str, str] | None,
    log: IO[bytes] | None,
) -> subprocess.Popen:
    """Inicia o processo sem janela de console, com saída redirecionada ao log.

    Nunca usa ``PIPE`` sem consumidor: um pipe cheio travaria o motor do jogo.
    """
    kwargs: dict[str, Any] = {
        "cwd": cwd,
        "env": env,
        "stdin": subprocess.DEVNULL,
        "stdout": log if log is not None else subprocess.DEVNULL,
        "stderr": subprocess.STDOUT if log is not None else subprocess.DEVNULL,
        "close_fds": True,
    }
    if sys.platform == "win32":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
    else:
        kwargs["start_new_session"] = True
    proc = subprocess.Popen(list(argv), **kwargs)  # noqa: S603
    _LIVE_PROCS.add(proc)
    return proc


def _signal_process(proc: subprocess.Popen, *, force: bool) -> None:
    """Envia SIGTERM/SIGKILL ao grupo (POSIX) ou ``terminate``/``kill`` (Windows)."""
    try:
        if sys.platform != "win32":
            import signal

            os.killpg(os.getpgid(proc.pid), signal.SIGKILL if force else signal.SIGTERM)
        elif force:
            proc.kill()
        else:
            proc.terminate()
    except (ProcessLookupError, PermissionError, OSError):
        with contextlib.suppress(Exception):
            if force:
                proc.kill()
            else:
                proc.terminate()


@dataclass(frozen=True)
class EmbedLabels:
    """Textos (já traduzidos) exibidos pelo painel."""

    hint: str
    back: str
    focus: str
    loading: str
    embedded: str
    closed: str
    err_exited: str
    err_timeout: str
    err_launch: str
    embed_failed: str


@dataclass(frozen=True)
class EmbedTheme:
    """Cores do painel (padrão: estética DOOM)."""

    bg: str = "#000000"
    bar_bg: str = "#1a0000"
    bar_fg: str = "#ff4400"
    btn_bg: str = "#550000"
    btn_fg: str = "#ff9900"
    boot_fg: str = "#ff3300"
    font: str = "Courier New"


class EmbeddedAppPanel(tk.Frame):
    """Frame que executa um programa externo e embute sua janela nativa.

    Ciclo de vida: ``booting`` (console animado enquanto o processo abre a
    janela) -> ``embedded`` (janela reparentada e redimensionada junto com o
    frame) -> ``closed``. Em caso de erro permanece em ``failed`` mostrando a
    mensagem no console até o usuário clicar em voltar.

    Args:
        parent: Widget pai onde o painel será colocado (``place`` pelo chamador).
        argv: Linha de comando completa do processo.
        backend: Backend nativo (:func:`get_backend`).
        labels: Textos da interface.
        theme: Cores.
        cwd / env: Diretório de trabalho e ambiente do processo.
        log_path: Arquivo que recebe stdout/stderr do processo.
        boot_lines: Linhas do console animado.
        window_timeout: Segundos máximos esperando a janela aparecer.
        on_status: Callback para mensagens de status.
        on_close: Callback chamado uma vez quando o painel é encerrado.
    """

    def __init__(
        self,
        parent: tk.Widget,
        argv: Sequence[str],
        *,
        backend: WindowBackend,
        labels: EmbedLabels,
        theme: EmbedTheme | None = None,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        log_path: Path | None = None,
        boot_lines: Sequence[str] = (),
        window_timeout: float = 25.0,
        on_status: Callable[[str], None] | None = None,
        on_close: Callable[[], None] | None = None,
        adopt_handle: int | None = None,
        adopt_pids: Callable[[], Sequence[int]] | None = None,
        show_bar: bool = True,
    ) -> None:
        """Monta a UI (barra + palco) sem iniciar o processo; chame :meth:`start`.

        Modo *adotar* (``adopt_handle`` e/ou ``adopt_pids``): nada é iniciado;
        o painel incorpora uma janela de um programa **já em execução** e, ao
        fechar, apenas devolve a janela ao desktop (o programa continua vivo).
        ``adopt_pids`` é chamado a cada ciclo para reencontrar a janela pelo
        nome do executável caso o handle original desapareça.
        """
        self._theme = theme or EmbedTheme()
        super().__init__(parent, bg=self._theme.bg)
        self._argv = list(argv)
        self._backend = backend
        self._labels = labels
        self._cwd = cwd
        self._env = env
        self._log_path = log_path
        self._boot_lines = list(boot_lines)
        self._timeout = float(window_timeout)
        self._adopt_handle = adopt_handle
        self._adopt_pids = adopt_pids
        self._adopting = adopt_handle is not None or adopt_pids is not None
        self._show_bar = show_bar
        self._on_status = on_status
        self._on_close = on_close

        self.phase = "idle"
        self._proc: subprocess.Popen | None = None
        self._log: IO[bytes] | None = None
        self._hwnd: int | None = None
        self._applied_size: tuple[int, int] | None = None
        self._started_at = 0.0
        self._tick_job: str | None = None
        self._boot_job: str | None = None
        self._resize_job: str | None = None
        self._hotkey_latched = False
        self._closing = False
        self._root_widget: tk.Misc | None = None
        self._hotkey_bind: str | None = None

        self._build_ui()
        self.bind("<Destroy>", self._on_destroy, add="+")

    def _build_ui(self) -> None:
        th = self._theme
        if self._show_bar:
            bar = tk.Frame(self, bg=th.bar_bg, height=26)
            bar.pack(fill="x", side="top")
            bar.pack_propagate(False)
            tk.Label(
                bar, text=self._labels.hint, bg=th.bar_bg, fg=th.bar_fg,
                font=(th.font, 9, "bold"),
            ).pack(side="left", padx=10)
            for text, cmd in ((self._labels.back, self.close), (self._labels.focus, self.focus_game)):
                tk.Button(
                    bar, text=text, bg=th.btn_bg, fg=th.btn_fg, font=(th.font, 8, "bold"),
                    relief="flat", cursor="hand2", command=cmd,
                    activebackground=th.btn_bg, activeforeground=th.btn_fg,
                ).pack(side="right", padx=4, pady=2)

        self._stage = tk.Frame(self, bg=th.bg)
        self._stage.pack(fill="both", expand=True)
        self._host = tk.Frame(self._stage, bg=th.bg, takefocus=True, highlightthickness=0)
        self._host.place(x=0, y=0, relwidth=1, relheight=1)
        self._boot = tk.Text(
            self._stage, bg=th.bg, fg=th.boot_fg, font=(th.font, 10), relief="flat",
            bd=0, highlightthickness=0, insertbackground=th.boot_fg, cursor="arrow",
        )
        self._boot.place(x=0, y=0, relwidth=1, relheight=1)
        self._host.bind("<Configure>", self._on_host_configure, add="+")
        self._host.bind("<FocusIn>", lambda _e: self.focus_game(), add="+")

    def set_argv(self, argv: Sequence[str]) -> None:
        """Define a linha de comando (permitido apenas antes de :meth:`start`)."""
        if self.phase == "idle":
            self._argv = list(argv)

    def start(self) -> None:
        """Inicia o processo e o acompanhamento da janela."""
        if self.phase != "idle":
            return
        self.update_idletasks()
        self._host.update_idletasks()
        self._root_widget = self.winfo_toplevel()
        self._hotkey_bind = self._root_widget.bind(
            "<Control-Shift-F12>", lambda _e: self.close(), add="+"
        )
        self._notify(self._labels.loading)
        if self._adopting:
            logger.info("Adotando janela existente (handle=%s)", self._adopt_handle)
        else:
            try:
                self._open_log()
                self._proc = _spawn(self._argv, self._cwd, self._env, self._log)
            except OSError as exc:
                logger.warning("Falha ao iniciar %s: %s", self._argv[:1], exc)
                self._fail(self._labels.err_launch.format(reason=exc))
                return
            logger.info("Processo embutido iniciado: PID %s (%s)", self._proc.pid, self._argv[0])
        self.phase = "booting"
        self._started_at = time.monotonic()
        self._type_boot(0)
        self._schedule_tick()

    def focus_game(self) -> None:
        """Dá o foco de teclado à janela embutida."""
        if self.phase == "embedded" and self._hwnd is not None:
            self._backend.focus(self._hwnd)

    def close(self) -> None:
        """Encerra o jogo e remove o painel (idempotente)."""
        if self._closing:
            return
        self._closing = True
        self._shutdown(destroy=True)

    def _schedule_tick(self) -> None:
        if not self._closing:
            self._tick_job = self.after(_TICK_MS, self._tick)

    def _tick(self) -> None:
        """Um passo do acompanhamento: processo vivo? janela? tamanho? atalho?"""
        self._tick_job = None
        if self._closing:
            return
        proc = self._proc
        if proc is None and not self._adopting:
            return
        if proc is not None:
            code = proc.poll()
            if code is not None:
                self._on_process_exit(code)
                return
        elif self.phase == "embedded" and self._hwnd is not None and not self._backend.is_alive(self._hwnd):
            self.close()
            return

        if self.phase in ("booting", "embedded"):
            self._ensure_embedded()
        if self._closing or self.phase not in ("booting", "embedded"):
            return
        if self.phase == "booting" and time.monotonic() - self._started_at > self._timeout:
            self._fail(self._labels.err_timeout.format(seconds=int(self._timeout)))
            return
        if self._hotkey_pressed():
            self.close()
            return
        self._schedule_tick()

    def _hotkey_pressed(self) -> bool:
        """Detecta a *borda* do atalho global (evita fechar repetidamente)."""
        pids = [os.getpid()] + ([self._proc.pid] if self._proc else list(self._target_pids()))
        down = self._backend.exit_hotkey_down(pids)
        fired = down and not self._hotkey_latched
        self._hotkey_latched = down
        return fired

    def _target_pids(self) -> Sequence[int]:
        """PIDs cujas janelas podem ser incorporadas (processo lançado ou adotado)."""
        if self._proc is not None:
            return [self._proc.pid]
        if self._adopt_pids is not None:
            try:
                return list(self._adopt_pids())
            except Exception:  # noqa: BLE001
                return []
        return []

    def _ensure_embedded(self) -> None:
        """Localiza/reparenta a janela e mantém o tamanho sincronizado."""
        proc, host_id = self._proc, self._host.winfo_id()
        if proc is None and not self._adopting:
            return
        if self._hwnd is not None and not self._backend.is_alive(self._hwnd):
            self._hwnd = None
            self._applied_size = None
        if self._hwnd is None:
            if self._adopt_handle is not None and self._backend.is_alive(self._adopt_handle):
                found: int | None = self._adopt_handle
            elif proc is not None:
                found = self._backend.find_window(proc.pid)
            else:
                pids = self._target_pids()
                found = self._backend.find_window_in(pids) if pids else None
            if found is None:
                return
            self._hwnd = found
        if not self._backend.is_embedded(self._hwnd, host_id):
            self._embed_now(host_id)
            return
        self._sync_size(force=False)

    def _embed_now(self, host_id: int) -> None:
        hwnd = self._hwnd
        if hwnd is None:
            return
        width, height = self._host_size()
        try:
            self._backend.embed(hwnd, host_id, width, height)
        except EmbedError as exc:
            logger.warning("Incorporação falhou: %s", exc)
            self._fail(self._labels.embed_failed.format(reason=exc))
            return
        self._applied_size = (width, height)
        first = self.phase != "embedded"
        self.phase = "embedded"
        if first:
            with contextlib.suppress(tk.TclError):
                self._boot.place_forget()
            self._notify(self._labels.embedded)
        self._backend.focus(hwnd)

    def _host_size(self) -> tuple[int, int]:
        return max(1, self._host.winfo_width()), max(1, self._host.winfo_height())

    def _on_host_configure(self, _event: tk.Event) -> None:
        """Coalesce redimensionamentos do hospedeiro em uma única atualização."""
        if self._resize_job is None and self.phase == "embedded":
            self._resize_job = self.after_idle(self._apply_pending_resize)

    def _apply_pending_resize(self) -> None:
        self._resize_job = None
        if not self._closing:
            self._sync_size(force=True)

    def _sync_size(self, *, force: bool) -> None:
        if self._hwnd is None or self.phase != "embedded":
            return
        want = self._host_size()
        if force or want != self._applied_size or self._backend.size_of(self._hwnd) != want:
            self._backend.resize(self._hwnd, *want)
            self._applied_size = want

    def _on_process_exit(self, code: int) -> None:
        """Processo terminou: encerra normalmente, ou reporta falha durante o boot."""
        logger.info("Processo embutido encerrou (código %s)", code)
        if self.phase == "embedded":
            self.close()
            return
        self._fail(self._labels.err_exited.format(code=code, log=self._log_tail()))

    def _fail(self, message: str) -> None:
        """Mostra ``message`` no console e encerra o processo, mantendo o painel."""
        self.phase = "failed"
        self._cancel_jobs()
        self._terminate_process()
        with contextlib.suppress(tk.TclError):
            self._boot.place(x=0, y=0, relwidth=1, relheight=1)
            self._boot.insert("end", "\n" + message + "\n")
            self._boot.see("end")
        self._notify(message)

    def _type_boot(self, idx: int) -> None:
        """Animação de digitação do console (cosmética)."""
        self._boot_job = None
        if self._closing or self.phase != "booting" or idx >= len(self._boot_lines):
            return
        with contextlib.suppress(tk.TclError):
            self._boot.insert("end", self._boot_lines[idx])
            self._boot.see("end")
            self._boot_job = self.after(70, lambda: self._type_boot(idx + 1))

    def _open_log(self) -> None:
        if self._log_path is None:
            return
        try:
            self._log_path.parent.mkdir(parents=True, exist_ok=True)
            self._log = self._log_path.open("wb")
        except OSError as exc:
            logger.debug("Sem log do motor (%s): %s", self._log_path, exc)
            self._log = None

    def _log_tail(self, lines: int = 3) -> str:
        if self._log_path is None:
            return ""
        try:
            if self._log is not None:
                self._log.flush()
            text = self._log_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""
        return " | ".join(ln.strip() for ln in text.strip().splitlines()[-lines:])

    def _cancel_jobs(self) -> None:
        for attr in ("_tick_job", "_boot_job", "_resize_job"):
            job = getattr(self, attr)
            if job is not None:
                with contextlib.suppress(tk.TclError, ValueError):
                    self.after_cancel(job)
                setattr(self, attr, None)

    def _terminate_process(self) -> None:
        """Pede o fim do processo (gracioso -> SIGTERM -> SIGKILL) sem bloquear a UI."""
        proc, hwnd = self._proc, self._hwnd
        self._hwnd = None
        if hwnd is not None:
            with contextlib.suppress(Exception):
                self._backend.release_pointer()
                if self._adopting:
                    self._backend.detach(hwnd, restore=True)
                else:
                    self._backend.detach(hwnd)
                    self._backend.request_close(hwnd)
        if proc is None or proc.poll() is not None:
            self._release_proc(proc)
            return
        _signal_process(proc, force=False)
        deadline = time.monotonic() + 2.0
        reaper_host = self._root_widget

        def _reap() -> None:
            if proc.poll() is not None:
                self._release_proc(proc)
                return
            if time.monotonic() < deadline and reaper_host is not None:
                try:
                    reaper_host.after(150, _reap)
                    return
                except tk.TclError:
                    pass
            _signal_process(proc, force=True)
            with contextlib.suppress(Exception):
                proc.wait(timeout=0.5)
            self._release_proc(proc)

        _reap()

    def _release_proc(self, proc: subprocess.Popen | None) -> None:
        if proc is not None:
            _LIVE_PROCS.discard(proc)
        if self._log is not None:
            with contextlib.suppress(OSError):
                self._log.close()
            self._log = None

    def _shutdown(self, *, destroy: bool) -> None:
        self._cancel_jobs()
        was_embedded = self.phase == "embedded"
        self.phase = "closed"
        self._terminate_process()
        root = self._root_widget
        if root is not None and self._hotkey_bind:
            with contextlib.suppress(tk.TclError, ValueError):
                root.unbind("<Control-Shift-F12>", self._hotkey_bind)
        self._hotkey_bind = None
        with contextlib.suppress(Exception):
            self._backend.close()
        if was_embedded or destroy:
            self._notify(self._labels.closed)
        callback, self._on_close = self._on_close, None
        if destroy:
            with contextlib.suppress(tk.TclError):
                self.destroy()
        if callback is not None:
            with contextlib.suppress(Exception):
                callback()

    def _on_destroy(self, event: tk.Event) -> None:
        """Se o widget for destruído por fora (ex.: app saindo), limpa o processo."""
        if event.widget is not self or self._closing:
            return
        self._closing = True
        self._shutdown(destroy=False)

    def _notify(self, message: str) -> None:
        if self._on_status is not None:
            with contextlib.suppress(Exception):
                self._on_status(message)
