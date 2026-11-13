"""Codis - escala global da interface (UI scale).

Resolve o problema de a interface ficar grande demais em telas pequenas (ou
pequena demais em telas grandes) e o slider de tamanho não ter efeito.

Como funciona
-------------
* ``ui_scale`` (config) é um percentual inteiro (50-250). ``0`` = automático,
  calculado a partir da resolução e do DPI reais da tela.
* O Tk guarda fontes em cache pela descrição (``{Segoe UI} 9``), portanto mudar
  ``tk scaling`` em tempo de execução NÃO afeta widgets existentes nem novos.
  Por isso a escala é aplicada reconfigurando a fonte de cada widget com o
  tamanho multiplicado, guardando o tamanho-base original para que aplicações
  repetidas nunca se acumulem.
* Widgets criados depois (diálogos, painéis lazy) são capturados por um bind
  global em ``<Map>``.
* Widgets cujo dono gerencia a própria fonte (editor, terminal) devem ser
  marcados com :func:`exclude_from_scale`.
* :func:`px` converte medidas em pixels fixos para o fator atual.
"""

from __future__ import annotations

import contextlib
import logging
import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk
from typing import Any

logger = logging.getLogger("Codis.UIScale")

MIN_PERCENT = 50
MAX_PERCENT = 250
AUTO_MIN = 70
AUTO_MAX = 160
STEP = 10

_STD_FONTS = (
    "TkDefaultFont",
    "TkTextFont",
    "TkMenuFont",
    "TkHeadingFont",
    "TkCaptionFont",
    "TkSmallCaptionFont",
    "TkIconFont",
    "TkTooltipFont",
)
_MIN_PT = 6
_BASE_ATTR = "_codis_base_font"
_APPLIED_ATTR = "_codis_applied_font"
_GEN_ATTR = "_codis_scale_gen"
_SKIP_ATTR = "_codis_no_ui_scale"
_DIM_BASE_ATTR = "_codis_base_dims"
_DIM_APPLIED_ATTR = "_codis_applied_dims"
_DIM_MIN_BASE = 3

_factor: float = 1.0


def clamp_percent(value: Any) -> int:
    """Converte ``value`` em percentual válido (0 = automático)."""
    try:
        pct = int(float(value))
    except (TypeError, ValueError):
        return 0
    if pct <= 0:
        return 0
    return max(MIN_PERCENT, min(MAX_PERCENT, pct))


def px(value: int | float) -> int:
    """Escala uma medida em pixels fixos para o fator atual."""
    return max(1, round(value * _factor))


def exclude_from_scale(widget: tk.Misc) -> None:
    """Marca ``widget`` para que a escala global não altere sua fonte."""
    setattr(widget, _SKIP_ATTR, True)


def auto_percent(root: tk.Misc) -> int:
    """Percentual automático a partir da altura útil da tela e do DPI.

    Referência: 1080 px de altura a 96 DPI = 100 %. Telas pequenas (ex.:
    1366x768) reduzem; telas grandes sem escala do SO aumentam. O resultado é
    limitado a ``AUTO_MIN``-``AUTO_MAX`` e arredondado em passos de 5.
    """
    try:
        height = root.winfo_screenheight()
        dpi = float(root.winfo_fpixels("1i"))
    except tk.TclError:
        return 100
    os_zoom = max(0.75, dpi / 96.0)
    pct = height / (1080.0 * os_zoom) * 100.0
    pct = round(pct / 5.0) * 5
    return int(max(AUTO_MIN, min(AUTO_MAX, pct)))


def fit_window(root: tk.Tk, width: int, height: int, margin: int = 80) -> tuple[int, int]:
    """Limita ``width x height`` à área útil da tela (descontando ``margin``)."""
    try:
        sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
    except tk.TclError:
        return width, height
    return max(320, min(width, sw - 16)), max(240, min(height, sh - margin))


def _parse_font(root: tk.Misc, spec: Any) -> tuple[str, int, tuple[str, ...]] | None:
    """Decompõe ``spec`` em (família, tamanho, estilos); ``None`` se nomeada."""
    try:
        parts = root.tk.splitlist(spec)
    except tk.TclError:
        return None
    if len(parts) < 2:
        return None
    try:
        size = int(float(parts[1]))
    except (TypeError, ValueError):
        return None
    return str(parts[0]), size, tuple(str(p) for p in parts[2:])


def _scaled_size(base: int, factor: float) -> int:
    magnitude = max(_MIN_PT, round(abs(base) * factor))
    return -magnitude if base < 0 else magnitude


