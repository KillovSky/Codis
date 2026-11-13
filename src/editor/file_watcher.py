"""Codis - FileWatcher: detecta modificações externas em arquivos abertos.

Utiliza polling leve baseado em ``os.stat()`` (sem dependência de inotify/FSEvents).
Intervalo de verificação: 2 segundos por padrão - negligenciável em CPU.

Uso:
    watcher = FileWatcher(on_changed=callback, interval_ms=2000)
    watcher.watch(Path("/caminho/para/arquivo.py"))
    watcher.stop()   # ao fechar o arquivo/aba
"""

from __future__ import annotations

import contextlib
import os
import tkinter as tk
from collections.abc import Callable
from pathlib import Path


class FileWatcher:
    """Observa modificações externas em um único arquivo via polling.

    Args:
        widget:       Widget Tk para usar ``after()`` (não precisa ser visível).
        on_changed:   Callback chamado quando o arquivo é modificado externamente.
                      Recebe o Path do arquivo modificado.
        interval_ms:  Intervalo de polling em milissegundos (padrão: 2000).
    """

    def __init__(
        self,
        widget: tk.Widget,
        on_changed: Callable[[Path], None],
        interval_ms: int = 2000,
    ) -> None:
        """Inicializa o observador de mudanças em arquivo do sistema de arquivos."""
        self._widget = widget
        self._on_changed = on_changed
        self._interval = interval_ms
        self._path: Path | None = None
        self._mtime: float = 0.0
        self._size: int = 0
        self._job: str | None = None
        self._paused: bool = False
        self._notified: bool = False

    def watch(self, path: Path) -> None:
        """Começa a observar *path*. Cancela qualquer watch anterior.

        Args:
            path: Arquivo a observar. None cancela o watch.
        """
        self.stop()
        if not path or not path.is_file():
            return
        self._path = path
        self._update_stat()
        self._notified = False
        self._schedule()

    def stop(self) -> None:
        """Para o polling. Seguro chamar mesmo sem watch ativo."""
        if self._job:
            with contextlib.suppress(tk.TclError):
                self._widget.after_cancel(self._job)
        self._job = None
        self._path = None
        self._mtime = 0.0
        self._size = 0
        self._notified = False

    def pause(self) -> None:
        """Pausa temporariamente o polling (ex: durante save do próprio Codis)."""
        self._paused = True

    def resume(self, grace_ms: int = 1500) -> None:
        """Retoma o polling após uma pausa.

        Args:
            grace_ms: Tempo de espera antes de reativar (para o write do SO
                       terminar sem falso positivo).
        """

        def _do_resume():
            """Limpa o estado de pausa e atualiza o baseline sem notificar."""
            self._paused = False
            if self._path:
                self._update_stat()
                self._notified = False

        with contextlib.suppress(tk.TclError):
            self._widget.after(grace_ms, _do_resume)

    def reset_baseline(self) -> None:
        """Atualiza o baseline (mtime/size) sem disparar notificação.

        Chame após salvar para não acusar o próprio write como externo.
        """
        self._update_stat()
        self._notified = False

    def _update_stat(self) -> None:
        """Atualiza mtime e size de referência."""
        if not self._path:
            return
        with contextlib.suppress(OSError, ValueError):
            st = os.stat(self._path)
            self._mtime = st.st_mtime
            self._size = st.st_size

    def _schedule(self) -> None:
        """Agenda a próxima verificação de polling via ``after()``."""
        with contextlib.suppress(tk.TclError):
            self._job = self._widget.after(self._interval, self._poll)

    def _poll(self) -> None:
        """Verifica se o arquivo foi modificado ou removido desde a última checagem.

        Notifica ``on_changed`` no máximo uma vez por modificação detectada,
        evitando disparos repetidos enquanto o estado externo permanece o
        mesmo. Reagenda a si mesma ao final, mantendo o ciclo de polling.
        """
        self._job = None
        if not self._path or self._paused:
            self._schedule()
            return

        try:
            if not self._path.exists():
                if not self._notified:
                    self._notified = True
                    self._on_changed(self._path)
                self._schedule()
                return

            st = os.stat(self._path)
            if st.st_mtime != self._mtime or st.st_size != self._size:
                self._mtime = st.st_mtime
                self._size = st.st_size
                if not self._notified:
                    self._notified = True
                    self._on_changed(self._path)
        except OSError:
            pass

        self._schedule()
