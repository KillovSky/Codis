"""Codis - DOOM embutido no editor (easter egg ``/codis doom``).

Responsabilidades deste módulo (a parte genérica de reparentamento de janelas
vive em :mod:`src.ui.window_embed`):

1. Descobrir o executável do motor (gzdoom, chocolate-doom, crispy-doom...).
2. Descobrir um IWAD (doom.wad, doom2.wad, freedoom...).
3. Montar a linha de comando adequada à família do motor.
4. Criar o :class:`~src.ui.window_embed.EmbeddedAppPanel` sobre o editor, ou
   iniciar o jogo em janela externa quando a plataforma não permite embutir.

Configuração (``~/.codis/config.json``) e variáveis de ambiente::

    doom_engine_path  / CODIS_DOOM_ENGINE   caminho do executável
    doom_iwad_path    / CODIS_DOOM_IWAD     caminho do IWAD
    doom_extra_args                         argumentos extras (string estilo shell)
    DOOMWADDIR / DOOMWADPATH                diretórios de IWAD (convenção do DOOM)
"""

from __future__ import annotations

import logging
import os
import shlex
import shutil
import subprocess
import sys
import tkinter as tk
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.constants import CONFIG_DIR
from src.i18n import _t
from src.ui import window_embed as we

logger = logging.getLogger("Codis.Doom")

__all__ = [
    "DoomLaunchError",
    "DoomPlan",
    "build_argv",
    "classify_engine",
    "find_engine",
    "find_iwad",
    "resolve_plan",
    "start_embedded",
    "launch_external",
]

ENGINE_NAMES: tuple[str, ...] = (
    "gzdoom",
    "uzdoom",
    "crispy-doom",
    "chocolate-doom",
    "dsda-doom",
    "woof",
    "prboom-plus",
    "prboom",
    "zdoom",
    "doom",
)

IWAD_NAMES: tuple[str, ...] = (
    "doom2.wad",
    "doom.wad",
    "freedoom2.wad",
    "freedoom1.wad",
    "plutonia.wad",
    "tnt.wad",
    "doom1.wad",
    "freedm.wad",
)

_DOOM_DIR = CONFIG_DIR / "doom"
_LOG_PATH = _DOOM_DIR / "last_run.log"

_BOOT_LINES: tuple[str, ...] = (
    "DOOM v1.9  (c) 1993 id Software, Ltd.\n",
    "V_Init: allocate screens.\n",
    "M_LoadDefaults: Load system defaults.\n",
    "Z_Init: Init zone memory allocation daemon.\n",
    "W_Init: Init WADfiles.\n",
    "R_Init: Init DOOM refresh daemon -\n",
    "..................................................\n",
    "P_Init: Init Playloop state.\n",
    "I_Init: Setting up machine state.\n",
    "D_CheckNetGame: Checking network game status.\n",
    "startskill 2  deathmatch: 0  startmap: 1  startepisode: 1\n",
    "player 1 of 1 (1 nodes)\n",
    "\n",
    "rip and tear, KillovSky\n",
)


class DoomLaunchError(RuntimeError):
    """Falha ao preparar o DOOM; ``kind`` é ``engine``, ``iwad`` ou ``launch``."""

    def __init__(self, kind: str, message: str) -> None:
        """Guarda o tipo da falha junto da mensagem já traduzida."""
        super().__init__(message)
        self.kind = kind


@dataclass(frozen=True)
class DoomPlan:
    """Resultado da descoberta: o que será executado."""

    engine: Path
    family: str
    iwad: Path
    extra_args: tuple[str, ...] = ()


def classify_engine(path: str | Path) -> str:
    """Classifica o motor pelo nome do executável.

    Returns:
        ``"zdoom"`` (gzdoom/zdoom), ``"chocolate"`` (chocolate/crispy),
        ``"boom"`` (prboom/dsda/woof) ou ``"generic"``.
    """
    stem = Path(path).stem.lower()
    if "zdoom" in stem:
        return "zdoom"
    if "chocolate" in stem or "crispy" in stem:
        return "chocolate"
    if any(tag in stem for tag in ("prboom", "dsda", "woof")):
        return "boom"
    return "generic"


