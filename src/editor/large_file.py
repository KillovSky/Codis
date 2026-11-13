"""Module: large_file - Virtual file buffer para edição de arquivos gigantes no Tkinter.

Arquitetura de três camadas:
  Tier 1 (< GIANT_FILE_BYTES)  - inserção assíncrona em chunks, undo e features pesadas off.
  Tier 2 (>= GIANT_FILE_BYTES) - viewport virtual via mmap; só VIEWPORT_LINES linhas no widget.
"""

from __future__ import annotations

import contextlib
import mmap
import threading
from collections.abc import Callable
from pathlib import Path

_SCAN_CHUNK = 4 * 1024 * 1024


class VirtualFileBuffer:
    """
    Buffer de arquivo com mmap + rastreador de edições para arquivos >= 50 MB.

    - O índice de linhas mapeia número de linha (0-based) -> offset em bytes.
    - Linhas editadas ficam em ``_edits: dict[int, str]``.
    - O salvamento faz streaming: para cada linha, usa o edit dict quando disponível,
      senão lê diretamente do mmap (zero-copy para as partes não modificadas).
    - O índice é construído em thread de background; ``is_ready`` indica quando termina.
    """

    def __init__(
        self,
        path: Path,
        encoding: str,
        eol: str,
        on_ready: Callable[[int], None],
    ) -> None:
        """Inicializa o buffer virtual de arquivo grande sobre o widget ``txt``."""
        self._path = path
        self._encoding = encoding
        self._on_ready = on_ready

        self._f: object | None = None
        self._mm: mmap.mmap | None = None
        self._line_offsets: list[int] = []
        self._line_ends: list[int] = []
        self._edits: dict[int, str] = {}
        self._lock = threading.Lock()

        self.is_ready = False
        self.total_lines = 0
        self.error: str | None = None

        self._cancelled = False
        self._thread = threading.Thread(
            target=self._build_index, daemon=True, name="vfb-index"
        )
        self._thread.start()

    def _build_index(self) -> None:
        """Constrói o índice byte-offset por linha. Executa em thread de background."""
        try:
            if self._cancelled:
                return
            self._f = open(self._path, "rb")
            size = self._path.stat().st_size

            if size == 0:
                self._line_offsets = [0]
                self._line_ends = [0]
                self.total_lines = 1
                self.is_ready = True
                self._on_ready(1)
                return

            self._mm = mmap.mmap(
                self._f.fileno(),
                0,
                access=mmap.ACCESS_READ,
            )
            mm = self._mm
            n = len(mm)
            offsets = [0]
            ends: list[int] = []

            pos = 0
            while pos < n and not self._cancelled:
                chunk_end = min(pos + _SCAN_CHUNK, n)
                chunk = mm[pos:chunk_end]
                local = 0
                while True:
                    nl = chunk.find(b"\n", local)
                    if nl < 0:
                        break
                    abs_nl = pos + nl

                    line_end = (
                        abs_nl - 1
                        if abs_nl > 0 and mm[abs_nl - 1: abs_nl] == b"\r"
                        else abs_nl
                    )
                    ends.append(line_end)
                    next_pos = abs_nl + 1
                    if next_pos < n:
                        offsets.append(next_pos)
                    local = nl + 1
                pos = chunk_end

            if len(ends) < len(offsets):
                ends.append(n)

            if self._cancelled:
                return
            with self._lock:
                self._line_offsets = offsets
                self._line_ends = ends
                self.total_lines = len(offsets)
                self.is_ready = True

            self._on_ready(self.total_lines)

        except OSError as exc:
            self.error = str(exc)
            self._on_ready(0)

    def get_lines(self, start: int, end: int) -> list[str]:
        """Retorna linhas[start:end] (0-based, end exclusive) decodificadas.

        Usa o edit dict se a linha foi modificada; caso contrário lê do mmap.
        """
        if not self.is_ready or self._mm is None:
            return []
        result: list[str] = []
        end = min(end, self.total_lines)
        with self._lock:
            for i in range(start, end):
                if i in self._edits:
                    result.append(self._edits[i])
                else:
                    try:
                        b_start = self._line_offsets[i]
                        b_end = (
                            self._line_ends[i]
                            if i < len(self._line_ends)
                            else len(self._mm)
                        )
                        raw = bytes(self._mm[b_start:b_end])
                        result.append(raw.decode(
                            self._encoding, errors="replace"))
                    except (IndexError, ValueError):
                        result.append("")
        return result

    def flush_widget_edits(self, widget_lines: list[str], viewport_start: int) -> None:
        """Persiste as edições do widget no edit dict antes de um viewport shift.

        Compara cada linha do widget com o original; guarda apenas as diferentes.

        Args:
            widget_lines:    Linhas atuais do widget Text (0-based dentro do viewport).
            viewport_start:  Linha absoluta (0-based) correspondente à linha 0 do widget.
        """
        with self._lock:
            for rel, content in enumerate(widget_lines):
                abs_line = viewport_start + rel
                if abs_line >= self.total_lines:
                    break

                if abs_line in self._edits:
                    original = self._edits[abs_line]
                else:
                    try:
                        b_start = self._line_offsets[abs_line]
                        b_end = (
                            self._line_ends[abs_line]
                            if abs_line < len(self._line_ends)
                            else len(self._mm)
                        )
                        original = bytes(self._mm[b_start:b_end]).decode(
                            self._encoding, errors="replace"
                        )
                    except (IndexError, ValueError, TypeError):
                        original = ""
                if content != original:
                    self._edits[abs_line] = content
                elif abs_line in self._edits:
                    del self._edits[abs_line]

    def has_edits(self) -> bool:
        """Retorna ``True`` se o buffer contém edições não salvas em relação ao conteúdo original."""
        return bool(self._edits)

    def save_to_path(
        self,
        dest: Path,
        eol: str,
        encoding: str,
        trim_trailing: bool = False,
        insert_final_newline: bool = True,
    ) -> None:
        """Salva o arquivo completo (com edições aplicadas) em `dest`.

        Usa escrita atômica: grava em .codis_tmp e faz rename() para o destino.
        Para as linhas não editadas, lê diretamente do mmap (zero-copy do ponto
        de vista da string Python).

        Args:
            dest:                  Caminho de destino.
            eol:                   Sequência de final de linha (\\n, \\r\\n, \\r).
            encoding:              Codificação de saída.
            trim_trailing:         Remove espaços finais de cada linha.
            insert_final_newline:  Insere \\n no fim do arquivo se não houver.

        Raises:
            RuntimeError: Se o índice ainda não foi construído.
            OSError:       Erro de I/O ao escrever.
        """
        if not self.is_ready:
            raise RuntimeError("VirtualFileBuffer: índice ainda não pronto")

        eol_bytes = eol.encode(encoding)
        tmp = dest.with_suffix(dest.suffix + ".codis_tmp")

        try:
            with open(tmp, "wb") as out, self._lock:
                total = self.total_lines
                for i in range(total):
                    if i in self._edits:
                        line_str = self._edits[i]
                    else:
                        if self._mm is not None:
                            try:
                                b_start = self._line_offsets[i]
                                b_end = (
                                    self._line_ends[i]
                                    if i < len(self._line_ends)
                                    else len(self._mm)
                                )
                                raw_bytes = bytes(self._mm[b_start:b_end])
                                line_str = raw_bytes.decode(
                                    self._encoding, errors="replace"
                                )
                            except (IndexError, ValueError):
                                line_str = ""
                        else:
                            line_str = ""

                    if trim_trailing:
                        line_str = line_str.rstrip()

                    line_bytes = line_str.encode(encoding, errors="replace")

                    if i < total - 1:
                        out.write(line_bytes + eol_bytes)
                    else:
                        out.write(line_bytes)
                        if insert_final_newline:
                            out.write(eol_bytes)

            tmp.replace(dest)

        except OSError:
            with contextlib.suppress(OSError):
                tmp.unlink()
            raise

    def close(self) -> None:
        """Fecha o mmap e o handle de arquivo."""
        with contextlib.suppress(OSError, Exception):
            if self._mm is not None:
                self._mm.close()
                self._mm = None
        with contextlib.suppress(OSError, Exception):
            if self._f is not None:
                self._f.close()
                self._f = None

    def __del__(self) -> None:
        """Cancela timers pendentes ao destruir o buffer virtual."""
        self.close()
