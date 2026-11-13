"""Cliente HTTP/JSON mínimo (urllib) com tratamento de 429 e TLS configurável."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from src.chat.util import ssl_context


class HTTPFailure(ConnectionError):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(f"HTTP {status}: {message}" if status > 0 else message)
        self.status = status


def request_json(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    body: Any = None,
    params: dict[str, Any] | None = None,
    timeout: float = 15,
    verify: bool = True,
    retry_429: bool = True,
    with_headers: bool = False,
) -> tuple:
    """Faz a requisição; devolve ``(status, json)`` (json vazio se a resposta não for JSON).

    Erros de rede viram :class:`HTTPFailure` com status ``-1``; respostas HTTP de erro
    (4xx/5xx) são *devolvidas* (não levantam) para o chamador decidir.
    """
    if params:
        url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
    data = json.dumps(body).encode() if body is not None else None
    hdrs = {"Accept": "application/json", "User-Agent": "Codis-Chat"}
    if data is not None:
        hdrs["Content-Type"] = "application/json"
    hdrs.update(headers or {})
    ctx = ssl_context(verify) if url.startswith("https") else None
    for attempt in (0, 1):
        req = urllib.request.Request(url, data=data, method=method, headers=hdrs)
        try:
            with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
                raw, status, rh = resp.read(), resp.status, dict(resp.headers)
        except urllib.error.HTTPError as exc:
            raw, status, rh = exc.read(), exc.code, dict(exc.headers)
            if status == 429 and retry_429 and attempt == 0:
                wait = _retry_after(exc.headers.get("Retry-After"), raw)
                time.sleep(min(wait, 30))
                continue
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            reason = getattr(exc, "reason", exc)
            raise HTTPFailure(-1, str(reason)) from exc
        try:
            obj = json.loads(raw) if raw else {}
        except ValueError:
            obj = {}
        return (status, obj, rh) if with_headers else (status, obj)
    return (429, {}, {}) if with_headers else (429, {})


def _retry_after(header: str | None, raw: bytes) -> float:
    for cand in (header,):
        if cand:
            try:
                return float(cand)
            except ValueError:
                pass
    try:
        d = json.loads(raw)
        return float(d.get("retry_after") or d.get("parameters", {}).get("retry_after") or 1)
    except (ValueError, AttributeError, TypeError):
        return 1.0
