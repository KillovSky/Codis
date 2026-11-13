"""Cliente DAP (Debug Adapter Protocol) real para o Codis.

Diferente de um simples lançador de subprocesso, este módulo implementa o
papel de *cliente* do protocolo DAP: conecta-se via TCP a um servidor
debugpy já em execução, realiza o handshake completo (``initialize`` -> ``attach``/``launch`` -> ``setBreakpoints`` -> ``configurationDone``) e then
permite enviar comandos de execução (``continue``, ``next``, ``stepIn``,
``stepOut``, ``pause``, ``disconnect``) e consultar estado (``stackTrace``,
``scopes``, ``variables``, ``evaluate``).

Sem este cliente, iniciar o debugpy com ``--listen`` apenas abre um socket
e o processo roda livremente (ou fica parado para sempre se
``--wait-for-client`` estiver ativo) - nada no Codis fala DAP de fato, então
breakpoints/stepping nunca têm efeito. Esta classe resolve isso: o próprio
Codis é o cliente DAP.

Uso típico (a partir do ``DebugPanel``):

    client = DAPClient(
        on_event=self._on_dap_event,      # despachado na thread Tk
        on_close=self._on_dap_close,
    )
    client.connect("127.0.0.1", 5678)
    client.attach(breakpoints={"path/to/script.py": [3, 7]})
    ...
    client.continue_()
    client.step_over()
    client.disconnect()

O framing usado é idêntico ao LSP (``Content-Length: N\\r\\n\\r\\n`` + corpo
JSON), pois DAP reutiliza o mesmo formato de transporte.
"""

from __future__ import annotations

import contextlib
import json
import logging
import re
import socket
import threading
import time
from collections.abc import Callable
from typing import Any

logger = logging.getLogger("Codis.DAP")

_REQUEST_TIMEOUT = 5.0
_CONNECT_RETRIES = 30
_CONNECT_RETRY_DELAY = 0.2


