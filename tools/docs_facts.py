#!/usr/bin/env python3
"""Extrai do código os números que a documentação cita.

Uso:
    python tools/docs_facts.py            # imprime os fatos (texto)
    python tools/docs_facts.py --json     # imprime em JSON
    python tools/docs_facts.py --check    # falha (exit 1) se algum .md/.html citar número desatualizado

Só usa a stdlib e *não* importa ``src`` (não precisa de Tkinter): lê o código com ``ast``/regex.
Contagem de testes usa ``pytest --collect-only`` quando disponível; senão, contagem estática.
"""
from __future__ import annotations

import ast
import glob
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def _literal(rel: str, name: str):
    for node in ast.parse(_read(rel)).body:
        tgt = node.targets[0] if isinstance(node, ast.Assign) else getattr(node, "target", None)
        if getattr(tgt, "id", "") == name:
            return ast.literal_eval(node.value)
    raise KeyError(name)


def _flat(d: dict, p: str = "") -> dict:
    out: dict = {}
    for k, v in d.items():
        kk = f"{p}.{k}" if p else k
        out.update(_flat(v, kk) if isinstance(v, dict) else {kk: v})
    return out


def collect_tests() -> dict:
    res = {"method": "static", "total": 0, "files": 0, "per_file": {}}
    try:
        out = subprocess.run(
            [sys.executable, "-m", "pytest", "tests", "--collect-only", "-q", "-p", "no:cacheprovider"],
            cwd=ROOT, capture_output=True, text=True, timeout=300,
            env={**__import__("os").environ, "PYTHONDONTWRITEBYTECODE": "1"},
        ).stdout
        per = {m.group(1): int(m.group(2)) for m in re.finditer(r"^(tests/\S+\.py): (\d+)$", out, re.M)}
        if per:
            res.update(method="pytest", per_file=per)
    except (OSError, subprocess.SubprocessError):
        pass
    if not res["per_file"]:
        for f in sorted(glob.glob(str(ROOT / "tests/test_*.py"))):
            tree = ast.parse(Path(f).read_text(encoding="utf-8"))
            n = sum(isinstance(x, ast.FunctionDef) and x.name.startswith("test") for x in ast.walk(tree))
            res["per_file"]["tests/" + Path(f).name] = n
    res["total"] = sum(res["per_file"].values())
    res["files"] = len(res["per_file"])
    gui = 0
    for f in glob.glob(str(ROOT / "tests_gui/test_*.py")):
        gui += len(re.findall(r"^\s*def test_", Path(f).read_text(encoding="utf-8"), re.M))
    res["gui_tests"] = gui
    res["gui_files"] = len(glob.glob(str(ROOT / "tests_gui/test_*.py")))
    return res


def facts(with_tests: bool = True) -> dict:
    f: dict = {}
    const = _read("src/constants.py")
    f["version"] = re.search(r'APP_VERSION = "([^"]+)"', const).group(1)
    f["codename"] = re.search(r'APP_CODENAME = "([^"]+)"', const).group(1)
    base = _read("src/theme/base.py")
    th = re.findall(r'name="([a-z0-9_]+)",\s*\n\s*dark=(True|False)', base)
    f["themes"] = len(th)
    f["themes_dark"] = sum(d == "True" for _, d in th)
    f["themes_light"] = sum(d == "False" for _, d in th)
    f["theme_names"] = [n for n, _ in th]
    loc = {}
    for p in sorted(glob.glob(str(ROOT / "src/locales/*.json"))):
        loc[Path(p).stem] = len(_flat(json.loads(Path(p).read_text(encoding="utf-8"))))
    f["locales"] = len(loc)
    f["locale_keys"] = loc
    f["locale_keys_ref"] = loc["pt-BR"]
    f["locale_keys_min"] = min(loc.values())
    f["locale_keys_missing_max"] = f["locale_keys_ref"] - f["locale_keys_min"]
    pat = _read("src/syntax/patterns.py")
    blk = pat[pat.index("_LANG_TOKENS = {"):]
    f["highlight_languages"] = len(re.findall(r'^\s+"[a-z]+":\s*_[A-Z_]+_TOKENS', blk.split("\n}\n")[0], re.M))
    ext = _literal("src/constants.py", "EXT_LANG")
    f["extensions"] = len(ext)
    labels = _literal("src/constants.py", "LANG_LABEL")
    f["recognized_languages"] = len([k for k in labels if k != "text"])
    kd = _read("src/editor/kdocs.py")
    prof = kd[kd.index("profiles = ["):kd.index("mapping: dict")]
    f["kdocs_languages"] = len(re.findall(r"LanguageProfile\(\s*\"", prof))
    sa = _read("src/standalone.py")
    modes = re.findall(r'^    "(--[a-z0-9-]+)":', sa[sa.index("_ALL_MODES"):sa.index("def _print_help")], re.M)
    f["standalone_modes"] = len(modes)
    handled = set(re.findall(r'^    "(--[a-z0-9-]+)"\s*[:,]', sa[sa.index("_REGISTRY: dict"):], re.M))
    handled |= set(re.findall(r'"(--[a-z0-9-]+)"', sa[sa.index("_SPECIAL: dict"):sa.index("_SPECIAL: dict") + 900]))
    f["standalone_mode_names"] = modes
    desc_alias = set(re.findall(r"alias (--[a-z0-9-]+)", sa))
    f["standalone_aliases"] = len(desc_alias)
    sw = _read("src/ui/settings_window.py")
    f["settings_categories"] = len(re.findall(r'^\s+\("[a-z_]+", "[^"]*", "setting\.cat_', sw, re.M))
    f["skills"] = len(glob.glob(str(ROOT / "src/ia/skills_default/*.md")))
    prov = _read("src/ia/providers.py")
    f["ia_providers"] = len(re.findall(r"^class \w+Provider\(BaseProvider\)", prov, re.M))
    f["ia_tools"] = len(re.findall(r'^\s+"name":\s*"[a-z_]+"', _read("src/ia/toolbox.py"), re.M))
    f["chat_protocols"] = len(re.findall(r"import \w+Adapter as cls", _read("src/chat/adapters/__init__.py")))
    f["event_kinds"] = len(re.findall(r'^    [A-Z_]+ = "[a-z_.]+"', _read("src/events/kinds.py"), re.M))
    api = _read("src/plugins/api.py")
    cls = api[api.index("class PluginAPI"):]
    f["plugin_api_methods"] = len(set(re.findall(r"^    def ([a-z][a-zA-Z_]*)\(", cls, re.M)))
    core = re.search(r"CORE_EVENTS: frozenset\[str\] = frozenset\(\s*\{(.*?)\}", api, re.S).group(1)
    f["plugin_core_events"] = re.findall(r'"([a-z_]+)"', core)
    cfg = ast.parse(_read("src/constants.py"))
    for n in cfg.body:
        tg = n.targets[0] if isinstance(n, ast.Assign) else getattr(n, "target", None)
        if getattr(tg, "id", "") == "DEFAULT_CONFIG":
            f["default_config_keys"] = len(n.value.keys)
    f["default_shortcuts"] = len(re.findall(r'"default":\s*"', _read("src/ui/key_remapper.py")))
    f["workflows"] = len(glob.glob(str(ROOT / ".github/workflows/*.yml")))
    f["ui_panel_classes"] = len({m for p in glob.glob(str(ROOT / "src/**/*.py"), recursive=True)
                                 for m in re.findall(r"^class (\w+Panel)\b", Path(p).read_text(encoding="utf-8"), re.M)})
    if with_tests:
        f["tests"] = collect_tests()
    return f


