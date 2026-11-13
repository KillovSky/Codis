"""Codis - Ligatures: motor de ligaturas pseudo via destaque visual.

Modo 'pseudo': detecta sequências ASCII comuns (ex: ->, !=, >=) nas linhas
visíveis e aplica uma tag de destaque colorido, sinalizando que a sequência
seria renderizada como ligatura em fontes compatíveis.

Modo 'native': delega ao sistema de fontes (Tk + HarfBuzz) - configurado em pane.py.
Modo 'both': aplica destaque pseudo APÓS ativar ligaturas nativas na fonte.
Modo 'off': nenhuma substituição.

O texto bruto (ASCII) é sempre preservado - salvo e copiado normalmente.
A engine opera apenas nas linhas visíveis para mínimo impacto na performance.
"""

from __future__ import annotations

import contextlib
import tkinter as tk

LIGATURE_PAIRS: list[tuple[str, str]] = [
    ("==>", "⟹"),
    ("<=>", "⟺"),
    ("-->", "⟶"),
    ("<--", "⟵"),
    ("->>", "↠"),
    ("->", "->"),
    ("<-", "<-"),
    ("=>", "⇒"),
    ("!==", "≢"),
    ("===", "≡"),
    ("!=", "≠"),
    (">=", "≥"),
    ("<=", "≤"),
    ("&&", "∧"),
    ("||", "∨"),
    ("...", "..."),
    ("::", "∷"),
    ("<>", "◇"),
]
"""Sequências ASCII reconhecidas e seus substitutos Unicode (tooltip/badge).

Ordenadas do padrão mais longo para o mais curto, garantindo que o match
guloso em :meth:`LigatureEngine._apply_line` priorize sempre a sequência
mais específica disponível em cada posição da linha.
"""

_TAG_PFX = "codis_lig_"


class LigatureEngine:
    """Motor de ligaturas pseudo para tk.Text.

    Realça visualmente as sequências de ligatura nas linhas visíveis.
    O texto fonte não é modificado.

    Args:
        text: Widget tk.Text alvo.
        accent_color: Cor de destaque para as sequências reconhecidas.
    """

    def __init__(self, text: tk.Text, accent_color: str = "#cba6f7") -> None:
        """Inicializa o motor de ligaduras tipográficas."""
        self._text = text
        self._accent = accent_color
        self._enabled = False
        self._job: str | None = None
        self._setup_tags()

    def _for_each_tag(self, action) -> None:
        """Executa ``action(tag)`` para a tag de cada sequência de ligatura.

        Centraliza a iteração sobre :data:`LIGATURE_PAIRS` e a supressão
        de ``TclError``, usada de forma idêntica ao configurar, remover
        ou recolorir as tags de destaque.

        Args:
            action: Callable que recebe o nome da tag e aplica a operação
                desejada no widget de texto.
        """
        for seq, _ in LIGATURE_PAIRS:
            tag = f"{_TAG_PFX}{seq}"
            with contextlib.suppress(tk.TclError):
                action(tag)

    def _setup_tags(self) -> None:
        """Cria tags de highlight para cada sequência de ligatura."""
        self._for_each_tag(
            lambda tag: self._text.tag_configure(tag, foreground=self._accent)
        )

    def set_enabled(self, enabled: bool) -> None:
        """Ativa ou desativa o motor."""
        was = self._enabled
        self._enabled = enabled
        if not enabled and was:
            self._clear_all()
        elif enabled and not was:
            self._schedule(0)

    def schedule(self, delay_ms: int = 80) -> None:
        """Agenda reaplicação com debounce (chamado pelo editor ao modificar/rolar)."""
        if self._enabled:
            self._schedule(delay_ms)

    def _schedule(self, delay_ms: int = 80) -> None:
        """Reagenda a próxima aplicação de ligaturas, cancelando a anterior."""
        if self._job:
            with contextlib.suppress(tk.TclError):
                self._text.after_cancel(self._job)
        self._job = self._text.after(delay_ms, self._apply_visible)

    def _apply_visible(self) -> None:
        """Aplica ligaturas apenas nas linhas visíveis (performance)."""
        self._job = None
        if not self._enabled:
            return
        try:
            height = self._text.winfo_height()
            top = int(self._text.index("@0,0").split(".")[0])
            bot = int(self._text.index(
                f"@0,{max(height - 1, 1)}").split(".")[0])
        except (tk.TclError, ValueError):
            return

        top = max(1, top - 3)
        bot = bot + 3

        self._clear_region(f"{top}.0", f"{bot}.end")

        for ln in range(top, bot + 1):
            try:
                line_text = self._text.get(f"{ln}.0", f"{ln}.end")
            except tk.TclError:
                break
            if not line_text:
                continue
            self._apply_line(ln, line_text)

    def _apply_line(self, ln: int, line: str) -> None:
        """Aplica highlight de ligaturas em uma linha (match guloso)."""
        occupied: set[int] = set()

        for seq, _ in LIGATURE_PAIRS:
            slen = len(seq)
            pos = 0
            while True:
                idx = line.find(seq, pos)
                if idx == -1:
                    break
                region = set(range(idx, idx + slen))
                if region & occupied:
                    pos = idx + 1
                    continue
                occupied.update(region)
                tag = f"{_TAG_PFX}{seq}"
                with contextlib.suppress(tk.TclError):
                    self._text.tag_add(
                        tag, f"{ln}.{idx}", f"{ln}.{idx + slen}")
                pos = idx + slen

    def _clear_region(self, start: str, end: str) -> None:
        """Remove todas as tags de ligatura de uma região."""
        self._for_each_tag(lambda tag: self._text.tag_remove(tag, start, end))

    def _clear_all(self) -> None:
        """Remove todas as tags de ligatura do documento inteiro."""
        self._clear_region("1.0", "end")

    def update_accent(self, color: str) -> None:
        """Atualiza a cor de destaque (chamado ao trocar tema)."""
        self._accent = color
        self._for_each_tag(
            lambda tag: self._text.tag_configure(tag, foreground=color))

    def on_change(self) -> None:
        """Deve ser chamado pelo editor ao modificar o conteúdo."""
        self.schedule(80)

    def on_scroll(self) -> None:
        """Deve ser chamado pelo editor ao rolar o conteúdo."""
        self.schedule(120)
