"""Codis IA - Provedores de modelos de linguagem.

Cada provedor implementa a interface BaseProvider:
    - name / icon / description   - metadados de exibição
    - configure(cfg)              - recebe dict de configuração
    - send(messages, on_chunk, on_done, on_error) - streaming assíncrono
    - cancel()                    - aborta a requisição em curso
    - check() -> str|None         - testa conectividade (None = OK)

Provedores disponíveis
----------------------
    AnthropicProvider        - Claude (API Anthropic)
    OpenAIProvider           - ChatGPT / GPT-4 (API OpenAI)
    OpenAICompatibleProvider - Qualquer servidor /v1/chat/completions
                               (LM Studio, Jan, Groq, Together AI, OpenRouter...)
    GeminiProvider           - Google Gemini
    OllamaProvider           - Ollama via HTTP (localhost:11434)
    LocalLibProvider         - llama-cpp-python direto (execução local pesada)
"""

from __future__ import annotations

import json
import logging
import threading
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import Any

logger = logging.getLogger("Codis.IA")

_OnChunk = Callable[[str], None]
_OnDone = Callable[[str], None]
_OnError = Callable[[str], None]


class BaseProvider(ABC):
    """Classe base abstrata para todos os providers de IA do Codis."""

    name: str = ""
    icon: str = "[AI]"
    description: str = ""
    requires_key: bool = True

    def __init__(self) -> None:
        """Inicializa o provider de IA base."""
        self._cfg: dict[str, Any] = {}
        self._cancelled: threading.Event = threading.Event()

    def configure(self, cfg: dict[str, Any]) -> None:
        """Configura o provider com credenciais e parâmetros."""
        self._cfg = cfg

    def cancel(self) -> None:
        """Cancela a requisição de IA em andamento, se houver."""
        self._cancelled.set()

    def _reset_cancel(self) -> None:
        """Começa uma nova requisição com um evento de cancelamento **novo**.

        Reutilizar (``clear``) o mesmo evento deixava a thread de uma requisição
        cancelada voltar a rodar quando uma nova era enviada logo em seguida.
        """
        self._cancelled = threading.Event()

    def _snapshot(self, options: dict[str, Any] | None = None) -> dict[str, Any]:
        """Config efetiva *desta* requisição (cópia: imune a ``configure`` posterior)."""
        cfg = dict(self._cfg)
        if options:
            cfg.update(options)
        return cfg

    @abstractmethod
    def send(
        self,
        messages: list[dict[str, Any]],
        on_chunk: _OnChunk,
        on_done: _OnDone,
        on_error: _OnError,
        options: dict[str, Any] | None = None,
    ) -> None:
        """Envia mensagens ao modelo em background thread.

        ``options`` sobrepõe a config só desta requisição (system prompt,
        ferramentas, callbacks) sem mexer no estado compartilhado do provedor.
        """

    def check(self, model_id: str = "") -> str | None:
        """Testa conectividade e credenciais. Retorna None se OK."""
        return None


def _post_stream(
    url: str,
    payload: dict,
    headers: dict[str, str],
    on_line: Callable[[bytes], None],
    cancelled: threading.Event,
    timeout: int = 120,
) -> None:
    """POST com streaming SSE/NDJSON."""
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            for line in resp:
                if cancelled.is_set():
                    break
                if line.strip():
                    on_line(line)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code}: {body}") from exc


def _post_json(
    url: str,
    payload: dict,
    headers: dict[str, str],
    timeout: int = 60,
) -> dict:
    """POST que retorna JSON completo."""
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code}: {body}") from exc


