"""Codis - Execução de Plugins em Processos Separados.

Arquitetura:
  - Cada plugin marcado como ``isolated: true`` no MANIFEST é lançado em um
    processo filho via ``multiprocessing`` ou subprocess Python.
  - Comunicação bidirecional via ``multiprocessing.Queue`` serializada com
    ``json`` (não pickle, para segurança contra execução arbitrária).
  - O processo filho importa e executa o plugin via um stub seguro
    (``_plugin_worker``) que NÃO tem acesso ao Tkinter nem ao estado do app.
  - Chamadas de API (ex: output, status, comandos) são enviadas ao processo
    principal como mensagens JSON e aplicadas no thread UI via ``root.after()``.
  - Travamentos no plugin NÃO travam o editor; o processo é reiniciável.
  - Timeout configurável: processos sem resposta são encerrados e reportados.

Protocolo de mensagens (JSON): -> para o worker:  {"type": "call", "method": "...", "args": [...], "id": N} -> para o worker:  {"type": "event", "event": "...", "data": {...}} <- do worker:      {"type": "result", "id": N, "value": ..., "error": "..."} <- do worker:      {"type": "api", "method": "...", "args": [...]} <- do worker:      {"type": "log", "level": "...", "msg": "..."} <- do worker:      {"type": "ready"} <- do worker:      {"type": "exit", "code": N}
"""

from __future__ import annotations

import contextlib
import json
import logging
import multiprocessing
import os
import queue
import threading
import time
import traceback
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger("Codis.PluginProc")

_CALL_TIMEOUT = 10.0
_POLL_INTERVAL = 0.05
_HEARTBEAT_TIMEOUT = 30.0
_MAX_RESTARTS = 3