class DAPClient:
    """Cliente DAP mínimo, porém funcional, voltado para debugpy.

    Todas as chamadas de rede ocorrem em uma thread de leitura dedicada;
    callbacks (``on_event``/``on_close``) são despachados via
    ``tk_widget.after(0, ...)`` quando um widget Tk é fornecido, garantindo
    que a UI só seja tocada na thread principal.
    """

    def __init__(
        self,
        on_event: Callable[[str, dict], None] | None = None,
        on_close: Callable[[], None] | None = None,
        tk_widget: Any = None,
    ) -> None:
        """Cria o cliente (ainda não conectado).

        Args:
            on_event: Chamado com ``(event_name, body)`` para cada evento
                DAP assíncrono recebido (``stopped``, ``output``,
                ``terminated``, ``continued``, ``exited`` etc.).
            on_close: Chamado quando a conexão é perdida ou encerrada.
            tk_widget: Widget Tk usado para despachar callbacks na thread
                principal via ``.after(0, ...)``. Se ``None``, os callbacks
                são chamados diretamente na thread de leitura (cuidado).
        """
        self._on_event = on_event
        self._on_close = on_close
        self._tk_widget = tk_widget

        self._sock: socket.socket | None = None
        self._seq = 0
        self._seq_lock = threading.Lock()
        self._pending: dict[int, dict[str, Any]] = {}
        self._pending_lock = threading.Lock()

        self._reader_thread: threading.Thread | None = None
        self._alive = False
        self._initialized = False
        self._initialized_event = threading.Event()
        self._supports_config_done = False

        self.is_attached = False
        self.is_stopped = False
        self.last_stopped_thread_id: int | None = None
        self.last_stopped_reason: str = ""

    def connect(self, host: str, port: int, retries: int = _CONNECT_RETRIES) -> bool:
        """Conecta ao servidor debugpy, tentando algumas vezes (ele pode
        ainda não estar escutando logo após o processo ser lançado).

        Returns:
            True se a conexão TCP foi estabelecida com sucesso.
        """
        for attempt in range(retries):
            try:
                sock = socket.create_connection((host, port), timeout=2.0)
                self._sock = sock
                self._alive = True
                self._reader_thread = threading.Thread(
                    target=self._reader_loop, daemon=True
                )
                self._reader_thread.start()
                logger.debug(
                    "DAP: conectado a %s:%s (tentativa %d)", host, port, attempt + 1
                )
                return True
            except OSError:
                time.sleep(_CONNECT_RETRY_DELAY)
        logger.warning("DAP: não foi possível conectar a %s:%s", host, port)
        return False

    def attach(
        self,
        breakpoints: dict[str, list[int]] | None = None,
        timeout: float = _REQUEST_TIMEOUT,
    ) -> bool:
        """Executa o handshake completo de attach com debugpy.

        A ordem exigida pelo debugpy é sutil e não corresponde à ingênua
        leitura da spec DAP genérica: o request ``attach`` deve ser
        ENVIADO logo após ``initialize`` (antes de ``setBreakpoints``/
        ``configurationDone``), mas sua RESPOSTA só chega depois que
        ``configurationDone`` é processado. Ou seja: -> initialize          (aguarda resposta) -> attach               (envia, NÃO aguarda resposta ainda) <- initialized event    (aguarda) -> setBreakpoints        (aguarda resposta, por arquivo) -> configurationDone     (aguarda resposta) <- attach response       (chega só agora - lida em seguida) <- stopped/process events (a execução já pode ter iniciado)

        Enviar ``configurationDone`` antes de ``attach`` faz o debugpy
        recusá-lo ("only allowed during handling of a launch/attach
        request"); bloquear esperando a resposta de ``attach`` antes de
        enviar ``configurationDone`` trava ambos os lados. Esta
        implementação envia ``attach`` de forma assíncrona (sem bloquear)
        e só lê sua resposta depois de completar a configuração.

        Args:
            breakpoints: Mapa ``{caminho_absoluto: [linhas]}`` a enviar via
                ``setBreakpoints`` antes de liberar a execução.

        Returns:
            True se o handshake foi concluído sem erros bloqueantes.
        """
        if not self._sock:
            return False

        init_resp = self._request(
            "initialize",
            {
                "clientID": "codis",
                "clientName": "Codis",
                "adapterID": "debugpy",
                "pathFormat": "path",
                "linesStartAt1": True,
                "columnsStartAt1": True,
                "supportsRunInTerminalRequest": False,
                "supportsVariableType": True,
                "supportsVariablePaging": False,
                "supportsConfigurationDoneRequest": True,
            },
            timeout=timeout,
        )
        if init_resp is None or not init_resp.get("success", False):
            logger.warning("DAP: initialize falhou: %s", init_resp)
            return False
        caps = init_resp.get("body", {}) or {}
        self._supports_config_done = bool(
            caps.get("supportsConfigurationDoneRequest", True)
        )

        attach_seq = self._next_seq()
        attach_event = threading.Event()
        attach_box: dict[str, Any] = {}
        with self._pending_lock:
            self._pending[attach_seq] = {
                "event": attach_event, "box": attach_box}
        self._send(
            {
                "seq": attach_seq,
                "type": "request",
                "command": "attach",
                "arguments": {
                    "justMyCode": False,
                    "redirectOutput": True,
                },
            }
        )

        if not self._wait_for_initialized(timeout=timeout):
            logger.warning("DAP: evento 'initialized' não recebido a tempo")
            with self._pending_lock:
                self._pending.pop(attach_seq, None)
            return False

        if breakpoints:
            for path, lines in breakpoints.items():
                self._request(
                    "setBreakpoints",
                    {
                        "source": {"path": path},
                        "breakpoints": [{"line": ln} for ln in sorted(lines)],
                    },
                    timeout=timeout,
                )

        if self._supports_config_done:
            self._request("configurationDone", {}, timeout=timeout)

        got = attach_event.wait(timeout)
        with self._pending_lock:
            self._pending.pop(attach_seq, None)
        attach_resp = attach_box.get("response") if got else None
        if attach_resp is None or not attach_resp.get("success", False):
            logger.warning("DAP: attach falhou: %s", attach_resp)
            return False

        self.is_attached = True
        self._initialized = True
        return True

    def _wait_for_initialized(self, timeout: float) -> bool:
        """Bloqueia até o evento ``initialized`` ser recebido (ou timeout).

        Usa um ``threading.Event`` interno alimentado por ``_handle_message``
        quando esse evento específico chega, evitando uma espera ocupada.
        """
        if self._initialized_event.is_set():
            return True
        return self._initialized_event.wait(timeout)

    def set_breakpoints(
        self, path: str, lines: list[int], timeout: float = _REQUEST_TIMEOUT
    ) -> list[dict]:
        """Atualiza os breakpoints para um arquivo específico em tempo real.

        Pode ser chamado a qualquer momento após ``attach`` (inclusive com
        a execução parada ou em andamento) - debugpy reavalia a lista
        completa enviada, então o conjunto é sempre substituído, não
        somado.

        Returns:
            A lista de breakpoints confirmados pelo adaptador (cada um com
            ``verified``: bool), ou lista vazia em caso de falha.
        """
        resp = self._request(
            "setBreakpoints",
            {
                "source": {"path": path},
                "breakpoints": [{"line": ln} for ln in sorted(lines)],
            },
            timeout=timeout,
        )
        if resp and resp.get("success"):
            return resp.get("body", {}).get("breakpoints", [])
        return []

    def continue_(self, thread_id: int | None = None) -> None:
        """Libera a execução (Continue / F5)."""
        self._async_request("continue", {"threadId": thread_id or 0})

    def step_over(self, thread_id: int | None = None) -> None:
        """Executa a linha atual sem entrar em chamadas de função (Step Over / F10)."""
        self._async_request("next", {"threadId": thread_id or 0})

    def step_in(self, thread_id: int | None = None) -> None:
        """Entra na próxima chamada de função (Step Into / F11)."""
        self._async_request("stepIn", {"threadId": thread_id or 0})

    def step_out(self, thread_id: int | None = None) -> None:
        """Sai da função atual, voltando ao chamador (Step Out / Shift+F11)."""
        self._async_request("stepOut", {"threadId": thread_id or 0})

    def pause(self, thread_id: int | None = None) -> None:
        """Pausa a execução em andamento."""
        self._async_request("pause", {"threadId": thread_id or 0})

    def disconnect(self) -> None:
        """Encerra a sessão de depuração e fecha a conexão."""
        if self._sock:
            with contextlib.suppress(Exception):
                self._request(
                    "disconnect", {"terminateDebuggee": True}, timeout=1.5)
        self._shutdown()

    def threads(self, timeout: float = _REQUEST_TIMEOUT) -> list[dict]:
        """Lista as threads ativas na sessão de depuração."""
        resp = self._request("threads", {}, timeout=timeout)
        if resp and resp.get("success"):
            return resp.get("body", {}).get("threads", [])
        return []

    def stack_trace(
        self, thread_id: int, timeout: float = _REQUEST_TIMEOUT
    ) -> list[dict]:
        """Retorna os frames da pilha de chamadas para a thread parada."""
        resp = self._request(
            "stackTrace", {"threadId": thread_id}, timeout=timeout)
        if resp and resp.get("success"):
            return resp.get("body", {}).get("stackFrames", [])
        return []

    def scopes(self, frame_id: int, timeout: float = _REQUEST_TIMEOUT) -> list[dict]:
        """Retorna os escopos (locals/globals) de um frame."""
        resp = self._request("scopes", {"frameId": frame_id}, timeout=timeout)
        if resp and resp.get("success"):
            return resp.get("body", {}).get("scopes", [])
        return []

    def variables(
        self, variables_ref: int, timeout: float = _REQUEST_TIMEOUT
    ) -> list[dict]:
        """Retorna as variáveis de um escopo/estrutura, por referência."""
        resp = self._request(
            "variables", {"variablesReference": variables_ref}, timeout=timeout
        )
        if resp and resp.get("success"):
            return resp.get("body", {}).get("variables", [])
        return []

    def evaluate(
        self, expr: str, frame_id: int | None, timeout: float = _REQUEST_TIMEOUT
    ) -> dict:
        """Avalia uma expressão no contexto de um frame parado (REPL real)."""
        args: dict[str, Any] = {"expression": expr, "context": "repl"}
        if frame_id is not None:
            args["frameId"] = frame_id
        resp = self._request("evaluate", args, timeout=timeout)
        if resp and resp.get("success"):
            return resp.get("body", {})
        return {"result": "", "error": (resp or {}).get("message", "evaluate failed")}

    def _next_seq(self) -> int:
        with self._seq_lock:
            self._seq += 1
            return self._seq

    def _send(self, data: dict) -> None:
        if not self._sock:
            return
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        header = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
        try:
            self._sock.sendall(header + body)
        except OSError as exc:
            logger.debug("DAP: erro ao enviar: %s", exc)
            self._shutdown()

    def _request(
        self, command: str, arguments: dict, timeout: float = _REQUEST_TIMEOUT
    ) -> dict | None:
        """Envia um request DAP e bloqueia até a resposta correspondente
        chegar (ou o timeout expirar). Seguro para chamar fora da thread
        de leitura - não deve ser chamado de dentro de ``on_event``.
        """
        seq = self._next_seq()
        event = threading.Event()
        box: dict[str, Any] = {}

        with self._pending_lock:
            self._pending[seq] = {"event": event, "box": box}

        self._send(
            {
                "seq": seq,
                "type": "request",
                "command": command,
                "arguments": arguments,
            }
        )

        got = event.wait(timeout)
        with self._pending_lock:
            self._pending.pop(seq, None)
        if not got:
            logger.debug("DAP: timeout esperando resposta de '%s'", command)
            return None
        return box.get("response")

    def _async_request(self, command: str, arguments: dict) -> None:
        """Envia um request DAP sem esperar a resposta (fire-and-forget) -
        usado para comandos de execução, cujo efeito real chega via os
        eventos ``stopped``/``continued``/``terminated``, não na resposta
        síncrona em si."""
        seq = self._next_seq()
        self._send(
            {
                "seq": seq,
                "type": "request",
                "command": command,
                "arguments": arguments,
            }
        )

    def _dispatch_event(self, name: str, body: dict) -> None:
        if name == "initialized":
            self._initialized_event.set()
        elif name == "stopped":
            self.is_stopped = True
            self.last_stopped_thread_id = body.get("threadId")
            self.last_stopped_reason = body.get("reason", "")
        elif name in ("continued", "exited", "terminated"):
            self.is_stopped = False

        if not self._on_event:
            return
        if self._tk_widget is not None:
            with contextlib.suppress(Exception):
                self._tk_widget.after(0, lambda: self._on_event(name, body))
        else:
            with contextlib.suppress(Exception):
                self._on_event(name, body)

    def _reader_loop(self) -> None:
        buf = b""
        sock = self._sock
        if sock is None:
            return
        while self._alive:
            try:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                buf += chunk
                while True:
                    sep = buf.find(b"\r\n\r\n")
                    if sep == -1:
                        break
                    header = buf[:sep]
                    m = re.search(rb"Content-Length:\s*(\d+)", header)
                    if not m:
                        buf = buf[sep + 4:]
                        continue
                    length = int(m.group(1))
                    body_start = sep + 4
                    body_end = body_start + length
                    if len(buf) < body_end:
                        break
                    raw = buf[body_start:body_end]
                    buf = buf[body_end:]
                    try:
                        msg = json.loads(raw.decode("utf-8", errors="replace"))
                    except json.JSONDecodeError:
                        continue
                    self._handle_message(msg)
            except OSError as exc:
                logger.debug("DAP: reader encerrado: %s", exc)
                break
        self._shutdown()

    def _handle_message(self, msg: dict) -> None:
        mtype = msg.get("type")
        if mtype == "response":
            req_seq = msg.get("request_seq")
            with self._pending_lock:
                waiter = self._pending.get(req_seq)
            if waiter:
                waiter["box"]["response"] = msg
                waiter["event"].set()
        elif mtype == "event":
            self._dispatch_event(msg.get("event", ""),
                                 msg.get("body", {}) or {})

    def _shutdown(self) -> None:
        if not self._alive:
            return
        self._alive = False
        with contextlib.suppress(Exception):
            if self._sock:
                self._sock.close()
        self._sock = None
        with self._pending_lock:
            for waiter in self._pending.values():
                waiter["event"].set()
            self._pending.clear()
        if self._on_close:
            if self._tk_widget is not None:
                with contextlib.suppress(Exception):
                    self._tk_widget.after(0, self._on_close)
            else:
                with contextlib.suppress(Exception):
                    self._on_close()