def _engine_dirs(env: Mapping[str, str], home: Path) -> list[Path]:
    """Diretórios extras (além do PATH) onde procurar o motor."""
    dirs = [_DOOM_DIR, home / ".codis" / "doom", Path(__file__).resolve().parents[2] / "doom"]
    if sys.platform == "win32":
        bases = [env.get("ProgramFiles"), env.get("ProgramFiles(x86)")]
        local = env.get("LOCALAPPDATA")
        if local:
            bases.append(str(Path(local) / "Programs"))
        subs = ("GZDoom", "UZDoom", "Chocolate Doom", "Crispy Doom", "DSDA-Doom", "Woof")
        dirs.extend(Path(base) / sub for base in filter(None, bases) for sub in subs)
    return dirs


def find_engine(
    configured: str | None = None,
    env: Mapping[str, str] | None = None,
    home: Path | None = None,
    which: Callable[[str], str | None] = shutil.which,
) -> Path | None:
    """Localiza o executável do motor.

    Ordem: caminho configurado -> ``CODIS_DOOM_ENGINE`` -> PATH -> diretórios
    conhecidos. Um caminho configurado inválido é ignorado (cai no próximo).
    """
    env = os.environ if env is None else env
    home = Path.home() if home is None else home
    for explicit in (configured, env.get("CODIS_DOOM_ENGINE")):
        if explicit and Path(explicit).expanduser().is_file():
            return Path(explicit).expanduser()
    for name in ENGINE_NAMES:
        found = which(name)
        if found:
            return Path(found)
    suffixes = (".exe", "") if sys.platform == "win32" else ("",)
    for directory in _engine_dirs(env, home):
        for name in ENGINE_NAMES:
            for suffix in suffixes:
                candidate = directory / (name + suffix)
                if candidate.is_file():
                    return candidate
    return None


def _iwad_dirs(engine_dir: Path, env: Mapping[str, str], home: Path) -> list[Path]:
    dirs: list[Path] = []
    if env.get("DOOMWADDIR"):
        dirs.append(Path(env["DOOMWADDIR"]))
    dirs.extend(Path(p) for p in env.get("DOOMWADPATH", "").split(os.pathsep) if p)
    dirs += [engine_dir, _DOOM_DIR, home / ".codis" / "doom"]
    if sys.platform == "win32":
        pf = [env.get("ProgramFiles(x86)"), env.get("ProgramFiles")]
        for base in filter(None, pf):
            for game in ("Ultimate Doom", "Doom 2", "Final Doom"):
                dirs.append(Path(base) / "Steam" / "steamapps" / "common" / game / "base")
        dirs.append(home / "Documents" / "My Games" / "GZDoom")
    else:
        dirs += [
            home / ".local" / "share" / "games" / "doom",
            home / ".config" / "gzdoom",
            home / ".steam" / "steam" / "steamapps" / "common" / "Ultimate Doom" / "base",
            Path("/usr/share/games/doom"),
            Path("/usr/share/doom"),
            Path("/usr/local/share/doom"),
            Path("/usr/share/games/freedoom"),
        ]
    return dirs


def find_iwad(
    engine_dir: Path,
    configured: str | None = None,
    env: Mapping[str, str] | None = None,
    home: Path | None = None,
) -> Path | None:
    """Localiza um IWAD (comparação case-insensitive, vale para Linux)."""
    env = os.environ if env is None else env
    home = Path.home() if home is None else home
    for explicit in (configured, env.get("CODIS_DOOM_IWAD")):
        if explicit and Path(explicit).expanduser().is_file():
            return Path(explicit).expanduser()
    for directory in _iwad_dirs(engine_dir, env, home):
        try:
            names = {p.name.lower(): p for p in directory.iterdir() if p.is_file()}
        except OSError:
            continue
        for wanted in IWAD_NAMES:
            if wanted in names:
                return names[wanted]
    return None


def build_argv(
    engine: Path,
    family: str,
    iwad: Path,
    width: int,
    height: int,
    extra_args: tuple[str, ...] = (),
) -> list[str]:
    """Monta a linha de comando em modo janela para a família do motor.

    Apenas opções conhecidas por família são usadas; o tamanho real é imposto
    depois pelo reparentamento, então famílias incertas recebem o mínimo.
    """
    argv = [str(engine), "-iwad", str(iwad)]
    if family == "zdoom":
        argv += ["-width", str(width), "-height", str(height), "+vid_fullscreen", "0"]
    elif family == "chocolate":
        argv += ["-window", "-geometry", f"{width}x{height}"]
    else:
        argv += ["-window"]
    return [*argv, *extra_args]


