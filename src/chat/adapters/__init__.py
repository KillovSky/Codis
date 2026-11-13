"""Adaptadores de protocolo do Chat."""

from __future__ import annotations

from src.chat.adapters.base import SERVER_TAB, Adapter, AdapterFatal, Backoff, Events
from src.chat.models import ChatProfile, ChatProtocol


def make_adapter(profile: ChatProfile, events: Events | None = None) -> Adapter:
    """Instancia o adaptador do protocolo do perfil (import tardio: carrega só o necessário)."""
    proto = profile.proto
    if proto in (ChatProtocol.IRC, ChatProtocol.IRCS):
        from src.chat.adapters.irc import IRCAdapter as cls
    elif proto in (ChatProtocol.XMPP, ChatProtocol.XMPPS):
        from src.chat.adapters.xmpp import XMPPAdapter as cls
    elif proto == ChatProtocol.MATRIX:
        from src.chat.adapters.matrix import MatrixAdapter as cls
    elif proto == ChatProtocol.SLACK:
        from src.chat.adapters.slack import SlackAdapter as cls
    elif proto == ChatProtocol.DISCORD:
        from src.chat.adapters.discord import DiscordAdapter as cls
    elif proto == ChatProtocol.TELEGRAM:
        from src.chat.adapters.telegram import TelegramAdapter as cls
    elif proto == ChatProtocol.MATTERMOST:
        from src.chat.adapters.mattermost import MattermostAdapter as cls
    elif proto == ChatProtocol.MQTT:
        from src.chat.adapters.mqtt import MQTTAdapter as cls
    else:
        from src.chat.adapters.ws import WSAdapter as cls
    return cls(profile, events)


__all__ = ["SERVER_TAB", "Adapter", "AdapterFatal", "Backoff", "Events", "make_adapter"]
