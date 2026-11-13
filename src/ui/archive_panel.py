"""Module: archive_panel - Visualizador de arquivos compactados.

Suporte nativo:
  - ZIP  - via módulo `zipfile` da stdlib
  - RAR  - requer pacote `rarfile` (exibe aviso se ausente)
  - 7-Zip - requer pacote `py7zr`  (exibe aviso se ausente)
  - GZip / BZip2 / TAR - via módulo `tarfile` da stdlib

Interface:
  - TreeView com estrutura de pastas do arquivo compactado
  - Duplo-clique ou botão "Abrir no editor" extrai o arquivo para um diretório
    temporário e invoca o callback `on_open_file(path)`
  - Botão "Extrair selecionado..." deixa o usuário escolher o destino
  - Barra de status com tamanho comprimido × tamanho real
"""

from __future__ import annotations

import contextlib
import logging
import tarfile
import tempfile
import tkinter as tk
import zipfile
from collections.abc import Callable
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from src.i18n import _t

logger = logging.getLogger("Codis")

try:
    import rarfile as _rarfile_mod

    _RARFILE_OK = True
except ImportError:
    _RARFILE_OK = False

try:
    import py7zr as _py7zr_mod

    _PY7ZR_OK = True
except ImportError:
    _PY7ZR_OK = False


def _is_safe_member(dest: Path, member_name: str) -> bool:
    """Verifica se ``member_name`` extraído em ``dest`` fica contido em ``dest``.

    Protege contra path traversal (zip-slip/tar-slip): entradas de arquivo
    compactado com caminhos como ``../../etc/cron.d/evil`` podem escrever
    fora do diretório de destino escolhido pelo usuário se não forem
    validadas antes da extração.
    """
    try:
        resolved_dest = dest.resolve()
        resolved_target = (dest / member_name).resolve()
    except (OSError, ValueError):
        return False
    return resolved_target == resolved_dest or resolved_dest in resolved_target.parents


def _detect_kind(path: Path) -> str:
    """Detecta o tipo do arquivo por magic bytes."""
    try:
        with path.open("rb") as handle:
            header = handle.read(10)
    except OSError:
        return "unknown"
    if header[:4] in (b"PK\x03\x04", b"PK\x05\x06"):
        return "zip"
    if header[:6] in (b"Rar!\x1a\x07\x00", b"Rar!\x1a\x07\x01"):
        return "rar"
    if header[:6] == b"7z\xbc\xaf\x27\x1c":
        return "7z"
    if (
        header[:2] == b"\x1f\x8b"
        or header[:5] in (b"ustar",)
        or path.suffix in (".tar", ".tgz", ".tar.gz", ".tar.bz2", ".tar.xz")
    ):
        return "tar"

    return {".zip": "zip", ".rar": "rar", ".7z": "7z"}.get(
        path.suffix.lower(), "unknown"
    )


class _ZipReader:
    """Wrapper de leitura para arquivos ZIP."""

    def __init__(self, path: Path) -> None:
        """Inicializa o leitor de arquivos ZIP."""
        self._zf = zipfile.ZipFile(path, "r")

    def list_entries(self) -> list[tuple[str, bool, int, int]]:
        """Retorna lista de (nome, is_dir, compressed_size, file_size)."""
        result = []
        for info in self._zf.infolist():
            is_dir = info.filename.endswith("/")
            result.append(
                (info.filename, is_dir, info.compress_size, info.file_size))
        return result

    def extract_one(self, name: str, dest: Path) -> Path:
        """Extrai uma entrada específica do ZIP para o destino."""
        if not _is_safe_member(dest, name):
            raise ValueError(
                f"Entrada insegura no ZIP (path traversal): {name!r}")
        self._zf.extract(name, dest)
        return dest / name

    def extract_all(self, dest: Path) -> None:
        """Extrai todo o conteúdo do ZIP para o destino, validando cada entrada."""
        safe_members = [
            info for info in self._zf.infolist() if _is_safe_member(dest, info.filename)
        ]
        unsafe_count = len(self._zf.infolist()) - len(safe_members)
        if unsafe_count:
            logger.warning(
                "Ignorando %d entrada(s) insegura(s) (path traversal) na extração do ZIP.",
                unsafe_count,
            )
        self._zf.extractall(dest, members=safe_members)

    def close(self) -> None:
        """Fecha o arquivo ZIP e libera recursos."""
        self._zf.close()


