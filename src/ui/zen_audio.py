"""Codis - Lógica de áudio do Zen Player (sem dependência de Tkinter).

Contém:
    * Equalizador de 10 bandas, presets e conversão para mpv/VLC.
    * Resolução de capa: imagem ao lado do arquivo, arte embutida
      (mutagen) e frame/arte via ffmpeg, com cache em disco.
    * Leitura de metadados (título/artista/álbum) com fallback pelo nome.
    * Capa gerada (gradiente determinístico) quando nada é encontrado.

Tudo é opcional e degrada com elegância: sem ``mutagen``, ``ffmpeg`` ou
``Pillow`` o player continua funcionando com a capa gerada.
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import logging
import shutil
import subprocess
from collections.abc import Sequence
from pathlib import Path
from typing import Any

logger = logging.getLogger("Codis.ZenAudio")

EQ_BANDS_HZ: tuple[int, ...] = (31, 62, 125, 250, 500, 1000, 2000, 4000, 8000, 16000)
EQ_BAND_LABELS: tuple[str, ...] = (
    "31", "62", "125", "250", "500", "1k", "2k", "4k", "8k", "16k",
)
EQ_MIN_DB = -12.0
EQ_MAX_DB = 12.0

EQ_PRESETS: dict[str, tuple[float, ...]] = {
    "Flat": (0, 0, 0, 0, 0, 0, 0, 0, 0, 0),
    "Bass Boost": (6, 5, 4, 2.5, 1, 0, 0, 0, 0, 0),
    "Treble Boost": (0, 0, 0, 0, 0, 1, 2.5, 4, 5, 6),
    "Vocal": (-2, -2, -1, 1, 3, 4, 3, 1, 0, -1),
    "Rock": (5, 4, 2.5, 1, -1, -1.5, 1, 3, 4, 4.5),
    "Pop": (-1.5, 0, 2, 3.5, 4, 3, 1, -0.5, -1, -1.5),
    "Jazz": (3, 2, 1, 2, -1.5, -1.5, 0, 1.5, 2.5, 3),
    "Classical": (4, 3, 2, 1.5, -0.5, -0.5, 0, 2, 3, 3.5),
    "Electronic": (5, 4.5, 1, 0, -2, 2, 1, 1.5, 4, 5),
    "Hip-Hop": (5.5, 5, 3, 3, -1, -1, 1.5, -0.5, 2, 3),
    "Acoustic": (4, 3.5, 2.5, 1, 2, 2, 3, 3, 2.5, 1.5),
    "Lo-fi": (3, 2.5, 1.5, 0.5, 0, -1, -2.5, -4, -6, -8),
    "Late Night": (2, 1.5, 1, 0, 0, 0.5, 1, 0, -1.5, -3),
}
DEFAULT_PRESET = "Flat"


def clamp_gains(gains: Sequence[float] | None) -> list[float]:
    """Normaliza uma lista de ganhos para 10 valores dentro de ±12 dB."""
    values = list(gains or [])[: len(EQ_BANDS_HZ)]
    values += [0.0] * (len(EQ_BANDS_HZ) - len(values))
    out: list[float] = []
    for raw in values:
        try:
            value = float(raw)
        except (TypeError, ValueError):
            value = 0.0
        out.append(round(max(EQ_MIN_DB, min(EQ_MAX_DB, value)), 1))
    return out


def auto_preamp(gains: Sequence[float]) -> float:
    """Pré-amplificação negativa que evita clipping quando há bandas > 0 dB."""
    peak = max([0.0, *clamp_gains(gains)])
    return -round(peak * 0.85, 1)


def match_preset(gains: Sequence[float]) -> str | None:
    """Nome do preset embutido que coincide com ``gains`` (ou ``None``)."""
    current = clamp_gains(gains)
    for name, preset in EQ_PRESETS.items():
        if current == clamp_gains(preset):
            return name
    return None


def mpv_af_string(gains: Sequence[float]) -> str:
    """Cadeia de filtros ``af`` do mpv equivalente ao equalizador.

    Retorna string vazia quando todas as bandas estão em 0 dB (sem filtro).
    """
    values = clamp_gains(gains)
    if not any(values):
        return ""
    parts = [f"volume={auto_preamp(values)}dB"]
    for freq, gain in zip(EQ_BANDS_HZ, values):
        if gain:
            parts.append(f"equalizer=f={freq}:t=o:w=1:g={gain}")
    return "lavfi=[" + ",".join(parts) + "]"


def apply_vlc_equalizer(player: Any, gains: Sequence[float], enabled: bool) -> bool:
    """Aplica o equalizador num ``vlc.MediaPlayer``. Retorna sucesso."""
    try:
        import vlc  # type: ignore[import-not-found]

        if not enabled or not any(clamp_gains(gains)):
            player.set_equalizer(None)
            return True
        values = clamp_gains(gains)
        equalizer = vlc.AudioEqualizer()
        equalizer.set_preamp(auto_preamp(values))
        for index, gain in enumerate(values):
            equalizer.set_amp_at_index(gain, index)
        player.set_equalizer(equalizer)
        return True
    except (ImportError, AttributeError, OSError, RuntimeError, TypeError) as exc:
        logger.debug("VLC equalizer: %s", exc)
        return False

_IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".webp", ".bmp")
_FOLDER_COVER_NAMES = ("cover", "folder", "front", "album", "albumart", "artwork", "thumb")


def _is_local(path: str) -> bool:
    return bool(path) and "://" not in path


def _clean_title(name: str) -> str:
    """Nome amigável: tira pastas e extensão quando ``name`` parece um caminho."""
    name = (name or "").strip()
    if not name:
        return ""
    looks_like_path = "/" in name or "\\" in name
    suffix = Path(name).suffix.lower()
    has_media_ext = 1 < len(suffix) <= 5 and suffix[1:].isalnum() and suffix in {
        ".mp3", ".flac", ".wav", ".m4a", ".ogg", ".aac", ".wma", ".opus", ".mp4",
        ".mkv", ".webm", ".avi", ".mov", ".wmv", ".flv", ".m4v", ".ts", ".3gp"}
    if looks_like_path or has_media_ext:
        return Path(name.replace("\\", "/")).stem
    return name


def read_metadata(path: str, fallback_title: str = "") -> dict[str, str]:
    """Título/artista/álbum do arquivo; usa o nome do arquivo se faltar tag."""
    meta = {"title": "", "artist": "", "album": ""}
    if _is_local(path) and Path(path).is_file():
        try:
            import mutagen  # type: ignore[import-not-found]

            tags = mutagen.File(path, easy=True)
            if tags:
                for key in meta:
                    value = tags.get(key)
                    if value:
                        meta[key] = str(value[0]).strip()
        except ImportError:
            pass
        except Exception as exc:  # noqa: BLE001
            logger.debug("mutagen(%s): %s", path, exc)
    if not meta["title"]:
        meta["title"] = _clean_title(fallback_title) or (
            Path(path).stem if _is_local(path) else path)
    if " - " in meta["title"] and not meta["artist"]:
        artist, _, title = meta["title"].partition(" - ")
        if artist.strip() and title.strip():
            meta["artist"], meta["title"] = artist.strip(), title.strip()
    return meta


def find_sidecar_cover(path: str) -> Path | None:
    """Imagem com o mesmo nome do áudio, ou cover/folder/front na pasta."""
    if not _is_local(path):
        return None
    media = Path(path)
    folder = media.parent
    try:
        if not folder.is_dir():
            return None
        files = {p.name.lower(): p for p in folder.iterdir() if p.is_file()}
    except OSError:
        return None
    for ext in _IMAGE_EXTS:
        hit = files.get(media.stem.lower() + ext)
        if hit:
            return hit
    for base in _FOLDER_COVER_NAMES:
        for ext in _IMAGE_EXTS:
            hit = files.get(base + ext)
            if hit:
                return hit
    return None


def extract_embedded_cover(path: str) -> bytes | None:
    """Arte embutida nas tags (ID3 APIC, FLAC picture, MP4 covr, OGG)."""
    if not _is_local(path) or not Path(path).is_file():
        return None
    try:
        import mutagen  # type: ignore[import-not-found]

        audio = mutagen.File(path)
        if audio is None:
            return None
        tags = getattr(audio, "tags", None)
        pictures = getattr(audio, "pictures", None)
        if pictures:
            return bytes(pictures[0].data)
        if tags is not None:
            for key in tags.keys():
                if str(key).startswith("APIC"):
                    return bytes(tags[key].data)
            covr = tags.get("covr") if hasattr(tags, "get") else None
            if covr:
                return bytes(covr[0])
    except ImportError:
        return None
    except Exception as exc:  # noqa: BLE001
        logger.debug("capa embutida(%s): %s", path, exc)
    return None


def extract_ffmpeg_thumbnail(path: str, timeout: float = 8.0) -> bytes | None:
    """Capa/frame via ffmpeg (arte anexada em áudio ou frame de vídeo)."""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg or not _is_local(path) or not Path(path).is_file():
        return None
    for extra in (["-an", "-ss", "3"], ["-an"]):
        cmd = [ffmpeg, "-v", "error", *extra, "-i", path, "-frames:v", "1",
               "-f", "image2pipe", "-vcodec", "mjpeg", "-"]
        try:
            proc = subprocess.run(  # noqa: S603
                cmd, capture_output=True, timeout=timeout, check=False
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        if proc.returncode == 0 and proc.stdout:
            return proc.stdout
    return None


def _cache_dir() -> Path:
    path = Path.home() / ".codis" / "cache" / "covers"
    with contextlib.suppress(OSError):
        path.mkdir(parents=True, exist_ok=True)
    return path


def _cache_key(path: str) -> str:
    try:
        stat = Path(path).stat()
        stamp = f"{path}|{stat.st_mtime_ns}|{stat.st_size}"
    except OSError:
        stamp = path
    return hashlib.sha1(stamp.encode("utf-8", "replace")).hexdigest()  # noqa: S324


def resolve_cover_bytes(path: str) -> tuple[bytes | None, str]:
    """Descobre a capa de ``path``. Retorna ``(bytes, origem)``.

    Ordem: arquivo ao lado -> cache -> arte embutida -> ffmpeg. A origem é
    ``sidecar``, ``cache``, ``embedded``, ``ffmpeg`` ou ``none``.
    Bloqueante: chame fora da thread de interface.
    """
    sidecar = find_sidecar_cover(path)
    if sidecar is not None:
        with contextlib.suppress(OSError):
            return sidecar.read_bytes(), "sidecar"
    if not _is_local(path):
        return None, "none"

    cached = _cache_dir() / f"{_cache_key(path)}.jpg"
    if cached.is_file():
        with contextlib.suppress(OSError):
            return cached.read_bytes(), "cache"

    for origin, getter in (("embedded", extract_embedded_cover),
                           ("ffmpeg", extract_ffmpeg_thumbnail)):
        data = getter(path)
        if data:
            with contextlib.suppress(OSError):
                cached.write_bytes(data)
            return data, origin
    return None, "none"


def _hsv_to_rgb(h: float, s: float, v: float) -> tuple[int, int, int]:
    import colorsys

    r, g, b = colorsys.hsv_to_rgb(h, s, v)
    return int(r * 255), int(g * 255), int(b * 255)


def fallback_cover(seed: str, size: int = 512):
    """Capa gerada: gradiente diagonal determinístico a partir do ``seed``."""
    from PIL import Image, ImageChops, ImageDraw

    digest = hashlib.sha1(seed.encode("utf-8", "replace")).digest()  # noqa: S324
    hue = digest[0] / 255
    c1 = _hsv_to_rgb(hue, 0.65, 0.85)
    c2 = _hsv_to_rgb((hue + 0.12 + digest[1] / 255 * 0.2) % 1, 0.75, 0.38)
    ramp = Image.linear_gradient("L").resize((size, size))
    diagonal = ImageChops.add(ramp, ramp.transpose(Image.Transpose.ROTATE_90), scale=2.0)
    img = Image.composite(Image.new("RGB", (size, size), c2),
                          Image.new("RGB", (size, size), c1), diagonal)
    draw = ImageDraw.Draw(img, "RGBA")
    cx, cy = size // 2, size // 2
    for r, alpha in ((size * 0.36, 26), (size * 0.26, 34), (size * 0.16, 46)):
        draw.ellipse((cx - r, cy - r, cx + r, cy + r), outline=(255, 255, 255, alpha), width=3)
    r = size * 0.055
    draw.ellipse((cx - r, cy - r, cx + r, cy + r), fill=(255, 255, 255, 90))
    return img


def dominant_color(image) -> str:
    """Cor de destaque (#rrggbb) da capa, saturada e com brilho utilizável."""
    import colorsys

    small = image.convert("RGB").resize((24, 24))
    best, best_score = (124, 77, 255), -1.0
    for r, g, b in small.getdata():
        h, s, v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
        score = s * (1 - abs(v - 0.65))
        if score > best_score:
            best_score, best = score, (r, g, b)
    h, s, v = colorsys.rgb_to_hsv(*(c / 255 for c in best))
    r, g, b = colorsys.hsv_to_rgb(h, max(s, 0.45), max(min(v, 0.95), 0.6))
    return f"#{int(r * 255):02x}{int(g * 255):02x}{int(b * 255):02x}"


def load_cover_image(path: str, size: int = 512):
    """Carrega a capa como imagem PIL quadrada. Nunca levanta.

    Returns:
        tuple: ``(imagem_PIL, origem)``; sem capa real usa o gradiente gerado
        e a origem ``generated``.
    """
    seed = Path(path).stem if _is_local(path) else path
    try:
        from PIL import Image, ImageOps

        data, origin = resolve_cover_bytes(path)
        if data:
            with Image.open(io.BytesIO(data)) as raw:
                img = ImageOps.fit(raw.convert("RGB"), (size, size), Image.LANCZOS)
            return img, origin
        return fallback_cover(seed, size), "generated"
    except Exception as exc:  # noqa: BLE001
        logger.debug("capa(%s): %s", path, exc)
        try:
            return fallback_cover(seed, size), "generated"
        except Exception:  # noqa: BLE001
            return None, "none"
