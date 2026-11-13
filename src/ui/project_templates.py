"""Codis - Project Templates.

Cria novos projetos a partir de templates pré-definidos ou customizados.
Templates são pastas com arquivos e um `template.json` de metadados.

Features
--------
- 10+ templates embutidos (Python, FastAPI, Flask, CLI, JS, TS, React, etc.)
- Templates customizados: salve qualquer pasta como template
- Variáveis de substituição: {{project_name}}, {{author}}, {{year}}, ...
- Preview da estrutura antes de criar
- Suporte a comandos pós-criação (ex: git init, pip install)
- Atalho: Ferramentas -> 📋 Novo Projeto a partir de Template
"""

from __future__ import annotations

import contextlib
import json
import re
import shutil
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox
from typing import Any

from src.constants import CONFIG_DIR, FONT_MONO, FONT_UI
from src.i18n import _t
from src.ui.grab_safe import safe_grab_set

_TEMPLATES_DIR = CONFIG_DIR / "templates"

_BUILTIN_TEMPLATES: list[dict[str, Any]] = [
    {
        "id": "python_basic",
        "label": _t("tmpl.label.python_basic"),
        "description": _t("tmpl.desc.python_basic"),
        "category": "Python",
        "files": {
            "{{project_name}}.py": (
                '"""{{project_name}} - {{description}}\n\nAutor: {{author}}\nData:  {{year}}\n"""\n'
                "from __future__ import annotations\n\nimport argparse\nimport logging\nimport sys\n\n"
                'logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")\n'
                "logger = logging.getLogger(__name__)\n\n\ndef parse_args() -> argparse.Namespace:\n"
                '    p = argparse.ArgumentParser(description="{{description}}")\n'
                '    p.add_argument("--verbose", "-v", action="store_true")\n'
                "    return p.parse_args()\n\n\ndef main(args: argparse.Namespace) -> int:\n"
                "    if args.verbose:\n        logging.getLogger().setLevel(logging.DEBUG)\n"
                '    logger.info("{{project_name}} iniciado")\n    return 0\n\n\n'
                'if __name__ == "__main__":\n    sys.exit(main(parse_args()))\n'
            ),
            "README.md": "# {{project_name}}\n\n{{description}}\n\n## Uso\n\n```bash\npython {{project_name}}.py\n```\n",
            "requirements.txt": "# Dependências\n",
            ".gitignore": "__pycache__/\n*.py[cod]\n*.egg-info/\ndist/\nbuild/\n.venv/\n",
        },
        "post_commands": [],
    },
    {
        "id": "python_package",
        "label": _t("tmpl.label.python_package"),
        "description": _t("tmpl.desc.python_package"),
        "category": "Python",
        "files": {
            "pyproject.toml": (
                '[build-system]\nrequires = ["setuptools>=68"]\nbuild-backend = "setuptools.backends.legacy:build"\n\n'
                '[project]\nname = "{{project_name}}"\nversion = "0.1.0"\ndescription = "{{description}}"\n'
                'authors = [{name = "{{author}}"}]\nrequires-python = ">=3.11"\n'
            ),
            "src/{{project_name}}/__init__.py": '"""{{project_name}} - {{description}}"""\n\n__version__ = "0.1.0"\n',
            "src/{{project_name}}/main.py": 'def main() -> None:\n    print("Hello from {{project_name}}")\n\n\nif __name__ == "__main__":\n    main()\n',
            "tests/__init__.py": "",
            "tests/test_main.py": 'from {{project_name}}.main import main\n\n\ndef test_main(capsys):\n    main()\n    out, _ = capsys.readouterr()\n    assert "{{project_name}}" in out\n',
            "README.md": "# {{project_name}}\n\n{{description}}\n\n## Instalação\n\n```bash\npip install -e .\n```\n",
            ".gitignore": "__pycache__/\n*.py[cod]\ndist/\nbuild/\n.venv/\n*.egg-info/\n",
        },
        "post_commands": [],
    },
    {
        "id": "fastapi",
        "label": _t("tmpl.label.fastapi"),
        "description": _t("tmpl.desc.fastapi"),
        "category": "Python",
        "files": {
            "main.py": (
                "from fastapi import FastAPI\nfrom app.routers import items\n\n"
                'app = FastAPI(title="{{project_name}}", version="0.1.0")\n\n'
                'app.include_router(items.router)\n\n\n@app.get("/")\nasync def root():\n    return {"message": "{{project_name}}"}\n'
            ),
            "app/__init__.py": "",
            "app/routers/__init__.py": "",
            "app/routers/items.py": (
                "from fastapi import APIRouter\nfrom pydantic import BaseModel\n\n"
                'router = APIRouter(prefix="/items", tags=["items"])\n\n\n'
                "class Item(BaseModel):\n    name: str\n    price: float\n\n\n"
                '@router.get("/")\nasync def list_items():\n    return []\n\n\n'
                '@router.post("/")\nasync def create_item(item: Item):\n    return item\n'
            ),
            "requirements.txt": "fastapi>=0.111\nuvicorn[standard]>=0.29\npydantic>=2.7\n",
            "README.md": "# {{project_name}}\n\n{{description}}\n\n## Executar\n\n```bash\nuvicorn main:app --reload\n```\n",
            ".gitignore": "__pycache__/\n*.py[cod]\n.venv/\n.env\n",
        },
        "post_commands": [],
    },
    {
        "id": "flask",
        "label": _t("tmpl.label.flask"),
        "description": _t("tmpl.desc.flask"),
        "category": "Python",
        "files": {
            "app/__init__.py": (
                "from flask import Flask\nfrom .views import main\n\n\n"
                "def create_app():\n    app = Flask(__name__)\n"
                "    app.register_blueprint(main)\n    return app\n"
            ),
            "app/views.py": (
                'from flask import Blueprint, render_template\n\nmain = Blueprint("main", __name__)\n\n\n'
                '@main.route("/")\ndef index():\n    return render_template("index.html", title="{{project_name}}")\n'
            ),
            "app/templates/index.html": (
                "<!DOCTYPE html>\n<html>\n<head><title>{{ title }}</title></head>\n"
                "<body><h1>{{ title }}</h1></body>\n</html>\n"
            ),
            "run.py": 'from app import create_app\n\napp = create_app()\n\nif __name__ == "__main__":\n    app.run(debug=True)\n',
            "requirements.txt": "flask>=3.0\n",
            "README.md": "# {{project_name}}\n\n{{description}}\n\n## Executar\n\n```bash\npython run.py\n```\n",
            ".gitignore": "__pycache__/\n*.py[cod]\n.venv/\n.env\ninstance/\n",
        },
        "post_commands": [],
    },
    {
        "id": "cli_tool",
        "label": _t("tmpl.label.cli_tool"),
        "description": _t("tmpl.desc.cli_tool"),
        "category": "Python",
        "files": {
            "{{project_name}}.py": (
                '"""{{project_name}} - {{description}}"""\nimport typer\nfrom rich.console import Console\nfrom rich import print\n\n'
                'app = typer.Typer(help="{{description}}")\nconsole = Console()\n\n\n'
                '@app.command()\ndef main(\n    name: str = typer.Argument(help="Nome"),\n'
                '    verbose: bool = typer.Option(False, "--verbose", "-v"),\n) -> None:\n'
                '    """Executa o comando principal."""\n    console.print(f"[bold green]{{project_name}}[/] - olá, {name}!")\n\n\n'
                'if __name__ == "__main__":\n    app()\n'
            ),
            "requirements.txt": "typer>=0.12\nrich>=13\n",
            "README.md": "# {{project_name}}\n\n{{description}}\n\n## Uso\n\n```bash\npython {{project_name}}.py --help\n```\n",
            ".gitignore": "__pycache__/\n*.py[cod]\n.venv/\n",
        },
        "post_commands": [],
    },
    {
        "id": "node_basic",
        "label": _t("tmpl.label.node_basic"),
        "description": _t("tmpl.desc.node_basic"),
        "category": "JavaScript",
        "files": {
            "package.json": (
                '{{\n  "name": "{{project_name_kebab}}",\n  "version": "0.1.0",\n'
                '  "description": "{{description}}",\n  "type": "module",\n'
                '  "main": "src/index.js",\n  "scripts": {{\n'
                '    "start": "node src/index.js",\n    "dev": "node --watch src/index.js"\n'
                "  }}\n}}\n"
            ),
            "src/index.js": '// {{project_name}} - {{description}}\n\nconsole.log("Hello from {{project_name}}");\n',
            "README.md": "# {{project_name}}\n\n{{description}}\n\n## Uso\n\n```bash\nnpm start\n```\n",
            ".gitignore": "node_modules/\ndist/\n.env\n",
        },
        "post_commands": [],
    },
    {
        "id": "typescript_basic",
        "label": _t("tmpl.label.typescript_basic"),
        "description": _t("tmpl.desc.typescript_basic"),
        "category": "TypeScript",
        "files": {
            "package.json": (
                '{{\n  "name": "{{project_name_kebab}}",\n  "version": "0.1.0",\n'
                '  "scripts": {{\n    "build": "tsc",\n    "start": "ts-node src/index.ts",\n'
                '    "dev": "ts-node-dev src/index.ts"\n  }},\n'
                '  "devDependencies": {{\n    "typescript": "^5",\n    "ts-node": "^10",\n    "@types/node": "^20"\n  }}\n}}\n'
            ),
            "tsconfig.json": (
                '{{\n  "compilerOptions": {{\n    "target": "ES2022",\n    "module": "commonjs",\n'
                '    "strict": true,\n    "outDir": "./dist",\n    "rootDir": "./src"\n  }},\n'
                '  "include": ["src"]\n}}\n'
            ),
            "src/index.ts": '// {{project_name}} - {{description}}\n\nconst greeting: string = "Hello from {{project_name}}";\nconsole.log(greeting);\n',
            "README.md": "# {{project_name}}\n\n{{description}}\n\n## Uso\n\n```bash\nnpm install\nnpm run dev\n```\n",
            ".gitignore": "node_modules/\ndist/\n.env\n",
        },
        "post_commands": [],
    },
    {
        "id": "data_science",
        "label": _t("tmpl.label.data_science"),
        "description": _t("tmpl.desc.data_science"),
        "category": "Python",
        "files": {
            "notebooks/.gitkeep": "",
            "data/raw/.gitkeep": "",
            "data/processed/.gitkeep": "",
            "src/__init__.py": "",
            "src/data.py": "import pandas as pd\nfrom pathlib import Path\n\nDATA_DIR = Path(__file__).parent.parent / 'data'\n\n\ndef load_raw(name: str) -> pd.DataFrame:\n    return pd.read_csv(DATA_DIR / 'raw' / name)\n",
            "requirements.txt": "pandas>=2\nnumpy>=1.26\nmatplotlib>=3.8\nseaborn>=0.13\njupyterlab>=4\nscikit-learn>=1.4\n",
            "README.md": "# {{project_name}}\n\n{{description}}\n\n## Setup\n\n```bash\npip install -r requirements.txt\njupyterlab\n```\n",
            ".gitignore": "__pycache__/\n*.py[cod]\n.venv/\ndata/raw/*\n!data/raw/.gitkeep\n*.ipynb_checkpoints/\n",
        },
        "post_commands": [],
    },
    {
        "id": "mkdocs",
        "label": _t("tmpl.label.mkdocs"),
        "description": _t("tmpl.desc.mkdocs"),
        "category": "Docs",
        "files": {
            "mkdocs.yml": (
                "site_name: {{project_name}}\nsite_description: {{description}}\n\ntheme:\n"
                "  name: material\n  palette:\n    scheme: slate\n\nnav:\n"
                "  - Home: index.md\n  - Início Rápido: quickstart.md\n"
            ),
            "docs/index.md": "# {{project_name}}\n\n{{description}}\n",
            "docs/quickstart.md": "# Início Rápido\n\nInstruções rápidas de uso.\n",
            "requirements.txt": "mkdocs>=1.5\nmkdocs-material>=9\n",
            ".gitignore": "site/\n",
        },
        "post_commands": [],
    },
    {
        "id": "makefile_c",
        "label": _t("tmpl.label.makefile_c"),
        "description": _t("tmpl.desc.makefile_c"),
        "category": "C/C++",
        "files": {
            "Makefile": (
                "CC = gcc\nCFLAGS = -Wall -Wextra -std=c17 -Iinclude\nSRC = $(wildcard src/*.c)\n"
                "OBJ = $(SRC:.c=.o)\nBIN = {{project_name}}\n\n"
                "all: $(BIN)\n\n$(BIN): $(OBJ)\n\t$(CC) -o $@ $^\n\n"
                "clean:\n\trm -f $(OBJ) $(BIN)\n\n.PHONY: all clean\n"
            ),
            "src/main.c": '#include <stdio.h>\n#include "{{project_name}}.h"\n\nint main(void) {\n    printf("Hello from {{project_name}}\\n");\n    return 0;\n}\n',
            "include/{{project_name}}.h": "#pragma once\n\nvoid hello(void);\n",
            "README.md": "# {{project_name}}\n\n{{description}}\n\n## Build\n\n```bash\nmake\n./{{project_name}}\n```\n",
            ".gitignore": "*.o\n{{project_name}}\nbuild/\n",
        },
        "post_commands": [],
    },
]