class _TarReader:
    """Wrapper de leitura para arquivos TAR (gz, bz2, xz, plain)."""

    def __init__(self, path: Path) -> None:
        """Inicializa o leitor de arquivos TAR/TAR.GZ."""
        self._tf = tarfile.open(path, "r:*")

    def list_entries(self) -> list[tuple[str, bool, int, int]]:
        """Lista as entradas do arquivo TAR."""
        result = []
        for m in self._tf.getmembers():
            result.append((m.name, m.isdir(), m.size, m.size))
        return result

    def extract_one(self, name: str, dest: Path) -> Path:
        """Extrai uma entrada específica do TAR."""
        if not _is_safe_member(dest, name):
            raise ValueError(
                f"Entrada insegura no TAR (path traversal): {name!r}")
        member = self._tf.getmember(name)
        self._tf.extract(member, dest, filter="data")
        return dest / name

    def extract_all(self, dest: Path) -> None:
        """Extrai todo o conteúdo do TAR para o destino.

        Usa ``filter="data"`` (disponível desde Python 3.12, mínimo exigido
        pelo projeto) para bloquear path traversal, links simbólicos/hard
        perigosos e outros vetores de tar-slip nativamente.
        """
        self._tf.extractall(dest, filter="data")

    def close(self) -> None:
        """Fecha o arquivo TAR e libera recursos."""
        self._tf.close()


class _RarReader:
    """Wrapper de leitura para arquivos RAR (requer rarfile)."""

    def __init__(self, path: Path) -> None:
        """Inicializa o leitor de arquivos RAR."""
        if not _RARFILE_OK:
            raise RuntimeError(
                "Para abrir arquivos RAR instale o pacote rarfile:\n"
                "    pip install rarfile\n"
                "Além disso, o unrar/rar precisa estar no PATH."
            )
        self._rf = _rarfile_mod.RarFile(str(path))

    def list_entries(self) -> list[tuple[str, bool, int, int]]:
        """Lista as entradas do arquivo RAR."""
        result = []
        for info in self._rf.infolist():
            is_dir = info.is_dir()
            result.append(
                (info.filename, is_dir, info.compress_size, info.file_size))
        return result

    def extract_one(self, name: str, dest: Path) -> Path:
        """Extrai uma entrada específica do arquivo RAR."""
        if not _is_safe_member(dest, name):
            raise ValueError(
                f"Entrada insegura no RAR (path traversal): {name!r}")
        self._rf.extract(name, str(dest))
        return dest / name

    def extract_all(self, dest: Path) -> None:
        """Extrai todo o conteúdo do arquivo RAR, validando cada entrada."""
        all_names = [info.filename for info in self._rf.infolist()]
        safe_names = [n for n in all_names if _is_safe_member(dest, n)]
        if len(safe_names) != len(all_names):
            logger.warning(
                "Ignorando %d entrada(s) insegura(s) (path traversal) na extração do RAR.",
                len(all_names) - len(safe_names),
            )
        self._rf.extractall(str(dest), members=safe_names)

    def close(self) -> None:
        """Fecha o arquivo RAR e libera recursos."""
        self._rf.close()