class AnthropicProvider(BaseProvider):
    name = "Anthropic (Claude)"
    icon = "✳"
    description = "Claude models by Anthropic. Requires an API key from anthropic.com."
    _API_URL = "https://api.anthropic.com/v1/messages"
    _API_VERSION = "2023-06-01"
    _DEFAULT_MODEL = "claude-3-5-sonnet-20241022"

    def send(self, messages, on_chunk, on_done, on_error, options=None) -> None:
        """Envia uma mensagem para a API Anthropic e retorna a resposta."""
        self._reset_cancel()
        cfg = self._snapshot(options)
        cancelled = self._cancelled

        def _worker() -> None:
            api_key = cfg.get("chave_api", "")
            model_id = cfg.get("modelo", self._DEFAULT_MODEL)
            max_tok = int(cfg.get("max_tokens", 4096))
            system_prompt = cfg.get("sistema", "")
            tools_schema = cfg.get("_ferramentas")
            on_tool_call = cfg.get("_on_tool_call")
            if not api_key:
                on_error("Anthropic API key not configured.")
                return
            headers = {
                "Content-Type": "application/json",
                "x-api-key": api_key,
                "anthropic-version": self._API_VERSION,
            }

            anthropic_tools: list[dict] | None = None
            if tools_schema and on_tool_call:
                anthropic_tools = []
                for t in tools_schema:
                    fn = t.get("function", t)
                    anthropic_tools.append(
                        {
                            "name": fn["name"],
                            "description": fn.get("description", ""),
                            "input_schema": fn.get(
                                "parameters", {
                                    "type": "object", "properties": {}}
                            ),
                        }
                    )

            current_messages = list(messages)
            accumulated: list[str] = []

            for _loop in range(20):
                if cancelled.is_set():
                    break
                payload: dict[str, Any] = {
                    "model": model_id,
                    "max_tokens": max_tok,
                    "stream": True,
                    "messages": current_messages,
                }
                if system_prompt:
                    payload["system"] = system_prompt
                if anthropic_tools:
                    payload["tools"] = anthropic_tools

                event_buffer: list[dict] = []
                block_map: dict[int, dict] = {}

                try:

                    def _on_line(
                        lb: bytes,
                    ) -> None:  # pylint: disable=cell-var-from-loop
                        text = lb.decode("utf-8", errors="replace").strip()
                        if text.startswith("data:"):
                            text = text[5:].strip()
                        if not text or text == "[DONE]":
                            return
                        try:
                            event = json.loads(text)
                            ev_type = event.get("type", "")
                            if ev_type == "content_block_start":
                                idx = event.get("index", 0)
                                block_map[idx] = event.get("content_block", {})
                            elif ev_type == "content_block_delta":
                                idx = event.get("index", 0)
                                delta = event.get("delta", {})
                                if delta.get("type") == "text_delta":
                                    chunk = delta.get("text", "")
                                    if chunk:
                                        accumulated.append(chunk)
                                        on_chunk(chunk)
                                elif delta.get("type") == "input_json_delta":
                                    blk = block_map.setdefault(idx, {})
                                    blk["_json_acc"] = blk.get(
                                        "_json_acc", ""
                                    ) + delta.get("partial_json", "")
                            event_buffer.append(event)
                        except (json.JSONDecodeError, KeyError):
                            pass

                    _post_stream(
                        self._API_URL, payload, headers, _on_line, cancelled
                    )
                except (RuntimeError, OSError) as exc:
                    on_error(str(exc))
                    return

                stop_reason = None
                for ev in reversed(event_buffer):
                    if ev.get("type") == "message_delta":
                        stop_reason = ev.get("delta", {}).get("stop_reason")
                        break

                if stop_reason != "tool_use" or not on_tool_call:
                    break

                tool_use_blocks = [
                    b for b in block_map.values() if b.get("type") == "tool_use"
                ]
                if not tool_use_blocks:
                    break

                assistant_content: list[dict] = []
                for blk in sorted(block_map.items()):
                    b = blk[1]
                    if b.get("type") == "text" and b.get("text"):
                        assistant_content.append(
                            {"type": "text", "text": b["text"]})
                    elif b.get("type") == "tool_use":
                        try:
                            inp = json.loads(b.get("_json_acc", "{}") or "{}")
                        except json.JSONDecodeError:
                            inp = {}
                        assistant_content.append(
                            {
                                "type": "tool_use",
                                "id": b.get("id", ""),
                                "name": b.get("name", ""),
                                "input": inp,
                            }
                        )

                current_messages = list(current_messages)
                current_messages.append(
                    {"role": "assistant", "content": assistant_content}
                )

                tool_results: list[dict] = []
                for blk in tool_use_blocks:
                    try:
                        inp = json.loads(blk.get("_json_acc", "{}") or "{}")
                    except json.JSONDecodeError:
                        inp = {}
                    result = on_tool_call(blk.get("name", ""), inp)
                    tool_results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": blk.get("id", ""),
                            "content": str(result),
                        }
                    )
                current_messages.append(
                    {"role": "user", "content": tool_results})

            on_done("".join(accumulated))

        threading.Thread(target=_worker, daemon=True,
                         name="ia-anthropic").start()

    def check(self, model_id: str = "") -> str | None:
        """Verifica se as credenciais Anthropic estão válidas e acessíveis."""
        api_key = self._cfg.get("chave_api", "")
        if not api_key:
            return "API key not configured."
        try:
            _post_json(
                self._API_URL,
                {
                    "model": self._DEFAULT_MODEL,
                    "max_tokens": 1,
                    "messages": [{"role": "user", "content": "ping"}],
                },
                {
                    "Content-Type": "application/json",
                    "x-api-key": api_key,
                    "anthropic-version": self._API_VERSION,
                },
                timeout=10,
            )
            return None
        except RuntimeError as exc:
            return str(exc)


