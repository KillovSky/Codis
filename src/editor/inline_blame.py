"""Codis - InlineBlame: anotações Git blame exibidas inline ao lado de cada linha.

Exibe informações de autoria Git diretamente no editor, ao lado de cada linha,
de forma semelhante ao recurso "Git Lens" do VS Code. Mostra autor, hash
abreviado e tempo relativo (ex: ``KillovSky, a1b2c3d, 3 dias atrás``).

Suporta dois modos de exibição:
  - **current_line**: Exibe blame apenas na linha onde o cursor está.
  - **all_lines**: Exibe blame em todas as linhas do viewport visível.

O blame é executado via ``git blame --porcelain`` em thread separada para
não bloquear a interface.

IMPORTANTE - texto fantasma, nunca conteúdo real:
  As anotações são puramente visuais. Elas são desenhadas como pequenos
  ``tk.Label`` flutuantes, posicionados sobre o widget de texto com
  ``place(in_=...)`` a partir das coordenadas de pixel de ``dlineinfo()``.
  Em nenhum momento o texto da anotação é inserido no buffer do
  ``tk.Text`` - diferente de um placeholder real, o usuário nunca pode
  clicar "dentro" dele, selecioná-lo, apagá-lo ou salvá-lo junto do
  arquivo. Re-renderizar (por debounce, troca de tema, scroll ou
  movimento do cursor) apenas reposiciona/reaproveita esses labels;
  nunca duplica texto, porque não há texto algum a duplicar.
"""

from __future__ import annotations

import contextlib
import logging
import re
import subprocess
import threading
import tkinter as tk
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from src.constants import FONT_SIZE, FONT_UI
from src.i18n import _t

if TYPE_CHECKING:
    from src.theme.base import Theme

logger = logging.getLogger("Codis")

_BLAME_MAX_LEN = 60

_GAP_PX = 14

_BLAME_DEBOUNCE_MS = 600


def _relative_time(timestamp: int) -> str:
    """Converte timestamp Unix em string de tempo relativo legível.

    Args:
        timestamp: Timestamp Unix (segundos desde epoch).

    Returns:
        str: String de tempo relativo (ex: 'há 3 dias', 'há 2 meses').
    """
    now = datetime.now(tz=UTC).timestamp()
    delta = int(now - timestamp)
    if delta < 60:
        return _t("blame.just_now", "agora mesmo")
    if delta < 3600:
        m = delta // 60
        return _t("blame.minutes_ago", "{n}min atrás", n=m)
    if delta < 86400:
        h = delta // 3600
        return _t("blame.hours_ago", "{n}h atrás", n=h)
    if delta < 86400 * 30:
        d = delta // 86400
        return _t("blame.days_ago", "{n} dias atrás", n=d)
    if delta < 86400 * 365:
        mo = delta // (86400 * 30)
        return _t("blame.months_ago", "{n} meses atrás", n=mo)
    y = delta // (86400 * 365)
    return _t("blame.years_ago", "{n} anos atrás", n=y)


def _parse_blame_output(raw: str) -> dict[int, dict]:
    """Parseia a saída de ``git blame --porcelain`` em um dicionário por linha.

    Args:
        raw: Saída bruta do comando git blame --porcelain.

    Returns:
        dict[int, dict]: Mapeamento de linha_1based -> {author, hash, timestamp, summary}.
    """
    result: dict[int, dict] = {}
    current_hash = ""
    current_meta: dict = {}
    current_line = 0

    for line in raw.splitlines():
        header_match = re.match(
            r"^([0-9a-f]{40})\s+\d+\s+(\d+)(?:\s+\d+)?$", line)
        if header_match:
            current_hash = header_match.group(1)[:7]
            current_line = int(header_match.group(2))
            current_meta = {"hash": current_hash}
            continue

        if line.startswith("author ") and "hash" in current_meta:
            current_meta["author"] = line[7:].strip()
        elif line.startswith("author-time ") and "hash" in current_meta:
            with contextlib.suppress(ValueError):
                current_meta["timestamp"] = int(line[12:].strip())
        elif line.startswith("summary ") and "hash" in current_meta:
            current_meta["summary"] = line[8:].strip()
        elif line.startswith("\t") and current_line > 0 and "hash" in current_meta:
            result[current_line] = dict(current_meta)
            current_line = 0

    return result


