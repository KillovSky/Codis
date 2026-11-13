"""Motor de snippets do editor do Codis.

Provê ``SnippetEngine`` para expandir atalhos de texto em blocos de código
com placeholders posicionáveis (``$1``, ``$2``, ``$0``), e ``Snippet``,
a estrutura de dados que representa um snippet individual.
"""

from __future__ import annotations

import json
import logging
import re
import tkinter as tk
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from src.constants import SNIPPETS_DIR

logger = logging.getLogger("Codis")


@dataclass
class Snippet:
    """Um snippet de código expansível."""

    prefix: str
    body: str
    description: str = ""
    scope: list[str] = field(default_factory=list)


class SnippetEngine:
    """
    Motor de snippets estilo VSCode.
    Suporta: $1..$n (tabstops), ${1:default} (placeholders),
             ${1|choice1,choice2|} (choices), $0 (cursor final),
             variáveis: $TM_FILENAME, $TM_FILEPATH, etc.
    """

    BUILTIN_SNIPPETS: dict[str, list[Snippet]] = {
        "python": [
            Snippet(
                "def",
                "def ${1:function_name}(${2:args}):\n    ${3:pass}",
                "Function definition",
            ),
            Snippet(
                "class",
                "class ${1:ClassName}(${2:object}):\n    def __init__(self${3:, args}):\n        ${4:pass}",
                "Class definition",
            ),
            Snippet("if", "if ${1:condition}:\n    ${2:pass}", "If statement"),
            Snippet(
                "ifel",
                "if ${1:condition}:\n    ${2:pass}\nelse:\n    ${3:pass}",
                "If-else",
            ),
            Snippet(
                "for", "for ${1:item} in ${2:iterable}:\n    ${3:pass}", "For loop"
            ),
            Snippet(
                "while", "while ${1:condition}:\n    ${2:pass}", "While loop"),
            Snippet(
                "try",
                "try:\n    ${1:pass}\nexcept ${2:Exception} as ${3:e}:\n    ${4:pass}",
                "Try-except",
            ),
            Snippet(
                "with",
                "with ${1:context} as ${2:name}:\n    ${3:pass}",
                "With statement",
            ),
            Snippet(
                "lc", "[${1:expr} for ${2:item} in ${3:iterable}]", "List comprehension"
            ),
            Snippet(
                "dc",
                "{${1:key}: ${2:value} for ${3:item} in ${4:iterable}}",
                "Dict comprehension",
            ),
            Snippet(
                "prop",
                "@property\ndef ${1:name}(self):\n    return self._${1:name}",
                "Property",
            ),
            Snippet(
                "main", 'if __name__ == "__main__":\n    ${1:main()}', "Main guard"
            ),
            Snippet("pr", "print(${1})", "Print"),
            Snippet("iff", "from ${1:module} import ${2:name}", "Import from"),
            Snippet("imp", "import ${1:module}", "Import"),
        ],
        "javascript": [
            Snippet(
                "fn", "function ${1:name}(${2:params}) {\n    ${3}\n}", "Function"),
            Snippet(
                "afn",
                "const ${1:name} = (${2:params}) => {\n    ${3}\n}",
                "Arrow function",
            ),
            Snippet(
                "cl",
                "class ${1:Name} ${2:extends ${3:Base} }{\n    constructor(${4}) {\n        ${5}\n    }\n}",
                "Class",
            ),
            Snippet("imp", "import ${1:name} from '${2:module}';", "Import"),
            Snippet("exp", "export ${1:default} ${2:name};", "Export"),
            Snippet("if", "if (${1:condition}) {\n    ${2}\n}", "If"),
            Snippet(
                "ifel",
                "if (${1:condition}) {\n    ${2}\n} else {\n    ${3}\n}",
                "If-else",
            ),
            Snippet(
                "for",
                "for (let ${1:i} = 0; ${1:i} < ${2:len}; ${1:i}++) {\n    ${3}\n}",
                "For loop",
            ),
            Snippet(
                "fore", "for (const ${1:item} of ${2:items}) {\n    ${3}\n}", "For-of"
            ),
            Snippet(
                "fori", "for (const ${1:key} in ${2:object}) {\n    ${3}\n}", "For-in"
            ),
            Snippet(
                "try", "try {\n    ${1}\n} catch (${2:err}) {\n    ${3}\n}", "Try-catch"
            ),
            Snippet("pr", "console.log(${1});", "console.log"),
            Snippet("pe", "console.error(${1});", "console.error"),
            Snippet("pt", "console.table(${1});", "console.table"),
            Snippet(
                "prom", "new Promise((resolve, reject) => {\n    ${1}\n})", "Promise"
            ),
            Snippet("aw", "const ${1:result} = await ${2:promise};", "Await"),
            Snippet("des", "const { ${1} } = ${2};", "Destructure object"),
            Snippet("da", "const [ ${1} ] = ${2};", "Destructure array"),
        ],
        "typescript": [
            Snippet(
                "int", "interface ${1:Name} {\n    ${2}: ${3:string};\n}", "Interface"
            ),
            Snippet("typ", "type ${1:Name} = ${2};", "Type alias"),
            Snippet("enu", "enum ${1:Name} {\n    ${2}\n}", "Enum"),
            Snippet(
                "gen",
                "function ${1:name}<${2:T}>(${3:arg}: ${2:T}): ${4:T} {\n    ${5}\n}",
                "Generic function",
            ),
        ],
        "html": [
            Snippet(
                "html5",
                '<!DOCTYPE html>\n<html lang="${1:en}">\n<head>\n'
                '    <meta charset="UTF-8">\n'
                '    <meta name="viewport" content="width=device-width, initial-scale=1.0">\n'
                "    <title>${2:Document}</title>\n</head>\n<body>\n    ${3}\n</body>\n</html>",
                "HTML5 boilerplate",
            ),
            Snippet("div", '<div class="${1}">${2}</div>', "Div"),
            Snippet(
                "inp",
                '<input type="${1:text}" name="${2}" id="${3}" placeholder="${4}">',
                "Input",
            ),
            Snippet(
                "btn",
                '<button type="${1:button}" onclick="${2}">${3:Click me}</button>',
                "Button",
            ),
            Snippet("a", '<a href="${1:#}">${2:Link}</a>', "Anchor"),
            Snippet(
                "img", '<img src="${1}" alt="${2}" width="${3}" height="${4}">', "Image"
            ),
            Snippet("ul", "<ul>\n    <li>${1}</li>\n</ul>", "Unordered list"),
            Snippet(
                "form",
                '<form action="${1:#}" method="${2:post}">\n    ${3}\n</form>',
                "Form",
            ),
            Snippet("script", '<script src="${1}"></script>', "Script tag"),
            Snippet("link", '<link rel="stylesheet" href="${1}">', "CSS link"),
            Snippet("meta", '<meta name="${1}" content="${2}">', "Meta tag"),
        ],
        "css": [
            Snippet(
                "flex",
                "display: flex;\nflex-direction: ${1:row};\njustify-content: ${2:flex-start};\nalign-items: ${3:stretch};",
                "Flexbox",
            ),
            Snippet(
                "grid",
                "display: grid;\ngrid-template-columns: ${1:repeat(3, 1fr)};\ngap: ${2:1rem};",
                "Grid",
            ),
            Snippet(
                "mq",
                "@media (${1:max-width}: ${2:768px}) {\n    ${3}\n}",
                "Media query",
            ),
            Snippet(
                "anim",
                "@keyframes ${1:name} {\n    from { ${2} }\n    to { ${3} }\n}",
                "Animation",
            ),
            Snippet("var", "var(--${1:name})", "CSS variable"),
            Snippet(
                "box",
                "box-shadow: ${1:0} ${2:0} ${3:0} ${4:0} ${5:#000};",
                "Box shadow",
            ),
        ],
        "rust": [
            Snippet(
                "fn", "fn ${1:name}(${2}) -> ${3:()} {\n    ${4}\n}", "Function"),
            Snippet(
                "pub fn",
                "pub fn ${1:name}(${2}) -> ${3:()} {\n    ${4}\n}",
                "Public function",
            ),
            Snippet(
                "str", "struct ${1:Name} {\n    ${2}: ${3:String},\n}", "Struct"),
            Snippet("enum", "enum ${1:Name} {\n    ${2},\n}", "Enum"),
            Snippet("impl", "impl ${1:Type} {\n    ${2}\n}", "Impl block"),
            Snippet("trt", "trait ${1:Name} {\n    ${2}\n}", "Trait"),
            Snippet(
                "match",
                "match ${1:expr} {\n    ${2:pattern} => ${3:expr},\n    _ => ${4:()},\n}",
                "Match",
            ),
            Snippet("if", "if ${1:condition} {\n    ${2}\n}", "If"),
            Snippet("for", "for ${1:item} in ${2:iter} {\n    ${3}\n}", "For"),
            Snippet("while", "while ${1:cond} {\n    ${2}\n}", "While"),
            Snippet(
                "let", "let ${1:mut }${2:name}: ${3:Type} = ${4};", "Let binding"),
            Snippet("pr", 'println!("${1:{:?}}", ${2});', "println!"),
            Snippet("epri", 'eprintln!("${1:{:?}}", ${2});', "eprintln!"),
            Snippet("opt", "Option<${1:T}>", "Option type"),
            Snippet("res", "Result<${1:T}, ${2:E}>", "Result type"),
        ],
        "go": [
            Snippet(
                "fn", "func ${1:name}(${2}) ${3:string} {\n    ${4}\n}", "Function"
            ),
            Snippet(
                "str", "type ${1:Name} struct {\n    ${2} ${3:string}\n}", "Struct"
            ),
            Snippet("if", "if ${1:condition} {\n    ${2}\n}", "If"),
            Snippet(
                "ife", "if err != nil {\n    return ${1:nil, }err\n}", "If error"),
            Snippet(
                "for",
                "for ${1:i} := 0; ${1:i} < ${2:n}; ${1:i}++ {\n    ${3}\n}",
                "For loop",
            ),
            Snippet(
                "fore", "for ${1:_, v} := range ${2:items} {\n    ${3}\n}", "For range"
            ),
            Snippet("go", "go func() {\n    ${1}\n}()", "Goroutine"),
            Snippet("pr", "fmt.Println(${1})", "fmt.Println"),
            Snippet("prf", 'fmt.Printf("${1}\\n"${2})', "fmt.Printf"),
        ],
    }

    def __init__(self) -> None:
        """Inicializa o motor de snippets com o conjunto de snippets fornecido."""
        self._snippets: dict[str, list[Snippet]] = {}

        for lang, snips in self.BUILTIN_SNIPPETS.items():
            self._snippets[lang] = list(snips)
        self._load_user_snippets()

    def _load_user_snippets(self) -> None:
        """Carrega snippets definidos pelo usuário a partir de arquivos JSON em SNIPPETS_DIR."""
        try:
            SNIPPETS_DIR.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            logger.debug("SNIPPETS_DIR inacessível: %s", exc)
            return
        for f in SNIPPETS_DIR.glob("*.json"):
            lang = f.stem
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                if lang not in self._snippets:
                    self._snippets[lang] = []
                for name, s in data.items():
                    body = s.get("body", "")
                    if isinstance(body, list):
                        body = "\n".join(body)
                    self._snippets[lang].append(
                        Snippet(
                            prefix=s.get("prefix", name),
                            body=body,
                            description=s.get("description", name),
                            scope=s.get("scope", [lang]),
                        )
                    )
            except (OSError, ValueError, KeyError) as exc:
                logger.warning("Snippet load error %s: %s", f, exc)

    def get_for_lang(self, lang: str) -> list[Snippet]:
        """Expande o snippet no editor ativo."""
        return self._snippets.get(lang, [])

    def add(self, lang: str, prefix: str, body: str, description: str = "") -> None:
        """Add or replace a snippet for the given language."""
        if lang not in self._snippets:
            self._snippets[lang] = []

        self._snippets[lang] = [
            s for s in self._snippets[lang] if s.prefix != prefix]
        self._snippets[lang].append(
            Snippet(prefix=prefix, body=body, description=description)
        )

    def remove(self, lang: str, prefix: str) -> None:
        """Remove a snippet by prefix."""
        if lang in self._snippets:
            self._snippets[lang] = [
                s for s in self._snippets[lang] if s.prefix != prefix
            ]

    def find_prefix(self, lang: str, text: str) -> Snippet | None:
        """Encontra snippet cujo prefix bate com text."""
        for s in self.get_for_lang(lang):
            if s.prefix == text:
                return s
        return None

    @staticmethod
    def expand(
        snippet: Snippet, text_widget: tk.Text, path: Path | None = None
    ) -> bool:
        """
        Expande um snippet no widget de texto na posição do cursor.

        Substitui variáveis especiais (``$TM_FILENAME``, ``$CURRENT_DATE``, etc.),
        respeita a indentação atual e posiciona o cursor no primeiro tab stop.

        Args:
            snippet: Objeto Snippet a ser expandido.
            text_widget: Widget ``tk.Text`` onde a expansão será inserida.
            path: Caminho do arquivo atual (para variáveis ``$TM_*``).

        Returns:
            ``True`` se a expansão foi bem-sucedida, ``False`` caso contrário.
        """
        try:
            pos = text_widget.index(tk.INSERT)
            line_no, _ = map(int, pos.split("."))
            line_text = text_widget.get(f"{line_no}.0", pos)
            m = re.search(r"[\w$]+$", line_text)
            if m:
                del_start = f"{line_no}.{len(line_text) - len(m.group())}"
                text_widget.delete(del_start, pos)
            body = snippet.body
            body = body.replace(
                "$TM_FILENAME", path.name if path else "untitled")
            body = body.replace("$TM_FILEPATH", str(path) if path else "")
            body = body.replace("$TM_DIRECTORY", str(
                path.parent) if path else "")
            body = body.replace(
                "$CURRENT_DATE", datetime.now().strftime("%Y-%m-%d"))
            body = body.replace(
                "$CURRENT_TIME", datetime.now().strftime("%H:%M:%S"))
            body = body.replace("$CURRENT_YEAR", datetime.now().strftime("%Y"))

            indent_m = re.match(r"^(\s*)", line_text)
            indent = indent_m.group(1) if indent_m else ""

            insert_pos = text_widget.index(tk.INSERT)
            body_lines = body.split("\n")
            expanded = ("\n" + indent).join(body_lines)
            text_widget.insert(tk.INSERT, expanded)

            for marker in ("$1", "${1:", "$0"):
                full_text = text_widget.get("1.0", "end")
                offset_start = len(text_widget.get("1.0", insert_pos))
                idx = full_text.find(marker, offset_start)
                if idx != -1:
                    m_start = text_widget.index(f"1.0 + {idx}c")
                    if marker.startswith("${1:"):
                        end_idx = full_text.index("}", idx)
                        default_text = full_text[idx + 4: end_idx]
                        text_widget.delete(
                            m_start, text_widget.index(f"1.0 + {end_idx + 1}c")
                        )
                        text_widget.insert(m_start, default_text)
                        text_widget.mark_set(tk.INSERT, m_start)
                        text_widget.tag_add(
                            tk.SEL, m_start, f"{m_start} + {len(default_text)}c"
                        )
                    elif marker == "$1":
                        text_widget.delete(
                            m_start, f"{m_start} + {len('$1')}c")
                        text_widget.mark_set(tk.INSERT, m_start)
                    else:
                        text_widget.delete(
                            m_start, f"{m_start} + {len('$0')}c")
                        text_widget.mark_set(tk.INSERT, m_start)
                    break

            return True
        except (tk.TclError, ValueError, IndexError) as exc:
            logger.debug("Snippet expand error: %s", exc)
            return False

    def register(self, lang: str, snippet: Snippet) -> None:
        """Adiciona um snippet à lista de uma linguagem (usado por plugins)."""
        if lang not in self._snippets:
            self._snippets[lang] = []
        self._snippets[lang].append(snippet)