class OpenAIProvider(BaseProvider):
    name = "OpenAI (ChatGPT)"
    icon = "🟢"
    description = "GPT models by OpenAI. Requires an API key from platform.openai.com."
    _DEFAULT_URL = "https://api.openai.com/v1/chat/completions"
    _DEFAULT_MODEL = "gpt-4o"

    def send(self, messages, on_chunk, on_done, on_error, options=None) -> None:
        """Envia uma mensagem para a API OpenAI e retorna a resposta."""
        self._reset_cancel()
        cfg = self._snapshot(options)
        cancelled = self._cancelled

        def _worker() -> None:
            api_key = cfg.get("chave_api", "")
            model_id = cfg.get("modelo", self._DEFAULT_MODEL)
            max_tok = int(cfg.get("max_tokens", 4096))
            url = cfg.get("url_base", self._DEFAULT_URL)
            system_prompt = cfg.get("sistema", "")
            tools_schema = cfg.get("_ferramentas")
            on_tool_call = cfg.get("_on_tool_call")
            if not api_key:
                on_error("OpenAI API key not configured.")
                return
            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            }
            base_msgs: list[dict] = []
            if system_prompt:
                base_msgs.append({"role": "system", "content": system_prompt})
            base_msgs.extend(messages)
            current_messages = list(base_msgs)
            accumulated: list[str] = []

            for _loop in range(20):
                if cancelled.is_set():
                    break
                payload: dict[str, Any] = {
                    "model": model_id,
                    "max_tokens": max_tok,
                    "stream": True,
                    "messages": current_messages,
                }
                if tools_schema and on_tool_call:
                    payload["tools"] = tools_schema
                    payload["tool_choice"] = "auto"

                finish_reason: list[str] = [""]
                tool_calls_acc: dict[int, dict] = {}
                try:

                    def _on_line(
                        lb: bytes,
                    ) -> None:  # pylint: disable=cell-var-from-loop
                        text = lb.decode("utf-8", errors="replace").strip()
                        if text.startswith("data:"):
                            text = text[5:].strip()
                        if not text or text == "[DONE]":
                            return
                        try:
                            ev = json.loads(text)
                            choice = ev.get("choices", [{}])[0]
                            delta = choice.get("delta", {})
                            reason = choice.get("finish_reason")
                            if reason:
                                finish_reason[0] = reason
                            chunk = delta.get("content", "")
                            if chunk:
                                accumulated.append(chunk)
                                on_chunk(chunk)
                            for tc in delta.get("tool_calls", []):
                                idx = tc.get("index", 0)
                                if idx not in tool_calls_acc:
                                    tool_calls_acc[idx] = {
                                        "id": "",
                                        "name": "",
                                        "args": "",
                                    }
                                tool_calls_acc[idx]["id"] += tc.get("id", "")
                                fn = tc.get("function", {})
                                tool_calls_acc[idx]["name"] += fn.get(
                                    "name", "")
                                tool_calls_acc[idx]["args"] += fn.get(
                                    "arguments", "")
                        except (json.JSONDecodeError, IndexError, KeyError):
                            pass

                    _post_stream(url, payload, headers,
                                 _on_line, cancelled)
                except (RuntimeError, OSError) as exc:
                    on_error(str(exc))
                    return

                if (
                    finish_reason[0] != "tool_calls"
                    or not on_tool_call
                    or not tool_calls_acc
                ):
                    break

                tool_calls_list = []
                for idx in sorted(tool_calls_acc):
                    tc = tool_calls_acc[idx]
                    tool_calls_list.append(
                        {
                            "id": tc["id"],
                            "type": "function",
                            "function": {"name": tc["name"], "arguments": tc["args"]},
                        }
                    )
                current_messages = list(current_messages)
                current_messages.append(
                    {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": tool_calls_list,
                    }
                )

                for tc in tool_calls_list:
                    fn = tc["function"]
                    try:
                        args = json.loads(fn.get("arguments", "{}") or "{}")
                    except json.JSONDecodeError:
                        args = {}
                    result = on_tool_call(fn["name"], args)
                    current_messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tc["id"],
                            "content": str(result),
                        }
                    )

            on_done("".join(accumulated))

        threading.Thread(target=_worker, daemon=True, name="ia-openai").start()

    def check(self, model_id: str = "") -> str | None:
        """Verifica se as credenciais OpenAI estão válidas e acessíveis."""
        api_key = self._cfg.get("chave_api", "")
        if not api_key:
            return "API key not configured."
        try:
            _post_json(
                self._cfg.get("url_base", self._DEFAULT_URL),
                {
                    "model": self._cfg.get("modelo", self._DEFAULT_MODEL),
                    "max_tokens": 1,
                    "stream": False,
                    "messages": [{"role": "user", "content": "ping"}],
                },
                {
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {api_key}",
                },
                timeout=10,
            )
            return None
        except RuntimeError as exc:
            return str(exc)


