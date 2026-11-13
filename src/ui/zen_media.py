"""
Codis - Zen Media Player
========================
Módulo responsável pelo player de mídia integrado ao modo Zen do Codis.

Componentes principais:
    - :class:`ZenMediaPlayer`   - Motor de reprodução (MPV ou VLC), thread-safe.
    - :class:`ZenMiniOrb`       - Janela-bolinha compacta; clique expande para o HUD.
    - :class:`ZenPlaylistPopup` - Popup de seleção rápida de faixa no HUD.
    - :class:`ZenMediaDialog`   - Diálogo de configuração de playlist e autoplay.

Arquitetura de janelas
----------------------
HUD e MiniOrb são ``tk.Toplevel`` **reais** (não ``tk.Frame`` com ``place()``).
Isso garante:
    * Movimentação e redimensionamento nativos pelo gerenciador de janelas do OS.
    * Independência do Codis: minimizar/maximizar o Codis não afeta o player.
    * Clicar fora não fecha nem esconde o player.
    * Fechar o HUD via ``✕`` converte para a bolinha - nunca some completamente.
    * A bolinha converte de volta para o HUD completo com um clique.

Correções de travamento
-----------------------
    * ``_stop_current`` é completamente não-bloqueante (sem ``join()`` na main thread).
    * Contador de geração ``_gen`` descarta callbacks de reproduções antigas.
    * Toda operação de backend roda em thread daemon separada.
"""

from __future__ import annotations

import contextlib
import logging
import math
import os
import random
import shutil
import threading
import tkinter as tk
from collections.abc import Callable
from pathlib import Path
from tkinter import filedialog
from typing import Any

from src.i18n import _t
from src.theme.utils import alpha_blend_hex
from src.ui.grab_safe import safe_grab_set
from src.ui.zen_audio import apply_vlc_equalizer, clamp_gains, mpv_af_string

logger = logging.getLogger("Codis.ZenMedia")

try:
    import mpv

    MPV_AVAILABLE = True
except (ImportError, OSError, Exception):
    MPV_AVAILABLE = False

try:
    import vlc

    VLC_AVAILABLE = True
except (ImportError, OSError, Exception):
    VLC_AVAILABLE = False


def _parse_entry(entry) -> dict[str, str]:
    """Converte uma entrada de playlist em dicionário ``{name, url}``.

    Aceita strings simples (caminhos ou URLs) ou dicionários já formatados.
    Para URLs do YouTube extrai um name legível do ID de vídeo.

    Args:
        entry: String de caminho/URL ou dicionário com chaves ``name`` e ``url``.

    Returns:
        Dicionário com as chaves ``"name"`` e ``"url"``.
    """
    if isinstance(entry, dict):
        return {
            "name": str(entry.get("name", entry.get("url", "Faixa"))),
            "url": str(entry.get("url", "")),
        }
    url = str(entry)
    if "youtube.com" in url or "youtu.be" in url:
        if "watch?v=" in url:
            name = url.rsplit("watch?v=", 1)[-1].split("&", 1)[0]
        else:
            name = url.rsplit("/", 1)[-1]
    else:
        name = Path(url).name or url
    return {"name": name, "url": url}


def _fmt_time(s: float) -> str:
    """Formata segundos em string legível ``M:SS`` ou ``H:MM:SS``.

    Args:
        s: Tempo em segundos. Valores nulos ou negativos retornam ``"0:00"``.

    Returns:
        String formatada no padrão ``M:SS`` ou ``H:MM:SS``.
    """
    if not s or s < 0:
        return "0:00"
    s = int(s)
    h = s // 3600
    m = (s % 3600) // 60
    s = s % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _clamp(value: float, lo: float, hi: float) -> float:
    """Restringe *value* ao intervalo [lo, hi].

    Args:
        value: Valor a restringir.
        lo:    Limite inferior.
        hi:    Limite superior.


    Returns:
        Valor restrito ao intervalo.
    """
    return max(lo, min(hi, value))


_alpha_blend_hex = alpha_blend_hex


