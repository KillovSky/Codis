"""Codis - Modos Standalone.

Executa qualquer módulo do Codis de forma isolada, inicializando apenas as
dependências mínimas (tema, i18n, config). A maioria dos painéis é descrita em
_PANEL_REGISTRY como dados; apenas casos com UI extra (regex, player,
acessibilidade, color-picker, archive) têm funções próprias.

Uso:
    python run.py --player            Zen Media Player
    python run.py --pomodoro          Pomodoro Timer
    python run.py --scratch           Bloco de Notas
    python run.py --regex             Regex Playground
    python run.py --rest              REST Client
    python run.py --diff A B          Diff Viewer (com arquivos opcionais)
    python run.py --color-picker      Seletor de Cores
    python run.py --snippets          Gerenciador de Snippets
    python run.py --paint [img]       Editor de Imagens
    python run.py --accessibility     Painel de Acessibilidade
    python run.py --sql               SQL Studio
    python run.py --tasks             Gerenciador de Tarefas
    python run.py --pip               Gerenciador de Pacotes Pip
    python run.py --git [dir]         Painel Git
    python run.py --formatter         Formatador JSON/YAML/TOML/XML
    python run.py --env               Variáveis de Ambiente
    python run.py --calendar          Calendário com Lembretes
    python run.py --clock             Relógio / Alarmes / Cronômetro
    python run.py --calculator        Calculadora Científica
    python run.py --flowchart [file]  Gerador de Fluxograma
    python run.py --todo              Lista de Tarefas Rápidas
    python run.py --output            Painel de Output
    python run.py --terminal [dir]    Terminal Integrado
    python run.py --processes         Gerenciador de Processos do Sistema
    python run.py --render [file]     Preview HTML/Markdown/SVG
    python run.py --stats [dir]       Estatísticas de Código
    python run.py --plugins           Gerenciador de Plugins
    python run.py --ssh               Conexão SSH Remota
    python run.py --archive [file]    Visualizador de Arquivo Compactado
    python run.py --help              Lista todos os modos

Ao fechar a janela standalone, o processo encerra automaticamente.
"""

from __future__ import annotations

import contextlib
import logging
import tkinter as tk
from collections.abc import Callable
from pathlib import Path
from typing import Any

logger = logging.getLogger("Codis.Standalone")


def _bootstrap() -> tuple[tk.Tk, Any, Any]:
    """Inicializa tema, config e i18n; retorna (root, tmgr, cfg).

    Mantém root oculto - a janela do painel cuida de si mesma.
    Os imports são locais para início rápido; apenas o necessário é
    carregado antes do Tk().
    """
    from src.config import ConfigManager
    from src.constants import CONFIG_DIR, PLUGINS_DIR, SNIPPETS_DIR, THEMES_DIR
    from src.syntax.patterns import _compile_all_patterns
    from src.theme.base import _define_themes
    from src.theme.manager import ThemeManager, _patch_all_themes, _patch_theme

    for d in (CONFIG_DIR, PLUGINS_DIR, SNIPPETS_DIR, THEMES_DIR):
        with contextlib.suppress(OSError):
            d.mkdir(parents=True, exist_ok=True)

    with contextlib.suppress(Exception):
        _compile_all_patterns()

    _define_themes()
    _patch_all_themes()

    root = tk.Tk()
    root.withdraw()

    cfg = ConfigManager()
    tmgr = ThemeManager(cfg.get("theme", "fluent_dark"))
    _patch_theme(tmgr.theme)
    tmgr.apply_ttk()

    return root, tmgr, cfg


def _mainloop(root: tk.Tk, win: tk.Toplevel) -> None:
    """Executa o mainloop e encerra o processo quando *win* for fechado."""

    def _on_close() -> None:
        with contextlib.suppress(tk.TclError):
            win.destroy()
        with contextlib.suppress(tk.TclError):
            root.destroy()

    win.protocol("WM_DELETE_WINDOW", _on_close)
    win.deiconify()
    win.focus_force()

    def _poll() -> None:
        if not win.winfo_exists():
            with contextlib.suppress(tk.TclError):
                root.destroy()
            return
        root.after(250, _poll)

    root.after(250, _poll)
    try:
        root.mainloop()
    except KeyboardInterrupt:
        with contextlib.suppress(tk.TclError):
            root.destroy()