def _fmt(n: int) -> str:
    return f"{n:,}".replace(",", ".")


# regra: (regex proibida nos docs, mensagem). Calculadas a partir dos fatos.
def rules(f: dict) -> list[tuple[str, str]]:
    r: list[tuple[str, str]] = [
        (r"3\.456", f"chaves i18n: o correto é {_fmt(f['locale_keys_ref'])} (pt-BR/en)"),
        (r"\b700\+ testes", "contagem de testes desatualizada"),
        (r"\b50\+ kinds", f"EventKind tem {f['event_kinds']} kinds"),
        (r"banner\.png", "o arquivo é .github/Banner.png (maiúsculo)"),
        (r"INTEGRATION\.py", "src/updater/INTEGRATION.py não existe"),
        (r"\bupdate_channel\b", "chave update_channel não existe no código"),
        (r"\b30\+ modos", f"são {f['standalone_modes']} modos"),
    ]
    return r


def check(f: dict) -> int:
    bad = 0
    files = [p for p in glob.glob(str(ROOT / "**/*"), recursive=True)
             if p.endswith((".md", ".html")) and "/src/" not in p and "/.git/" not in p
             and "/docs/" not in p.replace(str(ROOT), "") or p.endswith("docs/index.html")]
    files = [p for p in files if "tools/docs_facts.py" not in p and "RELATORIO" not in p and "CHANGELOG" not in p]
    for p in files:
        txt = Path(p).read_text(encoding="utf-8")
        for rx, msg in rules(f):
            for m in re.finditer(rx, txt):
                line = txt.count("\n", 0, m.start()) + 1
                print(f"{Path(p).relative_to(ROOT)}:{line}: {msg}")
                bad += 1
    exp = {
        "temas": f["themes"], "idiomas": f["locales"], "skills": f["skills"],
        "provedores": f["ia_providers"], "modos": f["standalone_modes"],
        "ferramentas": f["ia_tools"],
    }
    for p in files:
        txt = Path(p).read_text(encoding="utf-8")
        for word, val in exp.items():
            for m in re.finditer(rf"\b(\d+)\s+{word}\b", txt):
                n = int(m.group(1))
                if word in ("modos", "ferramentas", "idiomas") and n != val and n in (28, 29, 30, 40, 14, 13, 12):
                    if word == "idiomas" and n == 13:
                        continue  # "os outros 13 idiomas"
                    print(f"{Path(p).relative_to(ROOT)}: '{m.group(0)}' != {val}")
                    bad += 1
                elif word in ("temas", "skills", "provedores") and n != val and n in (20, 21, 22, 14, 15, 16, 5, 6, 7):
                    print(f"{Path(p).relative_to(ROOT)}: '{m.group(0)}' != {val}")
                    bad += 1
    print(f"{bad} problema(s) de consistência encontrados." if bad else "Docs consistentes com o código.")
    return 1 if bad else 0


def main() -> int:
    args = sys.argv[1:]
    f = facts(with_tests="--no-tests" not in args)
    if "--check" in args:
        return check(f)
    if "--json" in args:
        print(json.dumps(f, ensure_ascii=False, indent=2))
    else:
        for k, v in f.items():
            if isinstance(v, (list, dict)) and k != "tests":
                v = f"{len(v)} itens"
            if k == "tests":
                v = {a: b for a, b in v.items() if a != "per_file"}
            print(f"{k:26} {v}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
