"""Sessão de terminal: um shell, seu emulador de tela e as threads de E/S.

Não depende de Tkinter. A interface chama ``pump`` periodicamente na sua
thread para consumir a saída do programa e atualizar o emulador; as leituras e
escritas bloqueantes ficam em threads próprias, com fila limitada para que um
programa muito falante seja desacelerado pelo próprio PTY em vez de lotar a
memória ou travar a interface.
"""

from __future__ import annotations

import codecs
import contextlib
import itertools
import logging
import queue
import threading
import time
from collections import deque
from collections.abc import Sequence

from .backends import Backend, spawn_backend
from .vt import Run, VTScreen

logger = logging.getLogger("Codis.Terminal")

DEFAULT_HISTORY = 10_000
_INBOX_CHUNKS = 256
_FEED_SLICE = 1024


class TerminalSession:
    """Um programa interativo rodando num pseudo-terminal."""

    def __init__(
        self,
        label: str,
        cmd: Sequence[str],
        cwd: str,
        rows: int = 24,
        cols: int = 80,
        *,
        load_rc: bool = True,
        prefer_conpty: bool = True,
        history: int = DEFAULT_HISTORY,
        backend: Backend | None = None,
    ) -> None:
        self.label = label
        self.cmd = list(cmd)
        self.cwd = cwd
        self.screen = VTScreen(rows, cols)
        self.history: deque[list[Run]] = deque(maxlen=history)
        self.unrendered = 0
        self.history_cleared = False
        self.exited = False
        self.error = ""
        self.backend: Backend | None = None
        self._decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        self._carry = ""
        self._inbox: queue.Queue[bytes | None] = queue.Queue(maxsize=_INBOX_CHUNKS)
        self._outbox: queue.Queue[bytes | None] = queue.Queue()
        self._closing = False
        self._exit_reported = False
        self._line: list[str] = []
        try:
            self.backend = backend or spawn_backend(
                self.cmd, cwd, rows, cols, load_rc=load_rc, prefer_conpty=prefer_conpty
            )
        except (OSError, ValueError) as exc:
            self.error = str(exc)
            self.exited = True
            self._exit_reported = True
            self.screen.feed(f"\x1b[31m[Erro ao iniciar o shell: {exc}]\x1b[0m\r\n")
            return
        threading.Thread(target=self._read_loop, daemon=True, name=f"term-read-{label}").start()
        threading.Thread(target=self._write_loop, daemon=True, name=f"term-write-{label}").start()

    def _read_loop(self) -> None:
        backend = self.backend
        try:
            while not self._closing:
                data = backend.read(65536)
                if not data:
                    break
                while not self._closing:
                    try:
                        self._inbox.put(data, timeout=0.25)
                        break
                    except queue.Full:
                        continue
        except Exception:
            logger.debug("Leitor do terminal terminou com erro", exc_info=True)
        finally:
            with contextlib.suppress(queue.Full):
                self._inbox.put(None, timeout=1.0)

    def _write_loop(self) -> None:
        backend = self.backend
        while True:
            data = self._outbox.get()
            if data is None:
                return
            try:
                backend.write(data)
            except (OSError, ValueError):
                return

    @property
    def pending(self) -> bool:
        """``True`` se ainda há saída esperando para ser processada."""
        return bool(self._carry) or not self._inbox.empty()

    @property
    def backlog(self) -> int:
        """Linhas de histórico ainda não entregues à interface."""
        return min(self.unrendered, len(self.history))

    def pump(self, budget: float = 0.008, max_bytes: int = 262144) -> bool:
        """Processa a saída pendente respeitando um orçamento de tempo.

        Returns:
            ``True`` se algo foi consumido.
        """
        if self.backend is None:
            return False
        started = time.monotonic()
        consumed = 0
        got = False
        while True:
            if self._carry:
                got = True
                piece, self._carry = self._carry[:_FEED_SLICE], self._carry[_FEED_SLICE:]
                self.screen.feed(piece)
                consumed += len(piece)
            elif consumed < max_bytes:
                try:
                    item = self._inbox.get_nowait()
                except queue.Empty:
                    break
                got = True
                if item is None:
                    self._finish()
                    break
                self._carry = self._decoder.decode(item)
                continue
            else:
                break
            if time.monotonic() - started >= budget or consumed >= max_bytes:
                break
        if got:
            self._collect_history()
            reply = self.screen.take_responses()
            if reply:
                self.send(reply)
        return got

    def _collect_history(self) -> None:
        if self.screen.clear_history_requested:
            self.screen.clear_history_requested = False
            self.history.clear()
            self.unrendered = 0
            self.history_cleared = True
        for chars, attrs in self.screen.take_scrolled():
            self.history.append(VTScreen.runs_of(chars, attrs))
            self.unrendered += 1

    def take_unrendered(self, limit: int | None = None) -> list[list[Run]]:
        """Linhas de histórico novas, das mais antigas para as mais recentes.

        Args:
            limit: máximo de linhas a entregar agora; o resto fica para a
                próxima chamada (evita um quadro gigante após uma enxurrada).
        """
        count = min(self.unrendered, len(self.history))
        if count == 0:
            self.unrendered = 0
            return []
        take = count if limit is None else min(count, limit)
        start = len(self.history) - count
        lines = list(itertools.islice(self.history, start, start + take))
        self.unrendered = count - take
        return lines

    def _finish(self) -> None:
        self.exited = True
        tail = self._decoder.decode(b"", final=True)
        if tail:
            self.screen.feed(tail)
        if not self._exit_reported:
            self._exit_reported = True
            self.screen.feed("\r\n\x1b[90m[Processo encerrado. Pressione Enter para reiniciar]\x1b[0m\r\n")

    def send(self, data: bytes) -> None:
        """Envia bytes ao programa sem processamento."""
        if self.backend is not None and not self.exited and data:
            self._outbox.put(data)

    def type_input(self, data: bytes) -> None:
        """Envia entrada digitada; faz eco e edição de linha locais se o backend não for TTY."""
        if self.backend is None or self.exited or not data:
            return
        if not self.backend.local_echo:
            self._outbox.put(data)
            return
        for ch in data.decode("utf-8", errors="replace"):
            if ch in "\r\n":
                self.screen.feed("\r\n")
                line = "".join(self._line)
                self._line.clear()
                self._outbox.put((line + "\n").encode("utf-8", errors="replace"))
            elif ch in "\x7f\x08":
                if self._line:
                    self._line.pop()
                    self.screen.feed("\b \b")
            elif ch == "\x03":
                self._line.clear()
                self.screen.feed("^C\r\n")
                self.interrupt()
            elif ch == "\x15":
                erase = len(self._line)
                self._line.clear()
                self.screen.feed("\b \b" * erase)
            elif ch >= " ":
                self._line.append(ch)
                self.screen.feed(ch)

    def interrupt(self) -> None:
        """Ctrl+C para o programa em primeiro plano."""
        if self.backend is not None and not self.exited:
            threading.Thread(target=self.backend.interrupt, daemon=True, name="term-int").start()

    def resize(self, rows: int, cols: int) -> None:
        """Atualiza o tamanho do emulador e informa o programa."""
        if rows < 1 or cols < 1:
            return
        if (rows, cols) == (self.screen.rows, self.screen.cols):
            return
        self.screen.resize(rows, cols)
        self._collect_history()
        if self.backend is not None and not self.exited:
            self.backend.resize(rows, cols)

    def clear(self) -> None:
        """Limpa tela e histórico; pede ao shell que redesenhe o prompt."""
        self.history.clear()
        self.unrendered = 0
        self.history_cleared = True
        self.screen.clear_screen_and_history()
        if not self.screen.alt_screen:
            self.send(b"\x0c")

    @property
    def pid(self) -> int | None:
        return self.backend.pid if self.backend is not None else None

    @property
    def alive(self) -> bool:
        return self.backend is not None and not self.exited and self.backend.alive

    def terminate(self) -> None:
        """Pede o encerramento do shell e libera os recursos em segundo plano."""
        self._closing = True
        backend = self.backend
        self.exited = True
        if backend is None:
            return
        self._outbox.put(None)
        with contextlib.suppress(Exception):
            backend.terminate()

        def _cleanup() -> None:
            deadline = time.monotonic() + 3.0
            while time.monotonic() < deadline:
                with contextlib.suppress(queue.Empty):
                    while True:
                        self._inbox.get_nowait()
                if not backend.alive:
                    break
                time.sleep(0.05)
            with contextlib.suppress(Exception):
                backend.close()

        threading.Thread(target=_cleanup, daemon=True, name="term-cleanup").start()