def resolve_plan(cfg: Any | None = None) -> DoomPlan:
    """Descobre motor e IWAD ou levanta :class:`DoomLaunchError`."""

    def conf(key: str) -> str | None:
        if cfg is None:
            return None
        value = cfg.get(key, None)
        return str(value) if value else None

    engine = find_engine(conf("doom_engine_path"))
    if engine is None:
        raise DoomLaunchError("engine", _t("doom.err_no_engine"))
    iwad = find_iwad(engine.parent, conf("doom_iwad_path"))
    if iwad is None:
        raise DoomLaunchError("iwad", _t("doom.err_no_iwad", dir=str(_DOOM_DIR)))
    raw_extra = conf("doom_extra_args") or ""
    try:
        extra = tuple(shlex.split(raw_extra, posix=sys.platform != "win32"))
    except ValueError:
        extra = ()
    return DoomPlan(engine, classify_engine(engine), iwad, extra)


def _child_env() -> dict[str, str]:
    """Ambiente do processo: força X11 no SDL (Linux) e DPI compatível (Windows)."""
    env = os.environ.copy()
    if sys.platform == "win32":
        layers = env.get("__COMPAT_LAYER", "").split()
        if "HIGHDPIAWARE" not in layers:
            env["__COMPAT_LAYER"] = " ".join([*layers, "HIGHDPIAWARE"])
    else:
        env.setdefault("SDL_VIDEODRIVER", "x11")
    return env


def _labels() -> we.EmbedLabels:
    return we.EmbedLabels(
        hint=_t("doom.hint", hotkey=we.EXIT_HOTKEY_TEXT),
        back=_t("doom.btn_back"),
        focus=_t("doom.btn_focus"),
        loading=_t("doom.status_loading"),
        embedded=_t("doom.status_embedded", hotkey=we.EXIT_HOTKEY_TEXT),
        closed=_t("doom.status_closed"),
        err_exited=_t("doom.err_exited"),
        err_timeout=_t("doom.err_timeout"),
        err_launch=_t("doom.err_launch"),
        embed_failed=_t("doom.status_embed_failed"),
    )


def start_embedded(
    container: tk.Widget,
    plan: DoomPlan,
    *,
    on_status: Callable[[str], None] | None = None,
    on_close: Callable[[], None] | None = None,
    backend: we.WindowBackend | None = None,
) -> we.EmbeddedAppPanel:
    """Cria o painel sobre ``container`` e inicia o DOOM dentro dele.

    Raises:
        DoomLaunchError: ``kind="launch"`` se não houver backend de embutir.
    """
    backend = backend if backend is not None else we.get_backend()
    if backend is None:
        raise DoomLaunchError("launch", _t("doom.status_external"))
    panel = we.EmbeddedAppPanel(
        container,
        [],
        backend=backend,
        labels=_labels(),
        cwd=str(plan.engine.parent),
        env=_child_env(),
        log_path=_LOG_PATH,
        boot_lines=_BOOT_LINES,
        on_status=on_status,
        on_close=on_close,
    )
    panel.place(x=0, y=0, relwidth=1, relheight=1)
    panel.lift()
    panel.update_idletasks()
    width = max(320, panel.winfo_width())
    height = max(200, panel.winfo_height() - 26)
    panel.set_argv(build_argv(plan.engine, plan.family, plan.iwad, width, height, plan.extra_args))
    panel.start()
    return panel


def launch_external(plan: DoomPlan) -> subprocess.Popen | None:
    """Fallback: inicia o DOOM em janela própria (sem embutir)."""
    argv = build_argv(plan.engine, plan.family, plan.iwad, 960, 600, plan.extra_args)
    try:
        return subprocess.Popen(  # noqa: S603
            argv,
            cwd=str(plan.engine.parent),
            env=_child_env(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError as exc:
        logger.warning("Falha ao iniciar DOOM externo: %s", exc)
        raise DoomLaunchError("launch", _t("doom.err_launch").format(reason=exc)) from exc