class OpenAICompatibleProvider(BaseProvider):
    name = "OpenAI-Compatible (LM Studio / Groq / etc.)"
    icon = "🔀"
    description = (
        "LM Studio, Jan, LocalAI, vLLM, Groq, Together AI, OpenRouter, Mistral, etc."
    )
    requires_key = False
    _DEFAULT_URL = "http://localhost:1234/v1/chat/completions"
    _DEFAULT_MODEL = ""

    def send(self, messages, on_chunk, on_done, on_error, options=None) -> None:
        """Envia uma mensagem para a API compatível com OpenAI e retorna a resposta."""
        self._reset_cancel()
        cfg = self._snapshot(options)
        cancelled = self._cancelled

        def _worker() -> None:
            url = cfg.get("url", self._DEFAULT_URL)
            model_id = cfg.get("modelo", self._DEFAULT_MODEL)
            max_tok = int(cfg.get("max_tokens", 4096))
            api_key = cfg.get("chave_api", "")
            system_prompt = cfg.get("sistema", "")
            tools_schema = cfg.get("_ferramentas")
            on_tool_call = cfg.get("_on_tool_call")

            base_msgs: list[dict] = []
            if system_prompt:
                base_msgs.append({"role": "system", "content": system_prompt})
            base_msgs.extend(messages)
            current_messages = list(base_msgs)

            headers: dict[str, str] = {"Content-Type": "application/json"}
            if api_key:
                headers["Authorization"] = f"Bearer {api_key}"

            accumulated: list[str] = []

            for _loop in range(20):
                if cancelled.is_set():
                    break
                payload: dict[str, Any] = {
                    "messages": current_messages,
                    "stream": True,
                    "max_tokens": max_tok,
                }
                if model_id:
                    payload["model"] = model_id
                if tools_schema and on_tool_call:
                    payload["tools"] = tools_schema
                    payload["tool_choice"] = "auto"

                finish_reason: list[str] = [""]
                tool_calls_acc: dict[int, dict] = {}
                try:

                    def _on_line(
                        lb: bytes,
                    ) -> None:  # pylint: disable=cell-var-from-loop
                        text = lb.decode("utf-8", errors="replace").strip()
                        if text.startswith("data:"):
                            text = text[5:].strip()
                        if not text or text == "[DONE]":
                            return
                        try:
                            ev = json.loads(text)
                            choice = ev.get("choices", [{}])[0]
                            delta = choice.get("delta", {})
                            reason = choice.get("finish_reason")
                            if reason:
                                finish_reason[0] = reason
                            chunk = delta.get("content", "")
                            if chunk:
                                accumulated.append(chunk)
                                on_chunk(chunk)
                            for tc in delta.get("tool_calls", []):
                                idx = tc.get("index", 0)
                                if idx not in tool_calls_acc:
                                    tool_calls_acc[idx] = {
                                        "id": "",
                                        "name": "",
                                        "args": "",
                                    }
                                tool_calls_acc[idx]["id"] += tc.get("id", "")
                                fn = tc.get("function", {})
                                tool_calls_acc[idx]["name"] += fn.get(
                                    "name", "")
                                tool_calls_acc[idx]["args"] += fn.get(
                                    "arguments", "")
                        except (json.JSONDecodeError, IndexError, KeyError):
                            pass

                    _post_stream(url, payload, headers,
                                 _on_line, cancelled)
                except (RuntimeError, OSError, ConnectionRefusedError) as exc:
                    on_error(
                        f"Server not reachable at {url}\n\n"
                        f"Check if the server is running.\n\nError: {exc}"
                    )
                    return

                if (
                    finish_reason[0] != "tool_calls"
                    or not on_tool_call
                    or not tool_calls_acc
                ):
                    break

                tool_calls_list = []
                for idx in sorted(tool_calls_acc):
                    tc = tool_calls_acc[idx]
                    tool_calls_list.append(
                        {
                            "id": tc["id"],
                            "type": "function",
                            "function": {"name": tc["name"], "arguments": tc["args"]},
                        }
                    )
                current_messages = list(current_messages)
                current_messages.append(
                    {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": tool_calls_list,
                    }
                )
                for tc in tool_calls_list:
                    fn = tc["function"]
                    try:
                        args = json.loads(fn.get("arguments", "{}") or "{}")
                    except json.JSONDecodeError:
                        args = {}
                    result = on_tool_call(fn["name"], args)
                    current_messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tc["id"],
                            "content": str(result),
                        }
                    )

            on_done("".join(accumulated))

        threading.Thread(target=_worker, daemon=True,
                         name="ia-compat-openai").start()

    def check(self, model_id: str = "") -> str | None:
        """Verifica se o endpoint compatível com OpenAI está acessível."""
        url_chat = self._cfg.get("url", self._DEFAULT_URL)
        base = url_chat.split("/chat/completions")[0].split("/completions")[0]
        if not base.endswith("/v1"):
            base = base.rstrip("/") + "/v1"
        models_url = base + "/models"
        api_key = self._cfg.get("chave_api", "")
        try:
            req_headers: dict[str, str] = {}
            if api_key:
                req_headers["Authorization"] = f"Bearer {api_key}"
            req = urllib.request.Request(models_url, headers=req_headers)
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                models = [m.get("id", "") for m in data.get("data", [])]
                if models:
                    return None
                return "Server reachable but no models available."
        except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
            return f"Server not reachable at {models_url}: {exc}"


