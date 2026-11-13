"""Codis - Native Render / Preview panel.

Modos de renderização:
  - tkinterweb  - HtmlFrame com CSS/JS real (quando tkinterweb estiver instalado)
  - Interno     - tk.Text com formatação rica (fallback sem dependências extras)
  - Navegador   - abre no browser padrão do sistema

Tipos suportados: .html .htm .xhtml .md .markdown .svg .rst
"""

from __future__ import annotations

import contextlib
import re
import threading
import time
import tkinter as tk
import webbrowser
from collections.abc import Callable
from pathlib import Path
from tkinter import ttk

from src.constants import FONT_MONO, FONT_UI
from src.i18n import _t

import importlib.util

_TKWEB = importlib.util.find_spec("tkinterweb") is not None
_tkweb = None  # type: ignore


def _load_tkweb():
    """Importa ``tkinterweb`` sob demanda; marca como indisponível se falhar."""
    global _tkweb, _TKWEB
    if _tkweb is None:
        try:
            import tkinterweb as mod  # type: ignore

            _tkweb = mod
        except Exception:
            _TKWEB = False
            raise
    return _tkweb

_H_RE = re.compile(r"^(#{1,6})\s+(.+)$", re.M)
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*", re.DOTALL)
_IT_RE = re.compile(r"\*(.+?)\*", re.DOTALL)
_CODE_RE = re.compile(r"`(.+?)`")
_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^\)]+)\)")
_HR_RE = re.compile(r"^(?:-{3,}|_{3,}|\*{3,})$", re.M)
_BCODE_RE = re.compile(r"```(\w*)\n(.*?)```", re.DOTALL)
_BQ_RE = re.compile(r"^> (.+)$", re.M)
_UL_RE = re.compile(r"^[*\-+] (.+)$", re.M)
_OL_RE = re.compile(r"^\d+\. (.+)$", re.M)
_TABLE_RE = re.compile(r"(\|.+\|\n\|[-| :]+\|\n(?:\|.+\|\n?)+)")
_STRIKE_RE = re.compile(r"~~(.+?)~~", re.DOTALL)
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_HTML_ENTITY_RE = re.compile(r"&(amp|lt|gt|quot|apos|nbsp);")
_ENTITIES = {"amp": "&", "lt": "<", "gt": ">",
             "quot": '"', "apos": "'", "nbsp": " "}


def _md_table(m: re.Match) -> str:
    lines = [l_.strip() for l_ in m.group(0).strip().splitlines()]
    html = ["<table>"]
    first = True
    for line in lines:
        if re.match(r"^\|[-| :]+\|$", line):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        tag = "th" if first else "td"
        html.append(
            "<tr>" + "".join(f"<{tag}>{c}</{tag}>" for c in cells) + "</tr>")
        first = False
    html.append("</table>")
    return "\n".join(html)


def markdown_to_html(text: str) -> str:
    """Converte texto Markdown para HTML usando regras internas do Codis."""
    text = _BCODE_RE.sub(
        lambda m: f'<pre><code class="language-{m.group(1)}">{m.group(2)}</code></pre>',
        text,
    )
    text = _TABLE_RE.sub(_md_table, text)
    text = _BQ_RE.sub(r"<blockquote>\1</blockquote>", text)
    text = _HR_RE.sub("<hr>", text)
    text = _H_RE.sub(
        lambda m: f"<h{len(m.group(1))}>{m.group(2)}</h{len(m.group(1))}>", text
    )
    text = _STRIKE_RE.sub(r"<del>\1</del>", text)
    text = _BOLD_RE.sub(r"<strong>\1</strong>", text)
    text = _IT_RE.sub(r"<em>\1</em>", text)
    text = _CODE_RE.sub(r"<code>\1</code>", text)
    text = _LINK_RE.sub(r'<a href="\2">\1</a>', text)
    text = _UL_RE.sub(r"<li>\1</li>", text)
    text = _OL_RE.sub(r"<li>\1</li>", text)
    paragraphs = []
    for block in re.split(r"\n{2,}", text.strip()):
        block = block.strip()
        if not block:
            continue
        if re.match(r"^<(h[1-6]|ul|ol|li|table|pre|blockquote|hr)", block):
            paragraphs.append(block)
        else:
            paragraphs.append(f"<p>{block}</p>")
    return "\n".join(paragraphs)