def _fallback_label(win: tk.Toplevel, bg: str, fg: str, msg: str) -> None:
    """Exibe mensagem de erro amigável quando um painel não está disponível."""
    tk.Label(
        win,
        text=f"⚠  {msg}",
        bg=bg,
        fg=fg,
        font=("Arial", 11),
        justify="left",
        padx=20,
        pady=20,
    ).pack(expand=True)


class _PanelSpec:
    """Especificação de um painel standalone.

    Attributes:
        title_i18n:  chave i18n para o título da janela.
        title_def:   título padrão (se i18n não encontrar).
        geometry:    string de geometria Tk (ex.: ``"1100x760"``).
        bg_attr:     atributo do Theme usado como bg da janela (ex.: ``"panel_bg"``).
        cls_path:    caminho dotted para a classe do painel (ex.: ``"src.ui.sql_studio.SQLStudioPanel"``).
        resizable:   tuple (w, h) para ``win.resizable()``.
        post_build:  callable(panel, win, root, cfg) chamado após pack() do painel.
    """

    __slots__ = (
        "title_i18n",
        "title_def",
        "geometry",
        "bg_attr",
        "cls_path",
        "resizable",
        "post_build",
    )

    def __init__(
        self,
        title_i18n: str,
        title_def: str,
        geometry: str,
        bg_attr: str = "panel_bg",
        cls_path: str = "",
        resizable: tuple[bool, bool] = (True, True),
        post_build: Callable | None = None,
    ) -> None:
        self.title_i18n = title_i18n
        self.title_def = title_def
        self.geometry = geometry
        self.bg_attr = bg_attr
        self.cls_path = cls_path
        self.resizable = resizable
        self.post_build = post_build


def _run_spec(spec: _PanelSpec, path_arg: str | None = None) -> None:
    """Instancia e exibe um painel descrito por *spec*."""
    from src.i18n import _t

    root, tmgr, cfg = _bootstrap()
    t = tmgr.theme
    bg = getattr(t, spec.bg_attr, t.bg)

    win = tk.Toplevel(root)
    win.title(_t(spec.title_i18n, spec.title_def))
    win.geometry(spec.geometry)
    win.resizable(*spec.resizable)
    win.config(bg=bg)

    if spec.cls_path:
        mod_path, cls_name = spec.cls_path.rsplit(".", 1)
        try:
            import importlib

            mod = importlib.import_module(mod_path)
            cls = getattr(mod, cls_name)
            panel = cls(win, tmgr, cfg)
            panel.pack(fill="both", expand=True)
            if spec.post_build:
                spec.post_build(panel, win, root, cfg, path_arg)
        except Exception as exc:
            logger.warning("%s não disponível: %s", cls_name, exc)
            _fallback_label(
                win, bg, t.fg, f"{cls_name} não disponível:\n{exc}")

    _mainloop(root, win)


def _post_fit(panel, win, *_args) -> None:
    """post_build: ajusta minsize ao tamanho natural (para paineis fixos)."""
    with contextlib.suppress(tk.TclError):
        win.update_idletasks()
        win.minsize(win.winfo_reqwidth(), win.winfo_reqheight())


def _post_load_file(panel, _win, _root, _cfg, path_arg) -> None:
    """post_build: carrega arquivo se path_arg for fornecido."""
    if path_arg:
        fp = Path(path_arg)
        if fp.exists():
            with contextlib.suppress(Exception):
                panel.load_file(str(fp))


def _post_workspace(panel, _win, _root, _cfg, path_arg) -> None:
    """post_build: define workspace se path_arg for um diretório."""
    if path_arg:
        workdir = Path(path_arg).resolve()
        with contextlib.suppress(Exception):
            panel.set_workspace(workdir)