class ZenMediaPlayer:
    """Motor de reprodução de mídia usando python-vlc ou python-mpv.

    Thread-safety e prevenção de travamentos
    ----------------------------------------
    1. **Contador de geração** (``_gen``): cada ``play()`` incrementa o contador.
       já mudou quando executados - evita que o ``end-file`` de uma faixa
       antiga acione ``next()`` na nova.

    2. **Stop não-bloqueante**: desanexa a instância imediatamente e lança
       uma thread daemon de limpeza. A thread principal nunca bloqueia.

    Attributes:
        AUDIO_EXTENSIONS: Extensões de arquivo reconhecidas como mídia.
        REPEAT_OFF: Sem repetição.
        REPEAT_ONE: Repetir faixa atual.
        REPEAT_ALL: Repetir playlist inteira.
    """

    AUDIO_EXTENSIONS: frozenset = frozenset(
        {
            ".mp3",
            ".flac",
            ".wav",
            ".m4a",
            ".ogg",
            ".aac",
            ".wma",
            ".opus",
            ".mp4",
            ".mkv",
            ".webm",
            ".avi",
            ".mov",
            ".wmv",
            ".flv",
            ".m4v",
            ".ts",
            ".3gp",
        }
    )

    VIDEO_EXTENSIONS: frozenset = frozenset(
        {
            ".mp4",
            ".mkv",
            ".webm",
            ".avi",
            ".mov",
            ".wmv",
            ".flv",
            ".m4v",
            ".ts",
            ".3gp",
        }
    )

    REPEAT_OFF = "off"
    REPEAT_ONE = "one"
    REPEAT_ALL = "all"

    def __init__(self, tk_root: tk.Misc | None = None) -> None:
        """Inicializa o motor sem carregar nenhuma biblioteca de mídia.

        Args:
            tk_root: Janela raiz do Tkinter para agendamento de callbacks.
                     Se ``None``, tenta detectar automaticamente.
        """
        self._tk_root: tk.Misc | None = tk_root
        self._playlist: list[dict[str, str]] = []
        self._play_order: list[int] = []
        self._order_idx: int = 0
        self._idx: int = 0
        self._playing: bool = False
        self._paused: bool = False
        self._loading: bool = False
        self._duration: float = 0.0
        self._position: float = 0.0
        self._volume: int = 70
        self._player_instance: Any = None
        self._player_type: str | None = None
        self._stop_ev: threading.Event = threading.Event()
        self._listeners: dict[str, dict[Any, Callable]] = {
            "change": {}, "progress": {}, "state": {},
        }
        self._on_change_cb: Callable | None = lambda: self._fanout("change")
        self._on_progress_cb: Callable | None = (
            lambda pos, dur: self._fanout("progress", pos, dur)
        )
        self._on_state_cb: Callable | None = (
            lambda playing, paused, loading: self._fanout(
                "state", playing, paused, loading)
        )
        self._shuffle: bool = False
        self._repeat: str = self.REPEAT_OFF
        self._gen: int = 0
        self._ytdlp: bool = (
            shutil.which(
                "yt-dlp") is not None or shutil.which("youtube-dl") is not None
        )
        self._available: bool = MPV_AVAILABLE or VLC_AVAILABLE
        self._video_wid: int | None = None
        self._eq_gains: list[float] = clamp_gains(None)
        self._eq_enabled: bool = True

    def set_equalizer(
        self, gains: list[float] | None = None, enabled: bool | None = None
    ) -> None:
        """Define ganhos (±12 dB, 10 bandas) e/ou liga/desliga o equalizador.

        A mudança é aplicada imediatamente à faixa em reprodução e reaplicada
        automaticamente a cada nova faixa.
        """
        if gains is not None:
            self._eq_gains = clamp_gains(gains)
        if enabled is not None:
            self._eq_enabled = bool(enabled)
        self._apply_eq_to(self._player_instance, self._player_type)

    @property
    def eq_gains(self) -> list[float]:
        """Ganhos atuais do equalizador (cópia)."""
        return list(self._eq_gains)

    @property
    def eq_enabled(self) -> bool:
        """Se o equalizador está ativo."""
        return self._eq_enabled

    def _apply_eq_to(self, instance: Any, kind: str | None) -> None:
        """Aplica o equalizador a uma instância mpv/VLC (falhas são ignoradas)."""
        if instance is None or kind is None:
            return
        if kind == "mpv":
            chain = mpv_af_string(self._eq_gains) if self._eq_enabled else ""
            try:
                instance.af = chain
            except (AttributeError, RuntimeError, TypeError, OSError, SystemError) as exc:
                logger.debug("mpv af: %s", exc)
        elif kind == "vlc":
            apply_vlc_equalizer(instance, self._eq_gains, self._eq_enabled)

    def set_playlist(self, playlist: list) -> None:
        """Define a playlist completa, substituindo a anterior.

        Args:
            playlist: Lista de entradas aceitas por :func:`_parse_entry`.
        """
        self._playlist = [_parse_entry(e) for e in playlist if e]
        self._idx = 0
        self._rebuild_order()
        self._notify_change()

    def add_track(self, name: str, url: str) -> None:
        """Adiciona uma faixa ao final da playlist.

        Args:
            name: Nome exibido na interface.
            url:  Caminho local ou URL da faixa.
        """
        self._playlist.append({"name": name, "url": url})
        self._rebuild_order()
        self._notify_change()

    def remove_track(self, idx: int) -> None:
        """Remove a faixa no índice *idx* da playlist.

        Args:
            idx: Índice (base 0) da faixa a remover.
        """
        if 0 <= idx < len(self._playlist):
            self._playlist.pop(idx)
            self._idx = max(0, min(self._idx, len(self._playlist) - 1))
            self._rebuild_order()
            self._notify_change()

    def load_folder(self, folder_path: str) -> None:
        """
        Somente extensões em :attr:`AUDIO_EXTENSIONS` são incluídas.
        Os arquivos são ordenados alfabeticamente.

        Args:
            folder_path: Caminho absoluto para a folder raiz.
        """
        arquivos: list[str] = []
        for root, _dirs, fnames in os.walk(folder_path):
            for f in fnames:
                if Path(f).suffix.lower() in self.AUDIO_EXTENSIONS:
                    arquivos.append(os.path.join(root, f))
        arquivos.sort()
        if not arquivos:
            return
        for f in arquivos:
            self._playlist.append(
                {
                    "name": os.path.relpath(f, folder_path),
                    "url": f,
                }
            )
        self._rebuild_order()
        self._notify_change()

    def _rebuild_order(self) -> None:
        """Reconstrói a ordem de reprodução conforme o modo shuffle.

        No modo shuffle a faixa atual fica como primeiro item da nova ordem.
        """
        n = len(self._playlist)
        if n == 0:
            self._play_order = []
            self._order_idx = 0
            return
        if self._shuffle:
            indices = list(range(n))
            if self._idx in indices:
                indices.remove(self._idx)
                random.shuffle(indices)
                self._play_order = [self._idx] + indices
            else:
                random.shuffle(indices)
                self._play_order = indices
        else:
            self._play_order = list(range(n))
        with contextlib.suppress(ValueError):
            self._order_idx = self._play_order.index(self._idx)

    def play(self) -> None:
        """Inicia a reprodução da faixa atual de forma não-bloqueante.

        lança uma thread worker que para o backend antigo e inicia o novo.
        """
        if not self._playlist:
            return
        if not self._available:
            self._playing = False
            self._loading = False
            self._notify_state()
            self._notify_change()
            return

        self._gen += 1
        gen = self._gen

        entry = self._playlist[self._idx]
        url = entry["url"]
        if not url:
            return

        self._playing = True
        self._paused = False
        self._loading = True
        self._position = 0.0
        self._duration = 0.0
        self._notify_state()
        self._notify_change()

        old_instance = self._player_instance
        old_type = self._player_type
        old_stop_ev = self._stop_ev

        self._stop_ev = threading.Event()
        self._player_instance = None
        self._player_type = None

        def _worker():
            _stop_instance_bg(old_instance, old_type, old_stop_ev)
            if gen != self._gen:
                return
            if MPV_AVAILABLE:
                self._spawn_mpv(url, gen)
            elif VLC_AVAILABLE:
                self._spawn_vlc(url, gen)
            else:
                self._schedule_if(gen, self._cleanup_player)

        threading.Thread(target=_worker, daemon=True,
                         name="CodisMediaWorker").start()

    def stop(self) -> None:
        """Para a reprodução completamente de forma não-bloqueante."""
        self._gen += 1
        self._playing = False
        self._paused = False
        self._loading = False
        self._position = 0.0

        old_instance = self._player_instance
        old_type = self._player_type
        old_stop_ev = self._stop_ev

        self._stop_ev = threading.Event()
        self._player_instance = None
        self._player_type = None

        threading.Thread(
            target=lambda: _stop_instance_bg(
                old_instance, old_type, old_stop_ev),
            daemon=True,
            name="CodisMediaStop",
        ).start()

        self._notify_change()
        self._notify_state()

    def toggle_pause(self) -> None:
        """Alterna entre pause e play.

        Se o player não estiver tocando, inicia a reprodução da faixa atual.
        """
        if not self._playing:
            self.play()
            return
        if not self._player_instance:
            return
        self._paused = not self._paused
        if self._player_type == "mpv":
            with contextlib.suppress(tk.TclError):
                self._player_instance.pause = self._paused
        elif self._player_type == "vlc":
            with contextlib.suppress(tk.TclError):
                if self._paused:
                    self._player_instance.pause()
                else:
                    self._player_instance.play()
        self._notify_state()
        self._notify_change()

    def play_index(self, idx: int) -> None:
        """Salta diretamente para a faixa no índice absoluto *idx*.

        Args:
            idx: Índice (base 0) na playlist original.
        """
        if not (0 <= idx < len(self._playlist)):
            return
        self._idx = idx
        with contextlib.suppress(ValueError):
            self._order_idx = self._play_order.index(idx)
        self.play()

    def next(self) -> None:
        """Avança para a próxima faixa na ordem de reprodução.

        ``REPEAT_ONE`` reinicia a faixa atual; ``REPEAT_OFF`` para ao final.
        """
        if not self._playlist:
            return
        if self._repeat == self.REPEAT_ONE:
            if self._playing:
                self.play()
            return
        n = len(self._play_order)
        if n == 0:
            return
        self._order_idx = (self._order_idx + 1) % n
        if self._order_idx == 0 and self._repeat == self.REPEAT_OFF and n > 1:
            self.stop()
            return
        self._idx = self._play_order[self._order_idx]
        if self._playing:
            self.play()
        else:
            self._notify_change()

    def prev(self) -> None:
        """Volta para a faixa anterior.

        Se a posição atual for maior que 3 s, reinicia a faixa atual.
        """
        if not self._playlist:
            return
        if self._position > 3.0:
            self.seek(0.0)
            return
        n = len(self._play_order)
        if n == 0:
            return
        self._order_idx = (self._order_idx - 1) % n
        self._idx = self._play_order[self._order_idx]
        if self._playing:
            self.play()
        else:
            self._notify_change()

    def seek(self, position: float) -> None:
        """Busca a posição *position* (em segundos) na faixa atual.

        Args:
            position: Posição destino em segundos.
        """
        if not self._player_instance:
            return
        with contextlib.suppress(tk.TclError):
            if self._player_type == "mpv":
                self._player_instance.seek(position, reference="absolute")
            elif self._player_type == "vlc":
                self._player_instance.set_time(int(position * 1000))
        self._position = position

    def set_volume(self, volume: int) -> None:
        """Define o volume de reprodução (0-100).

        Args:
            volume: Novo volume.
        """
        self._volume = int(_clamp(volume, 0, 100))
        if not self._player_instance:
            return
        with contextlib.suppress(tk.TclError):
            if self._player_type == "mpv":
                self._player_instance.volume = self._volume
            elif self._player_type == "vlc":
                self._player_instance.audio_set_volume(self._volume)

    def set_shuffle(self, enabled: bool) -> None:
        """Ativa ou desativa o modo aleatório.

        Args:
            enabled: ``True`` para ativar.
        """
        self._shuffle = enabled
        self._rebuild_order()
        self._notify_change()

    def set_repeat(self, mode: str) -> None:
        """Define o modo de repetição.

        Args:
            mode: Um de :attr:`REPEAT_OFF`, :attr:`REPEAT_ONE` ou :attr:`REPEAT_ALL`.
        """
        if mode in (self.REPEAT_OFF, self.REPEAT_ONE, self.REPEAT_ALL):
            self._repeat = mode

    def on_change(self, cb: Callable) -> None:
        """Registra callback chamado quando a faixa atual muda (sem args).

        Args:
            cb: Função chamável sem argumentos.
        """
        self._listeners["change"][self._owner_key(cb)] = cb

    def on_progress(self, cb: Callable) -> None:
        """Registra callback de progresso: ``cb(posicao: float, duracao: float)``.

        Args:
            cb: Função chamável com dois floats.
        """
        self._listeners["progress"][self._owner_key(cb)] = cb

    def on_state(self, cb: Callable) -> None:
        """Registra callback de estado: ``cb(tocando, pausado, carregando)``.

        Args:
            cb: Função chamável com três booleanos.
        """
        self._listeners["state"][self._owner_key(cb)] = cb

    @staticmethod
    def _owner_key(cb: Callable) -> Any:
        """Chave do ouvinte: o objeto dono do método (ou o próprio callable)."""
        return id(getattr(cb, "__self__", cb))

    def add_listener(
        self,
        owner: object,
        change: Callable | None = None,
        progress: Callable | None = None,
        state: Callable | None = None,
    ) -> None:
        """Registra ouvintes de ``owner`` (substitui os anteriores do mesmo dono)."""
        key = id(owner)
        for kind, cb in (("change", change), ("progress", progress), ("state", state)):
            if cb is not None:
                self._listeners[kind][key] = cb

    def remove_listener(self, owner: object) -> None:
        """Remove todos os ouvintes registrados por ``owner``."""
        key = id(owner)
        for table in self._listeners.values():
            table.pop(key, None)

    def _fanout(self, kind: str, *args: Any) -> None:
        """Chama todos os ouvintes de ``kind`` isolando falhas individuais."""
        for cb in list(self._listeners[kind].values()):
            try:
                cb(*args)
            except tk.TclError:
                continue
            except Exception:  # noqa: BLE001
                logger.exception("ouvinte de %s falhou", kind)

    def _spawn_mpv(self, url: str, gen: int) -> None:
        """Inicializa e inicia a reprodução via python-mpv.

        callbacks de gerações antigas são descartados silenciosamente.

        Args:
            url: URL ou caminho local a reproduzir.
            gen: Geração de reprodução no momento do início.
        """
        if gen != self._gen:
            return
        self._player_type = "mpv"
        try:
            import locale

            with contextlib.suppress(tk.TclError):
                locale.setlocale(locale.LC_NUMERIC, "C")
            use_video = self._video_wid is not None
            mpv_kwargs: dict = {
                "ytdl": self._ytdlp,
                "video": use_video,
                "input_default_bindings": False,
                "input_vo_keyboard": False,
                "osc": False,
                "log_handler": lambda lv, c, m: logger.debug("[mpv/%s] %s", c, m),
                "loglevel": "error",
            }
            if use_video and self._video_wid is not None:
                import sys as _sys

                if _sys.platform == "win32":
                    mpv_kwargs["wid"] = str(self._video_wid)
                else:
                    mpv_kwargs["wid"] = str(self._video_wid)
            forced_ao = os.environ.get("CODIS_MEDIA_AO")
            if forced_ao:
                mpv_kwargs["ao"] = forced_ao
            player = mpv.MPV(**mpv_kwargs)

            @player.property_observer("time-pos")
            def _tp(_n, v):
                if v is not None and gen == self._gen:
                    self._position = v
                    if self._on_progress_cb:
                        self._schedule(
                            self._on_progress_cb, self._position, self._duration
                        )

            @player.property_observer("duration")
            def _dur(_n, v):
                if v is not None and gen == self._gen:
                    self._duration = v
                    if self._on_progress_cb:
                        self._schedule(
                            self._on_progress_cb, self._position, self._duration
                        )

            @player.property_observer("pause")
            def _pse(_n, v):
                if v is not None and gen == self._gen:
                    self._paused = bool(v)
                    self._schedule(self._notify_state)

            @player.event_callback("end-file")
            def _eof(_ev):
                if gen == self._gen and self._playing and not self._stop_ev.is_set():
                    self._schedule(self.next)

            if gen != self._gen:
                with contextlib.suppress(tk.TclError):
                    player.terminate()
                return

            with contextlib.suppress(tk.TclError):
                player.volume = self._volume
            self._apply_eq_to(player, "mpv")
            player.play(url)
            self._player_instance = player
            self._loading = False
            self._stop_ev.clear()
            self._schedule(self._notify_state)
        except Exception as exc:
            logger.exception("mpv falhou: %s", exc)
            if gen == self._gen:
                self._schedule(self._cleanup_player)

    def _spawn_vlc(self, url: str, gen: int) -> None:
        """Inicializa e inicia a reprodução via python-vlc.

        Lança uma thread de polling que também verifica a geração antes
        de disparar callbacks.

        Args:
            url: URL ou caminho local a reproduzir.
            gen: Geração de reprodução no momento do início.
        """
        if gen != self._gen:
            return
        self._player_type = "vlc"
        try:
            import sys as _sys

            use_video = self._video_wid is not None
            vlc_flags = ["--quiet"]
            if not use_video:
                vlc_flags += ["--no-video", "--no-xlib"]
            instancia = vlc.Instance(*vlc_flags)  # pylint: disable=no-member
            player = instancia.media_player_new()
            if use_video and self._video_wid is not None:
                with contextlib.suppress(AttributeError, RuntimeError):
                    if _sys.platform == "win32":
                        player.set_hwnd(self._video_wid)
                    elif _sys.platform == "darwin":
                        player.set_nsobject(self._video_wid)
                    else:
                        player.set_xwindow(self._video_wid)
            media = instancia.media_new(url)
            if "youtube.com" in url or "youtu.be" in url:
                media.add_option(":network-caching=1500")
            player.set_media(media)
            with contextlib.suppress(AttributeError, RuntimeError):
                player.audio_set_volume(self._volume)
            self._apply_eq_to(player, "vlc")
            player.play()

            if gen != self._gen:
                with contextlib.suppress(AttributeError, RuntimeError):
                    player.stop()
                return

            self._player_instance = player
            self._loading = False
            self._stop_ev.clear()
            self._schedule(self._notify_state)

            threading.Thread(
                target=self._vlc_poll_loop,
                args=(gen,),
                daemon=True,
                name="CodisVLCPoll",
            ).start()
        except Exception as exc:
            logger.exception("VLC falhou: %s", exc)
            if gen == self._gen:
                self._schedule(self._cleanup_player)

    def _vlc_poll_loop(self, gen: int) -> None:
        """Loop de polling do VLC em thread de background.

        Abandona silenciosamente se a geração mudar.

        Args:
            gen: Geração de reprodução que originou este loop.
        """
        while not self._stop_ev.wait(0.4):
            if gen != self._gen:
                break
            player = self._player_instance
            if player is None or self._player_type != "vlc":
                break
            with contextlib.suppress(tk.TclError):
                pos = player.get_time() / 1000.0
                dur = player.get_length() / 1000.0
                if pos > 0:
                    self._position = pos
                if dur > 0:
                    self._duration = dur
                if self._on_progress_cb and pos > 0 and gen == self._gen:
                    self._schedule(self._on_progress_cb,
                                   self._position, self._duration)
                if (_ := player.get_state()) in (
                    vlc.State.Ended,
                    vlc.State.Error,
                ):  # pylint: disable=no-member
                    if (
                        gen == self._gen
                        and self._playing
                        and not self._stop_ev.is_set()
                    ):
                        self._schedule(self.next)
                    break

    def _schedule(self, callback: Callable, *args) -> None:
        """Agenda *callback* na thread principal do Tkinter via ``after(0)``.

        Args:
            callback: Função a agendar.
            *args:    Argumentos posicionais passados ao callback.
        """
        root = self._get_tk_root()
        if root is not None:
            with contextlib.suppress(tk.TclError):
                root.after(0, lambda: callback(*args))
                return
        with contextlib.suppress(tk.TclError):
            callback(*args)

    def _schedule_if(self, gen: int, callback: Callable, *args) -> None:
        """Agenda *callback* somente se a geração atual ainda for *gen*.

        Args:
            gen:      Geração esperada.
            callback: Função a agendar.
            *args:    Argumentos posicionais.
        """

        def _guarded():
            if gen == self._gen:
                callback(*args)

        root = self._get_tk_root()
        if root is not None:
            with contextlib.suppress(tk.TclError):
                root.after(0, _guarded)
                return
        with contextlib.suppress(tk.TclError):
            _guarded()

    def _get_tk_root(self) -> tk.Misc | None:
        """Retorna a janela raiz do Tkinter ou ``None``."""
        if self._tk_root is not None:
            return self._tk_root
        return getattr(tk, "_default_root", None)

    def _cleanup_player(self) -> None:
        """Limpa o estado após erro de inicialização do backend."""
        self._loading = False
        self._playing = False
        self._paused = False
        self._player_instance = None
        self._notify_state()
        self._notify_change()

    def _notify_change(self) -> None:
        """Dispara o callback de mudança de faixa, se registrado."""
        if self._on_change_cb:
            with contextlib.suppress(tk.TclError):
                self._on_change_cb()

    def _notify_state(self) -> None:
        """Dispara o callback de estado, se registrado."""
        if self._on_state_cb:
            with contextlib.suppress(tk.TclError):
                self._on_state_cb(self._playing, self._paused, self._loading)

    @property
    def current_track(self) -> str:
        """Nome da faixa atualmente selecionada, ou ``"Sem playlist"``."""
        return self._playlist[self._idx]["name"] if self._playlist else "Sem playlist"

    @property
    def current_url(self) -> str:
        """URL/caminho da faixa atualmente selecionada."""
        return self._playlist[self._idx]["url"] if self._playlist else ""

    @property
    def playlist(self) -> list[dict[str, str]]:
        """Cópia da lista de entradas da playlist (somente leitura)."""
        return list(self._playlist)

    @property
    def is_playing(self) -> bool:
        """``True`` se o player estiver em modo de reprodução ativa."""
        return self._playing

    @property
    def is_paused(self) -> bool:
        """``True`` se a reprodução estiver pausada."""
        return self._paused

    @property
    def is_loading(self) -> bool:
        """``True`` se o backend ainda estiver carregando a mídia."""
        return self._loading

    @property
    def track_count(self) -> int:
        """Número de faixas na playlist."""
        return len(self._playlist)

    @property
    def current_index(self) -> int:
        """Índice (base 0) da faixa atualmente selecionada."""
        return self._idx

    @property
    def position(self) -> float:
        """Posição atual de reprodução em segundos."""
        return self._position

    @property
    def duration(self) -> float:
        """Duração total da faixa atual em segundos (0 enquanto carrega)."""
        return self._duration

    @property
    def volume(self) -> int:
        """Volume atual (0-100)."""
        return self._volume

    @property
    def shuffle(self) -> bool:
        """``True`` se o modo aleatório estiver ativado."""
        return self._shuffle

    @property
    def repeat(self) -> str:
        """Modo de repetição: ``"off"``, ``"one"`` ou ``"all"``."""
        return self._repeat

    @property
    def has_player(self) -> bool:
        """``True`` se pelo menos um backend (mpv ou vlc) estiver disponível."""
        return self._available