_TEMPLATE = """<!DOCTYPE html>
<html data-theme="{theme}"><head>
<meta charset="utf-8"><title>{title}</title>
<style>
  :root {{
    --bg:      {bg};
    --fg:      {fg};
    --muted:   {muted};
    --code-bg: {code_bg};
    --border:  {border};
    --accent:  {accent};
    --link:    {link};
  }}
  *, *::before, *::after {{ box-sizing: border-box; }}
  html {{ scroll-behavior: smooth; }}
  body {{
    font-family: system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif;
    font-size: 15px;
    line-height: 1.75;
    max-width: 860px;
    margin: 0 auto;
    padding: 2rem 1.5rem 4rem;
    color: var(--fg);
    background: var(--bg);
    -webkit-font-smoothing: antialiased;
  }}
  h1, h2, h3, h4, h5, h6 {{
    color: var(--accent);
    margin-top: 1.8em;
    margin-bottom: 0.5em;
    font-weight: 700;
    line-height: 1.25;
  }}
  h1 {{
    font-size: 2em;
    border-bottom: 2px solid var(--border);
    padding-bottom: 0.3em;
    margin-top: 0;
  }}
  h2 {{
    font-size: 1.5em;
    border-bottom: 1px solid var(--border);
    padding-bottom: 0.2em;
  }}
  h3 {{ font-size: 1.25em; }}
  h4 {{ font-size: 1.1em; }}
  h5, h6 {{ font-size: 1em; color: var(--muted); }}
  p {{ margin: 0.75em 0 1em; }}
  a {{ color: var(--link); text-decoration: none; }}
  a:hover {{ text-decoration: underline; }}
  code {{
    font-family: 'Cascadia Code', 'Fira Code', 'Consolas', monospace;
    font-size: 0.875em;
    background: var(--code-bg);
    color: var(--accent);
    padding: 0.18em 0.4em;
    border-radius: 5px;
    border: 1px solid var(--border);
  }}
  pre {{
    font-family: 'Cascadia Code', 'Fira Code', 'Consolas', monospace;
    background: var(--code-bg);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 1.1em 1.4em;
    overflow: auto;
    font-size: 0.875em;
    line-height: 1.6;
    margin: 1.2em 0;
  }}
  pre code {{
    background: transparent;
    color: var(--fg);
    padding: 0;
    border: none;
    border-radius: 0;
    font-size: inherit;
  }}
  blockquote {{
    border-left: 4px solid var(--accent);
    margin: 1em 0;
    padding: 0.6em 1.2em;
    color: var(--muted);
    background: var(--code-bg);
    border-radius: 0 8px 8px 0;
  }}
  blockquote p {{ margin: 0.3em 0; }}
  table {{
    border-collapse: collapse;
    width: 100%;
    margin: 1.2em 0;
    font-size: 0.95em;
    overflow-x: auto;
    display: block;
  }}
  th, td {{
    border: 1px solid var(--border);
    padding: 0.55em 0.9em;
    text-align: left;
  }}
  th {{
    background: var(--code-bg);
    font-weight: 600;
    color: var(--accent);
  }}
  tr:nth-child(even) {{ background: var(--code-bg); }}
  tr:hover {{ background: color-mix(in srgb, var(--accent) 8%, var(--bg)); }}
  ul, ol {{
    padding-left: 1.8em;
    margin: 0.5em 0 1em;
  }}
  li {{ margin-bottom: 0.35em; }}
  li > ul, li > ol {{ margin: 0.25em 0; }}
  hr {{
    border: none;
    border-top: 2px solid var(--border);
    margin: 2em 0;
  }}
  img {{
    max-width: 100%;
    height: auto;
    border-radius: 8px;
    display: block;
    margin: 1em auto;
  }}
  kbd {{
    display: inline-block;
    padding: 0.1em 0.45em;
    font-size: 0.85em;
    font-family: monospace;
    background: var(--code-bg);
    border: 1px solid var(--border);
    border-radius: 4px;
    box-shadow: 0 1px 0 var(--border);
  }}
  details {{ margin: 1em 0; }}
  summary {{
    cursor: pointer;
    font-weight: 600;
    color: var(--accent);
    padding: 0.3em 0;
  }}
  mark {{
    background: color-mix(in srgb, var(--accent) 30%, transparent);
    color: var(--fg);
    padding: 0 0.2em;
    border-radius: 3px;
  }}
  del {{ color: var(--muted); text-decoration: line-through; }}
  .task-list-item {{ list-style: none; }}
  .task-list-item input {{ margin-right: 0.5em; }}
  /* TOC */
  .toc {{
    background: var(--code-bg);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 1em 1.5em;
    margin: 1.5em 0;
    font-size: 0.9em;
  }}
  {extra_head}
</style>
</head>
<body>
{body}
{extra_scripts}
</body></html>"""

_SERVERS: dict = {}
_SERVERS_LOCK = threading.Lock()


def _start_server(directory: str) -> int:
    """Inicia (ou reutiliza) um servidor HTTP local somente-loopback para *directory*.

    Usa ``ThreadingHTTPServer`` para que conexões keep-alive do navegador não
    bloqueiem requisições concorrentes. O import de ``http.server`` é adiado
    até o primeiro uso para não penalizar o startup.
    """
    import functools
    from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

    class _QuietHandler(SimpleHTTPRequestHandler):
        def log_message(self, *_) -> None:
            """Suprime logs do servidor HTTP embutido - não-op intencional."""

    with _SERVERS_LOCK:
        port = _SERVERS.get(directory)
        if port is not None:
            return port
        handler = functools.partial(_QuietHandler, directory=directory)
        srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        srv.daemon_threads = True
        port = srv.server_address[1]
        threading.Thread(
            target=srv.serve_forever, daemon=True, name="CodisRenderHTTP"
        ).start()
        _SERVERS[directory] = port
        return port


def _strip_html(html: str) -> str:
    text = _HTML_TAG_RE.sub("", html)
    text = _HTML_ENTITY_RE.sub(
        lambda m: _ENTITIES.get(m.group(1), m.group(0)), text)
    return text


def _theme_vars(tmgr=None) -> dict:
    """Extrai variáveis de cor do tema atual."""
    if tmgr:
        t = tmgr.theme
        bh = t.editor_bg.lstrip("#")
        r, g, b = int(bh[0:2], 16), int(bh[2:4], 16), int(bh[4:6], 16)
        dark = (0.299 * r + 0.587 * g + 0.114 * b) < 128
        return {
            "theme": "dark" if dark else "light",
            "bg": t.editor_bg,
            "fg": t.fg,
            "muted": t.fg_muted,
            "code_bg": t.input_bg,
            "border": t.border,
            "accent": t.accent,
            "link": getattr(t, "t_link", "#6699ff" if dark else "#0055cc"),
        }
    return {
        "theme": "light",
        "bg": "#fafafa",
        "fg": "#1a1a1a",
        "muted": "#666",
        "code_bg": "#f0f0f0",
        "border": "#ddd",
        "accent": "#0055cc",
        "link": "#0055cc",
    }


