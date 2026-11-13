"""Backends de pseudo-terminal para o terminal integrado.

Cada backend expõe a mesma interface mínima (``read``, ``write``, ``resize``,
``interrupt``, ``terminate``, ``close``), de modo que a sessão do terminal não
precisa saber a plataforma:

- ``PosixPtyBackend``: ``pty.openpty`` com o PTY como terminal de controle, o
  que habilita ``Ctrl+C``, controle de jobs e ``SIGWINCH`` do jeito do kernel.
- ``ConPtyBackend``: ConPTY do Windows 10 (1809+) via ``ctypes``.
- ``WinPtyBackend``: pacote opcional ``winpty``, usado se o ConPTY falhar.
- ``PipeBackend``: pipes comuns, sem TTY. Interativo só com eco local; é o
  último recurso quando não há PTY disponível.
"""

from __future__ import annotations

import contextlib
import logging
import os
import signal
import subprocess
import sys
import threading
import time
from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence

logger = logging.getLogger("Codis.Terminal")

_NON_INTERACTIVE_FLAGS = frozenset({"-noninteractive"})
_NO_RC_FLAGS = frozenset({"--norc", "--no-rcs", "--no-config", "-noprofile"})


def interactive_command(cmd: Sequence[str], load_rc: bool = True) -> list[str]:
    """Ajusta a linha de comando de um shell para uso interativo em PTY.

    Remove ``-NonInteractive`` (que desliga prompt e edição de linha no
    PowerShell) e, se ``load_rc`` for verdadeiro, também as opções que impedem
    o shell de ler os arquivos de configuração do usuário.
    """
    blocked = _NON_INTERACTIVE_FLAGS | (_NO_RC_FLAGS if load_rc else frozenset())
    out = [cmd[0]] + [arg for arg in cmd[1:] if arg.lower() not in blocked]
    return out


def child_process_env(base: Mapping[str, str] | None = None) -> dict[str, str]:
    """Ambiente para programas externos lançados pelo Codis.

    Num executável do PyInstaller o bootloader aponta ``LD_LIBRARY_PATH`` (ou
    ``DYLD_LIBRARY_PATH``) para as bibliotecas embutidas. Um ``bash`` ou um
    servidor de linguagem lançado com isso carrega essas cópias em vez das do
    sistema e pode falhar com segfault; o valor original é restaurado.
    """
    env = dict(base if base is not None else os.environ)
    for var in ("LD_LIBRARY_PATH", "DYLD_LIBRARY_PATH", "LIBPATH"):
        original = env.pop(f"{var}_ORIG", None)
        if getattr(sys, "frozen", False) or original is not None:
            if original:
                env[var] = original
            else:
                env.pop(var, None)
    from src.pyexec import strip_bundle_env

    return strip_bundle_env(env)


def terminal_env(base: Mapping[str, str] | None = None) -> dict[str, str]:
    """Ambiente do shell: ``TERM`` e cores verdadeiras, com UTF-8 garantido."""
    env = child_process_env(base)
    env["TERM"] = "xterm-256color"
    env["COLORTERM"] = "truecolor"
    env["TERM_PROGRAM"] = "Codis"
    env.pop("LINES", None)
    env.pop("COLUMNS", None)
    if sys.platform != "win32" and not any(
        "UTF-8" in env.get(k, "").upper() or "UTF8" in env.get(k, "").upper()
        for k in ("LC_ALL", "LC_CTYPE", "LANG")
    ):
        env["LANG"] = "en_US.UTF-8" if sys.platform == "darwin" else "C.UTF-8"
    return env