def _plugin_worker(
    plugin_path: str,
    plugin_id: str,
    in_queue: multiprocessing.Queue,
    out_queue: multiprocessing.Queue,
) -> None:
    """Ponto de entrada do processo filho de plugin.

    Importa o módulo do plugin, chama ``activate(api)`` com uma API proxy
    e fica em loop processando mensagens do processo principal.

    Restrições intencionais:
      - Sem acesso a Tkinter (não está na mesma thread de UI).
      - Toda comunicação com o editor é via ``out_queue`` JSON.
      - Sem acesso ao estado global do CodisApp.
    """
    import importlib.util

    logging.basicConfig(
        level=logging.WARNING,
        format=f"PLUGIN[{plugin_id}] %(levelname)s %(message)s",
    )
    log = logging.getLogger(f"Codis.plugin.{plugin_id}")

    def _send(msg: dict) -> None:
        """Envia mensagem JSON para o processo principal."""
        with contextlib.suppress(Exception):
            out_queue.put_nowait(json.dumps(msg))

    class _ProxyAPI:
        """API de plugin segura para uso em processo isolado."""

        def __init__(self) -> None:
            """Inicializa o proxy de API para o contexto de execução de plugin."""
            self._call_id = 0

        def append_output(self, text: str, channel: str = "Plugin") -> None:
            """Acrescenta texto ao buffer de saída do processo."""
            _send({"type": "api", "method": "output", "args": [channel, text]})

        def set_status(self, message: str) -> None:
            """Define o status atual do processo."""
            _send({"type": "api", "method": "status", "args": [message]})

        def log_debug(self, msg: str) -> None:
            """Registra mensagem de nível DEBUG."""
            _send({"type": "log", "level": "debug", "msg": msg})

        def log_info(self, msg: str) -> None:
            """Registra mensagem de nível INFO."""
            _send({"type": "log", "level": "info", "msg": msg})

        def log_warning(self, msg: str) -> None:
            """Registra mensagem de nível WARNING."""
            _send({"type": "log", "level": "warning", "msg": msg})

        def log_error(self, msg: str) -> None:
            """Registra mensagem de nível ERROR."""
            _send({"type": "log", "level": "error", "msg": msg})

        def request(self, method: str, *args: Any) -> Any:
            """Envia uma requisição síncrona ao processo principal."""
            self._call_id += 1
            call_id = self._call_id
            _send({"type": "call", "method": method,
                  "args": list(args), "id": call_id})
            deadline = time.monotonic() + _CALL_TIMEOUT
            while time.monotonic() < deadline:
                try:
                    raw = in_queue.get(timeout=0.1)
                    msg = json.loads(raw)
                    if msg.get("type") == "result" and msg.get("id") == call_id:
                        if msg.get("error"):
                            raise RuntimeError(msg["error"])
                        return msg.get("value")
                except queue.Empty:
                    continue
                except (json.JSONDecodeError, KeyError):
                    continue
            raise TimeoutError(f"Timeout aguardando resposta de '{method}'")

    api = _ProxyAPI()
    mod = None

    try:
        spec = importlib.util.spec_from_file_location(
            f"codis_plugin_{plugin_id}", plugin_path
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        if hasattr(mod, "activate"):
            mod.activate(api)

        _send({"type": "ready"})
        log.info("Plugin '%s' ativado em processo isolado.", plugin_id)

    except Exception:
        err = traceback.format_exc()
        _send(
            {"type": "log", "level": "error",
                "msg": f"Falha ao ativar plugin:\n{err}"}
        )
        _send({"type": "exit", "code": 1})
        return

    try:
        while True:
            try:
                raw = in_queue.get(timeout=1.0)
            except queue.Empty:
                try:
                    os.getppid()
                except OSError:
                    break
                continue

            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue

            mtype = msg.get("type", "")

            if mtype == "event":
                event_name = msg.get("event", "")
                data = msg.get("data", {})
                handler = getattr(mod, f"on_{event_name}", None)
                if callable(handler):
                    with contextlib.suppress(Exception):
                        handler(data)

            elif mtype == "shutdown":
                break

    except KeyboardInterrupt:
        pass
    finally:
        if mod and hasattr(mod, "deactivate"):
            with contextlib.suppress(Exception):
                mod.deactivate()
        _send({"type": "exit", "code": 0})


@dataclass
class _ProcessRecord:
    """Estado de um plugin rodando em processo separado."""

    plugin_id: str
    plugin_path: Path
    process: multiprocessing.Process
    in_queue: multiprocessing.Queue
    out_queue: multiprocessing.Queue
    started_at: float = 0.0
    ready: bool = False
    last_heartbeat: float = 0.0
    call_callbacks: dict[int, Callable] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        """Valida e normaliza os campos do registro de processo após inicialização."""
        if self.call_callbacks is None:
            self.call_callbacks = {}


class IsolatedPluginManager:
    """Gerencia plugins executados em processos Python separados.

    Uso típico::

        mgr = IsolatedPluginManager(app)
        mgr.launch("meu_plugin", Path("plugins/meu_plugin.py"))
        mgr.send_event("meu_plugin", "file_saved", {"path": "/tmp/x.py"})
        mgr.shutdown("meu_plugin")

    Integração com PluginLoader:
        O PluginLoader detecta ``MANIFEST.isolated = True`` e delega o
        carregamento a este gerenciador em vez de importar no processo
        principal.
    """

    def __init__(self, app: Any, cfg: Any = None) -> None:
        """
        Args:
            app: Instância de CodisApp (acesso a _output, _status, root).
            cfg: ConfigManager opcional - lê plugin_timeout_s, plugin_heartbeat_s,
                 plugin_max_restarts das configurações do usuário.
        """
        self._app = app
        self._records: dict[str, _ProcessRecord] = {}
        self._lock = threading.Lock()
        self._max_restarts: int = 3

        if cfg is not None:
            global _CALL_TIMEOUT, _HEARTBEAT_TIMEOUT, _MAX_RESTARTS  # pylint: disable=global-statement
            _CALL_TIMEOUT = float(cfg.get("plugin_timeout_s", _CALL_TIMEOUT))
            _HEARTBEAT_TIMEOUT = float(
                cfg.get("plugin_heartbeat_s", _HEARTBEAT_TIMEOUT)
            )
            _MAX_RESTARTS = int(cfg.get("plugin_max_restarts", _MAX_RESTARTS))
            self._max_restarts = _MAX_RESTARTS

        self._monitor_thread = threading.Thread(
            target=self._monitor_loop,
            daemon=True,
            name="Codis-PluginProc-Monitor",
        )
        self._monitor_thread.start()

    def launch(self, plugin_id: str, plugin_path: Path) -> bool:
        """Lança o plugin em um processo separado.

        Args:
            plugin_id:   Identificador único do plugin.
            plugin_path: Caminho para o arquivo plugin.py.

        Returns:
            True se o processo foi iniciado com sucesso.
        """
        if not plugin_path.exists():
            logger.warning(
                "Plugin isolado '%s': arquivo não encontrado: %s",
                plugin_id,
                plugin_path,
            )
            return False

        with self._lock:
            if plugin_id in self._records:
                logger.info(
                    "Plugin '%s' já está rodando. Ignorando.", plugin_id)
                return True

        in_q: multiprocessing.Queue = multiprocessing.Queue()
        out_q: multiprocessing.Queue = multiprocessing.Queue()

        proc = multiprocessing.Process(
            target=_plugin_worker,
            args=(str(plugin_path), plugin_id, in_q, out_q),
            name=f"Codis-Plugin-{plugin_id}",
            daemon=True,
        )

        rec = _ProcessRecord(
            plugin_id=plugin_id,
            plugin_path=plugin_path,
            process=proc,
            in_queue=in_q,
            out_queue=out_q,
            started_at=time.monotonic(),
            last_heartbeat=time.monotonic(),
        )

        with self._lock:
            self._records[plugin_id] = rec

        proc.start()
        logger.info("Plugin isolado '%s' iniciado (PID %d).",
                    plugin_id, proc.pid or -1)

        threading.Thread(
            target=self._message_listener,
            args=(rec,),
            daemon=True,
            name=f"Codis-PluginMsg-{plugin_id}",
        ).start()

        return True

    def send_event(self, plugin_id: str, event: str, data: dict | None = None) -> None:
        """Envia um evento para o plugin em processo separado.

        Args:
            plugin_id: ID do plugin destino.
            event:     Nome do evento (ex: "file_saved", "theme_changed").
            data:      Dados adicionais serializáveis em JSON.
        """
        rec = self._records.get(plugin_id)
        if not rec or not rec.process.is_alive():
            return
        msg = {"type": "event", "event": event, "data": data or {}}
        with contextlib.suppress(Exception):
            rec.in_queue.put_nowait(json.dumps(msg))

    def send_result(
        self, plugin_id: str, call_id: int, value: Any = None, error: str = ""
    ) -> None:
        """Envia o resultado de uma chamada síncrona de volta ao worker."""
        rec = self._records.get(plugin_id)
        if not rec:
            return
        msg = {"type": "result", "id": call_id, "value": value, "error": error}
        with contextlib.suppress(Exception):
            rec.in_queue.put_nowait(json.dumps(msg))

    def shutdown(self, plugin_id: str, timeout: float = 3.0) -> bool:
        """Encerra graciosamente o processo do plugin.

        Args:
            plugin_id: ID do plugin a encerrar.
            timeout:   Tempo máximo de espera antes de forçar kill.

        Returns:
            True se encerrado com sucesso.
        """
        with self._lock:
            rec = self._records.pop(plugin_id, None)
        if not rec:
            return False

        with contextlib.suppress(Exception):
            rec.in_queue.put_nowait(json.dumps({"type": "shutdown"}))

        rec.process.join(timeout=timeout)

        if rec.process.is_alive():
            logger.warning(
                "Plugin '%s' não encerrou em %.1fs - forçando.", plugin_id, timeout
            )
            with contextlib.suppress(OSError):
                rec.process.terminate()
            rec.process.join(timeout=2.0)
            if rec.process.is_alive():
                with contextlib.suppress(OSError):
                    rec.process.kill()

        logger.info("Plugin isolado '%s' encerrado.", plugin_id)
        return True

    def _message_listener(self, rec: _ProcessRecord) -> None:
        """Thread que lê mensagens do worker e as aplica no processo principal."""
        while rec.process.is_alive():
            try:
                raw = rec.out_queue.get(timeout=0.5)
            except queue.Empty:
                continue
            except (OSError, EOFError, ValueError):
                break

            rec.last_heartbeat = time.monotonic()

            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue

            mtype = msg.get("type", "")

            if mtype == "ready":
                rec.ready = True
                logger.info("Plugin isolado '%s' pronto.", rec.plugin_id)

            elif mtype == "log":
                level = msg.get("level", "info")
                text = msg.get("msg", "")
                getattr(logger, level, logger.info)(
                    "[%s] %s", rec.plugin_id, text)
                self._append_output(rec.plugin_id, text)

            elif mtype == "api":
                method = msg.get("method", "")
                args = msg.get("args", [])
                self._dispatch_api(rec.plugin_id, method, args)

            elif mtype == "call":
                method = msg.get("method", "")
                args = msg.get("args", [])
                call_id = msg.get("id", 0)
                self._handle_call(rec, method, args, call_id)

            elif mtype == "exit":
                code = msg.get("code", 0)
                logger.info(
                    "Plugin isolado '%s' saiu com código %d.", rec.plugin_id, code
                )
                break

    def _append_output(self, plugin_id: str, text: str) -> None:
        """Encaminha texto de log para o painel Output do editor."""

        def _ui() -> None:
            with contextlib.suppress(Exception):
                output = getattr(self._app, "_output", None)
                if output and hasattr(output, "append"):
                    output.append(f"[plugin:{plugin_id}] {text}")

        self._run_in_ui(_ui)

    def _dispatch_api(self, plugin_id: str, method: str, args: list) -> None:
        """Aplica uma chamada de API do worker na thread de UI."""

        def _ui() -> None:
            with contextlib.suppress(Exception):
                if method == "output":
                    channel, text = (args + ["", ""])[:2]
                    output = getattr(self._app, "_output", None)
                    if output and hasattr(output, "append"):
                        output.append(f"[{channel}] {text}")
                elif method == "status":
                    text = args[0] if args else ""
                    status = getattr(self._app, "_status", None)
                    if status and hasattr(status, "notify"):
                        status.notify(text, duration=3000, level="info")

        self._run_in_ui(_ui)

    def _handle_call(
        self, rec: _ProcessRecord, method: str, args: list, call_id: int
    ) -> None:
        """Processa uma chamada síncrona do worker, respondendo com resultado."""

        def _ui() -> None:
            value = None
            error = ""
            try:
                if method == "get_active_file":
                    with contextlib.suppress(Exception):
                        value = str(
                            getattr(self._app, "_active_path", "") or "")
                elif method == "get_config":
                    key = args[0] if args else ""
                    cfg = getattr(self._app, "_cfg", None)
                    value = cfg.get(key) if cfg else None
                else:
                    error = f"Método desconhecido: {method}"
            except Exception as exc:
                error = str(exc)
            self.send_result(rec.plugin_id, call_id, value, error)

        self._run_in_ui(_ui)

    def _run_in_ui(self, fn: Callable) -> None:
        """Agenda *fn* no thread de UI via root.after()."""
        with contextlib.suppress(Exception):
            root = getattr(self._app, "_root", None) or getattr(
                self._app, "root", None)
            if root:
                root.after(0, fn)
            else:
                fn()

    def _monitor_loop(self) -> None:
        """Thread de monitoramento: detecta travamentos e processos mortos."""
        while True:
            time.sleep(5.0)
            now = time.monotonic()
            with self._lock:
                dead = [
                    pid
                    for pid, rec in self._records.items()
                    if not rec.process.is_alive()
                    or (rec.ready and now - rec.last_heartbeat > _HEARTBEAT_TIMEOUT)
                ]
            for pid in dead:
                logger.warning(
                    "Plugin isolado '%s' parece travado/morto - encerrando.", pid
                )
                self._append_output(
                    pid,
                    f"⚠ Plugin '{pid}' travou ou morreu inesperadamente. "
                    "Use o painel de Plugins para reativá-lo.",
                )
                with self._lock:
                    rec = self._records.pop(pid, None)
                if rec and rec.process.is_alive():
                    with contextlib.suppress(OSError):
                        rec.process.terminate()

    def restart(self, plugin_id: str) -> bool:
        """Reinicia um plugin isolado.

        Returns:
            True se reiniciado com sucesso.
        """
        with self._lock:
            rec = self._records.get(plugin_id)
        if not rec:
            return False
        path = rec.plugin_path
        self.shutdown(plugin_id)
        return self.launch(plugin_id, path)