class GeminiProvider(BaseProvider):
    """Provedor para a API Google Gemini."""

    name = "Google Gemini"
    icon = "*"
    description = (
        "Gemini models by Google. Requires an API key from aistudio.google.com."
    )

    _URL_TEMPLATE = "https://generativelanguage.googleapis.com/v1beta/{model}:streamGenerateContent?alt=sse&key={key}"
    _DEFAULT_MODEL = "models/gemini-1.5-flash"

    def send(self, messages, on_chunk, on_done, on_error, options=None) -> None:
        """Envia mensagens ao Google Gemini com streaming SSE."""
        self._reset_cancel()
        cfg = self._snapshot(options)
        cancelled = self._cancelled

        def _worker() -> None:
            api_key = cfg.get("chave_api", "")
            model_id = cfg.get("modelo", self._DEFAULT_MODEL)
            if not api_key:
                on_error("Gemini API key not configured.")
                return

            if not model_id.startswith(("models/", "tunedModels/")):
                model_id = "models/" + model_id

            gemini_contents = []
            for msg in messages:
                role = msg["role"]
                if role == "system":
                    continue
                gemini_role = "user" if role == "user" else "model"
                gemini_contents.append(
                    {"role": gemini_role, "parts": [{"text": msg["content"]}]}
                )

            payload: dict[str, Any] = {
                "contents": gemini_contents,
                "generationConfig": {
                    "maxOutputTokens": int(cfg.get("max_tokens", 4096)),
                },
            }

            system_prompt = cfg.get("sistema", "")
            if system_prompt:
                payload["system_instruction"] = {
                    "parts": [{"text": system_prompt}]}

            url = self._URL_TEMPLATE.format(model=model_id, key=api_key)
            headers = {"Content-Type": "application/json"}
            accumulated: list[str] = []
            try:

                def _on_line(lb: bytes) -> None:  # pylint: disable=cell-var-from-loop
                    text = lb.decode("utf-8", errors="replace").strip()
                    if text.startswith("data:"):
                        text = text[5:].strip()
                    if not text:
                        return
                    try:
                        ev = json.loads(text)
                        chunk = (
                            ev.get("candidates", [{}])[0]
                            .get("content", {})
                            .get("parts", [{}])[0]
                            .get("text", "")
                        )
                        if chunk:
                            accumulated.append(chunk)
                            on_chunk(chunk)
                    except (json.JSONDecodeError, IndexError, KeyError):
                        pass

                _post_stream(url, payload, headers, _on_line, cancelled)
                on_done("".join(accumulated))
            except (RuntimeError, OSError) as exc:
                on_error(str(exc))

        threading.Thread(target=_worker, daemon=True, name="ia-gemini").start()

    def check(self, model_id: str = "") -> str | None:
        """Verifica se a chave de API do Gemini está configurada."""
        api_key = self._cfg.get("chave_api", "")
        return None if api_key else "Gemini API key not configured."


