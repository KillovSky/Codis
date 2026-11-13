#!/usr/bin/env python3
"""Sincroniza arquivos de localização garantindo que todos compartilhem as mesmas chaves."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

IGNORED_FILES = {".translate_manifest.json"}


def load_all(locales_dir: Path) -> dict[str, dict[str, Any]]:
    """Carrega todos os arquivos JSON de locale do diretório informado.

    Ignora arquivos de controle internos (ver ``IGNORED_FILES``), que não
    são locales de verdade mesmo tendo extensão ``.json``.
    """
    files = sorted(
        p for p in locales_dir.glob("*.json") if p.name not in IGNORED_FILES
    )
    if not files:
        print(f"Nenhum .json encontrado em {locales_dir}", file=sys.stderr)
        raise SystemExit(1)

    loaded: dict[str, dict[str, Any]] = {}
    for path in files:
        try:
            loaded[path.name] = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            print(f"ERRO ao ler {path.name}: {exc}", file=sys.stderr)
            raise SystemExit(1) from exc
    return loaded


def collect_all_keys(all_data: dict[str, dict[str, Any]]) -> list[str]:
    """Retorna a união ordenada das chaves encontradas em todos os arquivos."""
    seen: set[str] = set()
    ordered: list[str] = []

    for locale_data in all_data.values():
        for key in locale_data:
            if key not in seen:
                seen.add(key)
                ordered.append(key)

    return ordered


def find_value(key: str, all_data: dict[str, dict[str, Any]]) -> str:
    """Retorna o primeiro valor disponível para a chave informada."""
    for locale_data in all_data.values():
        if key in locale_data:
            return str(locale_data[key])
    return f"[TODO] {key}"


def sync(
    all_data: dict[str, dict[str, Any]],
    all_keys: list[str],
) -> dict[str, tuple[dict[str, Any], list[str]]]:
    """Adiciona as chaves faltantes a cada arquivo sem alterar a ordem existente."""
    results: dict[str, tuple[dict[str, Any], list[str]]] = {}

    for filename, locale_data in all_data.items():
        added: list[str] = []
        new_data = dict(locale_data)

        for key in all_keys:
            if key not in new_data:
                new_data[key] = find_value(key, all_data)
                added.append(key)

        results[filename] = (new_data, added)

    return results


def parse_args() -> argparse.Namespace:
    """Lê os argumentos de linha de comando do script."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--locales-dir",
        default="src/locales",
        help="Diretório com os arquivos .json (default: src/locales)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Mostra o que seria feito, sem salvar os arquivos",
    )
    return parser.parse_args()


def main() -> None:
    """Executa a sincronização das chaves entre os arquivos de locale."""
    args = parse_args()

    locales_dir = Path(args.locales_dir)
    if not locales_dir.is_dir():
        print(f"Diretório não encontrado: {locales_dir}", file=sys.stderr)
        raise SystemExit(1)

    print(f"📂 Diretório: {locales_dir.resolve()}")
    all_data = load_all(locales_dir)
    print(f"📄 Arquivos encontrados: {', '.join(all_data)}")

    print("\n─── Contagem de keys antes da sincronização ───")
    for filename, locale_data in all_data.items():
        print(f"  {filename:<16} {len(locale_data):>5} keys")

    all_keys = collect_all_keys(all_data)
    print(f"\n🔑 Total de keys únicas (superconjunto): {len(all_keys)}")

    results = sync(all_data, all_keys)

    print("\n─── Keys adicionadas por arquivo ───")
    total_added = 0
    for filename, (_new_data, added) in results.items():
        count = len(added)
        total_added += count
        status = f"+{count}" if count else "✓ completo"
        print(f"  {filename:<16} {status}")
        if added and not args.dry_run:
            preview = added[:5]
            suffix = f" ...e mais {count - 5}" if count > 5 else ""
            print(f"    Adicionadas: {preview}{suffix}")

    if total_added == 0:
        print("\n✅ Todos os arquivos já estão sincronizados. Nada a fazer.")
        return

    if args.dry_run:
        print(f"\n[dry-run] {total_added} keys seriam adicionadas. Nenhum arquivo foi modificado.")
        return

    print("\n💾 Salvando arquivos...")
    for filename, (new_data, added) in results.items():
        if not added:
            continue

        output_path = locales_dir / filename
        output_path.write_text(
            json.dumps(new_data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"  ✓ {filename} salvo ({len(new_data)} keys)")

    print(f"\n✅ Sincronização concluída. {total_added} keys adicionadas no total.")
    print("   Procure por '[TODO]' nos arquivos para saber o que precisa de tradução.")


if __name__ == "__main__":
    main()