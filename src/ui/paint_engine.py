"""Motor do Paint (sem Tk): tudo aqui é testável sem interface.

Princípios de desempenho:

* **Custo proporcional ao que mudou**, nunca à imagem inteira: traços, formas e
  preenchimentos trabalham numa caixa (bbox) e o histórico guarda só os blocos
  (tiles) alterados.
* **Pincel por máscara**: o traço é acumulado numa máscara 'L' (máximo entre
  carimbos) e composto uma vez sobre o conteúdo *original* da área - antialiasing
  real, opacidade que não acumula dentro do mesmo traço e sem camadas do
  tamanho da imagem por pixel interpolado.
* **Balde por spans (scanline)**: o laço Python roda por trecho horizontal, não
  por pixel; a comparação de cor com tolerância é feita em C pelo Pillow.
* **Renderização por retângulo**: só o pedaço visível/sujo é reescalado
  (``resize(box=...)``); o xadrez vem de um ladrilho em cache e a grade de
  pixels é desenhada no próprio bitmap - sem milhares de itens de canvas.
"""

from __future__ import annotations

import math
import random
from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass, field

from PIL import Image, ImageChops, ImageDraw

Box = tuple[int, int, int, int]

TILE = 256
SUPERSAMPLE = 4
DEFAULT_HISTORY_BYTES = 384 * 1024 * 1024
MAX_HISTORY_ENTRIES = 200
GRID_MIN_ZOOM = 8.0
_BOX = getattr(Image, "BOX", 4)
_NEAREST = getattr(Image, "NEAREST", 0)
_BILINEAR = getattr(Image, "BILINEAR", 2)


def hex_to_rgb(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def rgb_to_hex(r: int, g: int, b: int) -> str:
    return f"#{r:02x}{g:02x}{b:02x}"


def clip_box(box: Box, w: int, h: int) -> Box | None:
    x0, y0, x1, y1 = max(0, box[0]), max(0, box[1]), min(w, box[2]), min(h, box[3])
    return (x0, y0, x1, y1) if x1 > x0 and y1 > y0 else None


def union_box(a: Box | None, b: Box | None) -> Box | None:
    if a is None:
        return b
    if b is None:
        return a
    return (min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3]))


def changed_bbox(a: Image.Image, b: Image.Image) -> Box | None:
    """Caixa dos pixels que diferem em QUALQUER canal (``getbbox`` de RGBA só olha o alfa)."""
    diff = ImageChops.difference(a, b)
    bands = diff.split()
    m = bands[0]
    for band in bands[1:]:
        m = ImageChops.lighter(m, band)
    return m.getbbox()