class OllamaProvider(BaseProvider):
    name = "Ollama (local HTTP)"
    icon = "🦙"
    description = (
        "Local models via Ollama. Install from ollama.com and run 'ollama serve'."
    )
    requires_key = False
    _DEFAULT_URL = "http://localhost:11434/api/chat"
    _MODELS_URL = "http://localhost:11434/api/tags"

    def send(self, messages, on_chunk, on_done, on_error, options=None) -> None:
        """Envia uma mensagem para o Ollama local e retorna a resposta."""
        self._reset_cancel()
        cfg = self._snapshot(options)
        cancelled = self._cancelled

        def _worker() -> None:
            url = cfg.get("url", self._DEFAULT_URL)
            model_id = cfg.get("modelo", "llama3")
            tools_schema = cfg.get("_ferramentas")
            on_tool_call = cfg.get("_on_tool_call")
            current_messages = list(messages)
            accumulated: list[str] = []

            for _loop in range(20):
                if cancelled.is_set():
                    break
                payload: dict[str, Any] = {
                    "model": model_id,
                    "messages": current_messages,
                    "stream": True,
                }
                if tools_schema and on_tool_call:
                    payload["tools"] = tools_schema

                headers = {"Content-Type": "application/json"}
                tool_calls_buf: list[dict] = []
                done_reason: list[str] = [""]
                try:

                    def _on_line(
                        lb: bytes,
                    ) -> None:  # pylint: disable=cell-var-from-loop
                        text = lb.decode("utf-8", errors="replace").strip()
                        if not text:
                            return
                        try:
                            ev = json.loads(text)
                            chunk = ev.get("message", {}).get("content", "")
                            if chunk:
                                accumulated.append(chunk)
                                on_chunk(chunk)
                            if ev.get("done"):
                                done_reason[0] = ev.get("done_reason", "stop")
                            for tc in ev.get("message", {}).get("tool_calls", []):
                                tool_calls_buf.append(tc)
                        except (json.JSONDecodeError, KeyError):
                            pass

                    _post_stream(url, payload, headers,
                                 _on_line, cancelled)
                except (RuntimeError, OSError, ConnectionRefusedError) as exc:
                    on_error(f"Ollama not reachable: {exc}")
                    return

                if not tool_calls_buf or not on_tool_call:
                    break

                current_messages = list(current_messages)
                current_messages.append(
                    {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": tool_calls_buf,
                    }
                )
                for tc in tool_calls_buf:
                    fn_info = tc.get("function", {})
                    args = fn_info.get("arguments", {})
                    if isinstance(args, str):
                        try:
                            args = json.loads(args)
                        except json.JSONDecodeError:
                            args = {}
                    result = on_tool_call(fn_info.get("name", ""), args)
                    current_messages.append(
                        {
                            "role": "tool",
                            "content": str(result),
                        }
                    )

            on_done("".join(accumulated))

        threading.Thread(target=_worker, daemon=True, name="ia-ollama").start()

    def list_models(self) -> list[str]:
        """Lista os modelos disponíveis no servidor Ollama local."""
        try:
            req = urllib.request.Request(self._MODELS_URL, method="GET")
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return [m["name"] for m in data.get("models", [])]
        except (OSError, urllib.error.URLError, json.JSONDecodeError):
            return []

    def check(self, model_id: str = "") -> str | None:
        """Verifica se o servidor Ollama está em execução e acessível."""
        models = self.list_models()
        if not models:
            return "Ollama server not reachable or no models installed."
        return None