class UIScaler:
    """Aplica e mantém a escala da UI para uma janela raiz."""

    def __init__(self, root: tk.Tk, cfg: Any) -> None:
        self._root = root
        self._cfg = cfg
        self._percent = 100
        self._gen = 0
        self._style_base: dict[str, int] = {}
        self._named_base: dict[str, tuple[str, int]] = {}
        self._bound = False

    @property
    def percent(self) -> int:
        """Percentual efetivo em uso."""
        return self._percent

    def resolve(self) -> int:
        """Percentual efetivo a partir da configuração (resolve o modo auto)."""
        pct = clamp_percent(self._cfg.get("ui_scale", 0))
        return pct or auto_percent(self._root)

    def start(self) -> None:
        """Aplica a escala configurada e passa a capturar novos widgets."""
        self.set_percent(self.resolve())
        if not self._bound:
            with contextlib.suppress(tk.TclError):
                self._root.bind_all("<Map>", self._on_map, add="+")
                self._bound = True

    def step(self, direction: int) -> int:
        """Aumenta/diminui em ``STEP`` pontos, persiste e aplica."""
        target = max(MIN_PERCENT, min(MAX_PERCENT, self._percent + direction * STEP))
        self._cfg.set("ui_scale", target)
        self.set_percent(target)
        return target

    def reset(self) -> int:
        """Volta ao modo automático."""
        self._cfg.set("ui_scale", 0)
        self.set_percent(self.resolve())
        return self._percent

    def on_config_change(self, value: Any) -> None:
        """Reage a alteração de ``ui_scale`` vinda do slider de configurações."""
        pct = clamp_percent(value)
        self.set_percent(pct or auto_percent(self._root))

    def set_percent(self, percent: int) -> None:
        """Aplica ``percent`` a fontes nomeadas, estilos ttk e toda a árvore."""
        global _factor
        percent = max(MIN_PERCENT, min(MAX_PERCENT, int(percent)))
        _factor = percent / 100.0
        self._percent = percent
        self._gen += 1
        self._scale_named_fonts()
        self._scale_styles()
        self._scale_tree(self._root)
        with contextlib.suppress(tk.TclError):
            self._root.update_idletasks()

    def _scale_named_fonts(self) -> None:
        for name in _STD_FONTS:
            try:
                font = tkfont.nametofont(name, root=self._root)
            except tk.TclError:
                continue
            if name not in self._named_base:
                self._named_base[name] = (font.actual("family"), int(font.cget("size")))
            _family, base = self._named_base[name]
            with contextlib.suppress(tk.TclError):
                font.configure(size=_scaled_size(base, _factor))

    def _scale_styles(self) -> None:
        try:
            style = ttk.Style(self._root)
            current = style.lookup("Treeview", "rowheight")
        except tk.TclError:
            return
        if "Treeview" not in self._style_base:
            try:
                base = int(current) if current else 22
            except (TypeError, ValueError):
                base = 22
            self._style_base["Treeview"] = base
        base = self._style_base["Treeview"]
        with contextlib.suppress(tk.TclError):
            style.configure("Treeview", rowheight=px(base))

    def reapply_styles(self) -> None:
        """Reaplica estilos ttk (chamar após troca de tema, que redefine rowheight)."""
        self._style_base.pop("Treeview", None)
        self._style_base["Treeview"] = 22
        self._scale_styles()

    def _scale_dims(self, widget: tk.Misc) -> None:
        """Escala height/width de frames de tamanho fixo (``propagate`` falso).

        Barras como status, abas e atividade usam ``pack_propagate(False)`` com
        altura em pixels; sem isso o texto escalado seria cortado (ou sobraria
        espaço) ao mudar a escala.
        """
        if not isinstance(widget, (tk.Frame, tk.LabelFrame)):
            return
        try:
            if widget.pack_propagate() and widget.grid_propagate():
                return
            cur = (int(widget.cget("width")), int(widget.cget("height")))
        except (tk.TclError, TypeError, ValueError):
            return
        base = getattr(widget, _DIM_BASE_ATTR, None)
        if base is None or cur != getattr(widget, _DIM_APPLIED_ATTR, None):
            base = cur
            setattr(widget, _DIM_BASE_ATTR, base)
        new = tuple(px(v) if v >= _DIM_MIN_BASE else v for v in base)
        try:
            widget.configure(width=new[0], height=new[1])
        except tk.TclError:
            return
        setattr(widget, _DIM_APPLIED_ATTR, new)

    def _scale_widget(self, widget: tk.Misc) -> None:
        if getattr(widget, _SKIP_ATTR, False):
            return
        if getattr(widget, _GEN_ATTR, -1) == self._gen:
            return
        self._scale_dims(widget)
        try:
            if "font" not in widget.keys():  # noqa: SIM118
                setattr(widget, _GEN_ATTR, self._gen)
                return
            current = str(widget.cget("font"))
        except tk.TclError:
            return
        base = getattr(widget, _BASE_ATTR, None)
        if base is None or current != getattr(widget, _APPLIED_ATTR, None):
            parsed = _parse_font(self._root, current)
            if parsed is None:
                setattr(widget, _GEN_ATTR, self._gen)
                return
            base = parsed
            setattr(widget, _BASE_ATTR, base)
        family, size, styles = base
        new_spec = (family, _scaled_size(size, _factor), *styles)
        try:
            widget.configure(font=new_spec)
            setattr(widget, _APPLIED_ATTR, str(widget.cget("font")))
        except tk.TclError:
            return
        setattr(widget, _GEN_ATTR, self._gen)

    def _scale_tree(self, widget: tk.Misc) -> None:
        stack = [widget]
        while stack:
            node = stack.pop()
            if isinstance(node, ttk.Widget) is False:
                self._scale_widget(node)
            try:
                stack.extend(node.winfo_children())
            except tk.TclError:
                continue

    def _on_map(self, event: tk.Event) -> None:
        widget = event.widget
        if isinstance(widget, tk.Misc):
            self._scale_widget(widget)