class InlineBlame:
    """Sistema de blame Git inline para o editor do Codis.

    Exibe anotações de autoria Git como texto fantasma sobreposto ao
    código - nunca como conteúdo real do arquivo. As anotações aparecem
    ao lado direito do código, com estilo visual discreto (itálico, cor
    apagada), e são implementadas com ``tk.Label`` flutuantes posicionados
    via ``place()`` sobre o widget de texto, reaproveitados a cada
    re-renderização.

    Args:
        txt: Widget tk.Text do editor.
        theme: Tema visual ativo.
        mode: Modo de exibição - ``'cursor'`` (só linha atual) ou ``'all'`` (todas visíveis).
    """

    def __init__(
        self,
        txt: tk.Text,
        theme: Theme,
        mode: str = "cursor",
    ) -> None:
        """Inicializa o sistema de blame inline.

        Args:
            txt: Widget tk.Text do editor.
            theme: Objeto Theme com as cores do tema ativo.
            mode: Modo de exibição ('cursor' ou 'all').
        """
        self._txt = txt
        self._theme = theme
        self._mode = mode
        self._file_path: Path | None = None
        self._git_root: Path | None = None
        self._blame_cache: dict[int, dict] = {}
        self._enabled = False
        self._after_id: str | None = None
        self._worker_thread: threading.Thread | None = None

        self._labels: dict[int, tk.Label] = {}
        self._idle_labels: list[tk.Label] = []

        self._configure_widget_bindings()

    def _configure_widget_bindings(self) -> None:
        """Reposiciona/oculta o blame fantasma quando o widget muda de tamanho.

        Sem isso, redimensionar a janela (ou o painel do editor) deixaria
        os balões fantasma desalinhados em relação ao texto até a próxima
        renderização espontânea.
        """
        with contextlib.suppress(tk.TclError):
            self._txt.bind("<Configure>", self._on_configure, add=True)

    def _on_configure(self, _event: tk.Event) -> None:
        if self._enabled and self._blame_cache:
            self._schedule_render()

    def set_file(self, path: Path | None, git_root: Path | None) -> None:
        """Define o arquivo atual e a raiz Git para execução do blame.

        Args:
            path: Caminho absoluto do arquivo aberto no editor.
            git_root: Raiz do repositório Git (onde .git está).
        """
        if path != self._file_path:
            self._file_path = path
            self._git_root = git_root
            self._blame_cache.clear()
            self.clear()
            if self._enabled and path:
                self._fetch_blame_async()

    def set_enabled(self, enabled: bool) -> None:
        """Habilita ou desabilita o blame inline.

        Args:
            enabled: True para ativar, False para desativar e limpar.
        """
        self._enabled = enabled
        if enabled:
            if self._file_path:
                self._fetch_blame_async()
        else:
            self.clear()
            self._blame_cache.clear()

    def set_mode(self, mode: str) -> None:
        """Altera o modo de exibição do blame.

        Args:
            mode: 'cursor' para apenas a linha atual, 'all' para todas visíveis.
        """
        self._mode = mode
        self.clear()
        if self._enabled:
            self._schedule_render()

    def notify_cursor_moved(self) -> None:
        """Notifica que o cursor se moveu - atualiza o blame se modo for 'cursor'.

        Deve ser chamado pelo EditorPane a cada movimento de cursor.
        """
        if self._enabled and self._mode == "cursor":
            self._schedule_render()

    def notify_scroll(self) -> None:
        """Notifica que o editor rolou - re-renderiza blame no modo 'all'.

        Deve ser chamado pelo EditorPane ao detectar rolagem.
        """
        if self._enabled and self._mode == "all":
            self._schedule_render()

    def clear(self) -> None:
        """Esconde todas as anotações de blame do editor.

        Apenas chama ``place_forget()`` nos labels fantasma - nunca toca
        no conteúdo do ``tk.Text``, então não há nada para "duplicar" ou
        deixar para trás entre uma renderização e outra.
        """
        with contextlib.suppress(tk.TclError):
            for lbl in self._labels.values():
                lbl.place_forget()
                self._idle_labels.append(lbl)
        self._labels.clear()

    def _fetch_blame_async(self) -> None:
        """Executa git blame em thread separada e atualiza o cache."""
        if not self._file_path or not self._git_root:
            return
        if self._worker_thread and self._worker_thread.is_alive():
            return

        file_path = self._file_path
        git_root = self._git_root

        def _worker() -> None:
            """Thread worker para git blame."""
            try:
                result = subprocess.run(
                    ["git", "blame", "--porcelain", str(file_path)],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    cwd=str(git_root),
                    timeout=20,
                    check=False,
                )
                if result.returncode == 0:
                    cache = _parse_blame_output(result.stdout)

                    self._txt.after(0, lambda c=cache: self._on_blame_ready(c))
            except (OSError, FileNotFoundError) as exc:
                logger.debug("InlineBlame fetch error: %s", exc)

        self._worker_thread = threading.Thread(target=_worker, daemon=True)
        self._worker_thread.start()

    def _on_blame_ready(self, cache: dict[int, dict]) -> None:
        """Callback chamado na main thread quando o blame termina.

        Args:
            cache: Dicionário linha -> metadados de blame.
        """
        self._blame_cache = cache
        if self._enabled:
            self._render()

    def _schedule_render(self) -> None:
        """Agenda renderização com debounce."""
        if self._after_id:
            with contextlib.suppress(tk.TclError):
                self._txt.after_cancel(self._after_id)
        self._after_id = self._txt.after(_BLAME_DEBOUNCE_MS, self._render)

    def _render(self) -> None:
        """Renderiza as anotações de blame no editor."""
        self._after_id = None
        if not self._txt.winfo_exists():
            return
        if not self._enabled or not self._blame_cache:
            self.clear()
            return

        wanted_lines: list[int]
        if self._mode == "cursor":
            wanted_lines = self._cursor_line()
        else:
            wanted_lines = self._visible_lines()

        wanted_set = set(wanted_lines)
        for line_num in list(self._labels.keys()):
            if line_num not in wanted_set:
                lbl = self._labels.pop(line_num)
                with contextlib.suppress(tk.TclError):
                    lbl.place_forget()
                self._idle_labels.append(lbl)

        for line_num in wanted_lines:
            self._annotate_line(line_num)

    def _cursor_line(self) -> list[int]:
        """Retorna a linha atual do cursor, se houver blame para ela."""
        try:
            idx = self._txt.index("insert")
            line_num = int(idx.split(".")[0])
        except (tk.TclError, ValueError):
            return []
        return [line_num] if line_num in self._blame_cache else []

    def _visible_lines(self) -> list[int]:
        """Retorna as linhas visíveis no viewport que possuem blame."""
        try:
            first = int(self._txt.index("@0,0").split(".")[0])
            last_idx = self._txt.index(f"@0,{self._txt.winfo_height()}")
            last = int(last_idx.split(".")[0])
        except (tk.TclError, ValueError):
            return []

        max_blame_line = max(self._blame_cache.keys(), default=0)
        return [
            ln
            for ln in range(first, min(last + 1, max_blame_line + 1))
            if ln in self._blame_cache
        ]

    def _take_label(self) -> tk.Label:
        """Obtém um label do pool ocioso, ou cria um novo se necessário."""
        if self._idle_labels:
            return self._idle_labels.pop()
        t = self._theme
        return tk.Label(
            self._txt,
            bg=getattr(t, "editor_bg", t.bg),
            fg=t.fg_muted,
            font=(FONT_UI, max(FONT_SIZE - 2, 7), "italic"),
            bd=0,
            padx=0,
            pady=0,
        )

    def _annotate_line(self, line_num: int) -> None:
        """Posiciona o texto fantasma de blame ao final de uma linha.

        Usa ``dlineinfo()`` para descobrir a posição em pixels do fim do
        texto da linha e sobrepõe um ``tk.Label`` ali com ``place()`` -
        sem inserir nenhum caractere no buffer do editor.

        Args:
            line_num: Número da linha (1-based) a anotar.
        """
        meta = self._blame_cache.get(line_num)
        if not meta:
            return

        author = meta.get("author", "?")
        commit_hash = meta.get("hash", "???????")
        timestamp = meta.get("timestamp", 0)
        rel_time = _relative_time(timestamp) if timestamp else "?"

        if author.lower() in ("not committed yet", ""):
            blame_text = _t("blame.uncommitted", "⟐ não commitado")
        else:
            max_author = min(len(author), 20)
            blame_text = f"⟐ {author[:max_author]}  {commit_hash}  {rel_time}"
            if len(blame_text) > _BLAME_MAX_LEN:
                blame_text = blame_text[:_BLAME_MAX_LEN] + "..."

        try:
            end_of_line = f"{line_num}.end"
            info = self._txt.dlineinfo(end_of_line)
            if info is None:
                if line_num in self._labels:
                    lbl = self._labels.pop(line_num)
                    lbl.place_forget()
                    self._idle_labels.append(lbl)
                return

            x, y, width, _height, _baseline = info
            pos_x = x + width + _GAP_PX

            lbl = self._labels.get(line_num)
            if lbl is None:
                lbl = self._take_label()
                self._labels[line_num] = lbl

            t = self._theme
            lbl.config(
                text=blame_text,
                bg=getattr(t, "editor_bg", t.bg),
                fg=t.fg_muted,
            )
            lbl.place(in_=self._txt, x=pos_x, y=y, anchor="nw")
            lbl.lift()
        except tk.TclError:
            pass

    def apply_theme(self, theme: Theme) -> None:
        """Aplica novo tema às anotações de blame.

        Args:
            theme: Novo objeto Theme com as cores atualizadas.
        """
        self._theme = theme
        if self._enabled:
            self.clear()
            self._schedule_render()

    def destroy(self) -> None:
        """Cancela timers, esconde labels e libera os widgets do pool."""
        if self._after_id:
            with contextlib.suppress(tk.TclError):
                self._txt.after_cancel(self._after_id)
        self.clear()
        for lbl in self._idle_labels:
            with contextlib.suppress(tk.TclError):
                lbl.destroy()
        self._idle_labels.clear()