class _SevenZipReader:
    """Wrapper de leitura para arquivos 7z (requer py7zr)."""

    def __init__(self, path: Path) -> None:
        """Inicializa o leitor de arquivos 7-Zip."""
        if not _PY7ZR_OK:
            raise RuntimeError(
                "Para abrir arquivos 7-Zip instale o pacote py7zr:\n    pip install py7zr"
            )
        self._archive = _py7zr_mod.SevenZipFile(str(path), mode="r")

    def list_entries(self) -> list[tuple[str, bool, int, int]]:
        """Lista as entradas do arquivo 7-Zip."""
        result = []
        for info in self._archive.list():
            is_dir = info.is_directory
            c_size = getattr(info, "compressed", 0) or 0
            u_size = getattr(info, "uncompressed", 0) or 0
            result.append((info.filename, is_dir, c_size, u_size))
        return result

    def extract_one(self, name: str, dest: Path) -> Path:
        """Extrai uma entrada específica do 7-Zip."""
        if not _is_safe_member(dest, name):
            raise ValueError(
                f"Entrada insegura no 7-Zip (path traversal): {name!r}")
        self._archive.extract(str(dest), targets=[name])
        return dest / name

    def extract_all(self, dest: Path) -> None:
        """Extrai todo o conteúdo do arquivo 7-Zip, validando cada entrada antes."""
        all_names = [info.filename for info in self._archive.list()]
        unsafe = [n for n in all_names if not _is_safe_member(dest, n)]
        if unsafe:
            logger.warning(
                "Ignorando %d entrada(s) insegura(s) (path traversal) na extração do 7-Zip.",
                len(unsafe),
            )
            self._archive.reset()
            safe_names = [n for n in all_names if n not in unsafe]
            self._archive.extract(str(dest), targets=safe_names)
            return
        self._archive.extractall(str(dest))

    def close(self) -> None:
        """Fecha o arquivo 7-Zip e libera recursos."""
        self._archive.close()


def _open_reader(
    path: Path,
) -> _ZipReader | _TarReader | _RarReader | _SevenZipReader:
    kind = _detect_kind(path)
    if kind == "zip":
        return _ZipReader(path)
    if kind == "tar":
        return _TarReader(path)
    if kind == "rar":
        return _RarReader(path)
    if kind == "7z":
        return _SevenZipReader(path)
    raise ValueError(f"Formato não suportado: {path.suffix}")


