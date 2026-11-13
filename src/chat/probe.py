"""Teste rápido de conexão ("Testar") sem iniciar uma sessão de chat."""

from __future__ import annotations

import socket

from src.chat.adapters.http import HTTPFailure, request_json
from src.chat.models import PROTO_DEFAULTS, ChatProfile, ChatProtocol
from src.chat.util import ssl_context


def probe(p: ChatProfile, timeout: float = 8) -> tuple[bool, str]:
    """Devolve ``(ok, mensagem)``. Valida alcance e, quando possível, a credencial."""
    proto = p.proto
    try:
        if proto == ChatProtocol.TELEGRAM:
            base = p.x("api_base", "https://api.telegram.org")
            code, d = request_json("POST", f"{base}/bot{p.decoded_token()}/getMe", body={}, timeout=timeout)
            return (True, f"Bot @{d['result'].get('username')} OK") if d.get("ok") else (False, d.get("description", f"HTTP {code}"))
        if proto == ChatProtocol.DISCORD:
            tok = p.decoded_token()
            auth = tok if p.x("token_kind", "bot") == "user" else f"Bot {tok}"
            code, d = request_json("GET", p.x("api_base", "https://discord.com/api/v10") + "/users/@me",
                                   headers={"Authorization": auth}, timeout=timeout)
            return (True, f"Conta {d.get('username')} OK") if code == 200 else (False, d.get("message", f"HTTP {code}"))
        if proto == ChatProtocol.SLACK:
            code, d = request_json("POST", p.x("api_base", "https://slack.com") + "/api/auth.test",
                                   headers={"Authorization": f"Bearer {p.decoded_token()}"}, body={}, timeout=timeout)
            return (True, f"Workspace {d.get('team')} como {d.get('user')} OK") if d.get("ok") else (False, d.get("error", f"HTTP {code}"))
        if proto in (ChatProtocol.MATRIX, ChatProtocol.MATTERMOST):
            scheme = "https" if p.tls or p.port == 443 else "http"
            base = p.x("api_base") or f"{scheme}://{p.host}:{p.port}"
            path = "/_matrix/client/versions" if proto == ChatProtocol.MATRIX else "/api/v4/system/ping"
            code, _ = request_json("GET", base + path, timeout=timeout, verify=p.verify_tls)
            return (True, "Servidor respondeu OK") if code == 200 else (False, f"HTTP {code}")
        if not p.host:
            return False, "Informe o servidor."
        port = p.port or PROTO_DEFAULTS.get(p.protocol, 0)
        raw = socket.create_connection((p.host, port), timeout=timeout)
        tls = proto in (ChatProtocol.IRCS, ChatProtocol.XMPPS, ChatProtocol.WSS) or (
            p.tls and proto in (ChatProtocol.IRC, ChatProtocol.MQTT, ChatProtocol.CODIS))
        if tls and proto != ChatProtocol.CODIS:
            raw = ssl_context(p.verify_tls).wrap_socket(raw, server_hostname=p.host)
            info = f"TLS {raw.version()}"
        else:
            info = "TCP"
        raw.close()
        return True, f"{p.host}:{port} acessível ({info})"
    except HTTPFailure as exc:
        return False, str(exc)
    except Exception as exc:  # noqa: BLE001
        return False, str(exc) or type(exc).__name__


def validate(p: ChatProfile, taken: set[str]) -> str | None:
    """Mensagem de erro de validação do perfil, ou None se está tudo certo."""
    if not p.name.strip():
        return "Dê um nome à conexão."
    if p.name.casefold() in taken:
        return "Já existe uma conexão com esse nome."
    needs_host = p.proto not in (ChatProtocol.SLACK, ChatProtocol.DISCORD, ChatProtocol.TELEGRAM, ChatProtocol.XMPP,
                                 ChatProtocol.XMPPS)
    if needs_host and not p.host.strip():
        return "Informe o servidor."
    if not 1 <= p.port <= 65535:
        return "Porta inválida (1-65535)."
    if p.proto in (ChatProtocol.IRC, ChatProtocol.IRCS):
        if not p.nick or " " in p.nick:
            return "Nick inválido (não pode ter espaços)."
    if p.proto in (ChatProtocol.XMPP, ChatProtocol.XMPPS) and "@" not in p.jid:
        return "Informe o JID completo (usuario@dominio)."
    if p.proto in (ChatProtocol.SLACK, ChatProtocol.DISCORD, ChatProtocol.TELEGRAM) and not p.decoded_token():
        return "Informe o token."
    if p.proto == ChatProtocol.MATRIX and not (p.decoded_token() or (p.jid and p.decoded_password())):
        return "Informe usuário e senha, ou um access_token."
    if p.proto == ChatProtocol.MATTERMOST and not (p.decoded_token() or (p.jid and p.decoded_password())):
        return "Informe login e senha, ou um Personal Access Token."
    if p.proto == ChatProtocol.CODIS and not p.room_id:
        return "Informe o ID da sala."
    return None