def _inject_style(html: str, tmgr=None) -> str:
    """Injeta CSS bonito num HTML existente ou cria documento completo.

    Lógica:
    - HTML com DOCTYPE + CSS próprio: injeta apenas variáveis CSS para tematizar
      sem sobrescrever o design do autor.
    - HTML com DOCTYPE sem CSS: injeta nossa stylesheet completa.
    - Fragment HTML (sem DOCTYPE): envolve no template completo.
    """
    tv = _theme_vars(tmgr)
    has_doctype = bool(re.search(r"<!DOCTYPE", html, re.I))
    has_style_already = bool(
        re.search(r"<style|<link[^>]+rel=['\"]stylesheet", html, re.I)
    )

    if has_doctype and has_style_already:
        css_vars = (
            "<style>"
            ":root{"
            f"--bg:{tv['bg']};--fg:{tv['fg']};--muted:{tv['muted']};"
            f"--code-bg:{tv['code_bg']};--border:{tv['border']};"
            f"--accent:{tv['accent']};--link:{tv['link']};"
            "}"
            f"body{{background:var(--bg,{tv['bg']});color:var(--fg,{tv['fg']});}}"
            "</style>\n"
        )
        return re.sub(
            r"(<style[^>]*>|<link[^>]+rel=['\"]stylesheet[^>]*>)",
            css_vars + r"\1",
            html,
            count=1,
            flags=re.I,
        )

    if has_doctype:
        style_tag = (
            "<style>\n"
            + _TEMPLATE.split("<style>")[1]
            .split("</style>")[0]
            .format(**tv, extra_head="")
            + "\n</style>"
        )
        return re.sub(r"(<head[^>]*>)", rf"\1\n{style_tag}", html, count=1, flags=re.I)

    return _TEMPLATE.format(
        title="Preview", body=html, extra_head="", extra_scripts="", **tv
    )