class ArchivePanel(tk.Frame):
    """Painel de visualização de arquivo compactado.

    Args:
        parent:       Widget pai Tkinter.
        path:         Caminho para o arquivo compactado.
        on_open_file: Callback invocado com o Path do arquivo extraído quando
                      o usuário escolhe "Abrir no editor".
    """

    _EXT_ICON: dict[str, str] = {
        ".py": "py",
        ".js": "📜",
        ".ts": "ts",
        ".json": "📋",
        ".html": "htm",
        ".css": "css",
        ".md": "md",
        ".txt": "txt",
        ".png": "🖼",
        ".jpg": "🖼",
        ".jpeg": "🖼",
        ".gif": "🖼",
        ".svg": "🖼",
        ".mp3": "🎵",
        ".wav": "🎵",
        ".mp4": "🎬",
        ".pdf": "📕",
        ".zip": "📦",
        ".tar": "📦",
        ".gz": "📦",
        ".exe": "yml",
        ".dll": "yml",
        ".sh": "🔧",
        ".bat": "🔧",
    }
    _DIR_ICON = "📁"
    _FILE_ICON = "txt"

    def __init__(
        self,
        parent: tk.Widget,
        path: Path,
        on_open_file: Callable[[Path], None] | None = None,
    ) -> None:
        """Inicializa o painel de gerenciamento de arquivos comprimidos."""
        super().__init__(parent)
        self._path = path
        self._on_open_file = on_open_file
        self._reader: _ZipReader | _TarReader | _RarReader | _SevenZipReader | None = (
            None
        )
        self._tmp_dir: tempfile.TemporaryDirectory | None = None

        self._build_ui()
        self.after(50, self._load_archive)

    def _build_ui(self) -> None:
        """Monta o layout do painel."""
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        toolbar = tk.Frame(self, padx=6, pady=4)
        toolbar.grid(row=0, column=0, sticky="ew")

        tk.Label(
            toolbar, text=f"📦  {self._path.name}", font=("TkDefaultFont", 10, "bold")
        ).pack(side="left")

        self._btn_open = tk.Button(
            toolbar,
            text=_t("archive.open_in_editor", "📂  Abrir no editor"),
            command=self._open_selected,
            state="disabled",
            relief="flat",
            padx=8,
        )
        self._btn_open.pack(side="right", padx=2)

        tk.Button(
            toolbar,
            text=_t("archive.extract_selected", "📤  Extrair selecionado..."),
            command=self._extract_selected,
            relief="flat",
            padx=8,
        ).pack(side="right", padx=2)

        tk.Button(
            toolbar,
            text=_t("archive.extract_all", "📥  Extrair tudo..."),
            command=self._extract_all,
            relief="flat",
            padx=8,
        ).pack(side="right", padx=2)

        frame = tk.Frame(self)
        frame.grid(row=1, column=0, sticky="nsew")
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)

        cols = ("size", "compressed", "ratio")
        self._tree = ttk.Treeview(
            frame,
            columns=cols,
            selectmode="browse",
        )
        self._tree.heading("#0", text=_t("archive.col_name", "Nome"))
        self._tree.heading("size", text=_t("archive.col_size", "Tamanho"))
        self._tree.heading(
            "compressed", text=_t("archive.col_compressed", "Comprimido")
        )
        self._tree.heading("ratio", text=_t("archive.col_reason"))

        self._tree.column("#0", width=360, minwidth=160)
        self._tree.column("size", width=90, anchor="e", minwidth=70)
        self._tree.column("compressed", width=90, anchor="e", minwidth=70)
        self._tree.column("ratio", width=60, anchor="e", minwidth=50)

        vsb = ttk.Scrollbar(frame, orient="vertical", command=self._tree.yview)
        hsb = ttk.Scrollbar(frame, orient="horizontal",
                            command=self._tree.xview)
        self._tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)

        self._tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")

        self._tree.bind("<<TreeviewSelect>>", self._on_select)
        self._tree.bind("<Double-1>", self._on_double_click)

        self._status_var = tk.StringVar(value="Carregando...")
        tk.Label(
            self,
            textvariable=self._status_var,
            anchor="w",
            padx=6,
            pady=2,
            font=("TkDefaultFont", 8),
        ).grid(row=2, column=0, sticky="ew")

    def _load_archive(self) -> None:
        """Abre o arquivo compactado e popula a árvore."""
        try:
            self._reader = _open_reader(self._path)
            entries = self._reader.list_entries()
        except Exception as exc:
            logger.warning("ArchivePanel._load_archive: %s", exc)
            self._status_var.set(f"Erro ao abrir: {exc}")
            messagebox.showerror(
                _t("archive.err_open", "Erro ao abrir arquivo"), str(exc), parent=self
            )
            return

        self._populate_tree(entries)

    def _populate_tree(self, entries: list[tuple[str, bool, int, int]]) -> None:
        """Preenche a Treeview com a estrutura de diretórios/arquivos."""
        self._tree.delete(*self._tree.get_children())

        entries_sorted = sorted(
            entries, key=lambda e: (not e[1], e[0].lower()))

        nodes: dict[str, str] = {"": ""}

        total_size = 0
        total_comp = 0

        for name, is_dir, comp_size, file_size in entries_sorted:
            parts = name.rstrip("/").split("/")
            parent_key = ""

            for depth, part in enumerate(parts):
                current_key = "/".join(parts[: depth + 1])
                if current_key not in nodes:
                    parent_iid = nodes[parent_key]
                    is_last = depth == len(parts) - 1
                    _is_dir = is_dir or not is_last

                    icon = self._DIR_ICON if _is_dir else self._get_icon(part)
                    text = f"{icon}  {part}"

                    if _is_dir:
                        vals = ("", "", "")
                    else:
                        ratio = (
                            f"{comp_size / file_size * 100:.0f}%"
                            if file_size > 0
                            else "-"
                        )
                        vals = (
                            self._fmt_size(file_size),
                            self._fmt_size(comp_size),
                            ratio,
                        )
                        total_size += file_size
                        total_comp += comp_size

                    iid = self._tree.insert(
                        parent_iid,
                        "end",
                        iid=current_key,
                        text=text,
                        values=vals,
                        open=(depth == 0),
                        tags=("dir" if _is_dir else "file",),
                    )

                    self._tree.item(
                        iid,
                        tags=(
                            ("dir" if _is_dir else "file"),
                            name if not _is_dir else "",
                        ),
                    )
                    nodes[current_key] = iid
                parent_key = current_key

        ratio_total = f"{total_comp / total_size * 100:.0f}%" if total_size > 0 else "-"
        n_files = sum(1 for _, is_dir, _, _ in entries if not is_dir)
        self._status_var.set(
            f"{n_files} arquivo(s)  -  "
            f"Total: {self._fmt_size(total_size)}  -  "
            f"Comprimido: {self._fmt_size(total_comp)}  -  "
            f"Razão: {ratio_total}"
        )

    @staticmethod
    def _fmt_size(n: int) -> str:
        for unit in ("B", "KB", "MB", "GB"):
            if n < 1024:
                return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
            n /= 1024
        return f"{n:.1f} TB"

    def _get_icon(self, filename: str) -> str:
        ext = Path(filename).suffix.lower()
        return self._EXT_ICON.get(ext, self._FILE_ICON)

    def _selected_entry_name(self) -> str | None:
        """Retorna o nome do arquivo selecionado no arquivo compactado, ou None."""
        sel = self._tree.selection()
        if not sel:
            return None
        iid = sel[0]
        tags = self._tree.item(iid, "tags")

        if len(tags) >= 2 and tags[0] == "file" and tags[1]:
            return tags[1]
        return None

    def _ensure_tmp(self) -> Path:
        """Garante que o diretório temporário existe."""
        if self._tmp_dir is None:
            self._tmp_dir = tempfile.TemporaryDirectory(
                prefix="codis_archive_")
        return Path(self._tmp_dir.name)

    def _on_select(self, _e: object = None) -> None:
        name = self._selected_entry_name()
        self._btn_open.config(state="normal" if name else "disabled")

    def _on_double_click(self, _e: object = None) -> None:
        self._open_selected()

    def _open_selected(self) -> None:
        """Extrai o arquivo selecionado para temp e chama on_open_file."""
        name = self._selected_entry_name()
        if not name or self._reader is None:
            return
        try:
            tmp = self._ensure_tmp()
            extracted = self._reader.extract_one(name, tmp)
            if not extracted.exists():
                extracted = tmp / Path(name).name
            if not extracted.exists():
                messagebox.showerror(
                    _t("archive.extract_error"),
                    f"Não foi possível localizar o arquivo extraído:\n{extracted}",
                    parent=self,
                )
                return
            if self._on_open_file:
                self._on_open_file(extracted)
        except Exception as exc:
            logger.exception("Erro ao extrair %s", name)
            messagebox.showerror(
                _t("archive.extract_error"), str(exc), parent=self)

    def _extract_selected(self) -> None:
        """Extrai o arquivo selecionado para um diretório escolhido pelo usuário."""
        name = self._selected_entry_name()
        if not name or self._reader is None:
            messagebox.showinfo(
                _t("archive.btn_extract"), _t("archive.select_file_first"), parent=self
            )
            return
        dest = filedialog.askdirectory(
            title=_t("archive.extract_to"), parent=self)
        if not dest:
            return
        try:
            out = self._reader.extract_one(name, Path(dest))
            messagebox.showinfo(
                "Extraído",
                f"Arquivo extraído com sucesso:\n{out}",
                parent=self,
            )
        except Exception as exc:
            messagebox.showerror(
                _t("archive.extract_error", "Erro ao extrair"), str(exc), parent=self
            )

    def _extract_all(self) -> None:
        """Extrai todos os arquivos do arquivo compactado para o diretório de destino."""
        if self._reader is None:
            return
        dest = filedialog.askdirectory(
            title=_t("archive.extract_all_to"), parent=self)
        if not dest:
            return
        try:
            self._reader.extract_all(Path(dest))
            messagebox.showinfo(
                "Extraído",
                f"Todos os arquivos extraídos para:\n{dest}",
                parent=self,
            )
        except Exception as exc:
            messagebox.showerror(
                _t("archive.extract_error", "Erro ao extrair"), str(exc), parent=self
            )

    def destroy(self) -> None:
        """Fecha o reader e libera o diretório temporário."""
        if self._reader is not None:
            with contextlib.suppress(Exception):
                self._reader.close()
            self._reader = None
        if self._tmp_dir is not None:
            with contextlib.suppress(Exception):
                self._tmp_dir.cleanup()
            self._tmp_dir = None
        super().destroy()