class Backend(ABC):
    """Interface comum dos backends."""

    pid: int | None = None
    local_echo = False

    @abstractmethod
    def read(self, size: int = 65536) -> bytes:
        """Bloqueia até haver saída. Devolve ``b""`` no fim do fluxo."""

    @abstractmethod
    def write(self, data: bytes) -> None:
        """Escreve ``data`` na entrada do programa (pode bloquear)."""

    @abstractmethod
    def resize(self, rows: int, cols: int) -> None:
        """Informa o novo tamanho da janela ao programa."""

    @abstractmethod
    def terminate(self) -> None:
        """Encerra o programa e seus filhos, sem bloquear por muito tempo."""

    @abstractmethod
    def close(self) -> None:
        """Libera os recursos do sistema."""

    @property
    @abstractmethod
    def alive(self) -> bool:
        """``True`` enquanto o processo do shell estiver em execução."""

    def interrupt(self) -> None:
        """Envia Ctrl+C ao programa em primeiro plano."""
        with contextlib.suppress(OSError):
            self.write(b"\x03")


def _kill_tree_posix(pid: int, sig: int) -> None:
    try:
        os.killpg(os.getpgid(pid), sig)
    except (ProcessLookupError, PermissionError, OSError):
        with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
            os.kill(pid, sig)