class _TextRenderer(tk.Frame):
    """Renderizador usando tk.Text - fallback quando tkinterweb não está disponível."""

    def __init__(self, parent: tk.Widget, tmgr) -> None:
        """Inicializa o renderer de Markdown baseado em widget de texto."""
        t = tmgr.theme
        super().__init__(parent, bg=t.editor_bg)
        self._tmgr = tmgr
        self._build()

    def _build(self) -> None:
        t = self._tmgr.theme
        bh = t.editor_bg.lstrip("#")
        r, g, b = int(bh[0:2], 16), int(bh[2:4], 16), int(bh[4:6], 16)
        dark = (0.299 * r + 0.587 * g + 0.114 * b) < 128

        fg = t.fg
        bg = t.editor_bg
        code_bg = t.input_bg
        muted = t.fg_muted
        border_c = t.border
        accent = t.accent
        link_fg = getattr(t, "t_link", "#6699ff" if dark else "#0055cc")
        sel_bg = t.sel_bg

        self._text = tk.Text(
            self,
            wrap="word",
            bg=bg,
            fg=fg,
            font=(FONT_UI, 11),
            relief="flat",
            bd=0,
            cursor="arrow",
            state="disabled",
            insertbackground=t.cursor,
            selectbackground=sel_bg,
            padx=30,
            pady=20,
        )
        vsb = ttk.Scrollbar(self, orient="vertical", command=self._text.yview)
        self._text.config(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        self._text.pack(fill="both", expand=True)

        self._text.tag_configure(
            "h1",
            font=(FONT_UI, 22, "bold"),
            foreground=accent,
            spacing3=10,
            spacing1=20,
        )
        self._text.tag_configure(
            "h2", font=(FONT_UI, 17, "bold"), foreground=accent, spacing3=8, spacing1=16
        )
        self._text.tag_configure(
            "h3", font=(FONT_UI, 14, "bold"), foreground=accent, spacing3=6, spacing1=10
        )
        self._text.tag_configure(
            "h4", font=(FONT_UI, 12, "bold"), foreground=fg, spacing3=4, spacing1=8
        )
        self._text.tag_configure("h5", font=(
            FONT_UI, 11, "bold"), foreground=muted)
        self._text.tag_configure("h6", font=(
            FONT_UI, 10, "bold"), foreground=muted)

        self._text.tag_configure("bold", font=(FONT_UI, 11, "bold"))
        self._text.tag_configure("italic", font=(FONT_UI, 11, "italic"))
        self._text.tag_configure("bold_it", font=(FONT_UI, 11, "bold italic"))
        self._text.tag_configure(
            "strike", font=(FONT_UI, 11), overstrike=True, foreground=muted
        )
        self._text.tag_configure(
            "code",
            font=(FONT_MONO, 9),
            background=code_bg,
            foreground=getattr(
                t, "t_string", "#f38ba8" if dark else "#c0392b"),
            relief="flat",
        )
        self._text.tag_configure(
            "pre",
            font=(FONT_MONO, 9),
            background=code_bg,
            foreground=fg,
            lmargin1=24,
            lmargin2=24,
            rmargin=24,
            spacing1=8,
            spacing3=8,
        )
        self._text.tag_configure(
            "blockquote",
            foreground=muted,
            lmargin1=30,
            lmargin2=30,
            font=(FONT_UI, 11, "italic"),
            spacing1=4,
            spacing3=4,
        )
        self._text.tag_configure("li", lmargin1=20, lmargin2=36, spacing1=3)
        self._text.tag_configure("oli", lmargin1=20, lmargin2=42, spacing1=3)
        self._text.tag_configure(
            "hr", foreground=border_c, font=(FONT_UI, 5), spacing1=10, spacing3=10
        )
        self._text.tag_configure("link", foreground=link_fg, underline=True)
        self._text.tag_configure(
            "svg_info", foreground=muted, font=(FONT_MONO, 8))
        self._text.tag_configure("th", font=(
            FONT_UI, 10, "bold"), foreground=accent)
        self._text.tag_configure("td", font=(FONT_UI, 10))
        self._text.tag_configure("del", foreground=muted, overstrike=True)
        self._text.tag_configure("muted", foreground=muted, font=(FONT_UI, 10))

    def render_markdown(self, source: str) -> None:
        """Renderiza o texto Markdown no widget de texto."""
        self._text.config(state="normal")
        self._text.delete("1.0", "end")
        try:
            import markdown as mdlib  # type: ignore

            html = mdlib.markdown(
                source,
                extensions=[
                    "fenced_code",
                    "tables",
                    "toc",
                    "attr_list",
                    "def_list",
                    "footnotes",
                ],
            )
            self._render_html_rich(html)
        except ImportError:
            self._render_md_directly(source)
        self._text.config(state="disabled")

    def render_html(self, source: str) -> None:
        """Renderiza HTML no widget de texto (sem suporte completo)."""
        self._text.config(state="normal")
        self._text.delete("1.0", "end")
        self._render_html_rich(source)
        self._text.config(state="disabled")

    def render_svg(self, source: str) -> None:
        """Renderiza SVG no widget de texto (descrição textual)."""
        self._text.config(state="normal")
        self._text.delete("1.0", "end")
        self._text.insert(
            "end",
            "ℹ  SVG: visualização interna mostra o código-fonte.\n"
            'Use "Abrir no Navegador" para renderização visual.\n\n',
            "blockquote",
        )
        self._text.insert("end", source, "svg_info")
        self._text.config(state="disabled")

    def clear(self, msg: str = "") -> None:
        """Limpa o conteúdo do widget de texto."""
        self._text.config(state="normal")
        self._text.delete("1.0", "end")
        if msg:
            self._text.insert("end", msg, "blockquote")
        self._text.config(state="disabled")

    def _render_md_directly(self, source: str) -> None:
        """Render Markdown diretamente sem biblioteca externa."""
        lines = source.splitlines(keepends=True)
        in_code = False
        code_buf: list = []
        ol_counter = 0
        for line in lines:
            if line.startswith("```"):
                if not in_code:
                    in_code = True
                    code_buf = []
                else:
                    in_code = False
                    self._text.insert("end", "".join(code_buf) + "\n", "pre")
                continue
            if in_code:
                code_buf.append(line)
                continue
            s = line.rstrip("\n\r")
            m = re.match(r"^(#{1,6})\s+(.*)", s)
            if m:
                level = len(m.group(1))
                self._insert_inline(m.group(2) + "\n", f"h{level}")
                ol_counter = 0
                continue
            if re.match(r"^(?:-{3,}|_{3,}|\*{3,})$", s):
                self._text.insert("end", "-" * 64 + "\n", "hr")
                ol_counter = 0
                continue
            m = re.match(r"^> (.+)", s)
            if m:
                self._insert_inline(m.group(1) + "\n", "blockquote")
                continue
            m = re.match(r"^(\d+)\. (.+)", s)
            if m:
                ol_counter += 1
                self._text.insert("end", f"  {ol_counter}. ", "oli")
                self._insert_inline(m.group(2) + "\n", "oli")
                continue
            m = re.match(r"^[*\-+] (.+)", s)
            if m:
                self._text.insert("end", "  - ", "li")
                self._insert_inline(m.group(1) + "\n", "li")
                continue
            if s:
                self._insert_inline(s + "\n")
            else:
                self._text.insert("end", "\n")

    def _insert_inline(self, text: str, base: str = "") -> None:
        """Insere texto com formatação inline (bold, italic, code, links)."""
        pattern = re.compile(
            r"(`[^`]+`)"
            r"|(\*\*\*[^*]+\*\*\*)"
            r"|(\*\*[^*]+\*\*)"
            r"|(\*[^*]+\*)"
            r"|(~~[^~]+~~)"
            r"|(\[[^\]]+\]\([^\)]+\))"
        )
        parts = pattern.split(text)
        for p in parts:
            if not p:
                continue
            if p.startswith("`") and p.endswith("`"):
                tags = ("code", base) if base else ("code",)
                self._text.insert("end", p[1:-1], tags)
            elif p.startswith("***") and p.endswith("***"):
                tags = ("bold_it", base) if base else ("bold_it",)
                self._text.insert("end", p[3:-3], tags)
            elif p.startswith("**") and p.endswith("**"):
                tags = ("bold", base) if base else ("bold",)
                self._text.insert("end", p[2:-2], tags)
            elif p.startswith("*") and p.endswith("*") and len(p) > 2:
                tags = ("italic", base) if base else ("italic",)
                self._text.insert("end", p[1:-1], tags)
            elif p.startswith("~~") and p.endswith("~~"):
                tags = ("del", base) if base else ("del",)
                self._text.insert("end", p[2:-2], tags)
            elif p.startswith("["):
                m = _LINK_RE.match(p)
                if m:
                    self._text.insert("end", m.group(1), "link")
                else:
                    self._text.insert("end", p, (base,) if base else ())
            else:
                self._text.insert("end", p, (base,) if base else ())

    def _render_html_rich(self, html: str) -> None:
        """Converte HTML para inserções no tk.Text com tags de formatação.

        Usa tokenização por sentinela \x00TYPE\x00value\x01 para blocos
        estruturais; texto entre tokens é renderizado como parágrafo com
        formatação inline. Corrige o bug do 'not tok' que descartava
        texto normal entre tokens.
        """
        html = re.sub(r"<head[^>]*>.*?</head>", "",
                      html, flags=re.DOTALL | re.I)
        html = re.sub(r"<script[^>]*>.*?</script>",
                      "", html, flags=re.DOTALL | re.I)
        html = re.sub(r"<style[^>]*>.*?</style>", "",
                      html, flags=re.DOTALL | re.I)

        for i in range(1, 7):
            html = re.sub(
                rf"<h{i}[^>]*>(.*?)</h{i}>",
                lambda m, l=i: f"\n\x00H{l}\x00{_strip_html(m.group(1)).strip()}\x01\n",
                html,
                flags=re.DOTALL | re.I,
            )

        html = re.sub(
            r"<pre[^>]*><code[^>]*>(.*?)</code></pre>|<pre[^>]*>(.*?)</pre>",
            lambda m: (
                f"\n\x00PRE\x00{_strip_html(m.group(1) or m.group(2))}\x01\n"),
            html,
            flags=re.DOTALL | re.I,
        )
        html = re.sub(
            r"<blockquote[^>]*>(.*?)</blockquote>",
            lambda m: f"\n\x00BQ\x00{_strip_html(m.group(1)).strip()}\x01\n",
            html,
            flags=re.DOTALL | re.I,
        )

        def _table_to_text(m: re.Match) -> str:
            inner = m.group(1)
            rows = re.findall(r"<tr[^>]*>(.*?)</tr>", inner, re.DOTALL | re.I)
            parts = ["\n\x00HR\x00\x01"]
            for row in rows:
                cells = re.findall(
                    r"<t[hd][^>]*>(.*?)</t[hd]>", row, re.DOTALL | re.I)
                line = "  │  ".join(_strip_html(c).strip() for c in cells)
                is_th = bool(re.search(r"<th", row, re.I))
                parts.append(f"\x00{'TH' if is_th else 'TD'}\x00{line}\x01")
            parts.append("\x00HR\x00\x01\n")
            return "\n".join(parts)

        html = re.sub(
            r"<table[^>]*>(.*?)</table>", _table_to_text, html, flags=re.DOTALL | re.I
        )

        def _process_ol(m: re.Match) -> str:
            counter = [0]

            def _ol_item(mi: re.Match) -> str:
                counter[0] += 1
                return (
                    f"\n\x00OLI\x00{counter[0]}. {_strip_html(mi.group(1)).strip()}\x01"
                )

            return (
                re.sub(
                    r"<li[^>]*>(.*?)</li>", _ol_item, m.group(1), flags=re.DOTALL | re.I
                )
                + "\n"
            )

        html = re.sub(r"<ol[^>]*>(.*?)</ol>", _process_ol,
                      html, flags=re.DOTALL | re.I)
        html = re.sub(
            r"<li[^>]*>(.*?)</li>",
            lambda m: f"\n\x00LI\x00{_strip_html(m.group(1)).strip()}\x01",
            html,
            flags=re.DOTALL | re.I,
        )

        html = re.sub(r"<hr[^>]*/?>", "\n\x00HR\x00\x01\n", html, flags=re.I)
        html = re.sub(r"<br[^>]*/?>", "\n", html, flags=re.I)
        html = re.sub(
            r"<strong[^>]*>(.*?)</strong>|<b[^>]*>(.*?)</b>",
            lambda m: f"\x00BOLD\x00{m.group(1) or m.group(2)}\x01",
            html,
            flags=re.DOTALL | re.I,
        )
        html = re.sub(
            r"<em[^>]*>(.*?)</em>|<i[^>]*>(.*?)</i>",
            lambda m: f"\x00IT\x00{m.group(1) or m.group(2)}\x01",
            html,
            flags=re.DOTALL | re.I,
        )
        html = re.sub(
            r"<del[^>]*>(.*?)</del>|<s[^>]*>(.*?)</s>",
            lambda m: f"\x00DEL\x00{m.group(1) or m.group(2)}\x01",
            html,
            flags=re.DOTALL | re.I,
        )
        html = re.sub(
            r"<code[^>]*>(.*?)</code>",
            lambda m: f"\x00CODE\x00{_strip_html(m.group(1))}\x01",
            html,
            flags=re.DOTALL | re.I,
        )
        html = re.sub(
            r'<a[^>]*href=["\']([^"\']*)["\'][^>]*>(.*?)</a>',
            lambda m: f"\x00LINK\x00{_strip_html(m.group(2))}\x01",
            html,
            flags=re.DOTALL | re.I,
        )

        html = _HTML_TAG_RE.sub("", html)
        html = _HTML_ENTITY_RE.sub(
            lambda m: _ENTITIES.get(m.group(1), m.group(0)), html
        )
        html = re.sub(r"&#(\d+);", lambda m: chr(int(m.group(1))), html)
        html = re.sub(r"&#x([0-9a-fA-F]+);",
                      lambda m: chr(int(m.group(1), 16)), html)

        tokens = re.split(
            r"\x00([A-Z0-9]+)\x00(.*?)\x01", html, flags=re.DOTALL)
        idx = 0
        while idx < len(tokens):
            plain = tokens[idx]
            if plain:
                plain = re.sub(r"\n{3,}", "\n\n", plain)
                parts = re.split(r"(\n\n+)", plain)
                for part in parts:
                    if part.startswith("\n"):
                        self._text.insert("end", "\n")
                    elif part.strip():
                        self._insert_inline(part.rstrip("\n"))
                        self._text.insert("end", "\n")
            idx += 1
            if idx >= len(tokens):
                break

            kind = tokens[idx]
            idx += 1
            val = tokens[idx]
            idx += 1

            if kind.startswith("H") and kind[1:].isdigit():
                tag = f"h{kind[1]}"
                self._text.insert("end", val.strip() + "\n", tag)
            elif kind == "PRE":
                self._text.insert("end", val + "\n", "pre")
            elif kind == "BQ":
                self._insert_inline(val.strip() + "\n", "blockquote")
            elif kind == "LI":
                self._text.insert("end", "  • ", "li")
                self._insert_inline(val.strip() + "\n", "li")
            elif kind == "OLI":
                self._text.insert("end", f"  {val.strip()}\n", "oli")
            elif kind == "TH":
                self._text.insert("end", val.strip() + "\n", "th")
            elif kind == "TD":
                self._text.insert("end", val.strip() + "\n", "td")
            elif kind == "HR":
                self._text.insert("end", "─" * 60 + "\n", "hr")
            elif kind == "BOLD":
                self._insert_inline(_strip_html(val), "bold")
            elif kind == "IT":
                self._insert_inline(_strip_html(val), "italic")
            elif kind == "DEL":
                self._text.insert("end", _strip_html(val), "del")
            elif kind == "CODE":
                self._text.insert("end", val, "code")
            elif kind == "LINK":
                self._text.insert("end", val.strip(), "link")
            else:
                self._insert_inline(val)


class _WebRenderer(tk.Frame):
    """Renderizador usando tkinterweb - suporta CSS/JS real."""

    def __init__(self, parent: tk.Widget, tmgr) -> None:
        """Inicializa o renderer HTML baseado em tkinterweb."""
        t = tmgr.theme
        super().__init__(parent, bg=t.editor_bg)
        self._tmgr = tmgr
        self._file_path: Path | None = None
        self._frame = _load_tkweb().HtmlFrame(
            self,
            messages_enabled=False,
            vertical_scrollbar=True,
            horizontal_scrollbar=False,
        )
        self._frame.pack(fill="both", expand=True)

    def set_file_path(self, path: Path | None) -> None:
        """Informa o caminho do arquivo sendo renderizado (para resolver recursos relativos)."""
        self._file_path = path

    def _load_html_string(self, html: str) -> None:
        """Carrega HTML a partir de string, injetando CSS e resolvendo base_url se possível."""
        styled = _inject_style(html, self._tmgr)
        if self._file_path:
            base = self._file_path.parent.as_uri() + "/"
            with contextlib.suppress(Exception):
                self._frame.load_html(styled, base_url=base)
                return
        self._frame.load_html(styled)

    def _load_file_via_server(self, path: Path) -> bool:
        """Carrega arquivo HTML diretamente via servidor HTTP local.

        Isso garante que CSS, imagens e fontes com caminhos relativos
        sejam resolvidos corretamente - sem depender de base_url.

        Returns:
            True se conseguiu usar o servidor, False se deve usar fallback de string.
        """
        with contextlib.suppress(Exception):
            port = _start_server(str(path.parent))
            url = f"http://127.0.0.1:{port}/{path.name}"
            load_fn = getattr(self._frame, "load_url", None)
            if load_fn:
                load_fn(url)
                return True
            load_fn = getattr(self._frame, "load_website", None)
            if load_fn:
                load_fn(url)
                return True
        return False

    def render_markdown(self, source: str) -> None:
        """Converte Markdown para HTML e renderiza via tkinterweb."""
        try:
            import markdown as mdlib  # type: ignore

            html = mdlib.markdown(
                source,
                extensions=[
                    "fenced_code",
                    "tables",
                    "toc",
                    "attr_list",
                    "def_list",
                    "footnotes",
                ],
            )
        except ImportError:
            html = markdown_to_html(source)
        tv = _theme_vars(self._tmgr)
        full = _TEMPLATE.format(
            title="Preview", body=html, extra_head="", extra_scripts="", **tv
        )
        self._load_html_string(full)

    def render_html(self, source: str, from_path: Path | None = None) -> None:
        """Renderiza HTML no widget tkinterweb.

        Se ``from_path`` estiver disponível, usa o servidor HTTP local para
        que recursos relativos (CSS externo, imagens) sejam carregados.
        """
        path = from_path or self._file_path
        if path and path.exists() and self._load_file_via_server(path):
            return
        self._load_html_string(source)

    def render_svg(self, source: str) -> None:
        """Embute SVG em HTML e renderiza via tkinterweb."""
        tv = _theme_vars(self._tmgr)
        body = f'<div style="text-align:center;padding:2rem">{source}</div>'
        full = _TEMPLATE.format(
            title="SVG Preview", body=body, extra_head="", extra_scripts="", **tv
        )
        self._load_html_string(full)

    def clear(self, msg: str = "") -> None:
        """Limpa o painel tkinterweb."""
        tv = _theme_vars(self._tmgr)
        body = f'<p style="color:{tv["muted"]}">{msg}</p>' if msg else ""
        full = _TEMPLATE.format(
            title="", body=body, extra_head="", extra_scripts="", **tv
        )
        self._frame.load_html(full)


def _render_to_browser(path: Path, tmgr=None) -> str | None:
    suffix = path.suffix.lower()
    if suffix not in (".html", ".htm", ".xhtml", ".md", ".markdown", ".svg"):
        return _t("render.unsupported")

    tv = _theme_vars(tmgr)
    base_dir = path.parent
    content = path.read_text(encoding="utf-8", errors="replace")

    if suffix in (".md", ".markdown"):
        try:
            import markdown as mdlib  # type: ignore

            body = mdlib.markdown(
                content,
                extensions=["fenced_code", "tables",
                            "toc", "attr_list", "footnotes"],
            )
        except ImportError:
            body = markdown_to_html(content)
        tmp = base_dir / f"__codis_preview_{path.stem}.html"
        tmp.write_text(
            _TEMPLATE.format(
                title=path.stem, body=body, extra_head="", extra_scripts="", **tv
            ),
            encoding="utf-8",
        )
        url = tmp.as_uri()
        threading.Thread(
            target=lambda: (time.sleep(120), tmp.unlink()
                            if tmp.exists() else None),
            daemon=True,
        ).start()
    elif suffix == ".svg":
        body = f'<div style="text-align:center;padding:2rem">{content}</div>'
        tmp = base_dir / f"__codis_preview_{path.stem}.html"
        tmp.write_text(
            _TEMPLATE.format(
                title=path.stem, body=body, extra_head="", extra_scripts="", **tv
            ),
            encoding="utf-8",
        )
        url = tmp.as_uri()
        threading.Thread(
            target=lambda: (time.sleep(120), tmp.unlink()
                            if tmp.exists() else None),
            daemon=True,
        ).start()
    else:
        port = _start_server(str(base_dir))
        url = f"http://127.0.0.1:{port}/{path.name}"

    try:
        webbrowser.open(url)
        return None
    except OSError as exc:
        return _t("render.browser_fail").format(err=exc)


class RenderPanel(tk.Frame):
    """Painel de Preview nativo com suporte a HTML, Markdown e SVG."""

    _SUPPORTED = {".html", ".htm", ".xhtml", ".md", ".markdown", ".svg"}

    def __init__(self, parent: tk.Widget, tmgr, cfg) -> None:
        """Inicializa o painel de renderização unificado."""
        t = tmgr.theme
        super().__init__(parent, bg=t.panel_bg)
        self._tmgr = tmgr
        self._cfg = cfg
        self._auto_var = tk.BooleanVar(value=False)
        self._cur_path: Path | None = None
        self._get_selection_cb: Callable | None = None
        self._renderer: _TextRenderer | _WebRenderer | None = None
        self._build()

    def _build(self) -> None:
        t = self._tmgr.theme

        for child in self.winfo_children():
            with contextlib.suppress(tk.TclError):
                child.destroy()

        tb = tk.Frame(self, bg=t.panel_bg)
        tb.pack(fill="x", padx=6, pady=(6, 2))

        engine = "tkinterweb" if _TKWEB else "Text"
        tk.Label(
            tb,
            text=f"🌐  {_t('render.title', 'Preview')}",
            bg=t.panel_bg,
            fg=t.fg,
            font=(FONT_UI, 10, "bold"),
        ).pack(side="left")
        tk.Label(
            tb, text=f"[{engine}]", bg=t.panel_bg, fg=t.fg_muted, font=(FONT_UI, 7)
        ).pack(side="left", padx=4)

        self._refresh_btn = tk.Label(
            tb,
            text=_t("render.refresh", "↻ Atualizar"),
            bg=t.input_bg,
            fg=t.fg,
            font=(FONT_UI, 8),
            cursor="hand2",
            padx=6,
            pady=2,
        )
        self._refresh_btn.pack(side="right", padx=2)
        self._refresh_btn.bind("<Button-1>", lambda _: self._do_internal())

        self._file_lbl = tk.Label(
            self,
            text=_t("render.no_file", "Nenhum arquivo"),
            bg=t.panel_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 8),
            anchor="w",
            wraplength=230,
            justify="left",
        )
        self._file_lbl.pack(fill="x", padx=8, pady=(4, 2))

        btn_row = tk.Frame(self, bg=t.panel_bg)
        btn_row.pack(fill="x", padx=8, pady=(0, 2))

        def _btn(parent, text, cmd, primary=False):
            bg = t.accent if primary else t.input_bg
            fg_c = "#FFF" if primary else t.fg
            b = tk.Label(
                parent,
                text=text,
                bg=bg,
                fg=fg_c,
                font=(FONT_UI, 8, "bold" if primary else "normal"),
                cursor="hand2",
                padx=8,
                pady=4,
            )
            b.pack(side="left", padx=(0, 3), pady=2)
            b.bind("<Button-1>", lambda _, c=cmd: c())
            b.bind("<Enter>", lambda _, w=b, og=bg: w.config(bg=t.sel_bg))
            b.bind("<Leave>", lambda _, w=b, og=bg: w.config(bg=og))
            return b

        _btn(
            btn_row,
            _t("render.open_internal", "Preview Interno"),
            self._do_internal,
            primary=True,
        )
        _btn(
            btn_row, _t("render.open_browser",
                        "🌐 Abrir no Browser"), self._do_browser
        )
        _btn(
            btn_row, _t("webpreview.open_advanced", "🚀 Preview Web"),
            self._do_advanced, primary=True,
        )

        sel_row = tk.Frame(self, bg=t.panel_bg)
        sel_row.pack(fill="x", padx=8, pady=(0, 2))
        _btn(
            sel_row,
            _t("render.preview_sel", "✂ Preview da Seleção"),
            self._preview_selection,
        )

        tk.Checkbutton(
            self,
            text=_t("render.auto_preview", "Auto-preview ao salvar"),
            variable=self._auto_var,
            bg=t.panel_bg,
            fg=t.fg_muted,
            activebackground=t.panel_bg,
            selectcolor=t.sel_bg,
            font=(FONT_UI, 8),
        ).pack(anchor="w", padx=8, pady=(0, 4))

        self._status_lbl = tk.Label(
            self,
            text="",
            bg=t.panel_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 8),
            anchor="w",
            wraplength=230,
            justify="left",
        )
        self._status_lbl.pack(fill="x", padx=8)

        tk.Frame(self, bg=t.border, height=1).pack(fill="x", padx=4, pady=2)

        self._view_container = tk.Frame(self, bg=t.panel_bg)
        self._view_container.pack(fill="both", expand=True)
        self._placeholder_lbl = tk.Label(
            self._view_container,
            text=_t("render.no_file", "Nenhum arquivo\nsuportado aberto."),
            bg=t.panel_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 9),
            wraplength=220,
            justify="center",
        )
        self._placeholder_lbl.pack(expand=True)

        if _TKWEB:
            tk.Label(
                self,
                text=_t("render.tkweb_credit"),
                bg=t.panel_bg,
                fg=t.fg_muted,
                font=(FONT_UI, 7),
            ).pack(anchor="w", padx=8, pady=(0, 2))
        else:
            tk.Label(
                self,
                text="ℹ pip install tkinterweb - para preview HTML/CSS real",
                bg=t.panel_bg,
                fg=t.fg_muted,
                font=(FONT_UI, 7),
            ).pack(anchor="w", padx=8, pady=(0, 2))

    def notify_active_file(self, path) -> None:
        """Notifica o painel sobre o arquivo atualmente ativo no editor."""
        if path is None:
            self._cur_path = None
            self._file_lbl.config(text=_t("render.no_file", "Nenhum arquivo"))
            return
        p = Path(path) if not isinstance(path, Path) else path
        self._cur_path = p
        sup = "+" if p.suffix.lower() in self._SUPPORTED else "-"
        self._file_lbl.config(text=f"{sup}  {p.name}")
        if p.suffix.lower() in self._SUPPORTED and self._cfg.get(
            "render_internal", True
        ):
            self._do_internal()

    def notify_file_saved(self, path) -> None:
        """Notifica o painel que o arquivo foi salvo (aciona re-render)."""
        if not self._auto_var.get():
            return
        p = Path(path) if not isinstance(path, Path) else path
        if p.suffix.lower() in self._SUPPORTED:
            self._do_internal()

    def set_advanced_callback(self, cb: Callable[[Path | None], None] | None) -> None:
        """Registra o abridor do Preview Web (navegador embutido) do app."""
        self._advanced_cb = cb

    def _do_advanced(self) -> None:
        cb = getattr(self, "_advanced_cb", None)
        if cb is None:
            self._status(_t("webpreview.unavailable", "Preview Web indisponível."), error=True)
            return
        cb(self._cur_path)

    def set_selection_callback(self, cb) -> None:
        """Registra callback chamado quando o usuário seleciona texto no preview."""
        self._get_selection_cb = cb

    def _do_internal(self) -> None:
        p = self._cur_path
        if p is None:
            self._status(_t("render.no_file", "Nenhum arquivo"))
            return
        suffix = p.suffix.lower()
        if suffix not in self._SUPPORTED:
            self._show_placeholder(
                _t("render.unsupported", "Tipo não suportado"))
            return
        self._status(_t("render.rendering", "Renderizando..."))
        self.update_idletasks()
        try:
            source = p.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            self._status(str(exc), error=True)
            return
        view = self._get_renderer()
        if hasattr(view, "set_file_path"):
            view.set_file_path(p)
        if suffix in (".md", ".markdown"):
            view.render_markdown(source)
        elif suffix == ".svg":
            view.render_svg(source)
        else:
            if hasattr(view, "render_html"):
                import inspect

                sig = inspect.signature(view.render_html)
                if "from_path" in sig.parameters:
                    view.render_html(source, from_path=p)
                else:
                    view.render_html(source)
            else:
                view.render_html(source)
        self._status(p.name)

    def _do_browser(self) -> None:
        p = self._cur_path
        if p is None:
            self._status(_t("render.no_file", "Nenhum arquivo"), error=True)
            return
        if p.suffix.lower() not in self._SUPPORTED:
            self._status(
                _t("render.unsupported", "Tipo não suportado"), error=True)
            return
        try:
            err = _render_to_browser(p, self._tmgr)
            if err:
                self._status(err, error=True)
            else:
                self._status(_t("render.browser_ok", "Aberto no browser."))
        except OSError as exc:
            self._status(
                _t("render.browser_fail", "Erro: {err}").format(err=exc), error=True
            )

    def _preview_selection(self) -> None:
        if not self._get_selection_cb:
            return
        sel = self._get_selection_cb()
        if not sel or not sel.strip():
            self._status(
                _t("msg.no_selection", "Nenhum texto selecionado."), error=True
            )
            return
        view = self._get_renderer()
        if sel.lstrip().startswith("#") or re.search(r"\*\*|__|\n#{", sel):
            view.render_markdown(sel)
        else:
            view.render_html(sel)
        self._status(_t("render.preview_sel", "Preview da seleção"))

    def _get_renderer(self) -> _TextRenderer | _WebRenderer:
        if self._renderer is None:
            self._placeholder_lbl.pack_forget()
            try:
                if not _TKWEB:
                    raise ImportError("tkinterweb ausente")
                self._renderer = _WebRenderer(self._view_container, self._tmgr)
            except Exception:
                self._renderer = _TextRenderer(
                    self._view_container, self._tmgr)
            self._renderer.pack(fill="both", expand=True)
        return self._renderer

    def _show_placeholder(self, msg: str) -> None:
        if self._renderer:
            self._renderer.pack_forget()
            self._renderer = None
        self._placeholder_lbl.config(text=msg)
        self._placeholder_lbl.pack(expand=True)

    def _status(self, msg: str, error: bool = False) -> None:
        t = self._tmgr.theme
        fg = getattr(t, "err_fg", "#f38ba8") if error else t.fg_muted
        self._status_lbl.config(text=msg, fg=fg)

    def apply_theme(self, tmgr) -> None:
        """Aplica o tema ``t`` ao painel de renderização."""
        self._tmgr = tmgr
        self._renderer = None
        for w in self.winfo_children():
            w.destroy()
        self.config(bg=tmgr.theme.panel_bg)
        self._build()
