"""Utilitários compartilhados para persistir listas de dataclasses em JSON.

Centraliza o padrão save/load repetido (com pequenas variações) em
``chat_panel.py``, ``remote_browser.py``, ``sql_studio.py``,
``task_manager.py`` e ``clock_panel.py``.

- Falhas de leitura (JSON corrompido, permissão negada, etc.) são
  registradas via ``logger.warning``/``logger.debug``.
- Chaves desconhecidas/ausentes são filtradas, evitando ``TypeError`` ao
  construir os dataclasses.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import sys
from dataclasses import asdict
from pathlib import Path
from typing import TypeVar

_T = TypeVar("_T")

_persist_logger = logging.getLogger("Codis.Persistence")


def load_dataclass_list[T](
    path: Path,
    cls: type[_T],
    *,
    logger: logging.Logger | None = None,
) -> list[_T]:
    """Carrega uma lista de instâncias de ``cls`` a partir de um arquivo JSON.

    Retorna lista vazia se o arquivo não existir ou não puder ser lido ou
    parseado (registrando um aviso via ``logger``, em vez de falhar
    silenciosamente). Tolera campos novos/removidos: chaves desconhecidas de
    cada item são descartadas antes de instanciar ``cls``, e itens que não
    sejam ``dict`` (ou que ainda assim falhem ao instanciar) são ignorados
    individualmente sem descartar o resto da lista.
    """
    log = logger or _persist_logger
    if not path.exists():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        log.warning("Falha ao carregar %s: %s", path, exc, exc_info=True)
        return []
    if not isinstance(raw, list):
        log.warning("Formato inesperado em %s (esperava uma lista JSON)", path)
        return []

    # type: ignore[attr-defined]  # pylint: disable=no-member
    known = set(cls.__dataclass_fields__)
    result: list[_T] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        try:
            result.append(cls(**{k: v for k, v in item.items() if k in known}))
        except (TypeError, ValueError) as exc:
            log.warning("Item inválido em %s ignorado: %s", path, exc)
    return result


def save_dataclass_list(path: Path, items: list, *, chmod_600: bool = False) -> None:
    """Salva uma lista de dataclasses como JSON em ``path``.

    Cria o diretório pai se necessário. Se ``chmod_600`` for ``True`` (usado
    para arquivos com dados sensíveis, como tokens/senhas em base64),
    restringe a permissão do arquivo a ``0600`` em sistemas não-Windows,
    em modo best-effort (falhas de ``chmod`` são ignoradas).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps([asdict(i) for i in items], indent=2, ensure_ascii=False)
    if not chmod_600 or sys.platform == "win32":
        path.write_text(payload, encoding="utf-8")
        return
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(payload)
        os.replace(tmp, path)
    except OSError:
        with contextlib.suppress(OSError):
            tmp.unlink()
        raise
    with contextlib.suppress(OSError):
        path.chmod(0o600)
