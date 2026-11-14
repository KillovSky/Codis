#!/usr/bin/env python3
"""Lista quais imagens de wiki/previews.md ainda não existem em docs/img/previews/.

Uso:
    python tools/check_previews.py            # informa as que faltam (sempre sai com 0)
    python tools/check_previews.py --strict   # sai com 1 se faltar alguma (para CI)
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PAGE = ROOT / "wiki" / "previews.md"
PREFIX = "https://raw.githubusercontent.com/KillovSky/Codis/main/"


def main() -> int:
    refs = re.findall(r"!\[[^\]]*\]\((" + re.escape(PREFIX) + r"[^)\s]+)\)", PAGE.read_text(encoding="utf-8"))
    missing = [r[len(PREFIX):] for r in refs if not (ROOT / r[len(PREFIX):]).is_file()]
    print(f"{len(refs) - len(missing)}/{len(refs)} imagens presentes.")
    for m in missing:
        print(f"  falta: {m}")
    return 1 if missing and "--strict" in sys.argv else 0


if __name__ == "__main__":
    raise SystemExit(main())