def _stop_instance_bg(
    instance: Any,
    player_type: str | None,
    stop_ev: threading.Event,
) -> None:
    """Para uma instância de backend em thread de background, sem bloquear a UI.

    Projetada para ser chamada em uma thread daemon. Sinaliza o evento de

    Args:
        instance:    Instância MPV ou VLC a encerrar (pode ser ``None``).
        player_type: ``"mpv"`` ou ``"vlc"`` (pode ser ``None``).
        stop_ev:     Evento de parada associado ao backend.
    """
    with contextlib.suppress(tk.TclError):
        stop_ev.set()
    if instance is None:
        return
    with contextlib.suppress(tk.TclError):
        if player_type == "mpv":
            instance.terminate()
        elif player_type == "vlc":
            instance.stop()


class ZenPlaylistPopup:
    """Popup leve exibido ao clicar ☰ no HUD para listar e selecionar faixas.

    É um ``tk.Toplevel`` filho do HUD, sem decoração de janela.
    A faixa atual aparece destacada; clique em qualquer faixa a reproduz.
    Fecha com Escape ou clicando fora da lista.

    Args:
        anchor:  Widget âncora (botão ☰) para posicionamento.
        player:  Instância de :class:`ZenMediaPlayer` a controlar.
        bg:      Cor de fundo.
        fg:      Cor do texto.
        fg_dim:  Cor do texto secundário.
        accent:  Cor de destaque para a faixa atual.
    """

    _MAX_VISIBLE = 12

    def __init__(
        self,
        anchor: tk.Widget,
        player: ZenMediaPlayer,
        bg: str,
        fg: str,
        fg_dim: str,
        accent: str,
    ) -> None:
        """Inicializa o popup de playlist do player zen."""
        self._player = player
        self._destroyed = False

        root = anchor.winfo_toplevel()
        self._win = tk.Toplevel(root)
        self._win.overrideredirect(True)
        self._win.configure(bg=accent)
        self._win.attributes("-topmost", True)

        self._bg = bg
        self._fg = fg
        self._fg_dim = fg_dim
        self._accent = accent

        self._build(anchor)
        self._win.bind("<Escape>", lambda _: self._fechar())
        self._win.bind("<FocusOut>", self._on_focus_out)
        self._win.focus_set()

    def _build(self, anchor: tk.Widget) -> None:
        """Constrói a lista e posiciona acima do botão âncora.

        Args:
            anchor: Widget de referência para o posicionamento.
        """
        inner = tk.Frame(self._win, bg=self._bg)
        inner.pack(fill="both", expand=True, padx=1, pady=1)

        cab = tk.Frame(inner, bg=self._bg)
        cab.pack(fill="x", padx=6, pady=(5, 3))
        tk.Label(
            cab,
            text=_t("hc.zen_media.playlist"),
            bg=self._bg,
            fg=self._fg,
            font=("Segoe UI", 9, "bold"),
        ).pack(side="left")
        tk.Button(
            cab,
            text="✕",
            bg=self._bg,
            fg=self._fg_dim,
            relief="flat",
            bd=0,
            font=("Segoe UI", 8),
            cursor="hand2",
            command=self._fechar,
        ).pack(side="right")

        n_vis = min(len(self._player._playlist), self._MAX_VISIBLE)
        frame_lista = tk.Frame(inner, bg=self._bg)
        frame_lista.pack(fill="both", expand=True, padx=4, pady=(0, 4))

        sb = tk.Scrollbar(frame_lista, orient="vertical")
        self._lb = tk.Listbox(
            frame_lista,
            bg=self._bg,
            fg=self._fg,
            selectbackground=self._accent,
            selectforeground=self._bg,
            font=("Segoe UI", 9),
            relief="flat",
            bd=0,
            highlightthickness=0,
            activestyle="none",
            height=max(3, n_vis),
            yscrollcommand=sb.set,
        )
        sb.config(command=self._lb.yview)
        sb.pack(side="right", fill="y")
        self._lb.pack(side="left", fill="both", expand=True)

        current = self._player.current_index
        for i, entry in enumerate(self._player._playlist):
            prefix = "▶ " if i == current else "  "
            name = entry["name"]
            if len(name) > 40:
                name = name[:38] + "..."
            self._lb.insert("end", f"{prefix}{name}")
            if i == current:
                self._lb.itemconfig(i, fg=self._accent)

        if 0 <= current < self._lb.size():
            self._lb.see(current)
            self._lb.selection_set(current)

        self._lb.bind("<Button-1>", self._on_click)
        self._lb.bind("<Double-Button-1>", self._on_click)
        self._lb.bind("<Return>", self._on_enter)

        self._win.update_idletasks()
        ax = anchor.winfo_rootx()
        ay = anchor.winfo_rooty()
        pw = self._win.winfo_reqwidth()
        ph = self._win.winfo_reqheight()
        sw = anchor.winfo_screenwidth()
        sh = anchor.winfo_screenheight()
        x = int(_clamp(ax, 0, sw - pw))
        y = int(_clamp(ay - ph - 4, 0, sh - ph))
        self._win.geometry(f"+{x}+{y}")

    def _on_click(self, e: tk.Event) -> None:
        """Reproduz a faixa clicada."""
        idx = self._lb.nearest(e.y)
        if 0 <= idx < len(self._player._playlist):
            self._player.play_index(idx)
            self._fechar()

    def _on_enter(self, _e: tk.Event) -> None:
        """Reproduz a faixa selecionada ao pressionar Enter."""
        sel = self._lb.curselection()
        if sel:
            self._player.play_index(sel[0])
            self._fechar()

    def _on_focus_out(self, _e: tk.Event) -> None:
        """Fecha o popup ao perder o foco."""
        if not self._destroyed:
            self._win.after(150, self._fechar_se_sem_foco)

    def _fechar_se_sem_foco(self) -> None:
        """Fecha somente se a janela realmente não tiver mais foco."""
        if self._destroyed:
            return
        with contextlib.suppress(tk.TclError):
            if not self._win.focus_displayof():
                self._fechar()

    def _fechar(self) -> None:
        """Fecha e destrói o popup."""
        if self._destroyed:
            return
        self._destroyed = True
        with contextlib.suppress(tk.TclError):
            self._win.destroy()