_REGISTRY: dict[str, _PanelSpec] = {
    "--pomodoro": _PanelSpec(
        "standalone.pomodoro.title",
        "Codis - Pomodoro Timer",
        "400x280",
        "panel_bg",
        "src.ui.pomodoro_panel.PomodoroPanel",
        resizable=(False, False),
        post_build=_post_fit,
    ),
    "--scratch": _PanelSpec(
        "standalone.scratch.title",
        "Codis - Bloco de Notas",
        "720x540",
        "sidebar_bg",
        "src.ui.scratchpad.ScratchpadPane",
    ),
    "--rest": _PanelSpec(
        "standalone.rest.title",
        "Codis - REST Client",
        "1050x720",
        "panel_bg",
        "src.ui.rest_client.RestClientPanel",
    ),
    "--sql": _PanelSpec(
        "standalone.sql.title",
        "Codis - SQL Studio",
        "1100x760",
        "panel_bg",
        "src.ui.sql_studio.SQLStudioPanel",
    ),
    "--tasks": _PanelSpec(
        "standalone.tasks.title",
        "Codis - Gerenciador de Tarefas",
        "1050x720",
        "panel_bg",
        "src.ui.task_manager.TaskManagerPanel",
    ),
    "--pip": _PanelSpec(
        "standalone.pip.title",
        "Codis - Pip Manager",
        "1000x720",
        "panel_bg",
        "src.ui.pip_manager.PipManagerPanel",
    ),
    "--formatter": _PanelSpec(
        "standalone.formatter.title",
        "Codis - Formatter",
        "1000x700",
        "panel_bg",
        "src.ui.formatter_panel.FormatterPanel",
    ),
    "--env": _PanelSpec(
        "standalone.env.title",
        "Codis - Variáveis de Ambiente",
        "900x660",
        "panel_bg",
        "src.ui.env_manager.EnvManagerPanel",
    ),
    "--calendar": _PanelSpec(
        "standalone.calendar.title",
        "Codis - Calendário",
        "820x640",
        "panel_bg",
        "src.ui.calendar_panel.CalendarPanel",
    ),
    "--clock": _PanelSpec(
        "standalone.clock.title",
        "Codis - Relógio",
        "480x420",
        "panel_bg",
        "src.ui.clock_panel.ClockPanel",
    ),
    "--calculator": _PanelSpec(
        "standalone.calculator.title",
        "Codis - Calculadora",
        "360x560",
        "panel_bg",
        "src.ui.calculator_panel.CalculatorPanel",
        resizable=(False, True),
        post_build=_post_fit,
    ),
    "--todo": _PanelSpec(
        "standalone.todo.title",
        "Codis - Todo",
        "560x600",
        "panel_bg",
        "src.ui.todo_panel.TodoPanel",
    ),
    "--output": _PanelSpec(
        "standalone.output.title",
        "Codis - Output",
        "900x600",
        "panel_bg",
        "src.ui.output_panel.OutputPanel",
    ),
    "--processes": _PanelSpec(
        "standalone.processes.title",
        "Codis - Processos do Sistema",
        "1100x700",
        "panel_bg",
        "src.ui.process_manager.ProcessManagerPanel",
    ),
    "--flowchart": _PanelSpec(
        "standalone.flowchart.title",
        "Codis - Flowchart",
        "1050x720",
        "panel_bg",
        "src.ui.flowchart_panel.FlowchartPanel",
        post_build=_post_load_file,
    ),
    "--render": _PanelSpec(
        "standalone.render.title",
        "Codis - Preview",
        "1000x720",
        "panel_bg",
        "src.ui.render_panel.RenderPanel",
        post_build=_post_load_file,
    ),
    "--stats": _PanelSpec(
        "standalone.stats.title",
        "Codis - Estatísticas de Código",
        "900x640",
        "panel_bg",
        "src.ui.code_stats.CodeStatsPanel",
        post_build=_post_workspace,
    ),
}

_REGISTRY["--preview"] = _REGISTRY["--render"]
_REGISTRY["--procs"] = _REGISTRY["--processes"]
_REGISTRY["--calc"] = _REGISTRY["--calculator"]


