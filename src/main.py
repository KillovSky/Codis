"""Codis - ponto de entrada da aplicação."""

from __future__ import annotations

import contextlib
import faulthandler
import logging
import re
import sys
import tkinter as tk
import traceback
from pathlib import Path
from tkinter import messagebox
from tkinter import ttk as _ttk
from typing import Any

from src.app import CodisApp
from src.config import ConfigManager
from src.constants import (
    CONFIG_DIR,
    CONFIG_FILE,
    PLUGINS_DIR,
    SNIPPETS_DIR,
    THEMES_DIR,
)
from src.syntax.patterns import _compile_all_patterns
from src.theme.base import _define_themes
from src.theme.manager import ThemeManager, _patch_all_themes, _patch_theme
from src.ui.thread_safe_tk import install as _install_thread_safe_tk
from src.ui.wizard import FirstRunWizard

if sys.platform == "win32":
    try:
        from ctypes import windll as _WINDLL
    except ImportError:
        _WINDLL = None
else:
    _WINDLL = None

logger = logging.getLogger("Codis")


def _make_runtime_dirs() -> list[str]:
    """Cria os diretórios de runtime do Codis, se ainda não existirem.

    Returns:
        Lista de mensagens descrevendo os diretórios que falharam ao ser
        criados (vazia se todos foram criados com sucesso).
    """
    failures: list[str] = []
    for directory in (CONFIG_DIR, PLUGINS_DIR, SNIPPETS_DIR, THEMES_DIR):
        try:
            directory.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            failures.append(f"  {directory}\n    -> {exc}")
    return failures


def _patch_combobox_dropdown() -> None:
    """Aplica monkey-patch em ``ttk.Combobox`` para abrir o dropdown ao clicar.

    Necessário porque temas TTK customizados às vezes não disparam o
    comportamento nativo de abertura do dropdown ao primeiro clique;
    o patch força ``ttk::combobox::Post`` e usa um ``<Down>`` simulado
    como fallback caso o comando Tcl falhe.
    """
    orig_init = _ttk.Combobox.__init__

    def _patched_init(self: _ttk.Combobox, master: Any = None, **kw: Any) -> None:
        """Inicializador de ``ttk.Combobox`` com patch de dropdown para temas TTK customizados."""
        orig_init(self, master, **kw)

        def _post_dropdown(_event: Any) -> None:
            """Força abertura do dropdown via ``ttk::combobox::Post``; simula ``<Down>`` como fallback."""
            try:
                self.tk.call("ttk::combobox::Post", self)
            except tk.TclError:
                self.focus_force()
                self.after(1, lambda: self.event_generate("<Down>"))

        self.bind("<ButtonPress-1>", _post_dropdown, add="+")

    _ttk.Combobox.__init__ = _patched_init


def _apply_windows_dpi_fix(root: tk.Tk) -> None:
    """Ajusta DPI awareness e escala da UI no Windows.

    Sem efeito em outras plataformas. Usa ``SetProcessDpiAwareness`` via
    ``ctypes`` (quando disponível) e ajusta ``tk scaling`` ao DPI real do monitor
    (antes era fixo em 1.5, o que deixava a UI enorme em telas pequenas).
    A escala escolhida pelo usuário é aplicada depois por ``UIScaler``.

    Args:
        root: Janela raiz Tkinter, usada para aplicar o ``tk scaling``.
    """
    if sys.platform != "win32":
        return
    if _WINDLL is not None:
        with contextlib.suppress(OSError, AttributeError):
            _WINDLL.shcore.SetProcessDpiAwareness(1)
    with contextlib.suppress(tk.TclError):
        dpi = float(root.winfo_fpixels("1i"))
        root.tk.call("tk", "scaling", max(1.0, min(4.0, dpi / 72.0)))


def _open_cli_args(app: CodisApp) -> None:
    """Abre arquivos e pastas passados como argumentos na linha de comando.

    Diretórios são definidos como workspace; arquivos são abertos no editor.
    Caminhos inexistentes são silenciosamente ignorados.
    """
    for arg in sys.argv[1:]:
        path = Path(arg)
        if not path.exists():
            continue
        with contextlib.suppress(OSError, RuntimeError):
            if path.is_dir():
                app.set_workspace(path)
            else:
                app.open_file(path)