class ZenMiniOrb:
    """
    Floating mini-player redesenhado: pílula arredondada com ícone animado,
    glow pulsante, name da faixa truncado, barra de progresso e tempo.
    Clique único expande para o HUD completo; arrastar move pela tela.

    Args:
        parent:    Widget pai (janela raiz do Codis).
        player:    Instância de :class:`ZenMediaPlayer` a monitorar.
        on_expand: Callback chamado quando o usuário clica para expandir.
        tmgr:      Gerenciador de temas (opcional).
    """

    _PULSE_STEP = 0.08
    _PULSE_MS = 25
    _W = 210
    _H = 46
    _R = 23

    def __init__(
        self,
        parent: tk.Widget,
        player: ZenMediaPlayer,
        on_expand: Callable | None = None,
        tmgr=None,
    ) -> None:
        """Inicializa o mini orb flutuante do player zen."""
        t = tmgr.theme if tmgr else None
        accent = getattr(t, "accent", "#7c4dff") if t else "#7c4dff"
        bg = getattr(t, "floating_bg", "#111127") if t else "#111127"
        fg = getattr(t, "fg", "#e8eaf6") if t else "#e8eaf6"
        fg_dim = getattr(t, "fg_muted", "#546e7a") if t else "#546e7a"

        self._player = player
        self._accent = accent
        self._bg = bg
        self._fg = fg
        self._fg_dim = fg_dim
        self._on_expand = on_expand
        self._destroyed = False
        self._phase = 0.0
        self._anim_job: str | None = None
        self._drag_x = 0
        self._drag_y = 0
        self._drag_moved = False
        self._hovered = False

        W, H = self._W, self._H

        self._win = tk.Toplevel(parent)
        self._win.overrideredirect(True)
        self._win.attributes("-topmost", True)
        self._win.configure(bg=bg)
        self._win.geometry(f"{W}x{H}")
        self._win.resizable(False, False)
        with contextlib.suppress(tk.TclError):
            self._win.attributes("-alpha", 0.93)

        self._canvas = tk.Canvas(
            self._win,
            width=W,
            height=H,
            bg=bg,
            highlightthickness=0,
            cursor="hand2",
        )
        self._canvas.pack()

        for ev, cb in [
            ("<ButtonPress-1>", self._drag_start),
            ("<B1-Motion>", self._drag_move),
            ("<ButtonRelease-1>", self._drag_end),
            ("<Enter>", self._on_hover_enter),
            ("<Leave>", self._on_hover_leave),
        ]:
            self._canvas.bind(ev, cb)

        player.on_change(self._on_player_change)
        player.on_state(self._on_player_state)

        self._draw()
        self._start_anim()

    def _fill_pill(self, x0: int, y0: int, x1: int, y1: int, r: int, fill: str) -> None:
        """Preenche um retângulo com cantos completamente arredondados."""
        c = self._canvas
        d = r * 2
        c.create_arc(x0, y0, x0 + d, y0 + d, start=90,
                     extent=90, fill=fill, outline="")
        c.create_arc(x1 - d, y0, x1, y0 + d, start=0,
                     extent=90, fill=fill, outline="")
        c.create_arc(
            x0, y1 - d, x0 + d, y1, start=180, extent=90, fill=fill, outline=""
        )
        c.create_arc(
            x1 - d, y1 - d, x1, y1, start=270, extent=90, fill=fill, outline=""
        )
        c.create_rectangle(x0 + r, y0, x1 - r, y1, fill=fill, outline="")
        c.create_rectangle(x0, y0 + r, x1, y1 - r, fill=fill, outline="")

    def _draw(self) -> None:
        """Redesenha a pílula com estado atual do player."""
        if self._destroyed:
            return
        c = self._canvas
        c.delete("all")
        W, H = self._W, self._H
        R = self._R

        playing = self._player.is_playing and not self._player.is_paused
        paused = self._player.is_paused

        self._fill_pill(0, 0, W, H, R, self._bg)

        border_col = self._accent if playing else (
            "#cc8800" if paused else "#2a2a40")
        c.create_arc(
            0,
            0,
            2 * R,
            2 * R,
            start=90,
            extent=90,
            outline=border_col,
            style="arc",
            width=1,
        )
        c.create_arc(
            W - 2 * R,
            0,
            W,
            2 * R,
            start=0,
            extent=90,
            outline=border_col,
            style="arc",
            width=1,
        )
        c.create_arc(
            0,
            H - 2 * R,
            2 * R,
            H,
            start=180,
            extent=90,
            outline=border_col,
            style="arc",
            width=1,
        )
        c.create_arc(
            W - 2 * R,
            H - 2 * R,
            W,
            H,
            start=270,
            extent=90,
            outline=border_col,
            style="arc",
            width=1,
        )
        c.create_line(R, 0, W - R, 0, fill=border_col, width=1)
        c.create_line(R, H, W - R, H, fill=border_col, width=1)
        c.create_line(0, R, 0, H - R, fill=border_col, width=1)
        c.create_line(W, R, W, H - R, fill=border_col, width=1)

        orb_cx = H // 2
        orb_cy = H // 2
        orb_r_base = H // 2 - 8

        pulse = 0.78 + 0.22 * math.sin(self._phase) if playing else 1.0
        orb_r = max(4, int(orb_r_base * pulse))

        if playing:
            gr = orb_r + 5
            c.create_oval(
                orb_cx - gr,
                orb_cy - gr,
                orb_cx + gr,
                orb_cy + gr,
                fill=_alpha_blend_hex(self._accent, self._bg, 0.2),
                outline="",
            )

        orb_col = self._accent if playing else (
            "#cc8800" if paused else "#1e1e38")
        c.create_oval(
            orb_cx - orb_r,
            orb_cy - orb_r,
            orb_cx + orb_r,
            orb_cy + orb_r,
            fill=orb_col,
            outline="",
        )

        icon = "▶" if playing else ("⏸" if paused else "♪")
        icon_fg = "#ffffff" if (playing or paused) else self._fg_dim
        c.create_text(
            orb_cx, orb_cy, text=icon, fill=icon_fg, font=(
                "Segoe UI", 9, "bold")
        )

        if self._player.duration > 0:
            frac = _clamp(self._player.position /
                          self._player.duration, 0.0, 1.0)
            arc_r = orb_r_base + 4
            c.create_arc(
                orb_cx - arc_r,
                orb_cy - arc_r,
                orb_cx + arc_r,
                orb_cy + arc_r,
                start=90,
                extent=-(frac * 360),
                outline=self._accent,
                width=2,
                style="arc",
            )

        tx = H + 2
        tw = W - tx - 6
        track = self._player.current_track or (
            "Tocando..." if playing else "Zen Media")
        max_ch = max(6, tw // 6)
        label = track if len(track) <= max_ch else track[: max_ch - 1] + "..."
        c.create_text(
            tx,
            H // 2 - 6,
            text=label,
            anchor="w",
            fill=self._fg if (playing or paused) else self._fg_dim,
            font=("Segoe UI", 8, "bold"),
        )

        if self._player.duration > 0:
            ts = f"{_fmt_time(self._player.position)} / {_fmt_time(self._player.duration)}"
        elif playing or paused:
            ts = _fmt_time(self._player.position)
        else:
            ts = "- : -"
        c.create_text(
            tx, H // 2 + 7, text=ts, anchor="w", fill=self._fg_dim, font=("Segoe UI", 7)
        )

        if self._player.duration > 0:
            frac = _clamp(self._player.position /
                          self._player.duration, 0.0, 1.0)
            bx0 = R
            bx1 = W - R
            by = H - 4
            bw = bx1 - bx0
            c.create_line(bx0, by, bx1, by, fill="#2a2a40",
                          width=2, capstyle="round")
            filled = bx0 + int(bw * frac)
            if filled > bx0:
                c.create_line(
                    bx0, by, filled, by, fill=self._accent, width=2, capstyle="round"
                )

    def _start_anim(self) -> None:
        self._tick_anim()

    def _tick_anim(self) -> None:
        if self._destroyed:
            return
        self._phase += self._PULSE_STEP
        self._draw()
        self._anim_job = self._win.after(self._PULSE_MS, self._tick_anim)

    def _on_hover_enter(self, _e=None) -> None:
        self._hovered = True
        with contextlib.suppress(tk.TclError):
            self._win.attributes("-alpha", 1.0)

    def _on_hover_leave(self, _e=None) -> None:
        self._hovered = False
        with contextlib.suppress(tk.TclError):
            self._win.attributes("-alpha", 0.93)

    def _on_player_change(self) -> None:
        if not self._destroyed:
            with contextlib.suppress(tk.TclError):
                self._win.after(0, self._draw)

    def _on_player_state(self, *_) -> None:
        if not self._destroyed:
            with contextlib.suppress(tk.TclError):
                self._win.after(0, self._draw)

    def _drag_start(self, e: tk.Event) -> None:
        self._drag_x = e.x_root
        self._drag_y = e.y_root
        self._drag_moved = False

    def _drag_move(self, e: tk.Event) -> None:
        dx = e.x_root - self._drag_x
        dy = e.y_root - self._drag_y
        if abs(dx) > 3 or abs(dy) > 3:
            self._drag_moved = True
        self._drag_x = e.x_root
        self._drag_y = e.y_root
        with contextlib.suppress(tk.TclError):
            self._win.geometry(
                f"+{self._win.winfo_x() + dx}+{self._win.winfo_y() + dy}"
            )

    def _drag_end(self, _e: tk.Event) -> None:
        if not self._drag_moved and self._on_expand:
            with contextlib.suppress(tk.TclError):
                self._on_expand()
        self._drag_moved = False

    def show(self) -> None:
        """Exibe o mini orb na interface."""
        if self._destroyed:
            return
        with contextlib.suppress(tk.TclError):
            self._win.deiconify()
            self._win.lift()

    def hide(self) -> None:
        """Oculta o mini orb da interface."""
        if self._destroyed:
            return
        with contextlib.suppress(tk.TclError):
            self._win.withdraw()

    def move_to(self, x: int, y: int) -> None:
        """Move o mini orb para a posição (x, y)."""
        with contextlib.suppress(tk.TclError):
            self._win.geometry(f"+{x}+{y}")

    def winfo_exists(self) -> bool:
        """Retorna ``True`` se o orb ainda existe na interface."""
        try:
            return bool(self._win.winfo_exists())
        except tk.TclError:
            return False

    def stop_orb(self) -> None:
        """Para a animação de pulsação do orb."""
        self._destroyed = True
        if self._anim_job:
            with contextlib.suppress(tk.TclError):
                self._win.after_cancel(self._anim_job)
            self._anim_job = None

    def destroy(self) -> None:
        """Destrói o orb e para recursos associados."""
        self.stop_orb()
        with contextlib.suppress(tk.TclError):
            self._win.destroy()


class ZenMediaDialog:
    """Diálogo modal para configuração da playlist e opções de autoplay.

    Permite colar URLs do YouTube/streams ou adicionar uma folder local.
    Oferece botões de Salvar, Tocar Agora e Parar.

    Args:
        parent: Widget pai (janela raiz do Codis).
        tmgr:   Gerenciador de temas; se ``None`` usa paleta escura padrão.
        player: Instância de :class:`ZenMediaPlayer` a configurar.
    """

    def __init__(self, parent: tk.Widget, tmgr, player: ZenMediaPlayer, cfg) -> None:
        """Inicializa o diálogo principal do player de mídia zen."""
        t = tmgr.theme if tmgr else None
        bg = getattr(t, "floating_bg", "#06061a") if t else "#06061a"
        borda = getattr(t, "accent", "#7c4dff") if t else "#7c4dff"
        fg = getattr(t, "fg", "#e8eaf6") if t else "#e8eaf6"
        fg_dim = getattr(t, "fg_muted", "#546e7a") if t else "#546e7a"

        self._player = player
        self._cfg = cfg

        self._dlg = tk.Toplevel(parent)
        self._dlg.title("🎵  Zen Media - Playlist")
        self._dlg.configure(bg=bg)
        self._dlg.transient(parent)
        safe_grab_set(self._dlg)
        self._dlg.geometry("540x520")
        self._dlg.minsize(520, 420)

        tk.Frame(self._dlg, bg=borda, height=2).pack(fill="x")
        tk.Label(
            self._dlg,
            text=_t("hc.zen_media.zen_media_playlist"),
            bg=bg,
            fg=fg,
            font=("Segoe UI", 11, "bold"),
            pady=10,
        ).pack(fill="x", padx=14)

        partes = []
        if player.has_player:
            libs = []
            if MPV_AVAILABLE:
                libs.append("python-mpv")
            if VLC_AVAILABLE:
                libs.append("python-vlc")
            partes.append(f"Bibliotecas: {' + '.join(libs)}")
        else:
            partes.append("⚠  Nenhuma biblioteca instalada")
        partes.append(
            "yt-dlp: ok" if player._ytdlp else "yt-dlp: nao encontrado")
        tk.Label(
            self._dlg,
            text="  ".join(partes),
            bg=bg,
            fg=fg_dim,
            font=("Segoe UI", 8),
        ).pack(anchor="w", padx=14, pady=(0, 2))

        tk.Label(
            self._dlg,
            text=_t("zen_media.url_hint"),
            bg=bg,
            fg=fg_dim,
            font=("Segoe UI", 8),
            justify="left",
        ).pack(anchor="w", padx=14, pady=(0, 6))

        tf = tk.Frame(self._dlg, bg=borda, padx=1, pady=1)
        tf.pack(fill="both", expand=True, padx=14)
        self._txt = tk.Text(
            tf,
            bg="#030312",
            fg=fg,
            insertbackground=borda,
            font=("Segoe UI", 9),
            relief="flat",
            bd=0,
            highlightthickness=0,
        )
        sb = tk.Scrollbar(tf, command=self._txt.yview)
        self._txt.config(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self._txt.pack(fill="both", expand=True, padx=4, pady=4)

        for e in player._playlist:
            self._txt.insert("end", e["url"] + "\n")

        fr_opcoes = tk.Frame(self._dlg, bg=bg)
        fr_opcoes.pack(fill="x", padx=14, pady=(6, 0))

        kw = {
            "bg": "#0d0d30",
            "fg": fg,
            "relief": "flat",
            "font": ("Segoe UI", 9),
            "padx": 12,
            "pady": 4,
            "cursor": "hand2",
        }

        tk.Button(
            fr_opcoes,
            text=_t("hc.zen_media.adicionar_pasta"),
            command=self._choose_folder,
            **kw,
        ).pack(side="left", padx=(0, 8))

        self._autoplay_var = tk.BooleanVar(
            value=cfg.get("zen_media_autoplay", False))
        tk.Checkbutton(
            fr_opcoes,
            text=_t("hc.zen_media.auto_play_ao_entrar_no_zen_mod"),
            variable=self._autoplay_var,
            bg=bg,
            fg=fg_dim,
            selectcolor=bg,
            font=("Segoe UI", 8),
            activebackground=bg,
            activeforeground=fg,
        ).pack(side="left", padx=10)

        fr_rodape = tk.Frame(self._dlg, bg=bg)
        fr_rodape.pack(fill="x", padx=14, pady=(10, 12))

        def _saveit():
            """Salva a playlist e opções no objeto de configuração."""
            urls = [
                l_.strip()
                for l_ in self._txt.get("1.0", "end-1c").splitlines()
                if l_.strip()
            ]
            player.set_playlist(urls)
            cfg.set("zen_media_playlist", urls)
            cfg.set("zen_media_autoplay", self._autoplay_var.get())
            with contextlib.suppress(tk.TclError):
                cfg.save()
            self._dlg.destroy()

        def _play_now():
            """Salva e inicia a reprodução imediatamente."""
            _saveit()
            player.play()

        def _stopit():
            """Para a reprodução e fecha o diálogo."""
            player.stop()
            self._dlg.destroy()

        tk.Button(
            fr_rodape, text=_t("hc.zen_media.tocar_agora"), command=_play_now, **kw
        ).pack(side="left", padx=(0, 4))
        tk.Button(
            fr_rodape,
            text=_t("hc.zen_media.parar"),
            command=_stopit,
            bg="#0d0d30",
            fg="#ff4081",
            relief="flat",
            font=("Segoe UI", 9),
            padx=12,
            pady=4,
            cursor="hand2",
        ).pack(side="left", padx=(0, 4))
        tk.Button(
            fr_rodape, text=_t("hc.zen_media.salvar"), command=_saveit, **kw
        ).pack(side="left", padx=(0, 4))
        tk.Button(
            fr_rodape, text=_t("hc.zen_media.cancelar"), command=self._dlg.destroy, **kw
        ).pack(side="right")

        tk.Label(
            self._dlg,
            text=_t("hc.zen_media.requer_python_vlc_ou_python_mp"),
            bg=bg,
            fg="#2d3080",
            font=("Segoe UI", 7),
        ).pack(pady=(0, 4))

    def _choose_folder(self) -> None:
        """Abre seletor de folder e carrega os arquivos de mídia encontrados."""
        folder = filedialog.askdirectory(
            parent=self._dlg,
            title=_t("hc.zen_media.selecione_uma_pasta_com_m_sica"),
        )
        if folder:
            self._player.load_folder(folder)
            self._update_text()

    def _update_text(self) -> None:
        """Sincroniza a área de texto com a playlist atual do player."""
        self._txt.delete("1.0", tk.END)
        for e in self._player._playlist:
            self._txt.insert(tk.END, e["url"] + "\n")
