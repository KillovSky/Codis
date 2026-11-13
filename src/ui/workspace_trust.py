"""Codis - WorkspaceTrust: segurança ao abrir pastas não confiáveis.

Implementa o sistema de Workspace Trust do Codis - ao abrir uma pasta que
nunca foi aberta antes, o editor exibe um diálogo de confiança perguntando
se o usuário confia no conteúdo. Pastas não confiáveis têm recursos
potencialmente perigosos desabilitados:

  - Plugins são carregados em modo somente-leitura (sem execução de código)
  - Execução de tarefas e scripts é bloqueada
  - LSP é desabilitado (servidores podem executar código arbitrário)
  - O terminal integrado mostra aviso de modo restrito

A lista de pastas confiáveis é persistida em ``~/.codis/trusted_workspaces.json``.

Este módulo oferece a API de verificação de confiança e o diálogo visual,
sem conhecer detalhes internos do editor - a integração é feita pelo
``CodisApp`` via callbacks.
"""

from __future__ import annotations

import json
import logging
import tkinter as tk
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from src.constants import FONT_MONO, FONT_UI
from src.i18n import _t
from src.ui.grab_safe import safe_grab_set

if TYPE_CHECKING:
    from src.theme.base import Theme

logger = logging.getLogger("Codis")

_TRUST_FILE = Path.home() / ".codis" / "trusted_workspaces.json"