def run_player() -> None:
    """Abre o Zen Player de forma standalone (janela flutuante)."""
    from src.ui.zen_media import ZenMediaPlayer
    from src.ui.zen_player import MODE_FLOATING, ZenPlayerHost

    root, tmgr, cfg = _bootstrap()

    player = ZenMediaPlayer(tk_root=root)
    saved = cfg.get("zen_media_playlist", [])
    if saved:
        player.set_playlist(saved)
    host = ZenPlayerHost(root, player, cfg, tmgr, dock_before=None,
                         on_minimize=None)
    host.set_mode(MODE_FLOATING)
    win = host._win

    def _on_close() -> None:
        player.stop()
        host.destroy()
        with contextlib.suppress(tk.TclError):
            root.destroy()

    if win is not None:
        win.protocol("WM_DELETE_WINDOW", _on_close)
        win.focus_force()

    def _poll() -> None:
        if win is None or not win.winfo_exists():
            with contextlib.suppress(tk.TclError):
                root.destroy()
            return
        root.after(300, _poll)

    root.after(300, _poll)
    try:
        root.mainloop()
    except KeyboardInterrupt:
        player.stop()
        with contextlib.suppress(tk.TclError):
            root.destroy()


def run_regex() -> None:
    """Abre o Regex Playground standalone com área de texto de teste."""
    from src.constants import FONT_MONO, FONT_UI
    from src.i18n import _t
    from src.ui.regex_playground import RegexPlayground

    root, tmgr, _ = _bootstrap()
    t = tmgr.theme

    win = tk.Toplevel(root)
    win.title(_t("standalone.regex.title", "Codis - Regex Playground"))
    win.geometry("900x640")
    win.config(bg=t.panel_bg)

    frame_text = tk.Frame(win, bg=t.panel_bg)
    frame_text.pack(fill="both", expand=True, side="top", padx=8, pady=(8, 0))

    tk.Label(
        frame_text,
        text=_t("standalone.regex.input_label", "Texto de teste:"),
        bg=t.panel_bg,
        fg=t.fg_muted,
        font=(FONT_UI, 8),
    ).pack(anchor="w")

    test_text = tk.Text(
        frame_text,
        height=12,
        bg=t.editor_bg,
        fg=t.fg,
        font=(FONT_MONO, 11),
        relief="flat",
        bd=0,
        insertbackground=t.cursor,
        highlightthickness=1,
        highlightbackground=t.border,
    )
    test_text.pack(fill="both", expand=True, pady=(0, 4))

    def _get_text() -> str:
        return test_text.get("1.0", "end-1c")

    def _apply_replace(pattern: str, replacement: str, flags: str) -> None:
        import re

        flag_val = 0
        if "i" in flags:
            flag_val |= re.IGNORECASE
        if "m" in flags:
            flag_val |= re.MULTILINE
        new_content = re.sub(pattern, replacement, _get_text(), flags=flag_val)
        test_text.delete("1.0", "end")
        test_text.insert("1.0", new_content)

    RegexPlayground(win, tmgr, get_text=_get_text, apply_replace=_apply_replace).pack(
        fill="x", side="bottom"
    )
    _mainloop(root, win)


def run_diff(file_a: str | None = None, file_b: str | None = None) -> None:
    """Abre o Diff Viewer standalone, com ou sem arquivos pré-carregados."""
    from src.i18n import _t
    from src.ui.diff_panel import DiffPanel

    root, tmgr, cfg = _bootstrap()
    t = tmgr.theme

    win = tk.Toplevel(root)
    win.title(_t("standalone.diff.title", "Codis - Diff Viewer"))
    win.geometry("1150x720")
    win.config(bg=t.editor_bg)

    try:
        pane = DiffPanel(win, tmgr, cfg)
        pane.pack(fill="both", expand=True)
        if file_a and file_b:
            pa, pb = Path(file_a), Path(file_b)
            if pa.exists() and pb.exists():
                pane.load_files(str(pa), str(pb))
    except Exception as exc:
        logger.warning("DiffPanel não disponível: %s", exc)
        _fallback_label(win, t.editor_bg, t.fg, f"Diff não disponível:\n{exc}")

    _mainloop(root, win)