def main() -> None:
    """Inicializa e executa o Codis.

    Cria os diretórios de runtime, compila padrões de sintaxe, configura
    temas, aplica monkey-patch no ``ttk.Combobox`` para suportar
    temas customizados, configura o handler global de exceções e exibe o
    wizard de primeiro uso ou abre o editor diretamente.
    """
    from src import startup_profile as _sp

    _sp.mark("imports concluídos (interpretador + módulos)")
    logging.basicConfig(
        level=logging.WARNING,
        format="%(levelname)s  %(name)s - %(message)s",
    )
    with contextlib.suppress(OSError, ValueError, AttributeError, RuntimeError):
        faulthandler.enable(all_threads=True)

    dirs_failed = _make_runtime_dirs()

    with contextlib.suppress(re.error, ValueError):
        _compile_all_patterns()

    _define_themes()
    _patch_all_themes()
    _sp.mark("dirs + padrões de sintaxe + temas")

    root = tk.Tk()
    root.withdraw()
    _install_thread_safe_tk(root)
    _sp.mark("Tk() criado")
    import threading

    from src import _shells

    threading.Thread(target=_shells.warm, name="CodisPathIndex", daemon=True).start()

    _patch_combobox_dropdown()

    def _global_exc_handler(exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Handler global para exceções não tratadas no Tkinter.

        Registra o traceback completo e exibe um diálogo de erro ao usuário
        sugerindo deletar o arquivo de configuração caso o problema persista.
        """
        err = "".join(traceback.format_exception(exc_type, exc_val, exc_tb))
        logger.error("Exceção não tratada:\n%s", err)
        with contextlib.suppress(tk.TclError, RuntimeError):
            if root.winfo_exists():
                messagebox.showerror(
                    "Codis - Erro Inesperado",
                    f"Erro:\n\n{exc_val}\n\nSe persistir, delete ~/.codis/config.json e reinicie.",
                    parent=root,
                )

    root.report_callback_exception = _global_exc_handler

    if dirs_failed:
        root.deiconify()
        messagebox.showwarning(
            "Codis - Aviso de permissão",
            "Não foi possível criar diretórios:\n\n"
            + "\n".join(dirs_failed)
            + "\n\nO app pode funcionar de forma limitada.",
            parent=root,
        )
        root.withdraw()

    _apply_windows_dpi_fix(root)

    cfg = ConfigManager()
    tmgr = ThemeManager(cfg.get("theme", "fluent_dark"))
    _patch_theme(tmgr.theme)
    tmgr.apply_ttk()

    def _init_app() -> None:
        """Instancia o ``CodisApp``, processa argumentos CLI e exibe a janela.

        Em caso de falha crítica exibe um diálogo de erro e sugere deletar
        o arquivo de configuração corrompido.
        """
        try:
            root.deiconify()
            from src.stall_trace import maybe_start as _stall_trace

            _dog = _stall_trace(root)
            app = CodisApp(root)
            if _dog:
                _dog.mark("CodisApp() pronto")
            _sp.mark("CodisApp() construído")
            root.after_idle(lambda: _sp.mark("primeiro ciclo ocioso (janela utilizável)"))
            _open_cli_args(app)
        except (RuntimeError, tk.TclError) as exc:
            logger.critical(
                "Falha fatal ao inicializar CodisApp:\n%s", traceback.format_exc()
            )
            with contextlib.suppress(tk.TclError, RuntimeError):
                root.deiconify()
                messagebox.showerror(
                    "Codis - Falha na Inicialização",
                    f"Erro fatal:\n\n{exc}\n\nTente deletar {CONFIG_FILE} e reiniciar.",
                    parent=root,
                )

    first_run = not cfg.get("first_run_done", False)
    if first_run:
        try:
            wizard = FirstRunWizard(root, cfg, tmgr, _init_app)
            wizard.protocol("WM_DELETE_WINDOW", lambda: (
                wizard.destroy(), _init_app()))
        except (tk.TclError, RuntimeError) as exc:
            logger.warning(
                "FirstRunWizard falhou (%s) - iniciando direto.", exc)
            _init_app()
    else:
        _init_app()

    def _tk_interrupt_poll() -> None:
        """Pump Tkinter para capturar KeyboardInterrupt do terminal a cada 200 ms."""
        root.after(200, _tk_interrupt_poll)

    root.after(200, _tk_interrupt_poll)

    try:
        root.mainloop()
    except KeyboardInterrupt:
        with contextlib.suppress(tk.TclError):
            root.destroy()


if __name__ == "__main__":
    main()