def _tile_boxes(box: Box, w: int, h: int):
    x0, y0, x1, y1 = box
    for ty in range(y0 // TILE, (y1 - 1) // TILE + 1):
        for tx in range(x0 // TILE, (x1 - 1) // TILE + 1):
            yield (tx, ty), (tx * TILE, ty * TILE, min(w, (tx + 1) * TILE), min(h, (ty + 1) * TILE))


@dataclass
class _Entry:
    """Uma ação desfazível: blocos antes/depois, ou imagens inteiras (mudança de tamanho)."""

    tiles: dict[tuple[int, int], list] = field(default_factory=dict)
    full: tuple[Image.Image, Image.Image] | None = None
    bbox: Box | None = None

    @property
    def nbytes(self) -> int:
        if self.full is not None:
            return (self.full[0].width * self.full[0].height + self.full[1].width * self.full[1].height) * 4
        return sum((b.width * b.height + (a.width * a.height if a is not None else 0)) * 4
                   for b, a in self.tiles.values())


class Document:
    """Imagem RGBA + histórico por diferença + acúmulo de região suja."""

    def __init__(self, image: Image.Image, history_bytes: int = DEFAULT_HISTORY_BYTES) -> None:
        self.image = image if image.mode == "RGBA" else image.convert("RGBA")
        self.max_history_bytes = history_bytes
        self.undo_stack: deque[_Entry] = deque()
        self.redo_stack: list[_Entry] = []
        self._journal: dict[tuple[int, int], Image.Image] | None = None
        self.dirty: Box | None = None
        self.dirty_all = False
        self.revision = 0
        self.saved_revision = 0
        self.maybe_transparent = self._has_alpha()

    @property
    def size(self) -> tuple[int, int]:
        return self.image.size

    @property
    def modified(self) -> bool:
        return self.revision != self.saved_revision

    def mark_saved(self) -> None:
        self.saved_revision = self.revision

    def _has_alpha(self) -> bool:
        if "A" not in self.image.getbands():
            return False
        return self.image.getchannel("A").getextrema()[0] < 255

    @property
    def history_bytes(self) -> int:
        return sum(e.nbytes for e in self.undo_stack) + sum(e.nbytes for e in self.redo_stack)

    def mark(self, box: Box | None) -> None:
        if box is not None:
            self.dirty = union_box(self.dirty, box)

    def take_dirty(self) -> tuple[Box | None, bool]:
        d, a = self.dirty, self.dirty_all
        self.dirty, self.dirty_all = None, False
        return d, a

    def begin(self) -> None:
        """Abre uma operação; ``touch`` registra o que será alterado."""
        if self._journal is not None:
            self.commit()
        self._journal = {}

    def touch(self, box: Box) -> Box | None:
        """Guarda o conteúdo original dos blocos que ``box`` cobre (uma vez por bloco)."""
        w, h = self.image.size
        box = clip_box(box, w, h)
        if box is None:
            return None
        if self._journal is None:
            self._journal = {}
        for key, tb in _tile_boxes(box, w, h):
            if key not in self._journal:
                self._journal[key] = self.image.crop(tb)
        return box

    def original_crop(self, box: Box) -> Image.Image:
        """Pixels de ``box`` como eram quando a operação começou."""
        region = self.image.crop(box)
        if self._journal:
            for (tx, ty), before in self._journal.items():
                tb = (tx * TILE, ty * TILE, tx * TILE + before.width, ty * TILE + before.height)
                ix0, iy0 = max(tb[0], box[0]), max(tb[1], box[1])
                ix1, iy1 = min(tb[2], box[2]), min(tb[3], box[3])
                if ix1 > ix0 and iy1 > iy0:
                    piece = before.crop((ix0 - tb[0], iy0 - tb[1], ix1 - tb[0], iy1 - tb[1]))
                    region.paste(piece, (ix0 - box[0], iy0 - box[1]))
        return region

    def commit(self) -> bool:
        """Fecha a operação e a registra no histórico (só blocos que mudaram)."""
        j, self._journal = self._journal, None
        if not j:
            return False
        entry = _Entry()
        w, h = self.image.size
        for (tx, ty), before in j.items():
            tb = (tx * TILE, ty * TILE, min(w, (tx + 1) * TILE), min(h, (ty + 1) * TILE))
            if changed_bbox(before, self.image.crop(tb)) is None:
                continue
            entry.tiles[(tx, ty)] = [before, None]
            entry.bbox = union_box(entry.bbox, tb)
        if not entry.tiles:
            return False
        self._push(entry)
        return True

    def rollback(self) -> None:
        """Descarta a operação aberta restaurando o conteúdo original."""
        j, self._journal = self._journal, None
        if not j:
            return
        for (tx, ty), before in j.items():
            self.image.paste(before, (tx * TILE, ty * TILE))
            self.mark((tx * TILE, ty * TILE, tx * TILE + before.width, ty * TILE + before.height))

    def _push(self, entry: _Entry) -> None:
        self.undo_stack.append(entry)
        self.redo_stack.clear()
        self.revision += 1
        total = self.history_bytes
        while self.undo_stack and (total > self.max_history_bytes or len(self.undo_stack) > MAX_HISTORY_ENTRIES):
            total -= self.undo_stack.popleft().nbytes

    def replace_image(self, new: Image.Image, record: bool = True) -> None:
        """Troca a imagem inteira (abrir, redimensionar). Desfazível se ``record``."""
        if self._journal is not None:
            self.commit()
        new = new if new.mode == "RGBA" else new.convert("RGBA")
        old = self.image
        self.image = new
        self.dirty_all = True
        self.maybe_transparent = self._has_alpha()
        if record:
            self._push(_Entry(full=(old, new)))
        else:
            self.undo_stack.clear()
            self.redo_stack.clear()
            self.revision = self.saved_revision = 0

    def undo(self) -> bool:
        if self._journal:
            self.commit()
        if not self.undo_stack:
            return False
        e = self.undo_stack.pop()
        self._apply(e, undo=True)
        self.redo_stack.append(e)
        self.revision -= 1
        return True

    def redo(self) -> bool:
        if not self.redo_stack:
            return False
        e = self.redo_stack.pop()
        self._apply(e, undo=False)
        self.undo_stack.append(e)
        self.revision += 1
        return True

    def _apply(self, e: _Entry, undo: bool) -> None:
        if e.full is not None:
            self.image = e.full[0] if undo else e.full[1]
            self.dirty_all = True
            self.maybe_transparent = self.maybe_transparent or self._has_alpha()
            return
        for (tx, ty), pair in e.tiles.items():
            before, after = pair
            if undo:
                pair[1] = self.image.crop((tx * TILE, ty * TILE, tx * TILE + before.width, ty * TILE + before.height))
            img = before if undo else after
            self.image.paste(img, (tx * TILE, ty * TILE))
            self.mark((tx * TILE, ty * TILE, tx * TILE + img.width, ty * TILE + img.height))


def _opacity_lut(opacity: int) -> list[int]:
    return [v * opacity // 255 for v in range(256)]


def _is_binary(patch: Image.Image) -> bool:
    """True se a máscara só tem 0 e 255 (checagem barata pelo histograma)."""
    hist = patch.histogram()
    return hist[0] + hist[255] == patch.width * patch.height


def composite_patch(doc: Document, patch: Image.Image, box: Box, rgb: tuple[int, int, int], opacity: int = 255) -> None:
    """Compõe ``rgb`` através da máscara ``patch`` (tamanho de ``box``) sobre o conteúdo ORIGINAL de ``box``."""
    if opacity >= 255 and _is_binary(patch):
        doc.image.paste((*rgb, 255), (box[0], box[1], box[0] + patch.width, box[1] + patch.height), patch)
        doc.mark(box)
        return
    if opacity < 255:
        patch = patch.point(_opacity_lut(opacity))
    base = doc.original_crop(box)
    layer = Image.new("RGBA", patch.size, (*rgb, 0))
    layer.putalpha(patch)
    doc.image.paste(Image.alpha_composite(base, layer), (box[0], box[1]))
    doc.mark(box)


def new_stroke_mask(doc: Document) -> Image.Image:
    return Image.new("L", doc.image.size, 0)


def stamp_segment(mask: Image.Image, x0: float, y0: float, x1: float, y1: float, diameter: float,
                  aa: bool = True) -> Box | None:
    """Acumula na máscara um segmento com pontas redondas; devolve a caixa alterada."""
    w, h = mask.size
    pad = int(math.ceil(diameter / 2)) + 2
    box = clip_box((int(min(x0, x1)) - pad, int(min(y0, y1)) - pad, int(max(x0, x1)) + pad + 1,
                    int(max(y0, y1)) + pad + 1), w, h)
    if box is None:
        return None
    ss = SUPERSAMPLE if aa else 1
    pw, ph = box[2] - box[0], box[3] - box[1]
    patch = Image.new("L", (pw * ss, ph * ss), 0)
    d = ImageDraw.Draw(patch)
    p0 = ((x0 - box[0] + 0.5) * ss, (y0 - box[1] + 0.5) * ss)
    p1 = ((x1 - box[0] + 0.5) * ss, (y1 - box[1] + 0.5) * ss)
    rr = max(0.5, diameter * ss / 2)
    if (p0 != p1):
        d.line([p0, p1], fill=255, width=max(1, int(round(diameter * ss))))
    for p in (p0, p1):
        d.ellipse([p[0] - rr, p[1] - rr, p[0] + rr - (0 if aa else 1), p[1] + rr - (0 if aa else 1)], fill=255)
    if aa:
        patch = patch.resize((pw, ph), _BOX)
    mask.paste(ImageChops.lighter(mask.crop(box), patch), (box[0], box[1]))
    return box


def apply_stroke_region(doc: Document, mask: Image.Image, box: Box, rgb: tuple[int, int, int], opacity: int) -> None:
    """Compõe a região da máscara atualizada sobre o original (chame após ``stamp_segment``)."""
    doc.touch(box)
    composite_patch(doc, mask.crop(box), box, rgb, opacity)


def spray(doc: Document, x0: int, y0: int, x1: int, y1: int, rgb: tuple[int, int, int], opacity: int, size: int,
          density: int, rng: random.Random | None = None) -> Box | None:
    """Aerógrafo: pontos aleatórios com alpha decrescente do centro para a borda (acumulativo)."""
    rng = rng or random
    r = max(2, size * 2)
    w, h = doc.image.size
    box = clip_box((min(x0, x1) - r - 1, min(y0, y1) - r - 1, max(x0, x1) + r + 2, max(y0, y1) + r + 2), w, h)
    if box is None:
        return None
    doc.touch(box)
    overlay = Image.new("RGBA", (box[2] - box[0], box[3] - box[1]), (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    dist = max(1.0, math.hypot(x1 - x0, y1 - y0))
    steps = max(1, int(dist // 2) + 1)
    buckets: list[list[tuple[int, int]]] = [[], [], [], []]
    for i in range(steps):
        t = i / steps
        cx, cy = x0 + t * (x1 - x0), y0 + t * (y1 - y0)
        for _ in range(density):
            ang = rng.uniform(0, math.tau)
            rad = r * math.sqrt(rng.random())
            px, py = int(cx + rad * math.cos(ang)) - box[0], int(cy + rad * math.sin(ang)) - box[1]
            buckets[min(3, int(rad / r * 4))].append((px, py))
    for level, pts in enumerate(buckets):
        if pts:
            a = int(opacity * (1 - (level + 0.5) / 4) * 0.6)
            od.point(pts, fill=(*rgb, max(1, a)))
    doc.image.paste(Image.alpha_composite(doc.image.crop(box), overlay), (box[0], box[1]))
    doc.mark(box)
    return box


def draw_shape(doc: Document, kind: str, pts: Sequence[tuple[int, int]], rgb: tuple[int, int, int], opacity: int,
               width: int, fill: bool, aa: bool = True) -> Box | None:
    """Linha / retângulo / elipse / polígono com antialiasing por supersampling."""
    if not pts:
        return None
    w, h = doc.image.size
    pad = width + 3
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    box = clip_box((min(xs) - pad, min(ys) - pad, max(xs) + pad + 1, max(ys) + pad + 1), w, h)
    if box is None:
        return None
    doc.touch(box)
    ss = SUPERSAMPLE if aa else 1
    pw, ph = box[2] - box[0], box[3] - box[1]
    patch = Image.new("L", (pw * ss, ph * ss), 0)
    d = ImageDraw.Draw(patch)
    sp = [((x - box[0] + 0.5) * ss, (y - box[1] + 0.5) * ss) for x, y in pts]
    lw = max(1, int(round(width * ss)))
    if kind == "line" and len(sp) >= 2:
        d.line(sp[:2], fill=255, width=lw)
        rr = lw / 2
        for p in sp[:2]:
            d.ellipse([p[0] - rr, p[1] - rr, p[0] + rr, p[1] + rr], fill=255)
    elif kind in ("rect", "ellipse") and len(sp) >= 2:
        x0, x1 = sorted((sp[0][0], sp[1][0]))
        y0, y1 = sorted((sp[0][1], sp[1][1]))
        fn = d.rectangle if kind == "rect" else d.ellipse
        fn([x0, y0, x1, y1], outline=255, fill=255 if fill else None, width=lw)
    elif kind == "polygon" and len(sp) >= 3:
        if fill:
            d.polygon(sp, fill=255)
        d.line([*sp, sp[0]], fill=255, width=lw, joint="curve")
    else:
        return None
    if aa:
        patch = patch.resize((pw, ph), _BOX)
    composite_patch(doc, patch, box, rgb, opacity)
    return box


def region_mask(img: Image.Image, x: int, y: int, tol: int, contiguous: bool = True) -> tuple[Image.Image, Box] | None:
    """Máscara 'L' (255 = preencher) da região de cor parecida com a do pixel (x, y).

    Distância = soma das diferenças RGBA <= ``tol`` (Pillow, em C); a conectividade
    (4 vizinhos) é resolvida por spans horizontais em Python.
    """
    w, h = img.size
    if not (0 <= x < w and 0 <= y < h):
        return None
    seed = img.getpixel((x, y))
    total = None
    for band, sv in zip(img.split(), seed, strict=False):
        d = band.point([abs(v - sv) for v in range(256)])
        total = d if total is None else ImageChops.add(total, d)
    tol = max(0, min(255, int(tol)))
    near = total.point([255 if v <= tol else 0 for v in range(256)])
    if not contiguous:
        bb = near.getbbox()
        return (near, bb) if bb else None
    data = near.tobytes()
    rows = [bytearray(data[i * w:(i + 1) * w]) for i in range(h)]
    out = bytearray(w * h)
    stack = [(x, y)]
    minx, miny, maxx, maxy = w, h, -1, -1
    zero = b"\x00"
    while stack:
        sx, sy = stack.pop()
        row = rows[sy]
        if not row[sx]:
            continue
        left = row.rfind(zero, 0, sx) + 1
        right = row.find(zero, sx)
        if right < 0:
            right = w
        row[left:right] = bytes(right - left)
        base = sy * w
        out[base + left:base + right] = b"\xff" * (right - left)
        minx, maxx = min(minx, left), max(maxx, right)
        miny, maxy = min(miny, sy), max(maxy, sy + 1)
        for ny in (sy - 1, sy + 1):
            if 0 <= ny < h:
                nrow = rows[ny]
                pos = left
                while True:
                    pos = nrow.find(b"\xff", pos, right)
                    if pos < 0:
                        break
                    stack.append((pos, ny))
                    end = nrow.find(zero, pos, right)
                    if end < 0:
                        break
                    pos = end
    if maxx < 0:
        return None
    return Image.frombytes("L", (w, h), bytes(out)), (minx, miny, maxx, maxy)


def flood_fill(doc: Document, x: int, y: int, rgb: tuple[int, int, int], opacity: int, tol: int,
               contiguous: bool = True) -> Box | None:
    res = region_mask(doc.image, x, y, tol, contiguous)
    if res is None:
        return None
    mask, box = res
    box = doc.touch(box)
    if box is None:
        return None
    composite_patch(doc, mask.crop(box), box, rgb, opacity)
    return box


_checker_cache: dict[tuple[int, int, int], Image.Image] = {}


def _checker(sq: int, w: int, h: int, ox: int, oy: int) -> Image.Image:
    """Xadrez ancorado na origem da imagem (``ox``, ``oy``) para o retângulo w×h."""
    period = sq * 2
    key = (sq, w, h)
    big = _checker_cache.get(key)
    if big is None:
        if len(_checker_cache) > 8:
            _checker_cache.clear()
        tile = Image.new("RGB", (period, period), (153, 153, 153))
        td = ImageDraw.Draw(tile)
        td.rectangle([0, 0, sq - 1, sq - 1], fill=(192, 192, 192))
        td.rectangle([sq, sq, period - 1, period - 1], fill=(192, 192, 192))
        big = Image.new("RGB", (w + period, h + period))
        for yy in range(0, h + period, period):
            for xx in range(0, w + period, period):
                big.paste(tile, (xx, yy))
        _checker_cache[key] = big
    sx, sy = ox % period, oy % period
    return big.crop((sx, sy, sx + w, sy + h))


def render_rect(img: Image.Image, zoom: float, pan: tuple[int, int], rect: Box, bg: tuple[int, int, int],
                transparent: bool, grid: bool = True) -> Image.Image:
    """Renderiza o retângulo de TELA ``rect`` (x0, y0, x1, y1) como RGB.

    O mapeamento é contínuo (pixel k da imagem ocupa [pan + k*zoom, pan + (k+1)*zoom)),
    portanto renderizar em pedaços produz exatamente o mesmo que renderizar tudo.
    """
    sx0, sy0, sx1, sy1 = rect
    out = Image.new("RGB", (max(1, sx1 - sx0), max(1, sy1 - sy0)), bg)
    iw, ih = img.size
    px, py = pan
    ix0, iy0 = max(sx0, math.ceil(px)), max(sy0, math.ceil(py))
    ix1, iy1 = min(sx1, math.floor(px + iw * zoom)), min(sy1, math.floor(py + ih * zoom))
    if ix1 <= ix0 or iy1 <= iy0:
        return out
    eps = 1e-4
    box = ((ix0 - px) / zoom + eps, (iy0 - py) / zoom + eps,
           min(iw, (ix1 - px) / zoom + eps), min(ih, (iy1 - py) / zoom + eps))
    size = (ix1 - ix0, iy1 - iy0)
    if zoom >= 1.0:
        part = img.resize(size, _NEAREST, box=box)
    else:
        part = img.resize(size, _BILINEAR, box=box, reducing_gap=2.0)
    dest = (ix0 - sx0, iy0 - sy0)
    if transparent:
        sq = max(4, min(16, int(8 * min(zoom, 2.0))))
        back = _checker(sq, size[0], size[1], ix0 - px, iy0 - py)
        back.paste(part, (0, 0), part)
        out.paste(back, dest)
    else:
        out.paste(part.convert("RGB"), dest)
    if grid and zoom >= GRID_MIN_ZOOM:
        d = ImageDraw.Draw(out)
        gx0, gx1 = math.ceil((ix0 - px) / zoom), math.floor((ix1 - px) / zoom)
        gy0, gy1 = math.ceil((iy0 - py) / zoom), math.floor((iy1 - py) / zoom)
        for gx in range(gx0, gx1 + 1):
            x = int(round(px + gx * zoom)) - sx0
            d.line([(x, dest[1]), (x, dest[1] + size[1] - 1)], fill=(96, 96, 96))
        for gy in range(gy0, gy1 + 1):
            y = int(round(py + gy * zoom)) - sy0
            d.line([(dest[0], y), (dest[0] + size[0] - 1, y)], fill=(96, 96, 96))
    return out


def image_box_to_screen(box: Box, zoom: float, pan: tuple[int, int]) -> Box:
    """Caixa em pixels da imagem -> retângulo de tela (arredondado para fora)."""
    return (math.floor(pan[0] + box[0] * zoom), math.floor(pan[1] + box[1] * zoom),
            math.ceil(pan[0] + box[2] * zoom), math.ceil(pan[1] + box[3] * zoom))


def flatten_for_save(img: Image.Image, fmt: str) -> Image.Image:
    """JPEG/BMP não têm alpha: achata sobre branco (antes ficava preto onde havia transparência)."""
    fmt = fmt.upper()
    if fmt in ("JPEG", "JPG", "BMP"):
        bg = Image.new("RGB", img.size, (255, 255, 255))
        bg.paste(img, (0, 0), img)
        return bg
    if fmt == "GIF":
        return img.convert("P", palette=getattr(Image, "ADAPTIVE", 1))
    return img