def _apply_vars(text: str, vars_: dict[str, str]) -> str:
    """Substitui {{var}} no texto pelas variáveis fornecidas."""
    for key, value in vars_.items():
        text = text.replace("{{" + key + "}}", value)
    return text


def _make_vars(project_name: str, author: str, description: str) -> dict[str, str]:
    """Gera o dicionário de variáveis de substituição."""
    kebab = re.sub(r"[^a-z0-9]+", "-", project_name.lower()).strip("-")
    snake = re.sub(r"[^a-z0-9]+", "_", project_name.lower()).strip("_")
    return {
        "project_name": project_name,
        "project_name_kebab": kebab,
        "project_name_snake": snake,
        "author": author,
        "description": description,
        "year": str(datetime.now().year),
        "date": datetime.now().strftime("%Y-%m-%d"),
    }


class TemplateStore:
    """Gerencia templates embutidos e customizados."""

    def __init__(self) -> None:
        """Inicializa a store de templates de projeto."""
        _TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)
        self._custom: list[dict[str, Any]] = []
        self._load_custom()

    def _load_custom(self) -> None:
        for meta_file in _TEMPLATES_DIR.glob("*/template.json"):
            try:
                meta = json.loads(meta_file.read_text(encoding="utf-8"))
                meta["_dir"] = str(meta_file.parent)
                self._custom.append(meta)
            except (OSError, json.JSONDecodeError):
                pass

    def all_templates(self) -> list[dict[str, Any]]:
        """Retorna a lista de todos os templates disponíveis."""
        return _BUILTIN_TEMPLATES + self._custom

    def create_project(
        self,
        template: dict[str, Any],
        dest: Path,
        vars_: dict[str, str],
    ) -> list[str]:
        """Cria o projeto no diretório `dest`. Retorna lista de arquivos criados."""
        dest.mkdir(parents=True, exist_ok=True)
        created: list[str] = []

        if "_dir" in template:
            src_dir = Path(template["_dir"])
            for src_path in src_dir.rglob("*"):
                if src_path.name == "template.json":
                    continue
                rel = src_path.relative_to(src_dir)
                rel_str = _apply_vars(str(rel), vars_)
                dst_path = dest / rel_str
                if src_path.is_dir():
                    dst_path.mkdir(parents=True, exist_ok=True)
                else:
                    dst_path.parent.mkdir(parents=True, exist_ok=True)
                    raw = src_path.read_bytes()
                    try:
                        content = _apply_vars(raw.decode("utf-8"), vars_)
                        dst_path.write_text(content, encoding="utf-8")
                    except UnicodeDecodeError:
                        dst_path.write_bytes(raw)
                    created.append(str(dst_path.relative_to(dest)))
        else:
            for rel_path, content in template.get("files", {}).items():
                rel_path_expanded = _apply_vars(rel_path, vars_)
                content_expanded = _apply_vars(content, vars_)
                file_path = dest / rel_path_expanded
                file_path.parent.mkdir(parents=True, exist_ok=True)
                file_path.write_text(content_expanded, encoding="utf-8")
                created.append(rel_path_expanded)

        return created

    def save_folder_as_template(
        self,
        src: Path,
        meta: dict[str, str],
    ) -> Path:
        """Salva uma pasta como template customizado."""
        tid = re.sub(r"[^a-z0-9_]", "_", meta["id"].lower())
        out = _TEMPLATES_DIR / tid
        if out.exists():
            shutil.rmtree(out)
        shutil.copytree(str(src), str(out))
        meta_file = out / "template.json"
        meta_file.write_text(
            json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        self._custom.clear()
        self._load_custom()
        return out


class ProjectTemplateDialog(tk.Toplevel):
    """Diálogo de criação de projeto a partir de template.

    Abre em Ferramentas -> 📋 Novo Projeto a partir de Template.
    """

    def __init__(
        self,
        parent: tk.Widget,
        tmgr: Any,
        store: TemplateStore,
        on_open: Any,
        cfg: Any,
    ) -> None:
        """Inicializa o diálogo de configuração de template de projeto."""
        t = tmgr.theme
        super().__init__(parent)
        self.title(_t("tmpl.window_title"))
        self.configure(bg=t.floating_bg)
        self.geometry("820x580")
        self.minsize(640, 440)
        self.transient(parent)
        safe_grab_set(self)

        self._tmgr = tmgr
        self._store = store
        self._on_open = on_open
        self._cfg = cfg
        self._selected: dict[str, Any] | None = None
        self._category_var = tk.StringVar(value=_t("tmpl.category_all"))

        self._build(t)
        self._populate()
        self._center(parent)

    def _build(self, t: Any) -> None:
        tk.Frame(self, bg=t.accent, height=2).pack(fill="x")

        hdr = tk.Frame(self, bg=t.floating_bg)
        hdr.pack(fill="x", padx=14, pady=(10, 4))
        tk.Label(
            hdr,
            text="📋  Novo Projeto a partir de Template",
            bg=t.floating_bg,
            fg=t.t_func,
            font=(FONT_UI, 12, "bold"),
        ).pack(side="left")

        save_btn = tk.Label(
            hdr,
            text=_t("tmpl.save_as_btn"),
            bg=t.btn_bg,
            fg=t.fg,
            font=(FONT_UI, 8),
            cursor="hand2",
            padx=8,
            pady=3,
            relief="flat",
        )
        save_btn.pack(side="right")
        save_btn.bind("<Button-1>", lambda _: self._save_as_template())

        cat_frame = tk.Frame(self, bg=t.floating_bg)
        cat_frame.pack(fill="x", padx=14, pady=(0, 4))
        tk.Label(
            cat_frame,
            text=_t("templates.field_category", "Categoria:"),
            bg=t.floating_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 8),
        ).pack(side="left")

        categories = ["Todos"] + sorted(
            {t_["category"]
                for t_ in self._store.all_templates() if t_.get("category")}
        )
        for cat in categories:
            rb = tk.Radiobutton(
                cat_frame,
                text=cat,
                variable=self._category_var,
                value=cat,
                bg=t.floating_bg,
                fg=t.fg,
                selectcolor=t.editor_bg,
                activebackground=t.floating_bg,
                font=(FONT_UI, 8),
                command=self._populate,
            )
            rb.pack(side="left", padx=4)

        body = tk.Frame(self, bg=t.floating_bg)
        body.pack(fill="both", expand=True, padx=14, pady=4)

        left = tk.Frame(body, bg=t.floating_bg, width=310)
        left.pack(side="left", fill="both")
        left.pack_propagate(False)

        vsb = tk.Scrollbar(left, orient="vertical")
        self._listbox = tk.Listbox(
            left,
            bg=t.editor_bg,
            fg=t.fg,
            selectbackground=t.sel_bg,
            selectforeground=t.sel_fg,
            font=(FONT_UI, 9),
            relief="flat",
            bd=0,
            activestyle="none",
            highlightthickness=0,
            yscrollcommand=vsb.set,
        )
        vsb.config(command=self._listbox.yview)
        vsb.pack(side="right", fill="y")
        self._listbox.pack(fill="both", expand=True)
        self._listbox.bind("<<ListboxSelect>>", self._on_select)

        sep = tk.Frame(body, bg=t.border, width=1)
        sep.pack(side="left", fill="y", padx=8)

        right = tk.Frame(body, bg=t.floating_bg)
        right.pack(side="left", fill="both", expand=True)

        self._desc_lbl = tk.Label(
            right,
            text="",
            bg=t.floating_bg,
            fg=t.fg,
            font=(FONT_UI, 9),
            wraplength=360,
            justify="left",
            anchor="nw",
        )
        self._desc_lbl.pack(anchor="w", pady=(0, 6))

        fields_frame = tk.Frame(right, bg=t.floating_bg)
        fields_frame.pack(fill="x")

        ew = {
            "bg": t.input_bg,
            "fg": t.fg,
            "insertbackground": t.cursor,
            "relief": "flat",
            "bd": 0,
            "font": (FONT_MONO, 10),
            "highlightthickness": 1,
            "highlightbackground": t.border,
        }

        self._vars: dict[str, tk.StringVar] = {}
        labels = [
            ("name", "Nome do Projeto", _t("tmpl.name_default")),
            ("author", "Autor", self._cfg.get("user_name", "") if self._cfg else ""),
            ("description", "Descrição", _t("tmpl.desc_default")),
            ("dest", _t("templates.field_dest", "Pasta de destino:"), str(Path.home())),
        ]
        for key, label, default in labels:
            row = tk.Frame(fields_frame, bg=t.floating_bg)
            row.pack(fill="x", pady=3)
            tk.Label(
                row,
                text=label + ":",
                bg=t.floating_bg,
                fg=t.fg_muted,
                font=(FONT_UI, 8),
                width=18,
                anchor="w",
            ).pack(side="left")
            var = tk.StringVar(value=default)
            self._vars[key] = var
            entry = tk.Entry(row, textvariable=var, **ew, width=26)
            entry.pack(side="left", fill="x", expand=True)

            if key == "dest":
                browse = tk.Label(
                    row,
                    text="📂",
                    bg=t.btn_bg,
                    fg=t.fg,
                    font=(FONT_UI, 9),
                    cursor="hand2",
                    padx=4,
                    pady=1,
                    relief="flat",
                )
                browse.pack(side="left", padx=2)
                browse.bind("<Button-1>", lambda _: self._browse_dest())

        tk.Label(
            right,
            text=_t("tmpl.files_preview"),
            bg=t.floating_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 8),
        ).pack(anchor="w", pady=(8, 2))

        self._preview = tk.Text(
            right,
            bg=t.editor_bg,
            fg=t.fg,
            font=(FONT_MONO, 8),
            relief="flat",
            bd=0,
            state="disabled",
            height=7,
            wrap="none",
            cursor="arrow",
            padx=4,
            pady=4,
        )
        self._preview.pack(fill="both", expand=True)

        tk.Frame(self, bg=t.border, height=1).pack(fill="x", padx=14)

        foot = tk.Frame(self, bg=t.floating_bg)
        foot.pack(fill="x", padx=14, pady=8)

        self._status = tk.Label(
            foot, text="", bg=t.floating_bg, fg=t.fg_muted, font=(FONT_UI, 8)
        )
        self._status.pack(side="left")

        for text, cmd, primary in (
            (_t("tmpl.create_btn"), self._create_project, True),
            (_t("tmpl.close"), self.destroy, False),
        ):
            btn = tk.Label(
                foot,
                text=text,
                bg=t.btn_primary if primary else t.btn_bg,
                fg=t.btn_primary_fg if primary else t.fg,
                font=(FONT_UI, 9, "bold" if primary else "normal"),
                cursor="hand2",
                padx=12,
                pady=4,
                relief="flat",
            )
            btn.pack(side="right", padx=3)
            btn.bind("<Button-1>", lambda _, c=cmd: c())

        self._filtered: list[dict[str, Any]] = []

    def _populate(self) -> None:
        cat = self._category_var.get()
        self._listbox.delete(0, "end")
        self._filtered = [
            t_
            for t_ in self._store.all_templates()
            if cat == "Todos" or t_.get("category") == cat
        ]
        for t_ in self._filtered:
            self._listbox.insert("end", f"  {t_['label']}")

    def _on_select(self, _e: Any = None) -> None:
        sel = self._listbox.curselection()
        if not sel or sel[0] >= len(self._filtered):
            return
        self._selected = self._filtered[sel[0]]
        self._desc_lbl.config(text=self._selected.get("description", ""))
        self._update_preview()

    def _update_preview(self) -> None:
        if not self._selected:
            return
        files = self._selected.get("files", {})
        lines = []
        if "_dir" in self._selected:
            src = Path(self._selected["_dir"])
            for p in sorted(src.rglob("*")):
                if p.name == "template.json":
                    continue
                rel = p.relative_to(src)
                prefix = "📁 " if p.is_dir() else "  📄 "
                lines.append(f"  {prefix}{rel}")
        else:
            for path in sorted(files.keys()):
                lines.append(f"  📄 {path}")

        self._preview.config(state="normal")
        self._preview.delete("1.0", "end")
        self._preview.insert("1.0", "\n".join(lines))
        self._preview.config(state="disabled")

    def _browse_dest(self) -> None:
        path = filedialog.askdirectory(
            parent=self, title=_t("tmpl.dest_dialog_title"))
        if path:
            self._vars["dest"].set(path)

    def _create_project(self) -> None:
        if not self._selected:
            self._status.config(text=_t("tmpl.no_select"))
            return

        name = self._vars["name"].get().strip()
        author = self._vars["author"].get().strip()
        desc = self._vars["description"].get().strip()
        dest_base = Path(self._vars["dest"].get().strip())

        if not name:
            self._status.config(text=_t("tmpl.no_name"))
            return

        dest = dest_base / name
        if dest.exists() and not messagebox.askyesno(
            _t("tmpl.overwrite_title"),
            f"'{dest}' já existe. Sobrescrever arquivos?",
            parent=self,
        ):
            return

        vars_ = _make_vars(name, author, desc)

        try:
            created = self._store.create_project(self._selected, dest, vars_)
            self._status.config(
                text=_t("tmpl.created_status", n=len(created), name=dest.name)
            )
        except Exception as exc:
            messagebox.showerror(_t("tmpl.err_create"), str(exc), parent=self)
            return

        if messagebox.askyesno(
            _t("tmpl.open_prompt_title"),
            f"Projeto criado em:\n{dest}\n\nAbrir agora no Codis?",
            parent=self,
        ):
            self.destroy()
            self._on_open(dest)

    def _save_as_template(self) -> None:
        t = self._tmgr.theme
        src = filedialog.askdirectory(
            parent=self, title=_t("templates.select_folder"))
        if not src:
            return

        dlg = tk.Toplevel(self)
        dlg.title(_t("tmpl.save_title"))
        dlg.configure(bg=t.floating_bg)
        dlg.geometry("380x260")
        dlg.transient(self)
        safe_grab_set(dlg)
        dlg.resizable(False, False)

        ew = {
            "bg": t.input_bg,
            "fg": t.fg,
            "insertbackground": t.cursor,
            "relief": "flat",
            "bd": 0,
            "font": (FONT_MONO, 10),
            "highlightthickness": 1,
            "highlightbackground": t.border,
        }

        meta_vars: dict[str, tk.StringVar] = {}
        for key, label, default in [
            ("id", _t("tmpl.save_field_id"), Path(
                src).name.lower().replace(" ", "_")),
            ("label", _t("tmpl.save_field_label"), Path(src).name),
            ("description", "Descrição", ""),
            ("category", _t("tmpl.save_field_cat"),
             _t("tmpl.save_category_custom")),
        ]:
            row = tk.Frame(dlg, bg=t.floating_bg)
            row.pack(fill="x", padx=14, pady=4)
            tk.Label(
                row,
                text=label + ":",
                bg=t.floating_bg,
                fg=t.fg_muted,
                font=(FONT_UI, 8),
                width=14,
                anchor="w",
            ).pack(side="left")
            var = tk.StringVar(value=default)
            meta_vars[key] = var
            tk.Entry(row, textvariable=var, **ew).pack(
                side="left", fill="x", expand=True
            )

        def _do_save() -> None:
            meta = {k: v.get().strip() for k, v in meta_vars.items()}
            if not meta["id"]:
                return
            try:
                self._store.save_folder_as_template(Path(src), meta)
                dlg.destroy()
                self._populate()
                self._status.config(
                    text=_t("tmpl.saved_status", label=meta["label"]))
            except Exception as exc:
                messagebox.showerror(
                    _t("templates.err_title", "Erro ao criar projeto"),
                    str(exc),
                    parent=dlg,
                )

        btn_row = tk.Frame(dlg, bg=t.floating_bg)
        btn_row.pack(pady=10)
        for text, cmd, primary in (
            (_t("tmpl.save_btn"), _do_save, True),
            (_t("tmpl.cancel"), dlg.destroy, False),
        ):
            btn = tk.Label(
                btn_row,
                text=text,
                bg=t.btn_primary if primary else t.btn_bg,
                fg=t.btn_primary_fg if primary else t.fg,
                font=(FONT_UI, 9),
                cursor="hand2",
                padx=10,
                pady=4,
                relief="flat",
            )
            btn.pack(side="left", padx=4)
            btn.bind("<Button-1>", lambda _, c=cmd: c())

    def _center(self, parent: tk.Widget) -> None:
        self.update_idletasks()
        with contextlib.suppress(tk.TclError):
            px, py = parent.winfo_rootx(), parent.winfo_rooty()
            pw, ph = parent.winfo_width(), parent.winfo_height()
            w, h = self.winfo_width(), self.winfo_height()
            self.geometry(f"+{px + (pw - w) // 2}+{py + (ph - h) // 2}")