def _session_pids(sid: int) -> list[int]:
    """PIDs de todos os processos da sessão ``sid`` (inclui jobs em outros grupos).

    Shells com controle de jobs colocam cada job em um grupo de processos
    próprio, então ``killpg`` no grupo do shell não os alcança; todos, porém,
    continuam na mesma sessão. Usa ``/proc`` quando existe e ``pgrep -s`` nos demais.
    """
    pids: list[int] = []
    try:
        entries = os.listdir("/proc")
    except OSError:
        entries = None
    if entries is not None:
        for entry in entries:
            if not entry.isdigit():
                continue
            try:
                with open(f"/proc/{entry}/stat", encoding="utf-8", errors="replace") as handle:
                    data = handle.read()
                fields = data[data.rindex(")") + 2:].split()
                if int(fields[3]) == sid:
                    pids.append(int(entry))
            except (OSError, ValueError, IndexError):
                continue
        return pids
    try:
        out = subprocess.run(
            ["pgrep", "-s", str(sid)], capture_output=True, text=True, timeout=2, check=False
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    return [int(x) for x in out.split() if x.isdigit()]


def _signal_session(sid: int, sig: int) -> None:
    _kill_tree_posix(sid, sig)
    for pid in _session_pids(sid):
        with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
            os.kill(pid, sig)


class PosixPtyBackend(Backend):
    """PTY nativo para Linux e macOS."""

    def __init__(
        self, cmd: Sequence[str], cwd: str, env: Mapping[str, str], rows: int, cols: int
    ) -> None:
        import fcntl
        import pty
        import struct
        import termios

        self._fcntl, self._struct, self._termios = fcntl, struct, termios
        master, slave = pty.openpty()
        try:
            self._set_winsize(master, rows, cols)
            attrs = termios.tcgetattr(slave)
            iutf8 = getattr(termios, "IUTF8", 0)
            if iutf8:
                attrs[0] |= iutf8
                termios.tcsetattr(slave, termios.TCSANOW, attrs)

            def _become_session_leader() -> None:
                fcntl.ioctl(0, termios.TIOCSCTTY, 0)

            self._proc = subprocess.Popen(
                list(cmd),
                stdin=slave,
                stdout=slave,
                stderr=slave,
                cwd=cwd,
                env=dict(env),
                close_fds=True,
                start_new_session=True,
                preexec_fn=_become_session_leader,
            )
        except BaseException:
            os.close(master)
            raise
        finally:
            os.close(slave)
        self._master = master
        self._closed = False
        self.pid = self._proc.pid

    def _set_winsize(self, fd: int, rows: int, cols: int) -> None:
        packed = self._struct.pack("HHHH", max(1, rows), max(1, cols), 0, 0)
        self._fcntl.ioctl(fd, self._termios.TIOCSWINSZ, packed)

    def read(self, size: int = 65536) -> bytes:
        try:
            return os.read(self._master, size)
        except OSError:
            return b""

    def write(self, data: bytes) -> None:
        view = memoryview(data)
        while view:
            written = os.write(self._master, view)
            view = view[written:]

    def resize(self, rows: int, cols: int) -> None:
        with contextlib.suppress(OSError):
            self._set_winsize(self._master, rows, cols)

    @property
    def alive(self) -> bool:
        return self._proc.poll() is None

    def interrupt(self) -> None:
        with contextlib.suppress(OSError):
            os.write(self._master, b"\x03")

    def terminate(self) -> None:
        """Encerra o shell e todos os processos da sessão: HUP, depois TERM e KILL."""
        sid = self._proc.pid
        if self._proc.poll() is not None and not _session_pids(sid):
            return
        _signal_session(sid, signal.SIGHUP)

        def _escalate() -> None:
            for sig in (signal.SIGTERM, signal.SIGKILL):
                time.sleep(0.6)
                if self._proc.poll() is not None and not _session_pids(sid):
                    return
                _signal_session(sid, sig)
            with contextlib.suppress(Exception):
                self._proc.wait(timeout=1.0)

        threading.Thread(target=_escalate, daemon=True, name="pty-terminate").start()

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            with contextlib.suppress(OSError):
                os.close(self._master)


class PipeBackend(Backend):
    """Pipes sem TTY; stderr é unido ao stdout. Requer eco local na interface."""

    local_echo = True

    def __init__(self, cmd: Sequence[str], cwd: str, env: Mapping[str, str]) -> None:
        flags = 0
        extra: dict = {}
        if sys.platform == "win32":
            flags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
            env = {**env, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
        else:
            extra["start_new_session"] = True
        self._proc = subprocess.Popen(
            list(cmd),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            cwd=cwd,
            env=dict(env),
            bufsize=0,
            creationflags=flags,
            **extra,
        )
        self.pid = self._proc.pid

    def read(self, size: int = 65536) -> bytes:
        try:
            return os.read(self._proc.stdout.fileno(), size)
        except (OSError, ValueError):
            return b""

    def write(self, data: bytes) -> None:
        stdin = self._proc.stdin
        if stdin is None or stdin.closed:
            raise OSError("stdin fechado")
        stdin.write(data)
        stdin.flush()

    def resize(self, rows: int, cols: int) -> None:
        return None

    @property
    def alive(self) -> bool:
        return self._proc.poll() is None

    def interrupt(self) -> None:
        with contextlib.suppress(OSError, ValueError):
            if sys.platform == "win32":
                self._proc.send_signal(signal.CTRL_BREAK_EVENT)
            else:
                _kill_tree_posix(self._proc.pid, signal.SIGINT)

    def terminate(self) -> None:
        if self._proc.poll() is not None:
            return
        if sys.platform == "win32":
            with contextlib.suppress(OSError, subprocess.SubprocessError):
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(self._proc.pid)],
                    capture_output=True,
                    timeout=3,
                    check=False,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
        else:
            _kill_tree_posix(self._proc.pid, signal.SIGTERM)

            def _escalate() -> None:
                try:
                    self._proc.wait(timeout=1.5)
                except subprocess.TimeoutExpired:
                    _kill_tree_posix(self._proc.pid, signal.SIGKILL)

            threading.Thread(target=_escalate, daemon=True, name="pipe-terminate").start()

    def close(self) -> None:
        for stream in (self._proc.stdin, self._proc.stdout):
            if stream is not None:
                with contextlib.suppress(OSError, ValueError):
                    stream.close()


class WinPtyBackend(Backend):
    """Usa o pacote opcional ``winpty`` (pywinpty)."""

    def __init__(self, cmd: Sequence[str], cwd: str, rows: int, cols: int) -> None:
        import winpty

        self._pty = winpty.PtyProcess.spawn(list(cmd), cwd=cwd, dimensions=(rows, cols))
        self.pid = getattr(self._pty, "pid", None)

    def read(self, size: int = 65536) -> bytes:
        try:
            data = self._pty.read(size)
        except (EOFError, OSError):
            return b""
        return data.encode("utf-8", errors="replace") if isinstance(data, str) else data

    def write(self, data: bytes) -> None:
        self._pty.write(data.decode("utf-8", errors="replace"))

    def resize(self, rows: int, cols: int) -> None:
        with contextlib.suppress(Exception):
            self._pty.setwinsize(rows, cols)

    @property
    def alive(self) -> bool:
        try:
            return bool(self._pty.isalive())
        except Exception:
            return False

    def terminate(self) -> None:
        with contextlib.suppress(Exception):
            self._pty.terminate(force=True)

    def close(self) -> None:
        with contextlib.suppress(Exception):
            self._pty.close()


def conpty_supported() -> bool:
    """``True`` se este Windows tem a API de pseudo-console (10 build 17763+)."""
    if sys.platform != "win32":
        return False
    try:
        import ctypes

        return hasattr(ctypes.WinDLL("kernel32"), "CreatePseudoConsole")
    except (OSError, AttributeError):
        return False


class ConPtyBackend(Backend):
    """Pseudo-console do Windows via ``ctypes``.

    Todas as funções da API têm ``argtypes`` e ``restype`` declarados; sem isso
    o ``ctypes`` trata ponteiros como ``int`` de 32 bits e corrompe a memória em
    Python de 64 bits.
    """

    def __init__(
        self, cmd: Sequence[str], cwd: str, env: Mapping[str, str], rows: int, cols: int
    ) -> None:
        import ctypes
        from ctypes import wintypes

        self._ct = ctypes
        self._wt = wintypes
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._k32 = k32
        self._declare(k32)
        self._hpc = wintypes.HANDLE()
        self._closed = False
        self._lock = threading.Lock()
        self._h_in_w = wintypes.HANDLE()
        self._h_out_r = wintypes.HANDLE()
        self._h_proc = wintypes.HANDLE()
        self._h_thread = wintypes.HANDLE()
        self._attr_buf = None

        in_r, in_w = wintypes.HANDLE(), wintypes.HANDLE()
        out_r, out_w = wintypes.HANDLE(), wintypes.HANDLE()
        if not k32.CreatePipe(ctypes.byref(in_r), ctypes.byref(in_w), None, 0):
            raise ctypes.WinError(ctypes.get_last_error())
        if not k32.CreatePipe(ctypes.byref(out_r), ctypes.byref(out_w), None, 0):
            k32.CloseHandle(in_r)
            k32.CloseHandle(in_w)
            raise ctypes.WinError(ctypes.get_last_error())
        self._h_in_w, self._h_out_r = in_w, out_r
        try:
            hr = k32.CreatePseudoConsole(
                self._coord(cols, rows), in_r, out_w, 0, ctypes.byref(self._hpc)
            )
            if hr != 0:
                raise OSError(f"CreatePseudoConsole falhou: 0x{hr & 0xFFFFFFFF:08x}")
            k32.CloseHandle(in_r)
            k32.CloseHandle(out_w)
            in_r = out_w = None
            self._spawn(cmd, cwd, env)
        except BaseException:
            for handle in (in_r, out_w):
                if handle is not None:
                    k32.CloseHandle(handle)
            self._release()
            raise
        threading.Thread(target=self._watch_exit, daemon=True, name="conpty-watch").start()

    def _declare(self, k32) -> None:
        ct, wt = self._ct, self._wt
        HANDLE, DWORD, BOOL, LPVOID = wt.HANDLE, wt.DWORD, wt.BOOL, ct.c_void_p

        class COORD(ct.Structure):
            _fields_ = [("X", ct.c_short), ("Y", ct.c_short)]

        class STARTUPINFOW(ct.Structure):
            _fields_ = [
                ("cb", DWORD), ("lpReserved", wt.LPWSTR), ("lpDesktop", wt.LPWSTR),
                ("lpTitle", wt.LPWSTR), ("dwX", DWORD), ("dwY", DWORD),
                ("dwXSize", DWORD), ("dwYSize", DWORD), ("dwXCountChars", DWORD),
                ("dwYCountChars", DWORD), ("dwFillAttribute", DWORD), ("dwFlags", DWORD),
                ("wShowWindow", wt.WORD), ("cbReserved2", wt.WORD), ("lpReserved2", LPVOID),
                ("hStdInput", HANDLE), ("hStdOutput", HANDLE), ("hStdError", HANDLE),
            ]

        class STARTUPINFOEXW(ct.Structure):
            _fields_ = [("StartupInfo", STARTUPINFOW), ("lpAttributeList", LPVOID)]

        class PROCESS_INFORMATION(ct.Structure):
            _fields_ = [
                ("hProcess", HANDLE), ("hThread", HANDLE),
                ("dwProcessId", DWORD), ("dwThreadId", DWORD),
            ]

        self._COORD, self._SIEX, self._SI, self._PI = (
            COORD, STARTUPINFOEXW, STARTUPINFOW, PROCESS_INFORMATION,
        )
        k32.CreatePipe.argtypes = [ct.POINTER(HANDLE), ct.POINTER(HANDLE), LPVOID, DWORD]
        k32.CreatePipe.restype = BOOL
        k32.CreatePseudoConsole.argtypes = [COORD, HANDLE, HANDLE, DWORD, ct.POINTER(HANDLE)]
        k32.CreatePseudoConsole.restype = ct.c_long
        k32.ResizePseudoConsole.argtypes = [HANDLE, COORD]
        k32.ResizePseudoConsole.restype = ct.c_long
        k32.ClosePseudoConsole.argtypes = [HANDLE]
        k32.ClosePseudoConsole.restype = None
        k32.InitializeProcThreadAttributeList.argtypes = [
            LPVOID, DWORD, DWORD, ct.POINTER(ct.c_size_t),
        ]
        k32.InitializeProcThreadAttributeList.restype = BOOL
        k32.UpdateProcThreadAttribute.argtypes = [
            LPVOID, DWORD, ct.c_size_t, LPVOID, ct.c_size_t, LPVOID, LPVOID,
        ]
        k32.UpdateProcThreadAttribute.restype = BOOL
        k32.DeleteProcThreadAttributeList.argtypes = [LPVOID]
        k32.DeleteProcThreadAttributeList.restype = None
        k32.CreateProcessW.argtypes = [
            wt.LPCWSTR, wt.LPWSTR, LPVOID, LPVOID, BOOL, DWORD, LPVOID, wt.LPCWSTR,
            LPVOID, ct.POINTER(PROCESS_INFORMATION),
        ]
        k32.CreateProcessW.restype = BOOL
        k32.ReadFile.argtypes = [HANDLE, LPVOID, DWORD, ct.POINTER(DWORD), LPVOID]
        k32.ReadFile.restype = BOOL
        k32.WriteFile.argtypes = [HANDLE, LPVOID, DWORD, ct.POINTER(DWORD), LPVOID]
        k32.WriteFile.restype = BOOL
        k32.CloseHandle.argtypes = [HANDLE]
        k32.CloseHandle.restype = BOOL
        k32.WaitForSingleObject.argtypes = [HANDLE, DWORD]
        k32.WaitForSingleObject.restype = DWORD

    def _coord(self, cols: int, rows: int):
        return self._COORD(max(1, min(cols, 32767)), max(1, min(rows, 32767)))

    def _spawn(self, cmd: Sequence[str], cwd: str, env: Mapping[str, str]) -> None:
        ct, wt, k32 = self._ct, self._wt, self._k32
        size = ct.c_size_t(0)
        k32.InitializeProcThreadAttributeList(None, 1, 0, ct.byref(size))
        self._attr_buf = ct.create_string_buffer(size.value)
        attr_list = ct.cast(self._attr_buf, ct.c_void_p)
        if not k32.InitializeProcThreadAttributeList(attr_list, 1, 0, ct.byref(size)):
            raise ct.WinError(ct.get_last_error())
        pseudoconsole_attr = 0x00020016
        if not k32.UpdateProcThreadAttribute(
            attr_list, 0, pseudoconsole_attr, self._hpc, ct.sizeof(wt.HANDLE), None, None
        ):
            raise ct.WinError(ct.get_last_error())

        siex = self._SIEX()
        siex.StartupInfo.cb = ct.sizeof(self._SIEX)
        siex.lpAttributeList = attr_list
        info = self._PI()
        env_block = "".join(f"{k}={v}\0" for k, v in env.items()) + "\0"
        env_buf = ct.create_unicode_buffer(env_block, len(env_block))
        cmdline = ct.create_unicode_buffer(subprocess.list2cmdline(list(cmd)), 32768)
        flags = 0x00080000 | 0x00000400
        if not k32.CreateProcessW(
            None, cmdline, None, None, False, flags, ct.cast(env_buf, ct.c_void_p),
            cwd or None, ct.byref(siex), ct.byref(info),
        ):
            raise ct.WinError(ct.get_last_error())
        self._h_proc, self._h_thread = info.hProcess, info.hThread
        self.pid = info.dwProcessId

    def read(self, size: int = 65536) -> bytes:
        ct = self._ct
        buf = ct.create_string_buffer(size)
        got = self._wt.DWORD(0)
        ok = self._k32.ReadFile(self._h_out_r, buf, size, ct.byref(got), None)
        if not ok or got.value == 0:
            return b""
        return buf.raw[: got.value]

    def write(self, data: bytes) -> None:
        ct = self._ct
        view = memoryview(data)
        while view:
            chunk = bytes(view[:16384])
            written = self._wt.DWORD(0)
            ok = self._k32.WriteFile(
                self._h_in_w, chunk, len(chunk), ct.byref(written), None
            )
            if not ok or written.value == 0:
                raise OSError("falha ao escrever no ConPTY")
            view = view[written.value:]

    def resize(self, rows: int, cols: int) -> None:
        if self._hpc and not self._closed:
            self._k32.ResizePseudoConsole(self._hpc, self._coord(cols, rows))

    @property
    def alive(self) -> bool:
        if not self._h_proc:
            return False
        return self._k32.WaitForSingleObject(self._h_proc, 0) == 0x102

    def _watch_exit(self) -> None:
        """Ao sair o shell, fecha o pseudo-console para que a leitura termine."""
        self._k32.WaitForSingleObject(self._h_proc, 0xFFFFFFFF)
        with self._lock:
            if self._closed or not self._hpc:
                return
            hpc, self._hpc = self._hpc, self._wt.HANDLE()
        self._k32.ClosePseudoConsole(hpc)

    def terminate(self) -> None:
        if self.pid is None or not self.alive:
            return
        with contextlib.suppress(OSError, subprocess.SubprocessError):
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(self.pid)],
                capture_output=True,
                timeout=3,
                check=False,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )

    def _release(self) -> None:
        k32 = self._k32
        with self._lock:
            hpc, self._hpc = self._hpc, self._wt.HANDLE()
            self._closed = True
        if hpc:
            k32.ClosePseudoConsole(hpc)
        for name in ("_h_in_w", "_h_out_r", "_h_thread", "_h_proc"):
            handle = getattr(self, name, None)
            if handle:
                k32.CloseHandle(handle)
                setattr(self, name, self._wt.HANDLE())
        if self._attr_buf is not None:
            k32.DeleteProcThreadAttributeList(self._ct.cast(self._attr_buf, self._ct.c_void_p))
            self._attr_buf = None

    def close(self) -> None:
        if not self._closed:
            self._release()


def spawn_backend(
    cmd: Sequence[str],
    cwd: str,
    rows: int,
    cols: int,
    *,
    load_rc: bool = True,
    prefer_conpty: bool = True,
) -> Backend:
    """Cria o melhor backend disponível para ``cmd``.

    A ordem é: PTY nativo (POSIX), ConPTY, ``winpty`` e, por fim, pipes.
    """
    argv = interactive_command(cmd, load_rc)
    env = terminal_env()
    if sys.platform != "win32":
        try:
            return PosixPtyBackend(argv, cwd, env, rows, cols)
        except (OSError, ImportError, subprocess.SubprocessError) as exc:
            logger.warning("PTY indisponível (%s); usando pipes.", exc)
    else:
        if prefer_conpty and conpty_supported():
            try:
                return ConPtyBackend(argv, cwd, env, rows, cols)
            except (OSError, ImportError, AttributeError) as exc:
                logger.warning("ConPTY falhou (%s); tentando alternativas.", exc)
        try:
            return WinPtyBackend(argv, cwd, rows, cols)
        except (ImportError, OSError, AttributeError):
            pass
    return PipeBackend(cmd, cwd, env)