WARNING_LOCAL_LIB = """⚠  LOCAL LIBRARY EXECUTION MODE ... (mantido original)"""


class LocalLibProvider(BaseProvider):
    name = "Local (llama-cpp-python)"
    icon = "🖥"
    description = "Loads a GGUF model directly in memory using llama-cpp-python."
    requires_key = False
    _loaded_model: Any = None
    _model_path: str = ""
    _load_lock: threading.Lock = threading.Lock()

    def send(self, messages, on_chunk, on_done, on_error, options=None) -> None:
        """Envia uma mensagem para o provider de biblioteca local e retorna a resposta."""
        self._reset_cancel()
        cfg = self._snapshot(options)
        cancelled = self._cancelled

        def _worker() -> None:
            try:
                from llama_cpp import Llama
            except ImportError:
                on_error(
                    "llama-cpp-python not installed.\nInstall with: pip install llama-cpp-python"
                )
                return

            model_path = cfg.get("caminho_modelo", "")
            if not model_path:
                on_error("Path to .gguf file not configured.")
                return

            n_gpu = int(cfg.get("n_gpu_layers", 0))
            ctx_size = int(cfg.get("n_ctx", 4096))
            max_tok = int(cfg.get("max_tokens", 2048))

            cls = self.__class__
            with cls._load_lock:
                if cls._loaded_model is None or cls._model_path != model_path:
                    try:
                        on_chunk("⏳ Carregando modelo (pode demorar)...\n")
                        cls._loaded_model = Llama(
                            model_path=model_path,
                            n_gpu_layers=n_gpu,
                            n_ctx=ctx_size,
                            verbose=False,
                        )
                        cls._model_path = model_path
                        on_chunk("✅ Modelo carregado.\n\n")
                    except (ValueError, RuntimeError, OSError) as exc:
                        on_error(f"Error loading model: {exc}")
                        return

            model = cls._loaded_model

            prompt_parts = []
            for msg in messages:
                role = msg["role"]
                prompt_parts.append(
                    "<|im_start|>" + role + "\n" +
                    msg["content"] + "<|im_end|>"
                )
            prompt_parts.append("<|im_start|>assistant\n")
            full_prompt = "\n".join(prompt_parts)

            accumulated: list[str] = []
            try:
                output = model(
                    full_prompt,
                    max_tokens=max_tok,
                    stream=True,
                    stop=["<|im_end|>", "<|im_start|>"],
                )
                for chunk_data in output:
                    if cancelled.is_set():
                        break
                    chunk = chunk_data["choices"][0]["text"]
                    if chunk:
                        accumulated.append(chunk)
                        on_chunk(chunk)
                on_done("".join(accumulated))
            except (RuntimeError, ValueError) as exc:
                on_error(f"Error during generation: {exc}")

        threading.Thread(target=_worker, daemon=True,
                         name="ia-local-lib").start()

    def check(self, model_id: str = "") -> str | None:
        """Verifica se a biblioteca de IA local está disponível."""
        import importlib.util

        if importlib.util.find_spec("llama_cpp") is None:
            return "llama-cpp-python not installed (pip install llama-cpp-python)."
        import os

        model_path = self._cfg.get("caminho_modelo", "")
        if not model_path:
            return "Path to .gguf file not configured."
        if not os.path.exists(model_path):
            return f"File not found: {model_path}"
        return None

PROVIDERS: list[type[BaseProvider]] = [
    AnthropicProvider,
    OpenAIProvider,
    OpenAICompatibleProvider,
    GeminiProvider,
    OllamaProvider,
    LocalLibProvider,
]

PROVIDER_MAP: dict[str, type[BaseProvider]] = {
    cls.name: cls for cls in PROVIDERS}


def create_provider(name: str) -> BaseProvider | None:
    """Instancia e retorna o provider de IA correspondente ao ``kind`` solicitado."""
    cls = PROVIDER_MAP.get(name)
    return cls() if cls else None
