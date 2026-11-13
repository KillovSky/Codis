"""Testes de src/ui/zen_audio.py (EQ, capas, metadados) - sem Tk."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.ui import zen_audio as za


def _png(path: Path, color: str = "red") -> None:
    from PIL import Image

    Image.new("RGB", (40, 40), color).save(path)


def test_clamp_gains_pads_trims_and_limits():
    assert za.clamp_gains(None) == [0.0] * 10
    out = za.clamp_gains([99, -99, "x", None, 3.14159])
    assert out[:5] == [12.0, -12.0, 0.0, 0.0, 3.1]
    assert len(za.clamp_gains(list(range(30)))) == 10


def test_all_presets_valid_and_matchable():
    for name, gains in za.EQ_PRESETS.items():
        assert len(gains) == 10, name
        assert all(za.EQ_MIN_DB <= g <= za.EQ_MAX_DB for g in gains), name
        assert za.match_preset(gains) == name


def test_mpv_af_string_flat_is_empty_and_boost_has_preamp():
    assert za.mpv_af_string(za.EQ_PRESETS["Flat"]) == ""
    chain = za.mpv_af_string(za.EQ_PRESETS["Bass Boost"])
    assert chain.startswith("lavfi=[volume=-") and chain.endswith("]")
    assert "equalizer=f=31:t=o:w=1:g=6.0" in chain
    assert "f=16000" not in chain


def test_auto_preamp_prevents_clipping():
    assert za.auto_preamp([0] * 10) == 0.0
    assert za.auto_preamp([6] + [0] * 9) < 0
    assert za.auto_preamp([-6] * 10) == 0.0


def test_sidecar_prefers_same_name_then_folder_cover(tmp_path):
    song = tmp_path / "song.mp3"
    song.write_bytes(b"x")
    assert za.find_sidecar_cover(str(song)) is None
    _png(tmp_path / "Cover.PNG")
    assert za.find_sidecar_cover(str(song)).name == "Cover.PNG"
    _png(tmp_path / "SONG.jpg")
    assert za.find_sidecar_cover(str(song)).name == "SONG.jpg"


def test_urls_have_no_sidecar_and_fallback_cover_is_deterministic():
    assert za.find_sidecar_cover("https://x.test/a.mp3") is None
    a = za.fallback_cover("abc", 64)
    b = za.fallback_cover("abc", 64)
    c = za.fallback_cover("xyz", 64)
    assert a.tobytes() == b.tobytes() != c.tobytes()


def test_load_cover_image_sources(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    song = tmp_path / "a.wav"
    song.write_bytes(b"not audio")
    img, origin = za.load_cover_image(str(song), 64)
    assert origin == "generated" and img.size == (64, 64)
    _png(tmp_path / "a.png", "blue")
    img, origin = za.load_cover_image(str(song), 64)
    assert origin == "sidecar" and img.getpixel((5, 5)) == (0, 0, 255)


def test_corrupt_sidecar_falls_back_without_raising(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    song = tmp_path / "b.mp3"
    song.write_bytes(b"x")
    (tmp_path / "b.jpg").write_bytes(b"definitely not an image")
    img, origin = za.load_cover_image(str(song), 32)
    assert origin == "generated" and img is not None


def test_metadata_falls_back_to_filename_and_splits_artist(tmp_path):
    f = tmp_path / "Daft Punk - Digital Love.mp3"
    f.write_bytes(b"x")
    meta = za.read_metadata(str(f))
    assert meta["artist"] == "Daft Punk" and meta["title"] == "Digital Love"
    assert za.read_metadata("https://x.test/s", fallback_title="Live")["title"] == "Live"


def test_dominant_color_is_hex_and_picks_saturated():
    from PIL import Image

    img = Image.new("RGB", (30, 30), "#808080")
    for x in range(15):
        for y in range(30):
            img.putpixel((x, y), (230, 40, 40))
    color = za.dominant_color(img)
    assert color.startswith("#") and len(color) == 7
    r, g, b = (int(color[i:i + 2], 16) for i in (1, 3, 5))
    assert r > g and r > b


@pytest.mark.parametrize("bad", ["", None])
def test_resolve_cover_handles_empty_paths(bad):
    assert za.resolve_cover_bytes(bad or "") == (None, "none")


def test_playlist_relative_paths_become_clean_titles(tmp_path):
    f = tmp_path / "x.mp3"
    f.write_bytes(b"x")
    assert za.read_metadata(str(f), fallback_title="album/disc1/Bom Dia.mp3")["title"] == "Bom Dia"
    assert za.read_metadata(str(f), fallback_title="Meu Nome")["title"] == "Meu Nome"
