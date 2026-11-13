"""Ferramenta de tradução automatizada de arquivos de locale para o projeto.

Lê o arquivo de origem em português brasileiro, detecta mudanças por
manifesto de hashes, traduz em lote com retentativas e escreve os dados
traduzidos em arquivos JSON para cada idioma suportado.

Inclui máscaras de placeholders, pós-processamento de texto e opções de linha
de comando para execução parcial, simulação e criação de arquivos novos.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import re
import shutil
import sys
import time
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

try:
    from googletrans import Translator  # type: ignore[import]
except ImportError:
    print("ERRO: instale googletrans: pip install googletrans==3.1.0a0")
    sys.exit(1)

LOCALES_DIR = Path(__file__).resolve().parent / "src" / "locales"
SOURCE_FILE = LOCALES_DIR / "pt-BR.json"
SOURCE_LANG = "pt"
MANIFEST_FILE = LOCALES_DIR / ".translate_manifest.json"
BACKUP_DIR = LOCALES_DIR / ".backups"

SUPPORTED_LOCALES: list[str] = [
    "en",
    "es",
    "fr",
    "de",
    "it",
    "ja",
    "ko",
    "nl",
    "pl",
    "ru",
    "tr",
    "uk",
    "zh-CN",
]

GOOGLE_LANG_OVERRIDES: dict[str, str] = {
    "zh-CN": "zh-cn",
    "zh-TW": "zh-tw",
}

PRESERVE_UPPERCASE: list[str] = [
    "API",
    "URL",
    "SSH",
    "HTTP",
    "HTTPS",
    "JSON",
    "XML",
    "SQL",
    "HTML",
    "CSS",
    "JS",
    "TS",
    "UI",
    "UX",
    "ID",
    "TLS",
    "SSL",
    "JWT",
    "UUID",
    "TTS",
    "LSP",
    "DAP",
    "MCP",
    "CPU",
    "RAM",
    "FTP",
    "SFTP",
    "RPC",
    "WSL",
    "PAT",
    "EOL",
    "CRLF",
    "LF",
    "NVDA",
    "ORCA",
    "WCAG",
    "DOT",
    "SVG",
    "PNG",
    "PDF",
    "CSV",
    "OK",
    "TODO",
    "FIXME",
    "AI",
    "IA",
    "IP",
    "MUC",
    "XMPP",
    "IRC",
    "MQTT",
    "WS",
    "PID",
    "ACK",
]

BATCH_SIZE = 20
DELAY_BETWEEN_BATCHES = 2.0
DELAY_BETWEEN_FILES = 5.0
MAX_RETRIES = 6
RETRY_BACKOFF_BASE = 2.5
MAX_CONCURRENT_REQUESTS = 1

PLACEHOLDER_RE = re.compile(r"(\{[^}]+\})")
EMOJI_RE = re.compile(r"[\U00010000-\U0010FFFF]", flags=re.UNICODE)


class _ColorFormatter(logging.Formatter):
    """Formata mensagens de log com cores básicas para terminal."""

    COLORS = {
        logging.DEBUG: "\033[90m",
        logging.INFO: "\033[0m",
        logging.WARNING: "\033[33m",
        logging.ERROR: "\033[31m",
        logging.CRITICAL: "\033[1;31m",
    }
    RESET = "\033[0m"

    def format(self, record: logging.LogRecord) -> str:
        """Retorna a mensagem formatada com o código de cor adequado."""
        color = self.COLORS.get(record.levelno, self.RESET)
        msg = super().format(record)
        return f"{color}{msg}{self.RESET}"


def setup_logging(
    to_file: bool = False, logger: logging.Logger | None = None
) -> logging.Logger:
    """Configura o logger principal para saída em terminal e, opcionalmente, em arquivo."""
    if logger is None:
        logger = logging.getLogger("translate")

    logger.setLevel(logging.DEBUG)
    logger.handlers.clear()

    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setLevel(logging.INFO)
    stream_handler.setFormatter(_ColorFormatter("%(message)s"))
    logger.addHandler(stream_handler)

    if to_file:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        log_path = Path(f"translate_{stamp}.log")
        file_handler = logging.FileHandler(log_path, encoding="utf-8")
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(message)s")
        )
        logger.addHandler(file_handler)
        logger.info("📝 Log gravado em: %s", log_path)

    return logger


log = setup_logging()


def _hash(text: str) -> str:
    """Calcula um hash curto para identificar alterações de conteúdo."""
    return hashlib.md5(text.encode(), usedforsecurity=False).hexdigest()[:12]


def load_manifest() -> dict[str, dict[str, str]]:
    """Carrega o manifesto de hashes persistido em disco."""
    if MANIFEST_FILE.exists():
        try:
            return json.loads(MANIFEST_FILE.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            pass
    return {}


def save_manifest(manifest: dict[str, dict[str, str]]) -> None:
    """Salva o manifesto de hashes em formato JSON ordenado."""
    MANIFEST_FILE.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def mask(text: str) -> tuple[str, list[str]]:
    """Substitui placeholders por marcadores neutros para evitar interferência na tradução."""
    placeholders = PLACEHOLDER_RE.findall(text)
    masked = text
    for index, placeholder in enumerate(placeholders):
        masked = masked.replace(placeholder, f"__PH{index}__", 1)
    return masked, placeholders


def restore(text: str, placeholders: list[str]) -> str:
    """Restaura placeholders original após a tradução."""
    for index, placeholder in enumerate(placeholders):
        pattern = re.compile(rf"__\s*[pP][hH]\s*{index}\s*__")
        if pattern.search(text):
            text = pattern.sub(placeholder, text, count=1)
        else:
            text = text.replace(f"__PH{index}__", placeholder, 1)
    return text


def _fix_siglas(text: str) -> str:
    """Restaura siglas para o formato preservado em maiúsculas."""
    for sigla in PRESERVE_UPPERCASE:
        text = re.sub(
            rf"\b{re.escape(sigla)}\b",
            sigla,
            text,
            flags=re.IGNORECASE,
        )
    return text


def _fix_quotes(text: str) -> str:
    """Normaliza aspas tipográficas para o formato simples usado no projeto."""
    for current, replacement in [
        ("\u2018", "'"),
        ("\u2019", "'"),
        ("\u201c", '"'),
        ("\u201d", '"'),
    ]:
        text = text.replace(current, replacement)
    return text


def _fix_spaces(text: str) -> str:
    """Remove espaços duplicados e espaços antes de sinais de pontuação."""
    text = re.sub(r"  +", " ", text)
    text = re.sub(r" ([.,;:!?])", r"\1", text)
    return text.strip()


def _fix_ellipsis(text: str) -> str:
    """Mantém a forma da elipse sem alterar o texto de forma arbitrária."""
    return text


def postprocess(translated: str, original: str) -> str:
    """Aplica o pós-processamento textual após a tradução."""
    if not translated.strip():
        return original if original.strip() else translated

    text = translated
    text = _fix_quotes(text)
    text = _fix_spaces(text)
    text = _fix_siglas(text)

    if original and original[0].isupper() and text and text[0].islower():
        text = text[0].upper() + text[1:]

    if original.endswith("...") and not text.endswith("..."):
        text = text.rstrip(" .") + "..."

    return text


@lru_cache(maxsize=1)
def get_semaphore() -> asyncio.Semaphore:
    """Retorna um semáforo compartilhado para controlar a taxa de requisições."""
    return asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)


async def translate_batch(
    translator: Translator,
    texts: list[str],
    dest: str,
    src: str = SOURCE_LANG,
) -> list[str]:
    """Traduz uma lista de textos com backoff exponencial e retentativas."""
    async with get_semaphore():
        last_error: Exception | None = None
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                response = await translator.translate(texts, src=src, dest=dest)
                if not isinstance(response, list):
                    response = [response]
                return [item.text for item in response]
            except (
                ConnectionError,
                TimeoutError,
                RuntimeError,
                ValueError,
                OSError,
                TypeError,
            ) as exc:
                last_error = exc
                wait = RETRY_BACKOFF_BASE * (2 ** (attempt - 1))
                log.warning(
                    "    ⚠  Tentativa %d/%d falhou: %s - aguardando %.1fs...",
                    attempt,
                    MAX_RETRIES,
                    type(exc).__name__,
                    wait,
                )
                await asyncio.sleep(wait)

        log.error(
            "    ✗  Lote falhou após %d tentativas (%r). Mantendo texto original.",
            MAX_RETRIES,
            last_error,
        )
        return list(texts)


def load_json(path: Path) -> dict[str, Any]:
    """Carrega um arquivo JSON e retorna um dicionário, ou um objeto vazio em caso de erro."""
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        log.error("  ✗  JSON inválido em %s: %s", path, exc)
        return {}


def save_json(path: Path, data: dict[str, Any], dry_run: bool = False) -> bool:
    """Salva um dicionário em JSON ordenado e valida o conteúdo antes de gravar."""
    ordered = dict(sorted(data.items()))
    content = json.dumps(ordered, ensure_ascii=False, indent=2) + "\n"

    try:
        json.loads(content)
    except json.JSONDecodeError as exc:
        log.error("  ✗  Validação falhou, NÃO gravando %s: %s", path, exc)
        return False

    if dry_run:
        log.info("  [dry-run] salvaria %s (%d chaves)",
                 path.name, len(ordered))
        return True

    if path.exists():
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup = BACKUP_DIR / f"{path.stem}_{stamp}.json"
        shutil.copy2(path, backup)

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return True


class Stats:
    """Armazena os resultados resumidos de uma execução por idioma."""

    def __init__(self, locale: str) -> None:
        self.locale = locale
        self.new = 0
        self.updated = 0
        self.skipped = 0
        self.fallback = 0
        self.errors = 0
        self.elapsed = 0.0

    def summary(self) -> str:
        """Retorna uma string resumida com o status da tradução."""
        total = self.new + self.updated + self.skipped
        return (
            f"📊  {total:4d} chaves | "
            f"  {self.locale:8s} | "
            f"novas: {self.new:4d} | "
            f"atualizadas: {self.updated:4d} | "
            f"puladas: {self.skipped:4d} | "
            f"fallback: {self.fallback:3d} | "
            f"erros: {self.errors:2d} | "
            f"tempo: {self.elapsed:.1f}s"
        )


async def translate_file(
    translator: Translator,
    source_data: dict[str, str],
    target_path: Path,
    google_lang: str,
    manifest: dict[str, dict[str, str]],
    *,
    force: bool = False,
    dry_run: bool = False,
) -> Stats:
    """Traduz um arquivo de idioma usando o manifesto para detectar mudanças."""
    start_time = time.monotonic()
    locale = target_path.stem
    stats = Stats(locale)

    current = load_json(target_path)
    locale_manifest: dict[str, str] = manifest.get(locale, {})

    pending: list[tuple[str, str, str]] = []

    for key, src_text in source_data.items():
        if not isinstance(src_text, str):
            continue

        src_hash = _hash(src_text)
        cur_val = current.get(key)
        old_hash = locale_manifest.get(key)

        if cur_val is None:
            pending.append((key, src_text, "nova"))
        elif force:
            pending.append((key, src_text, "forçada"))
        elif src_hash != old_hash:
            pending.append((key, src_text, "atualizada"))
        else:
            stats.skipped += 1

    total = len(pending)

    log.info("\n%s", "=" * 60)
    log.info("  Idioma  : %s → Google: %s", locale, google_lang)
    log.info("  Arquivo : %s", target_path.name)
    log.info("  Total PT-BR : %4d chaves", len(source_data))
    log.info("  Já prontas  : %4d", stats.skipped)
    log.info("  Pendentes   : %4d", total)

    if total == 0:
        log.info("  ✓  Nada a fazer - arquivo já está completo e atualizado.")
        stats.elapsed = time.monotonic() - start_time
        return stats

    total_batches = (total + BATCH_SIZE - 1) // BATCH_SIZE

    for batch_index, start in enumerate(range(0, total, BATCH_SIZE), 1):
        batch = pending[start: start + BATCH_SIZE]

        masked_texts: list[str] = []
        placeholders_list: list[list[str]] = []

        for _, text, _ in batch:
            masked, placeholders = mask(text)
            masked_texts.append(masked)
            placeholders_list.append(placeholders)

        log.info(
            "  🔄 Lote %d/%d (%d chave(s), %d–%d de %d)",
            batch_index,
            total_batches,
            len(batch),
            start + 1,
            start + len(batch),
            total,
        )

        raw_translations = await translate_batch(
            translator, masked_texts, dest=google_lang
        )

        for (key, original, reason), raw, placeholders in zip(
            batch, raw_translations, placeholders_list, strict=False
        ):
            restored = restore(raw, placeholders)
            final = postprocess(restored, original)

            if not final.strip() and original.strip():
                final = original
                stats.fallback += 1

            if not dry_run:
                current[key] = final
                locale_manifest[key] = _hash(original)

            if reason == "nova":
                stats.new += 1
            else:
                stats.updated += 1

        for key, src_text in source_data.items():
            if key not in current:
                current[key] = src_text
                if not dry_run:
                    locale_manifest[key] = _hash(src_text)

        save_json(target_path, current, dry_run=dry_run)
        if not dry_run:
            manifest[locale] = locale_manifest
            save_manifest(manifest)

        if start + BATCH_SIZE < total:
            await asyncio.sleep(DELAY_BETWEEN_BATCHES)

    stats.elapsed = time.monotonic() - start_time
    log.info("  ✓  %s concluído em %.1fs", target_path.name, stats.elapsed)
    return stats


def create_missing_files(source_data: dict[str, str], dry_run: bool) -> None:
    """Cria arquivos JSON para locales suportados que ainda não existam."""
    for locale in SUPPORTED_LOCALES:
        path = LOCALES_DIR / f"{locale}.json"
        if path.exists():
            continue
        log.info("  ➕ Criando %s (arquivo novo)", path.name)
        save_json(path, dict(source_data), dry_run=dry_run)


def parse_args() -> argparse.Namespace:
    """Interpreta os argumentos da linha de comando."""
    parser = argparse.ArgumentParser(
        description="Traduz pt-BR.json para todos os demais locales.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "locales",
        nargs="*",
        metavar="LOCALE",
        help="Códigos de idioma a processar. Omita para processar todos.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Retraduz todas as chaves, ignorando o manifesto.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Simula sem gravar nenhum arquivo.",
    )
    parser.add_argument(
        "--create-missing",
        action="store_true",
        help="Cria arquivos JSON para locales suportados ainda inexistentes.",
    )
    parser.add_argument(
        "--log",
        action="store_true",
        help="Grava log detalhado em arquivo.",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=BATCH_SIZE,
        metavar="N",
        help=f"Chaves por lote (padrão: {BATCH_SIZE}).",
    )
    parser.add_argument(
        "--delay-batches",
        type=float,
        default=DELAY_BETWEEN_BATCHES,
        metavar="SEG",
        help=f"Segundos entre lotes (padrão: {DELAY_BETWEEN_BATCHES}).",
    )
    parser.add_argument(
        "--delay-files",
        type=float,
        default=DELAY_BETWEEN_FILES,
        metavar="SEG",
        help=f"Segundos entre idiomas (padrão: {DELAY_BETWEEN_FILES}).",
    )
    return parser.parse_args()


async def main() -> None:
    """Executa o ciclo principal de tradução conforme os argumentos recebidos."""
    args = parse_args()

    global BATCH_SIZE, DELAY_BETWEEN_BATCHES, DELAY_BETWEEN_FILES  # pylint: disable=global-statement
    BATCH_SIZE = args.batch_size
    DELAY_BETWEEN_BATCHES = args.delay_batches
    DELAY_BETWEEN_FILES = args.delay_files

    if args.log:
        setup_logging(to_file=True, logger=log)

    LOCALES_DIR.mkdir(parents=True, exist_ok=True)
    if not SOURCE_FILE.exists():
        log.error("ERRO: arquivo fonte não encontrado: %s", SOURCE_FILE)
        sys.exit(1)

    source_data: dict[str, str] = load_json(SOURCE_FILE)
    if not source_data:
        log.error("ERRO: pt-BR.json está vazio ou inválido.")
        sys.exit(1)

    log.info("🌐 Fonte: %s (%d chaves)", SOURCE_FILE.name, len(source_data))

    if args.create_missing:
        create_missing_files(source_data, dry_run=args.dry_run)

    all_locale_files = sorted(LOCALES_DIR.glob("*.json"))
    target_files = [
        path
        for path in all_locale_files
        if path.name != SOURCE_FILE.name and not path.name.startswith(".")
    ]

    if args.locales:
        requested = {
            (value if value.endswith(".json") else f"{value}.json")
            for value in args.locales
        }
        target_files = [
            path for path in target_files if path.name in requested]
        if not target_files:
            log.error("Nenhum arquivo encontrado para: %s", args.locales)
            sys.exit(1)

    if not target_files:
        log.warning("Nenhum arquivo de idioma encontrado para traduzir.")
        log.warning(
            "Dica: coloque arquivos .json em %s ou use --create-missing.", LOCALES_DIR
        )
        sys.exit(0)

    log.info(
        "🗂  Idiomas a processar (%d): %s",
        len(target_files),
        ", ".join(path.stem for path in target_files),
    )
    if args.dry_run:
        log.info("🔍 Modo DRY-RUN ativo - nenhum arquivo será gravado.")
    if args.force:
        log.info("⚡ Modo FORCE ativo - todas as chaves serão retraduzidas.")

    manifest = load_manifest()
    translator = Translator()
    all_stats: list[Stats] = []

    for index, target_path in enumerate(target_files):
        locale = target_path.stem
        google_lang = GOOGLE_LANG_OVERRIDES.get(locale, locale)

        try:
            stats = await translate_file(
                translator,
                source_data,
                target_path,
                google_lang,
                manifest,
                force=args.force,
                dry_run=args.dry_run,
            )
            all_stats.append(stats)
        except KeyboardInterrupt:
            log.warning(
                "\n⚠  Interrompido (Ctrl+C). Progresso salvo até aqui.")
            sys.exit(0)
        except (
            ConnectionError,
            TimeoutError,
            RuntimeError,
            ValueError,
            OSError,
            TypeError,
        ) as exc:
            log.error("  ✗  Erro inesperado em %s: %r", target_path.name, exc)
            log.error("     Continuando para o próximo idioma...")

        if index < len(target_files) - 1:
            log.info(
                "\n⏳ Aguardando %.0fs antes do próximo idioma...", DELAY_BETWEEN_FILES
            )
            await asyncio.sleep(DELAY_BETWEEN_FILES)

    log.info("\n%s", "=" * 60)
    log.info("📋 RELATÓRIO FINAL")
    log.info("%s", "=" * 60)
    for stats in all_stats:
        log.info(stats.summary())

    total_new = sum(stats.new for stats in all_stats)
    total_updated = sum(stats.updated for stats in all_stats)
    total_skipped = sum(stats.skipped for stats in all_stats)
    total_fallback = sum(stats.fallback for stats in all_stats)
    total_elapsed = sum(stats.elapsed for stats in all_stats)

    log.info("%s", "=" * 60)
    log.info(
        "  TOTAL | novas: %d | atualizadas: %d | puladas: %d | fallback: %d",
        total_new,
        total_updated,
        total_skipped,
        total_fallback,
    )
    log.info("  Tempo total de tradução: %.1fs", total_elapsed)
    log.info("✅ Concluído!")


if __name__ == "__main__":
    wall_start = time.time()
    asyncio.run(main())
    wall_elapsed = time.time() - wall_start
    print(f"\n⏱  Tempo de parede total: {wall_elapsed / 60:.1f} minuto(s)")