def run_color_picker() -> None:
    """Abre o Color Picker standalone."""
    try:
        from src.ui.color_picker import ColorPickerDialog  # noqa: PLC0415
    except ImportError:
        ColorPickerDialog = None  # type: ignore[assignment,misc]

    root, tmgr, _ = _bootstrap()
    try:
        if ColorPickerDialog is None:
            raise ImportError("ColorPickerDialog not available")
        dialog = ColorPickerDialog(
            root, tmgr, initial_color="#4f9eff", on_pick=lambda _c: None
        )
        dialog.protocol("WM_DELETE_WINDOW", lambda: (
            dialog.destroy(), root.destroy()))
        root.deiconify()
        root.withdraw()
        root.mainloop()
    except Exception as exc:
        logger.warning("ColorPicker não disponível: %s", exc)
        with contextlib.suppress(tk.TclError):
            root.destroy()


def run_snippets() -> None:
    """Abre o Gerenciador de Snippets standalone."""
    from src.editor.snippets import SnippetEngine
    from src.i18n import _t
    from src.ui.snippets_panel import SnippetsPanel

    root, tmgr, _ = _bootstrap()
    t = tmgr.theme
    try:
        engine = SnippetEngine()
        win = tk.Toplevel(root)
        win.title(_t("standalone.snippets.title", "Codis - Snippets"))
        win.geometry("820x620")
        win.config(bg=t.panel_bg)
        SnippetsPanel(win, tmgr, engine, get_editor=lambda: None).pack(
            fill="both", expand=True
        )
        _mainloop(root, win)
    except Exception as exc:
        logger.warning("SnippetsPanel não disponível: %s", exc)
        with contextlib.suppress(tk.TclError):
            root.destroy()


def run_paint(image_path: str | None = None) -> None:
    """Abre o Editor de Imagens standalone."""
    from src.i18n import _t
    from src.ui.paint_panel import PaintPanel

    root, tmgr, _ = _bootstrap()
    t = tmgr.theme
    win = tk.Toplevel(root)
    win.title(_t("standalone.paint.title", "Codis - Editor de Imagens"))
    win.geometry("1000x700")
    win.config(bg=t.panel_bg)
    initial = Path(image_path) if image_path else None
    PaintPanel(win, tmgr, initial_path=initial).pack(fill="both", expand=True)
    _mainloop(root, win)


def run_accessibility() -> None:
    """Abre o painel de Acessibilidade standalone."""
    from src.ui.accessibility import AccessibilityManager, AccessibilityPanel

    root, tmgr, cfg = _bootstrap()
    try:
        mgr = AccessibilityManager(None, cfg, tmgr)
        win = AccessibilityPanel(root, mgr, tmgr)
        win.protocol("WM_DELETE_WINDOW", lambda: (
            win.destroy(), root.destroy()))
        root.deiconify()
        root.withdraw()
        root.mainloop()
    except Exception as exc:
        logger.warning("AccessibilityPanel não disponível: %s", exc)
        with contextlib.suppress(tk.TclError):
            root.destroy()


def run_git(directory: str | None = None) -> None:
    """Abre o Painel Git standalone, opcionalmente em um diretório."""
    from src.i18n import _t
    from src.ui.git_panel import GitPanel

    root, tmgr, cfg = _bootstrap()
    t = tmgr.theme
    workdir = Path(directory).resolve() if directory else Path.cwd()
    win = tk.Toplevel(root)
    win.title(_t("standalone.git.title", "Codis - Git"))
    win.geometry("900x700")
    win.config(bg=t.panel_bg)
    GitPanel(win, tmgr, cfg, root_path=workdir).pack(fill="both", expand=True)
    _mainloop(root, win)


def run_terminal(cwd: str | None = None) -> None:
    """Abre o Terminal integrado standalone."""
    from src.i18n import _t
    from src.ui.terminal import TerminalPanel

    root, tmgr, cfg = _bootstrap()
    t = tmgr.theme
    workdir = Path(cwd).resolve() if cwd else Path.cwd()
    win = tk.Toplevel(root)
    win.title(_t("standalone.terminal.title", "Codis - Terminal"))
    win.geometry("1000x640")
    win.config(bg=t.terminal_bg)
    TerminalPanel(win, tmgr, cfg, cwd=str(workdir)
                  ).pack(fill="both", expand=True)
    _mainloop(root, win)