class WorkspaceTrust:
    """Gerencia o estado de confiança dos workspaces abertos no Codis.

    Persiste a lista de pastas confiáveis no disco e oferece API para
    verificar confiança, registrar confiança e revogar confiança.

    Args:
        config_dir: Diretório de configuração do Codis (padrão: ~/.codis).
    """

    def __init__(self, config_dir: Path | None = None) -> None:
        """Inicializa o gerenciador de Workspace Trust.

        Args:
            config_dir: Diretório de configuração. Se None, usa ~/.codis.
        """
        self._trust_file = (
            config_dir or Path.home() / ".codis"
        ) / "trusted_workspaces.json"
        self._trusted: set[str] = set()
        self._load()

    def _load(self) -> None:
        """Carrega a lista de workspaces confiáveis do disco."""
        try:
            if self._trust_file.exists():
                data = json.loads(self._trust_file.read_text(encoding="utf-8"))
                if isinstance(data, list):
                    self._trusted = set(data)
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            logger.warning(
                "WorkspaceTrust: falha ao carregar %s: %s", self._trust_file, exc
            )

    def _save(self) -> None:
        """Persiste a lista de workspaces confiáveis no disco."""
        try:
            self._trust_file.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._trust_file.with_suffix(".json.tmp")
            tmp.write_text(
                json.dumps(sorted(self._trusted),
                           indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            tmp.replace(self._trust_file)
        except OSError as exc:
            logger.warning("WorkspaceTrust: falha ao salvar: %s", exc)

    def is_trusted(self, path: Path) -> bool:
        """Verifica se um caminho está na lista de workspaces confiáveis.

        Considera confiança transitiva: se uma pasta pai está na lista,
        as subpastas também são consideradas confiáveis.

        Args:
            path: Caminho da pasta a verificar.

        Returns:
            bool: True se o workspace é confiável.
        """
        resolved = str(path.resolve())

        for trusted in self._trusted:
            if (
                resolved == trusted
                or resolved.startswith(trusted + "/")
                or resolved.startswith(trusted + "\\")
            ):
                return True
        return False

    def trust(self, path: Path, parent: bool = False) -> None:
        """Marca um workspace como confiável.

        Args:
            path: Caminho da pasta a marcar como confiável.
            parent: Se True, marca a pasta pai em vez de ``path``.
        """
        target = path.parent if parent else path
        self._trusted.add(str(target.resolve()))
        self._save()

    def prompt_if_needed(
        self,
        root: tk.Widget,
        path: Path,
        theme: Theme,
        on_trusted: Callable[[], None] | None = None,
        on_restricted: Callable[[], None] | None = None,
    ) -> bool:
        """Exibe o diálogo de confiança se a pasta ainda não for confiável.

        Se a pasta já for confiável, chama ``on_trusted`` e retorna True
        imediatamente sem exibir o diálogo.

        Args:
            root: Widget raiz Tkinter para centralizar o diálogo.
            path: Caminho da pasta sendo aberta.
            theme: Tema visual ativo.
            on_trusted: Callback acionado se o usuário confirmar confiança.
            on_restricted: Callback acionado se o usuário escolher modo restrito.

        Returns:
            bool: True se a pasta é/foi marcada como confiável, False para restrito.
        """
        if self.is_trusted(path):
            if on_trusted:
                on_trusted()
            return True

        dlg = _TrustDialog(root, path, theme)
        root.wait_window(dlg.win)

        if dlg.result == "trust":
            self.trust(path)
            if on_trusted:
                on_trusted()
            return True
        if dlg.result == "trust_parent":
            self.trust(path, parent=True)
            if on_trusted:
                on_trusted()
            return True
        if on_restricted:
            on_restricted()
        return False


class _TrustDialog:
    """Diálogo modal de confirmação de confiança de workspace.

    Exibe as informações da pasta, explica os riscos de abrir conteúdo
    não confiável e oferece três opções:
    - Confiar na pasta
    - Confiar na pasta pai
    - Abrir em modo restrito

    Args:
        parent: Widget pai Tkinter.
        path: Caminho da pasta a ser avaliada.
        theme: Tema visual ativo.
    """

    _W, _H = 520, 380

    def __init__(self, parent: tk.Widget, path: Path, theme: Theme) -> None:
        """Cria o diálogo de Workspace Trust.

        Args:
            parent: Widget pai.
            path: Pasta sendo aberta.
            theme: Objeto Theme com as cores do tema ativo.
        """
        self.result: str = "restricted"
        t = theme
        self.win = tk.Toplevel(parent)
        self.win.title(_t("trust.dialog_title", "Confiança no Workspace"))
        self.win.resizable(False, False)
        self.win.configure(bg=t.floating_bg)
        safe_grab_set(self.win)
        self.win.attributes("-topmost", True)
        self.win.protocol("WM_DELETE_WINDOW", self._on_restricted)

        parent.update_idletasks()
        px = getattr(parent, "winfo_rootx", lambda: 200)()
        py = getattr(parent, "winfo_rooty", lambda: 100)()
        pw = getattr(parent, "winfo_width", lambda: 800)()
        ph = getattr(parent, "winfo_height", lambda: 600)()
        x = px + (pw - self._W) // 2
        y = py + (ph - self._H) // 2
        self.win.geometry(f"{self._W}x{self._H}+{x}+{y}")

        self._build(t, path)
        self.win.bind("<Escape>", lambda _: self._on_restricted())

    def _build(self, t: Theme, path: Path) -> None:
        """Constrói os widgets do diálogo de confiança.

        Args:
            t: Tema visual ativo.
            path: Pasta sendo avaliada.
        """
        outer = tk.Frame(self.win, bg=t.border, padx=1, pady=1)
        outer.pack(fill="both", expand=True)
        inner = tk.Frame(outer, bg=t.floating_bg, padx=24, pady=20)
        inner.pack(fill="both", expand=True)

        hdr = tk.Frame(inner, bg=t.floating_bg)
        hdr.pack(fill="x", pady=(0, 16))

        tk.Label(hdr, text="🔒", bg=t.floating_bg, font=(FONT_UI, 24)).pack(
            side="left", padx=(0, 12)
        )

        title_frame = tk.Frame(hdr, bg=t.floating_bg)
        title_frame.pack(side="left", fill="x", expand=True)

        tk.Label(
            title_frame,
            text=_t("trust.dialog_heading",
                    "Você confia nos autores desta pasta?"),
            bg=t.floating_bg,
            fg=t.fg,
            font=(FONT_UI, 12, "bold"),
            anchor="w",
            wraplength=400,
        ).pack(fill="x")

        tk.Label(
            title_frame,
            text=_t(
                "trust.dialog_subheading",
                "O Codis oferece recursos avançados que podem executar código nesta pasta.",
            ),
            bg=t.floating_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 9),
            anchor="w",
            wraplength=400,
            justify="left",
        ).pack(fill="x", pady=(2, 0))

        path_frame = tk.Frame(inner, bg=t.input_bg, padx=8, pady=6)
        path_frame.pack(fill="x", pady=(0, 12))

        tk.Label(
            path_frame,
            text=str(path)[:80] + ("..." if len(str(path)) > 80 else ""),
            bg=t.input_bg,
            fg=t.fg,
            font=(FONT_MONO, 9),
            anchor="w",
        ).pack(fill="x")

        risk_text = _t(
            "trust.risk_explanation",
            "Recursos desabilitados em modo restrito:\n"
            "  - Execucao de plugins e scripts\n"
            "  - Servidores LSP (autocomplete avancado)\n"
            "  - Terminal integrado\n"
            "  - Formatacao automatica\n\n"
            "Abra somente código de fontes em que você confia.",
        )
        tk.Label(
            inner,
            text=risk_text,
            bg=t.floating_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 9),
            anchor="w",
            justify="left",
            wraplength=460,
        ).pack(fill="x", pady=(0, 16))

        tk.Frame(inner, bg=t.border, height=1).pack(fill="x", pady=(0, 12))

        parent_path = path.parent
        trust_parent_btn = tk.Button(
            inner,
            text=_t(
                "trust.trust_parent",
                "Sim, confiar na pasta pai: {parent}",
                parent=str(parent_path)[:40],
            ),
            bg=t.btn_bg,
            fg=t.fg,
            activebackground=t.btn_hover,
            relief="flat",
            padx=12,
            pady=5,
            font=(FONT_UI, 9),
            anchor="w",
            command=self._on_trust_parent,
        )
        trust_parent_btn.pack(fill="x", pady=(0, 4))

        trust_btn = tk.Button(
            inner,
            text=_t("trust.trust_folder", "Sim, confiar nesta pasta"),
            bg=t.accent,
            fg="#000",
            activebackground=t.accent,
            relief="flat",
            padx=12,
            pady=5,
            font=(FONT_UI, 9, "bold"),
            anchor="w",
            command=self._on_trust,
        )
        trust_btn.pack(fill="x", pady=(0, 4))

        restricted_btn = tk.Button(
            inner,
            text=_t(
                "trust.restricted_mode",
                "Não, abrir em Modo Restrito (recursos limitados)",
            ),
            bg=t.btn_bg,
            fg=t.warn_fg if hasattr(t, "warn_fg") else t.fg_muted,
            activebackground=t.btn_hover,
            relief="flat",
            padx=12,
            pady=5,
            font=(FONT_UI, 9),
            anchor="w",
            command=self._on_restricted,
        )
        restricted_btn.pack(fill="x")

        tk.Label(
            inner,
            text=_t(
                "trust.manage_hint",
                "Gerencie workspaces confiáveis em: Arquivo → Workspace Trust",
            ),
            bg=t.floating_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 8),
            anchor="w",
        ).pack(fill="x", pady=(12, 0))

    def _on_trust(self) -> None:
        """Trata a escolha 'Confiar nesta pasta'."""
        self.result = "trust"
        self.win.destroy()

    def _on_trust_parent(self) -> None:
        """Trata a escolha 'Confiar na pasta pai'."""
        self.result = "trust_parent"
        self.win.destroy()

    def _on_restricted(self) -> None:
        """Trata a escolha 'Modo Restrito' ou fechamento do diálogo."""
        self.result = "restricted"
        self.win.destroy()
