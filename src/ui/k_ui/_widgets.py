"""Widgets customizados da KUI (K-UI) do Codis.

Provê ``KGlassPane``, ``KTopBar``, ``KTabBar`` e demais componentes
visuais com efeito glassmorphism e animações fluidas usados na interface
do Codis.
"""

from __future__ import annotations

import contextlib
import datetime
import logging
import math
import random
import sys
import tkinter as tk
from pathlib import Path
from typing import Any

from ._colors import _hue, _lc_q, _pal, _safe_del, _wlift
from ._perf import _DEFAULT_PERF

try:
    from src.i18n import _t as _i18n_t
except ImportError:

    def _i18n_t(key: str, **kw) -> str:

        return key.split(".")[-1]

logger = logging.getLogger("Codis.KUI")
_TAU = math.tau


class KGlassPane:
    """Efeito de borda holográfica no editor sem criar widgets extras no layout.

    Nenhum widget novo é criado no _cf ou engine_parent. O efeito glass é
    aplicado diretamente no _ec (editor_container):
      1. highlightbackground animado: borda luminosa de 2px ao redor do _ec
      2. Faixa espectral de 2px (Canvas) dentro do _ec via place()
         - aparece acima do editor sem bloquear eventos de mouse (fina demais)
      3. TÁTICA #6: margens calculadas dinamicamente das barras reais
    """

    def __init__(self, dark: bool = True) -> None:
        """Inicializa o painel glassmorphism da KUI."""
        self._p = _pal(dark)
        self._dark = dark
        self._ph = random.uniform(0, _TAU)
        self._dead = False
        self._ec: tk.Widget | None = None
        self._spectrum: tk.Canvas | None = None
        self._anim_job: str | None = None
        self._seg_ids: list[int] = []
        self._seg_fill: list[str] = []
        self._seg_w = -1
        self._last_border = ""

    def attach(self, ec: tk.Widget) -> None:
        """Conecta o glass pane ao editor_container.

        Adiciona faixa espectral dentro de ec e começa a animar a borda.
        Deve ser chamado apos ec estar visivel (winfo_exists).
        """
        self._ec = ec
        if not ec or not ec.winfo_exists():
            return
        p = self._p
        void = p["void"]

        with contextlib.suppress(tk.TclError):
            if self._spectrum and self._spectrum.winfo_exists():
                self._spectrum.destroy()
            self._spectrum = tk.Canvas(
                ec, height=2, highlightthickness=0, bd=0, bg=void
            )

            self._spectrum.place(x=0, y=0, relwidth=1.0, height=2)
            _wlift(self._spectrum)
            self._seg_ids, self._seg_fill, self._seg_w = [], [], -1

        self._animate()

    _SEGMENTS = 48
    _FRAME_MS = 66
    _HIDDEN_MS = 400
    _UNFOCUSED_MS = 120

    def _ensure_segments(self, cv: tk.Canvas, w: int) -> None:
        """Cria os retângulos da faixa uma única vez (refaz só se a largura mudar)."""
        if self._seg_w == w and self._seg_ids:
            return
        cv.delete("all")
        n = self._SEGMENTS
        self._seg_ids = [
            cv.create_rectangle(
                int(i * w / n), 0, int((i + 1) * w / n) + 1, 2,
                fill=self._p["void"], outline="",
            )
            for i in range(n)
        ]
        self._seg_fill = [""] * n
        self._seg_w = w

    def _animate(self) -> None:
        """Executa um frame da animação da borda e agenda o próximo.

        Reaproveita os itens do canvas (``itemconfigure``) em vez de
        recriá-los, só repinta o que mudou e reduz a cadência quando a janela
        está oculta ou sem foco.
        """
        if self._dead:
            return
        ec = self._ec
        if not (ec and ec.winfo_exists()):
            return
        try:
            visible = bool(ec.winfo_viewable())
        except tk.TclError:
            return
        delay = self._FRAME_MS
        if not visible:
            delay = self._HIDDEN_MS
        else:
            with contextlib.suppress(tk.TclError, KeyError):
                if ec.focus_displayof() is None:
                    delay = self._UNFOCUSED_MS
            self._ph = (self._ph + 0.022 * delay / 38.0) % _TAU
            p = self._p
            glow = 0.45 + 0.55 * abs(math.sin(self._ph))
            color = _lc_q(p["void"], p["accent"], glow * 0.85)
            if color != self._last_border:
                self._last_border = color
                with contextlib.suppress(tk.TclError):
                    ec.configure(highlightbackground=color, highlightthickness=2)
            cv = self._spectrum
            if cv and cv.winfo_exists():
                w = cv.winfo_width() or ec.winfo_width() or 800
                self._ensure_segments(cv, w)
                n = self._SEGMENTS
                base = int(self._ph * 180 / math.pi)
                for i, item in enumerate(self._seg_ids):
                    r2, g2, b2 = _hue((base + i * 360 // n) % 360)
                    fill = f"#{r2:02x}{g2:02x}{b2:02x}"
                    if fill != self._seg_fill[i]:
                        self._seg_fill[i] = fill
                        with contextlib.suppress(tk.TclError):
                            cv.itemconfigure(item, fill=fill)
        self._anim_job = ec.after(delay, self._animate)

    def detach(self) -> None:
        """Remove efeito glass do editor (restaura aparencia padrao)."""
        self._dead = True
        if self._anim_job:
            with contextlib.suppress(tk.TclError):
                if self._ec:
                    self._ec.after_cancel(self._anim_job)
            self._anim_job = None
        with contextlib.suppress(tk.TclError):
            if self._spectrum and self._spectrum.winfo_exists():
                self._spectrum.place_forget()
                self._spectrum.destroy()
        with contextlib.suppress(tk.TclError):
            if self._ec and self._ec.winfo_exists():
                self._ec.configure(highlightthickness=0)
        self._spectrum = None
        self._ec = None

    def destroy(self) -> None:
        """Equivale a :meth:`detach`."""
        self.detach()


class KTopBar(tk.Canvas):
    """Barra superior do K-UI com título, ações e suporte a arrastar a janela."""

    def __init__(self, parent, on_portal, get_editor, dark=True, profile=_DEFAULT_PERF):
        """Inicializa a barra de topo K-UI com gradiente espectral animado.

        Args:
            parent: Widget pai Tkinter.
            on_portal: Callback invocado ao clicar no centro da barra (abre portal).
            get_editor: Callable sem argumentos que retorna o editor ativo.
            dark: True para paleta escura.
            profile: Perfil de desempenho atual (exibido como badge).
        """
        p = _pal(dark)
        super().__init__(
            parent, bg=p["top"], height=22, highlightthickness=0, bd=0, cursor="hand2"
        )
        self._p = p
        self._portal_cb = on_portal
        self._get_ed = get_editor
        self._dead = False
        self._ph = random.uniform(0, _TAU)
        self._glow_ph = random.uniform(0, _TAU)
        self._file_text = ""
        self._diag = "ok"
        self._diag_col = p["ok"]
        self._modified = False
        self._clock = datetime.datetime.now().strftime("%H:%M")
        self._profile = profile
        self._accent_ov = None
        self._accent_job = None
        self.bind("<Button-1>", self._click)
        self._seg_ids = []
        self._seg_fill = []
        self._static_key = None
        self._line_id = self._badge_id = 0
        self._last_line = self._last_badge = ""
        self._tick_data()
        self._animate()

    def _tick_data(self):
        """Atualiza relógio, nome do arquivo e estado de modificação a cada 30 s."""
        if self._dead or not self.winfo_exists():
            return
        self._clock = datetime.datetime.now().strftime("%H:%M")
        ed = self._get_ed()
        if ed:
            path = getattr(ed, "filepath", None) or getattr(ed, "path", None)
            self._file_text = Path(path).name if path else ""
            self._modified = (
                getattr(ed, "modified", False)
                or getattr(ed, "is_modified", lambda: False)()
            )
        else:
            self._file_text = ""
        self._modified = False
        self.after(30_000, self._tick_data)

    _SEGMENTS = 64
    _FRAME_MS = 66
    _HIDDEN_MS = 400
    _UNFOCUSED_MS = 160
    _PROFILE_BADGE = {
        "ultra": "⬡ ULTRA",
        "high": "⬡ HIGH",
        "balanced": "⬡ BAL",
        "low": "⬡ LOW",
        "minimal": "⬡ LOW",
        "power_saver": "⬡ BAL",
        "quantum": "⬡ QTM",
    }

    def _animate(self):
        """Executa um frame da animação da barra de topo e agenda o próximo.

        Cadência adaptativa: pausa quando a barra não está visível e reduz
        quando a janela está sem foco (antes: 26 FPS fixos, sempre).
        """
        if self._dead or not self.winfo_exists():
            return
        delay = self._FRAME_MS
        try:
            visible = bool(self.winfo_viewable())
        except tk.TclError:
            return
        if not visible:
            delay = self._HIDDEN_MS
        else:
            with contextlib.suppress(tk.TclError, KeyError):
                if self.focus_displayof() is None:
                    delay = self._UNFOCUSED_MS
            k = delay / 38.0
            self._ph = (self._ph + 0.016 * k) % _TAU
            self._glow_ph = (self._glow_ph + 0.022 * k) % _TAU
            self._draw()
        self.after(delay, self._animate)

    def _rebuild(self, W, H, key):
        """(Re)cria os itens do canvas - só quando tamanho/texto/tema mudam."""
        p = self._p
        self.delete("all")
        self.create_rectangle(0, 0, W, H, fill=p["top"], outline="")
        n = self._SEGMENTS
        self._seg_ids = [
            self.create_rectangle(
                int(i * W / n), 0, int((i + 1) * W / n) + 1, 4, fill=p["top"], outline=""
            )
            for i in range(n)
        ]
        self._seg_fill = [""] * n
        accent = self._accent_ov or p["accent"]
        self._line_id = self.create_line(0, H - 1, W, H - 1, fill=p["top"], width=1)
        cy = (H + 4) // 2 + 1
        if self._file_text:
            dot = " ●" if self._modified else ""
            fc = accent if self._modified else p["fg_dim"]
            self.create_text(W // 2, cy, text=f"◈  {self._file_text}{dot}", fill=fc,
                             font=("Segoe UI", 8), anchor="center")
        else:
            self.create_text(W // 2, cy, text=_i18n_t("kui.topbar_hint"),
                             fill=p["fg_muted"], font=("Segoe UI", 7), anchor="center")
        self.create_text(W - 8, cy, text=f"{self._diag}  {self._clock}",
                         fill=self._diag_col, font=("Segoe UI", 7), anchor="e")
        self._badge_id = self.create_text(
            8, cy, text=self._PROFILE_BADGE.get(self._profile, "⬡"),
            fill=p["accent2"], font=("Segoe UI", 6, "bold"), anchor="w")
        self._static_key = key
        self._last_line = self._last_badge = ""

    def _draw(self):
        """Atualiza a barra de topo: refaz só o que mudou (itens reaproveitados)."""
        if not self.winfo_exists():
            return
        W = self.winfo_width()
        H = self.winfo_height()
        if W < 4:
            return
        p = self._p
        key = (W, H, id(p), self._file_text, self._modified, self._diag,
               self._diag_col, self._clock, self._profile, self._accent_ov)
        if key != self._static_key or not self._seg_ids:
            self._rebuild(W, H, key)
        n = self._SEGMENTS
        base = int(self._ph * 180 / math.pi)
        fills = self._seg_fill
        for i, item in enumerate(self._seg_ids):
            r2, g2, b2 = _hue((base + i * 360 // n) % 360)
            fill = f"#{r2:02x}{g2:02x}{b2:02x}"
            if fill != fills[i]:
                fills[i] = fill
                self.itemconfigure(item, fill=fill)
        accent = self._accent_ov or p["accent"]
        line = _lc_q(p["top"], accent, 0.20 + 0.24 * abs(math.sin(self._glow_ph)))
        if line != self._last_line:
            self._last_line = line
            self.itemconfigure(self._line_id, fill=line)
        badge = _lc_q(p["top"], p["accent2"], 0.40 + 0.16 * abs(math.sin(self._glow_ph * 0.7)))
        if badge != self._last_badge:
            self._last_badge = badge
            self.itemconfigure(self._badge_id, fill=badge)

    def _click(self, e):
        """Trata clique na barra: clique na área de relógio ativa easter egg de cor."""
        if e.x > self.winfo_width() - 90:
            self._easter_clock()
        else:
            self._portal_cb()

    def _easter_clock(self):
        """Ativa cor aleatória de acento por 10 s (easter egg)."""
        self._accent_ov = random.choice(
            ["#ff4081", "#00e5ff", "#69ff47", "#ffd740", "#ea80fc", "#00ffcc"]
        )
        if self._accent_job:
            with contextlib.suppress(tk.TclError):
                self.after_cancel(self._accent_job)
        self._accent_job = self.after(10_000, self._reset_accent)

    def _reset_accent(self):
        """Restaura a cor de acento padrão após o easter egg de relógio."""
        self._accent_ov = None
        self._accent_job = None

    def set_diagnostics(self, errors, warnings):
        """Atualiza o indicador de diagnósticos (erros/avisos) na barra de topo.

        Args:
            errors: Quantidade de erros de lint/LSP.
            warnings: Quantidade de avisos.
        """
        if not self.winfo_exists():
            return
        p = self._p
        if errors:
            self._diag, self._diag_col = f"!{errors}", p["err"]
        elif warnings:
            self._diag, self._diag_col = f"⚠{warnings}", p["warn"]
        else:
            self._diag, self._diag_col = "ok", p["ok"]

    def set_profile(self, profile: str) -> None:
        """Define o perfil de desempenho exibido como badge.

        Args:
            profile: Chave do perfil (ex: 'ultra', 'balanced', 'low').
        """
        self._profile = profile

    def refresh(self) -> None:
        """Força atualização imediata dos dados (nome de arquivo e relógio)."""
        self._tick_data()

    def stop(self) -> None:
        """Interrompe a animação da barra de topo."""
        self._dead = True


class KTabBar(tk.Canvas):
    """Barra de abas estilizada do K-UI com indicador animado e scroll horizontal."""

    def __init__(self, parent, on_select, on_close, dark=True):
        """Inicializa a barra de abas K-UI com abas animadas e menu de contexto.

        Args:
            parent: Widget pai Tkinter.
            on_select: Callback(idx) invocado ao selecionar uma aba.
            on_close: Callback(idx) invocado ao fechar uma aba (botão do meio).
            dark: True para paleta escura.
        """
        p = _pal(dark)
        super().__init__(parent, bg=p["void2"],
                         height=36, highlightthickness=0, bd=0)
        self._p = p
        self._on_select = on_select
        self._on_close = on_close
        self._dead = False
        self._ph = random.uniform(0, _TAU)
        self._tabs = []
        self._hover = -1

        self._on_minimize_planet: Any = None
        self._on_dual_mode: Any = None
        self.bind("<Button-1>", self._click)
        self.bind("<Button-2>", self._middle)
        self.bind("<Button-3>", self._right_click)
        self.bind("<Motion>", self._motion)
        self.bind("<Leave>", lambda _: self._set_hover(-1))
        self._animate()

    def set_callbacks(self, minimize_planet=None, dual_mode=None):
        """Define callbacks para o menu de contexto das abas."""
        self._on_minimize_planet = minimize_planet
        self._on_dual_mode = dual_mode

    def set_tabs(self, tabs):
        """Atualiza a lista de abas exibidas.

        Args:
            tabs: Lista de dicionários com chaves 'name', 'active' e 'modified'.
        """
        self._tabs = tabs

    def _animate(self):
        """Executa um frame da animação das abas e agenda o próximo."""
        if self._dead or not self.winfo_exists():
            return
        delay = 66
        try:
            visible = bool(self.winfo_viewable())
        except tk.TclError:
            return
        if not visible:
            delay = 400
        else:
            with contextlib.suppress(tk.TclError, KeyError):
                if self.focus_displayof() is None:
                    delay = 160
            self._ph = (self._ph + 0.028 * delay / 36.0) % _TAU
            self._draw()
        self.after(delay, self._animate)

    def _draw(self):
        """Redesenha todas as abas com gradiente de seleção e indicadores."""
        if not self.winfo_exists():
            return
        W = self.winfo_width()
        H = self.winfo_height()
        if W < 4:
            return
        p = self._p
        self.delete("all")
        self.create_rectangle(0, 0, W, H, fill=p["void2"], outline="")
        self.create_line(0, 0, W, 0, fill=_lc_q(
            p["void2"], p["accent"], 0.13), width=1)
        if not self._tabs:
            return
        ow = min(180, max(90, W // max(1, len(self._tabs))))
        cy = H // 2
        for i, tab in enumerate(self._tabs):
            x1 = i * ow
            x2 = x1 + ow - 2
            xc = (x1 + x2) // 2
            act = tab.get("active", False)
            mod = tab.get("modified", False)
            name = tab.get("name", "")
            if act:
                pulse = 0.60 + 0.40 * abs(math.sin(self._ph))
                self.create_rectangle(
                    x1,
                    1,
                    x2,
                    H - 1,
                    fill=_lc_q(p["void2"], p["accent"], 0.30 * pulse),
                    outline="",
                )
                self.create_rectangle(
                    x1 + 2,
                    3,
                    x2 - 2,
                    H - 3,
                    fill=_lc_q(p["void2"], p["accent"], 0.15 * pulse),
                    outline="",
                )
                self.create_line(
                    x1 + 2,
                    H - 1,
                    x2 - 2,
                    H - 1,
                    fill=_lc_q(
                        p["accent"], "#ffffff", 0.26 *
                        abs(math.sin(self._ph * 1.1))
                    ),
                    width=2,
                )
                self.create_line(x1, 0, x2, 0, fill=p["accent"], width=1)
                c_fg = p["fg"]
            elif i == self._hover:
                self.create_rectangle(
                    x1 + 1,
                    2,
                    x2,
                    H - 2,
                    fill=_lc_q(p["void2"], p["glass"], 0.7),
                    outline="",
                )
                c_fg = p["fg"]
            else:
                c_fg = p["fg_dim"]
            mc = max(4, (ow - 24) // 7)
            disp = name if len(name) <= mc else name[: mc - 1] + "..."
            if mod:
                disp = "● " + disp
            self.create_text(
                xc, cy, text=disp, fill=c_fg, font=("Segoe UI", 8), anchor="center"
            )
            if i > 0:
                self.create_line(
                    x1, 4, x1, H - 4, fill=_lc_q(p["void2"], p["accent"], 0.09), width=1
                )

    def _tab_at(self, x):
        """Retorna o índice da aba na posição x, ou -1 se nenhuma.

        Args:
            x: Coordenada horizontal em pixels.
        """
        if not self._tabs:
            return -1
        ow = min(180, max(90, self.winfo_width() // max(1, len(self._tabs))))
        idx = x // ow
        return idx if 0 <= idx < len(self._tabs) else -1

    def _click(self, e):
        """Seleciona a aba clicada com o botão esquerdo.

        Args:
            e: Evento de mouse Tkinter.
        """
        idx = self._tab_at(e.x)
        if idx >= 0:
            with contextlib.suppress(tk.TclError):
                self._on_select(idx)

    def _middle(self, e):
        """Fecha a aba clicada com o botão do meio.

        Args:
            e: Evento de mouse Tkinter.
        """
        idx = self._tab_at(e.x)
        if idx >= 0:
            with contextlib.suppress(tk.TclError):
                self._on_close(idx)

    def _motion(self, e):
        """Atualiza o estado de hover das abas ao mover o mouse.

        Args:
            e: Evento de mouse Tkinter.
        """
        self._set_hover(self._tab_at(e.x))

    def _set_hover(self, idx):
        """Define o índice da aba em hover e redesenha.

        Args:
            idx: Índice da aba em hover, ou -1 para nenhuma.
        """
        self._hover = idx

    def _right_click(self, e):
        """Menu de contexto com opções: fechar, dual mode, minimizar como planeta."""
        idx = self._tab_at(e.x)
        if idx < 0:
            return
        p = self._p
        menu = tk.Menu(
            self,
            tearoff=0,
            bg=p["glass"],
            fg=p["fg"],
            activebackground=p["accent"],
            activeforeground=p["void"],
            font=("Segoe UI", 9),
            relief="flat",
            bd=1,
        )
        name = (
            self._tabs[idx].get("name", f"Aba {idx + 1}")
            if idx < len(self._tabs)
            else "Aba"
        )
        menu.add_command(
            label=f"  ◈  {name}", state="disabled", font=("Segoe UI", 8, "bold")
        )
        menu.add_separator()
        menu.add_command(
            label=_i18n_t("hc.k_ui.fechar_aba"), command=lambda: self._on_close(idx)
        )
        menu.add_command(
            label="  o  " + _i18n_t("kui.minimize_planet"),
            command=lambda: (
                self._on_minimize_planet(
                    idx) if self._on_minimize_planet else None
            ),
        )
        menu.add_separator()
        menu.add_command(
            label="  ⊞  " + _i18n_t("kui.dual_mode"),
            command=lambda: self._on_dual_mode(
                idx) if self._on_dual_mode else None,
        )
        menu.add_separator()
        menu.add_command(
            label="  ✕  " + _i18n_t("kui.close_all_tabs"), command=self._close_all
        )
        try:
            menu.tk_popup(e.x_root, e.y_root)
        finally:
            menu.grab_release()

    def _close_all(self):
        """Fecha todas as abas após confirmação do usuário."""
        for i in range(len(self._tabs) - 1, -1, -1):
            with contextlib.suppress(tk.TclError):
                self._on_close(i)

    def stop(self):
        """Interrompe a animação da barra de abas."""
        self._dead = True


class KStatusBar(tk.Canvas):
    _VIM = {
        "NORMAL": ("#7c4dff", "#ffffff"),
        "INSERT": ("#00bcd4", "#000000"),
        "VISUAL": ("#ff9800", "#000000"),
        "V-LINE": ("#ff9800", "#000000"),
        "REPLACE": ("#f44336", "#ffffff"),
        "COMMAND": ("#263238", "#ffd740"),
    }

    def __init__(self, parent, get_editor, dark=True):
        """Inicializa a barra de status K-UI com indicadores animados.

        Args:
            parent: Widget pai Tkinter.
            dark: True para paleta escura.
        """
        p = _pal(dark)
        super().__init__(parent, bg=p["void2"],
                         height=24, highlightthickness=0, bd=0)
        self._p = p
        self._get_ed = get_editor
        self._dead = False
        self._ph = random.uniform(0, _TAU)
        self._energy_ph = random.uniform(0, _TAU)
        self._vim_text = ""
        self._vim_bg = ""
        self._vim_fg = ""
        self._pos = "Ln 1  Col 1"
        self._lang = ""
        self._modified = False
        self._fps_str = ""
        self._seg_ids = []
        self._seg_fill = []
        self._static_key = None
        self._vim_glow_id = self._vim_box_id = self._dot_id = 0
        self._last_dyn = ("", "", "")
        self._tick_data()
        self._animate()

    def _tick_data(self):
        """Atualiza periodicamente os dados exibidos na barra de status."""
        if self._dead or not self.winfo_exists():
            return
        ed = self._get_ed()
        if ed:
            with contextlib.suppress(tk.TclError):
                ln, col = ed.text.index("insert").split(".")
                self._pos = f"Ln {ln}  Col {int(col) + 1}"
            with contextlib.suppress(tk.TclError):
                self._lang = getattr(ed, "lang", "") or ""
            with contextlib.suppress(tk.TclError):
                self._modified = (
                    getattr(ed, "modified", False)
                    or getattr(ed, "is_modified", lambda: False)()
                )
        self.after(200, self._tick_data)

    _SEGMENTS = 48
    _FRAME_MS = 66
    _HIDDEN_MS = 400
    _UNFOCUSED_MS = 160

    def _animate(self):
        """Executa um frame da animação da barra de status e agenda o próximo.

        Cadência adaptativa (pausa oculta, reduz sem foco); antes eram 24 FPS
        fixos recriando todos os itens do canvas a cada frame.
        """
        if self._dead or not self.winfo_exists():
            return
        delay = self._FRAME_MS
        try:
            visible = bool(self.winfo_viewable())
        except tk.TclError:
            return
        if not visible:
            delay = self._HIDDEN_MS
        else:
            with contextlib.suppress(tk.TclError, KeyError):
                if self.focus_displayof() is None:
                    delay = self._UNFOCUSED_MS
            k = delay / 42.0
            self._ph = (self._ph + 0.050 * k) % _TAU
            self._energy_ph = (self._energy_ph + 0.026 * k) % _TAU
            self._draw()
        self.after(delay, self._animate)

    def _rebuild(self, W, H, key):
        """(Re)cria os itens do canvas - só quando o conteúdo/tamanho/tema muda."""
        p = self._p
        self.delete("all")
        self.create_rectangle(0, 0, W, H, fill=p["void2"], outline="")
        n = self._SEGMENTS
        self._seg_ids = [
            self.create_rectangle(
                int(i * W / n), 0, int((i + 1) * W / n) + 1, 2, fill=p["void2"], outline=""
            )
            for i in range(n)
        ]
        self._seg_fill = [""] * n
        self._vim_glow_id = self._vim_box_id = self._dot_id = 0
        cy = H // 2 + 2
        x = 8
        if self._vim_text:
            m_bg = self._vim_bg or p["accent"]
            m_fg = self._vim_fg or p["void"]
            pw2 = max(56, len(self._vim_text) * 7 + 14)
            self._vim_glow_id = self.create_rectangle(
                x - 2, 2, x + pw2 + 2, H - 2, fill=p["void2"], outline="")
            self._vim_box_id = self.create_rectangle(
                x, 3, x + pw2, H - 3, fill=m_bg, outline=m_bg)
            self.create_text((2 * x + pw2) // 2, cy, text=self._vim_text, fill=m_fg,
                             font=("Segoe UI", 7, "bold"), anchor="center")
            x += pw2 + 12
        self.create_text(x, cy, text=self._pos, fill=p["fg_dim"],
                         font=("Segoe UI", 7), anchor="w")
        x += len(self._pos) * 5 + 14
        if self._lang:
            self.create_line(x, 4, x, H - 4, fill=_lc_q(p["void2"], p["accent"], 0.18), width=1)
            x += 8
            self.create_text(x, cy, text=self._lang, fill=p["fg_dim"],
                             font=("Segoe UI", 7), anchor="w")
        if self._fps_str:
            self.create_text(W - 48, cy, text=self._fps_str,
                             fill=_lc_q(p["void2"], p["quantum"], 0.38),
                             font=("Segoe UI", 6), anchor="w")
        if self._modified:
            self._dot_id = self.create_text(W - 10, cy, text="●", fill=p["warn"],
                                            font=("Segoe UI", 9), anchor="e")
        self._static_key = key
        self._last_dyn = ("", "", "")

    def _draw(self):
        """Atualiza a barra de status: refaz só o que mudou (itens reaproveitados)."""
        if not self.winfo_exists():
            return
        W = self.winfo_width()
        H = self.winfo_height()
        if W < 4:
            return
        p = self._p
        key = (W, H, id(p), self._vim_text, self._vim_bg, self._vim_fg, self._pos,
               self._lang, self._fps_str, self._modified)
        if key != self._static_key or not self._seg_ids:
            self._rebuild(W, H, key)
        n = self._SEGMENTS
        base = int(self._energy_ph * 180 / math.pi)
        fills = self._seg_fill
        for i, item in enumerate(self._seg_ids):
            r2, g2, b2 = _hue((base + i * 360 // n) % 360)
            fill = _lc_q(p["void2"], f"#{r2:02x}{g2:02x}{b2:02x}", 0.30)
            if fill != fills[i]:
                fills[i] = fill
                self.itemconfigure(item, fill=fill)
        glow_c = box_o = dot_c = ""
        if self._vim_glow_id:
            m_bg = self._vim_bg or p["accent"]
            glow = 0.18 + 0.22 * abs(math.sin(self._ph))
            glow_c = _lc_q(p["void2"], m_bg, glow * 0.36)
            box_o = _lc_q(m_bg, "#ffffff", glow * 0.46)
        if self._dot_id:
            dot_c = _lc_q(p["void2"], p["warn"], 0.42 + 0.48 * abs(math.sin(self._ph * 1.2)))
        dyn = (glow_c, box_o, dot_c)
        if dyn != self._last_dyn:
            if glow_c != self._last_dyn[0]:
                self.itemconfigure(self._vim_glow_id, fill=glow_c)
            if box_o != self._last_dyn[1]:
                self.itemconfigure(self._vim_box_id, outline=box_o)
            if dot_c != self._last_dyn[2]:
                self.itemconfigure(self._dot_id, fill=dot_c)
            self._last_dyn = dyn

    def set_vim_mode(self, mode, cmd=""):
        """Define 'vim mode'.

        Args:
            mode: Parâmetro da operação.
            cmd: Parâmetro da operação.
        """
        if not self.winfo_exists():
            return
        if mode in self._VIM:
            self._vim_text = f" {mode} " if mode != "COMMAND" else f" :{cmd} "
            self._vim_bg, self._vim_fg = self._VIM[mode]
        elif mode:
            self._vim_text = f" {mode} "
            self._vim_bg = self._p["accent"]
            self._vim_fg = self._p["void"]
        else:
            self._vim_text = self._vim_bg = self._vim_fg = ""

    def set_fps(self, fps):
        """Atualiza o contador de FPS exibido na barra de status.

        Args:
            fps: Valor float do FPS atual (0 para ocultar).
        """
        self._fps_str = f"{fps:.0f}fps" if fps > 0 else ""

    def stop(self):
        """Interrompe a animação da barra de status."""
        self._dead = True


class KPortal(tk.Toplevel):
    """Container que renderiza conteúdo em uma camada sobreposta (portal/overlay)."""

    def __init__(self, parent, commands, dark=True):
        """Cria a janela de portal (palete de comandos K-UI) fullscreen semi-transparente.

        Args:
            parent: Widget pai Tkinter.
            commands: Lista de tuplas (ícone, label, callback).
            dark: True para paleta escura.
        """
        super().__init__(parent)
        p = _pal(dark)
        self._p = p
        self._cmds = commands
        self._fil = []
        self._sel = 0
        self.overrideredirect(True)
        with contextlib.suppress(tk.TclError):
            self.attributes("-alpha", 0.92)
        self.configure(bg="#000000" if sys.platform != "win32" else p["void"])
        self.attributes("-topmost", True)
        try:
            px = parent.winfo_rootx()
            py = parent.winfo_rooty()
            pw = parent.winfo_width()
            ph = parent.winfo_height()
        except tk.TclError:
            px, py, pw, ph = (
                0,
                0,
                parent.winfo_screenwidth(),
                parent.winfo_screenheight(),
            )
        self.geometry(f"{pw}x{ph}+{px}+{py}")
        self._bg_canvas = tk.Canvas(self, bg="#000000", highlightthickness=0)
        self._bg_canvas.place(relx=0, rely=0, relwidth=1, relheight=1)
        self._bg_canvas.bind("<Button-1>", lambda _: _safe_del(self))
        self._bg_canvas.create_rectangle(
            0, 0, pw, ph, fill="#000000", stipple="gray50", outline=""
        )
        self._build(p, pw)
        self._update("")
        self.bind("<Escape>", lambda _: _safe_del(self))
        self.bind("<FocusOut>", self._focus_out)
        self.after(50, self._entry.focus_set)

    def _build(self, p, sw):
        """Constrói o layout interno do portal: campo de busca e listbox flutuante.

        Args:
            p: Dicionário de paleta de cores.
            sw: Largura da janela em pixels.
        """
        pw = max(520, sw // 2)
        panel = tk.Frame(
            self, bg=p["glass"], highlightbackground=p["accent"], highlightthickness=1
        )
        panel.place(relx=0.5, y=60, anchor="n", width=pw)
        panel.lift()
        rb = tk.Canvas(panel, height=3, bg=p["glass"], highlightthickness=0)
        rb.pack(fill="x")

        def _dr(e=None, cv=rb):
            w2 = cv.winfo_width()
            if w2 < 2:
                return
            cv.delete("all")
            for i in range(16):
                r2, g2, b2 = _hue(i * 22)
                cv.create_rectangle(
                    int(i * w2 / 16),
                    0,
                    int((i + 1) * w2 / 16),
                    3,
                    fill=f"#{r2:02x}{g2:02x}{b2:02x}",
                    outline="",
                )

        rb.bind("<Configure>", _dr)
        sf = tk.Frame(panel, bg=p["glass"])
        sf.pack(fill="x", padx=14, pady=10)
        tk.Label(
            sf, text="⌘", bg=p["glass"], fg=p["fg_dim"], font=("Segoe UI", 12)
        ).pack(side="left", padx=(0, 8))
        self._query = tk.StringVar()
        self._entry = tk.Entry(
            sf,
            textvariable=self._query,
            bg=p["glass"],
            fg=p["fg"],
            insertbackground=p["accent"],
            font=("Segoe UI", 13),
            relief="flat",
            bd=0,
            highlightthickness=0,
        )
        self._entry._kui_internal = True
        self._entry.pack(side="left", fill="x", expand=True)
        self._query.trace_add(
            "write", lambda *_: self._update(self._query.get()))
        self._entry.bind("<Return>", self._enter)
        self._entry.bind("<Down>", lambda _: self._move(+1))
        self._entry.bind("<Up>", lambda _: self._move(-1))
        self._entry.bind("<Escape>", lambda _: _safe_del(self))
        tk.Frame(panel, bg=p["accent"], height=1).pack(fill="x")
        self._cat = tk.Label(
            panel, text="", bg=p["glass"], fg=p["fg_dim"], font=("Segoe UI", 7)
        )
        self._cat.pack(anchor="w", padx=14, pady=(4, 0))
        lf = tk.Frame(panel, bg=p["glass"])
        lf.pack(fill="x", padx=2, pady=(0, 4))
        sb = tk.Scrollbar(lf, orient="vertical", bg=p["void"])
        self._lb = tk.Listbox(
            lf,
            bg=p["glass"],
            fg=p["fg"],
            selectbackground=p["accent"],
            selectforeground=p["void"],
            font=("Segoe UI", 10),
            relief="flat",
            bd=0,
            highlightthickness=0,
            activestyle="none",
            height=10,
            yscrollcommand=sb.set,
        )
        sb.config(command=self._lb.yview)
        sb.pack(side="right", fill="y")
        self._lb.pack(side="left", fill="x", expand=True)
        self._lb.bind("<Double-Button-1>", self._enter)
        self._lb.bind("<Button-1>", self._single_click)
        tk.Label(
            panel,
            text=_i18n_t("hc.k_ui.navegar_enter_executar_esc_fec"),
            bg=p["glass"],
            fg=p["fg_muted"],
            font=("Segoe UI", 7),
        ).pack(anchor="w", padx=14, pady=(0, 6))

    def _update(self, q):
        """Filtra e exibe os comandos conforme a query digitada.

        Args:
            q: Texto de busca.
        """
        q = q.strip().lower()
        self._fil = (
            [(i, l_, c) for i, l_, c in self._cmds if q in l_.lower()]
            if q
            else list(self._cmds)
        )
        self._lb.delete(0, "end")
        for ic, lb, _ in self._fil[:50]:
            self._lb.insert("end", f"  {ic}  {lb}")
        self._sel = 0
        if self._lb.size() > 0:
            self._lb.selection_set(0)
        self._lb.see(0)
        self._cat.config(
            text=(
                _i18n_t("kui.portal_results", n=len(self._fil))
                if q
                else _i18n_t("kui.portal_all")
            )
        )

    def _single_click(self, e):
        """Executa o comando correspondente ao clique simples na listbox.

        Args:
            e: Evento de mouse Tkinter.
        """
        idx = self._lb.nearest(e.y)
        if 0 <= idx < len(self._fil):
            self._run(idx)

    def _enter(self, _=None):
        """Executa o comando selecionado ao pressionar Enter."""
        sel = self._lb.curselection()
        idx = sel[0] if sel else self._sel
        if 0 <= idx < len(self._fil):
            self._run(idx)

    def _move(self, delta):
        """Move a seleção da listbox para cima ou para baixo.

        Args:
            delta: Deslocamento (+1 para baixo, -1 para cima).
        """
        n = self._lb.size()
        if n == 0:
            return
        sel = self._lb.curselection()
        cur = sel[0] if sel else 0
        new = max(0, min(n - 1, cur + delta))
        self._lb.selection_clear(0, "end")
        self._lb.selection_set(new)
        self._lb.see(new)
        self._sel = new

    def _run(self, idx):
        """Executa o callback do comando no índice fornecido e fecha o portal.

        Args:
            idx: Índice do comando na lista filtrada.
        """
        _, _, cb = self._fil[idx]
        _safe_del(self)
        with contextlib.suppress(tk.TclError):
            cb()

    def _focus_out(self, _=None) -> None:
        """Fecha o portal quando perde o foco."""
        self.after(100, lambda: _safe_del(self)
                   if self.winfo_exists() else None)


class KFloatWindow(tk.Toplevel):
    """Janela flutuante arrastável e redimensionável do K-UI."""

    def __init__(self, root, name, factory, on_close=None, dark=True):
        """Cria janela flutuante K-UI para ferramentas destacadas.

        Args:
            root: Janela raiz Tkinter.
            name: Título da janela.
            factory: Callable(parent) que constrói e retorna o conteúdo.
            on_close: Callback invocado ao fechar a janela.
            dark: True para paleta escura.
        """
        super().__init__(root)
        p = _pal(dark)
        self._close_cb = on_close
        self.title(f"◈  {name}")
        self.configure(bg=p["accent"])
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.resizable(True, True)
        chrome = tk.Frame(
            self, bg=p["glass"], highlightbackground=p["border"], highlightthickness=1
        )
        chrome.pack(fill="both", expand=True, padx=1, pady=1)
        hdr_bg = _lc_q(p["glass"], p["void"], 0.25)
        hdr = tk.Frame(chrome, bg=hdr_bg)
        hdr.pack(fill="x")
        rb = tk.Canvas(hdr, height=2, bg=hdr_bg, highlightthickness=0)
        rb.pack(fill="x")

        def _dr(e=None, cv=rb):
            w2 = cv.winfo_width()
            if w2 < 2:
                return
            cv.delete("all")
            for i in range(14):
                r2, g2, b2 = _hue(i * 26)
                cv.create_rectangle(
                    int(i * w2 / 14),
                    0,
                    int((i + 1) * w2 / 14),
                    2,
                    fill=f"#{r2:02x}{g2:02x}{b2:02x}",
                    outline="",
                )

        rb.bind("<Configure>", _dr)
        tr = tk.Frame(hdr, bg=hdr_bg)
        tr.pack(fill="x")
        tk.Label(
            tr,
            text=f"  ◈  {name}",
            bg=hdr_bg,
            fg=p["fg"],
            font=("Segoe UI", 9, "bold"),
            pady=5,
        ).pack(side="left", padx=4)
        cl = tk.Label(
            tr,
            text=_i18n_t("hc.k_ui.reencaixar"),
            bg=hdr_bg,
            fg=p["fg_dim"],
            font=("Segoe UI", 8),
            cursor="hand2",
            padx=10,
        )
        cl.pack(side="right")
        cl.bind("<Button-1>", lambda _: self._on_close())
        cl.bind("<Enter>", lambda _: cl.config(fg=p["accent2"]))
        cl.bind("<Leave>", lambda _: cl.config(fg=p["fg_dim"]))
        tk.Frame(chrome, bg=p["border"], height=1).pack(fill="x")
        cont = tk.Frame(chrome, bg=p["glass"])
        cont.pack(fill="both", expand=True)
        with contextlib.suppress(tk.TclError):
            w2 = factory(cont)
            if w2:
                w2.pack(fill="both", expand=True)
        rw = root.winfo_toplevel()
        W = max(rw.winfo_width(), 600)
        H = max(rw.winfo_height(), 400)
        pw = max(600, int(W * 0.60))
        ph = max(300, int(H * 0.42))
        self.geometry(
            f"{pw}x{ph}+{rw.winfo_rootx() + (W - pw) // 2}+{rw.winfo_rooty() + (H - ph) // 2}"
        )

    def _on_close(self) -> None:
        """Invoca o callback de fechamento e destrói a janela."""
        if self._close_cb:
            with contextlib.suppress((AttributeError, RuntimeError)):
                self._close_cb()
        _safe_del(self)


class KToast:
    """Notificação toast temporária exibida no canto da tela pelo K-UI."""

    def __init__(self, root, message, kind="info", ms=3000, dark=True):
        """Exibe uma notificação toast temporária no canto superior direito.

        Args:
            root: Janela raiz Tkinter.
            message: Texto da notificação.
            kind: Tipo ('info', 'warn', 'err').
            ms: Duração em milissegundos antes de desaparecer.
            dark: True para paleta escura.
        """
        p = _pal(dark)
        bc = {"info": p["accent2"], "warn": p["warn"], "err": p["err"]}.get(
            kind, p["accent2"]
        )
        self._win = tk.Toplevel(root)
        self._win.overrideredirect(True)
        self._win.attributes("-topmost", True)
        self._win.configure(bg=bc)
        inner = tk.Frame(self._win, bg=p["glass"], padx=14, pady=9)
        inner.pack(fill="both", expand=True, padx=1, pady=1)
        tk.Label(
            inner,
            text={"info": "i", "warn": "!", "err": "x"}.get(kind, "i"),
            bg=p["glass"],
            fg=bc,
            font=("Segoe UI", 11),
        ).pack(side="left", padx=(0, 8))
        tk.Label(
            inner,
            text=message,
            bg=p["glass"],
            fg=p["fg"],
            font=("Segoe UI", 9),
            wraplength=320,
            justify="left",
        ).pack(side="left")
        self._win.update_idletasks()
        sw = root.winfo_screenwidth()
        w2 = self._win.winfo_reqwidth()
        self._win.geometry(f"+{sw - w2 - 14}+52")
        self._win.after(ms, lambda: _safe_del(self._win))

    def destroy(self):
        """Destrói a janela toast imediatamente."""
        _safe_del(self._win)