def run_plugins() -> None:
    """Abre o Gerenciador de Plugins standalone."""
    from src.i18n import _t
    from src.plugins.loader import PluginLoader
    from src.ui.plugin_panel import PluginPanel

    root, tmgr, cfg = _bootstrap()
    t = tmgr.theme
    win = tk.Toplevel(root)
    win.title(_t("standalone.plugins.title", "Codis - Plugins"))
    win.geometry("1050x720")
    win.config(bg=t.panel_bg)
    try:
        loader = PluginLoader(cfg)
        PluginPanel(win, tmgr, loader, cfg).pack(fill="both", expand=True)
    except Exception as exc:
        logger.warning("PluginPanel não disponível: %s", exc)
        _fallback_label(win, t.panel_bg, t.fg,
                        f"Plugins não disponível:\n{exc}")
    _mainloop(root, win)


def run_ssh() -> None:
    """Abre o explorador de arquivos remotos standalone."""
    from src.i18n import _t
    from src.ui.remote_browser import RemoteBrowserPanel

    root, tmgr, cfg = _bootstrap()
    t = tmgr.theme
    win = tk.Toplevel(root)
    win.title(_t("rbrowser.window_title", "Codis - Arquivos Remotos"))
    from src.ui.remote_browser import fit_toplevel

    fit_toplevel(win, 1050, 700)
    win.config(bg=t.sidebar_bg)
    try:
        RemoteBrowserPanel(win, tmgr, cfg).pack(fill="both", expand=True)
    except Exception as exc:
        logger.warning("RemoteBrowserPanel não disponível: %s", exc)
        _fallback_label(
            win, t.panel_bg, t.fg, f"Explorador remoto não disponível:\n{exc}"
        )
    _mainloop(root, win)


def run_archive(file_path: str | None = None) -> None:
    """Abre o Visualizador de Arquivos Compactados standalone."""
    from tkinter import filedialog

    from src.i18n import _t
    from src.ui.archive_panel import ArchivePanel

    root, tmgr, _ = _bootstrap()
    t = tmgr.theme

    if not file_path:
        root.deiconify()
        chosen = filedialog.askopenfilename(
            title=_t("standalone.archive.pick_title",
                     "Abrir arquivo compactado"),
            filetypes=[
                (
                    _t("standalone.archive.filetypes_arch", "Arquivos Compactados"),
                    "*.zip *.tar *.tar.gz *.tar.bz2 *.tar.xz *.gz *.7z *.rar",
                ),
                (_t("standalone.archive.filetypes_all", "Todos os arquivos"), "*.*"),
            ],
        )
        root.withdraw()
        if not chosen:
            root.destroy()
            return
        file_path = chosen

    fp = Path(file_path)
    if not fp.exists():
        logger.error("Arquivo não encontrado: %s", fp)
        root.destroy()
        return

    win = tk.Toplevel(root)
    win.title(
        _t("standalone.archive.title", "Codis - Visualizador de Arquivo")
        + f" - {fp.name}"
    )
    win.geometry("900x640")
    win.config(bg=t.panel_bg)
    try:
        ArchivePanel(win, fp, on_open_file=None).pack(fill="both", expand=True)
    except Exception as exc:
        logger.warning("ArchivePanel não disponível: %s", exc)
        _fallback_label(win, t.panel_bg, t.fg,
                        f"Visualizador não disponível:\n{exc}")
    _mainloop(root, win)


def _make_registry_runner(flag: str) -> Callable:
    def _run(path_arg: str | None = None) -> None:
        _run_spec(_REGISTRY[flag], path_arg)

    return _run


_SPECIAL: dict[str, Callable] = {
    "--player": run_player,
    "--regex": run_regex,
    "--diff": run_diff,
    "--color-picker": run_color_picker,
    "--snippets": run_snippets,
    "--paint": run_paint,
    "--accessibility": run_accessibility,
    "--a11y": run_accessibility,
    "--git": run_git,
    "--terminal": run_terminal,
    "--plugins": run_plugins,
    "--ssh": run_ssh,
    "--archive": run_archive,
}

run_pomodoro = _make_registry_runner("--pomodoro")
run_scratch = _make_registry_runner("--scratch")
run_rest = _make_registry_runner("--rest")
run_sql = _make_registry_runner("--sql")
run_tasks = _make_registry_runner("--tasks")
run_pip = _make_registry_runner("--pip")
run_formatter = _make_registry_runner("--formatter")
run_env = _make_registry_runner("--env")
run_calendar = _make_registry_runner("--calendar")
run_clock = _make_registry_runner("--clock")
run_calculator = _make_registry_runner("--calculator")
run_todo = _make_registry_runner("--todo")
run_output = _make_registry_runner("--output")
run_processes = _make_registry_runner("--processes")
run_flowchart = _make_registry_runner("--flowchart")
run_render = _make_registry_runner("--render")
run_stats = _make_registry_runner("--stats")

_ALL_MODES: dict[str, str] = {
    "--player": "Zen Media Player",
    "--pomodoro": "Pomodoro Timer",
    "--scratch": "Bloco de Notas",
    "--regex": "Regex Playground",
    "--rest": "REST Client",
    "--diff": "Diff Viewer  [A B]",
    "--color-picker": "Seletor de Cores",
    "--snippets": "Gerenciador de Snippets",
    "--paint": "Editor de Imagens  [img]",
    "--accessibility": "Painel de Acessibilidade  (alias --a11y)",
    "--sql": "SQL Studio",
    "--tasks": "Gerenciador de Tarefas (Kanban)",
    "--pip": "Gerenciador de Pacotes Pip",
    "--git": "Painel Git  [dir]",
    "--formatter": "Formatador JSON/YAML/TOML/XML",
    "--env": "Variáveis de Ambiente",
    "--calendar": "Calendário com Lembretes",
    "--clock": "Relógio / Alarmes / Cronômetro",
    "--calculator": "Calculadora Científica  (alias --calc)",
    "--flowchart": "Gerador de Fluxograma  [py]",
    "--todo": "Lista de Tarefas Rápidas",
    "--output": "Painel de Output",
    "--terminal": "Terminal Integrado  [dir]",
    "--processes": "Gerenciador de Processos  (alias --procs)",
    "--render": "Preview HTML/Markdown/SVG  [file]  (alias --preview)",
    "--stats": "Estatísticas de Código  [dir]",
    "--plugins": "Gerenciador de Plugins",
    "--ssh": "Conexão SSH Remota",
    "--archive": "Visualizador de Arquivo  [file]",
}


def _print_help() -> None:
    w = 20
    print()
    print("  Codis - Modos Standalone")
    print("  " + "─" * 54)
    print("  Uso:  python run.py [MODO] [ARGS]")
    print()
    for flag, desc in _ALL_MODES.items():
        print(f"    {flag:<{w}}  {desc}")
    print()
    print("  Exemplos:")
    print("    python run.py --sql")
    print("    python run.py --diff antes.py depois.py")
    print("    python run.py --git /meu/projeto")
    print("    python run.py --paint foto.png")
    print("    python run.py --render index.html")
    print("    python run.py arquivo.py          (abre no editor)")
    print()


def dispatch(argv: list[str]) -> bool:
    """Verifica argv e executa o modo standalone correspondente.

    Returns:
        True  se um modo standalone foi executado (processo deve encerrar).
        False se nenhum modo foi identificado (execução normal do editor).
    """
    if not argv:
        return False

    flag = argv[0].lower()

    if flag in ("--help", "-h", "help"):
        _print_help()
        return True

    logging.basicConfig(
        level=logging.WARNING,
        format="%(levelname)s  %(name)s - %(message)s",
    )

    if flag == "--diff":
        run_diff(
            argv[1] if len(argv) > 1 else None,
            argv[2] if len(argv) > 2 else None,
        )
        return True

    _with_one_arg = {
        "--paint",
        "--git",
        "--flowchart",
        "--terminal",
        "--render",
        "--preview",
        "--stats",
        "--archive",
    }
    if flag in _with_one_arg:
        fn = _SPECIAL.get(flag) or _make_registry_runner(flag)
        fn(argv[1] if len(argv) > 1 else None)
        return True

    if flag in _REGISTRY:
        _run_spec(_REGISTRY[flag])
        return True

    if flag in _SPECIAL:
        _SPECIAL[flag]()
        return True

    return False
