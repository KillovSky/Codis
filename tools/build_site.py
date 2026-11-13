#!/usr/bin/env python3
"""Gera o site de documentação (docs/*.html) a partir dos arquivos Markdown.

Fontes:
  - wiki/*.md            -> guias (uma página HTML por arquivo; wiki/Home.md vira docs/documentacao.html)
  - CHANGELOG/ROADMAP/CONTRIBUTING/SECURITY/AUTHORS.md (raiz) -> páginas do projeto
A navegação lateral é lida de wiki/Home.md (grupos e ordem). docs/index.html é a página inicial
escrita à mão e NÃO é sobrescrita.

Uso:
    pip install -r tools/requirements-docs.txt
    python tools/build_site.py            # gera/atualiza docs/
    python tools/build_site.py --check    # falha se docs/ estiver desatualizado (para CI)
"""
from __future__ import annotations

import html
import json
import re
import sys
import tempfile
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from markdown_it import MarkdownIt

ROOT = Path(__file__).resolve().parent.parent
WIKI = ROOT / "wiki"
OUT = ROOT / "docs"
SITE = "https://killovsky.github.io/Codis/"
REPO = "https://github.com/KillovSky/Codis"
RAW = "https://raw.githubusercontent.com/KillovSky/Codis/main/"
PROJECT_DOCS = {  # arquivo na raiz -> (slug, rótulo)
    "CHANGELOG.md": ("changelog", "Changelog"),
    "ROADMAP.md": ("roadmap", "Roadmap"),
    "CONTRIBUTING.md": ("contributing", "Contribuindo"),
    "SECURITY.md": ("security", "Segurança"),
    "AUTHORS.md": ("authors", "Autores"),
}
HUB_SLUG = "documentacao"
EMOJI = re.compile("[\U0001F000-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF\uFE0F\u200D\u2190-\u21FF\u2300-\u23FF\u25A0-\u25FF\u2726\u2736]+")


@dataclass
class Page:
    slug: str
    src: Path
    title: str
    group: str
    body: str = ""
    toc: list = None
    text: str = ""
    desc: str = ""
    headings: list = None


def md_engine() -> MarkdownIt:
    return MarkdownIt("commonmark", {"html": True, "linkify": False}).enable("table").enable("strikethrough")


def gh_slug(text: str) -> str:
    t = html.unescape(re.sub(r"<[^>]+>", "", text)).strip().lower()
    t = re.sub(r"[^\w\- ]", "", t, flags=re.U)
    return t.replace(" ", "-")


def clean_title(t: str) -> str:
    return re.sub(r"\s+", " ", EMOJI.sub("", t)).strip()


def parse_groups() -> list[tuple[str, list[tuple[str, str]]]]:
    """Lê grupos e ordem de wiki/Home.md -> [(grupo, [(slug, rótulo)])]."""
    groups, cur = [], None
    for line in (WIKI / "Home.md").read_text(encoding="utf-8").splitlines():
        if line.startswith("## "):
            name = clean_title(line[3:])
            cur = (name, []) if name not in ("Visão Geral", "Licença") else None
            if cur:
                groups.append(cur)
        elif cur is not None:
            m = re.match(r"\|[^|]*\|\s*\[([^\]]+)\]\(([a-z0-9-]+)\.md\)", line)
            if m:
                cur[1].append((m.group(2), m.group(1)))
    return groups


def rewrite_href(href: str, wiki_slugs: set[str], origin_in_wiki: bool) -> tuple[str, bool]:
    """Converte links de .md para páginas do site / GitHub. Retorna (href, externo)."""
    if re.match(r"(https?:|mailto:|tel:|#)", href):
        return href, href.startswith("http")
    path, _, frag = href.partition("#")
    frag = f"#{frag}" if frag else ""
    norm = path
    while norm.startswith("../"):
        norm = norm[3:]
    if norm.startswith("./"):
        norm = norm[2:]
    if origin_in_wiki and not path.startswith("../") and "/" not in norm:
        norm = f"wiki/{norm}"
    elif not origin_in_wiki and not path.startswith(("../", "wiki/")) and False:
        pass
    m = re.fullmatch(r"wiki/([A-Za-z0-9_-]+)\.md", norm)
    if m:
        s = m.group(1)
        if s == "Home":
            return f"{HUB_SLUG}.html{frag}", False
        if s.lower() in wiki_slugs:
            return f"{s.lower()}.html{frag}", False
    if norm in PROJECT_DOCS:
        return f"{PROJECT_DOCS[norm][0]}.html{frag}", False
    if norm == "README.md":
        return f"index.html{frag}", False
    if norm.startswith("docs/") and norm.endswith(".html"):
        return f"{norm[5:]}{frag}", False
    if not norm or norm.startswith("/"):
        return href, False
    kind = "tree" if norm.endswith("/") else "blob"
    return f"{REPO}/{kind}/main/{norm}{frag}", True


def render(page: Page, md: MarkdownIt, wiki_slugs: set[str], origin_in_wiki: bool) -> None:
    raw = page.src.read_text(encoding="utf-8")
    body = md.render(raw)

    def fix_attr(m: re.Match) -> str:
        attr, val = m.group(1), m.group(2)
        if attr == "href":
            new, ext = rewrite_href(html.unescape(val), wiki_slugs, origin_in_wiki)
            return f'href="{html.escape(new, quote=True)}"'
        if attr == "src" and not re.match(r"(https?:|data:)", val):
            return f'src="{RAW}{val.lstrip("./").replace("../", "")}"'
        return m.group(0)

    body = re.sub(r'\b(href|src)="([^"]*)"', fix_attr, body)
    body = re.sub(r'<a href="(https?://[^"]+)"', r'<a href="\1" target="_blank" rel="noopener"', body)
    seen: dict[str, int] = {}
    toc: list[tuple[int, str, str]] = []

    def head(m: re.Match) -> str:
        lvl, inner = int(m.group(1)), m.group(2)
        base = gh_slug(inner)
        n = seen.get(base, 0)
        seen[base] = n + 1
        sid = base if n == 0 else f"{base}-{n}"
        if lvl in (2, 3):
            toc.append((lvl, sid, clean_title(html.unescape(re.sub(r"<[^>]+>", "", inner)))))
        return f'<h{lvl} id="{sid}">{inner}<a class="anchor" href="#{sid}" aria-label="Link para esta seção">#</a></h{lvl}>'

    body = re.sub(r"<h([1-4])>(.*?)</h\1>", head, body, flags=re.S)
    body = re.sub(r"<table>", '<div class="table-wrap"><table>', body)
    body = body.replace("</table>", "</table></div>")
    page.body, page.toc = body, toc
    plain = html.unescape(re.sub(r"<[^>]+>", " ", re.sub(r"<pre>.*?</pre>", " ", body, flags=re.S)))
    plain = re.sub(r"\s+", " ", plain).strip()
    page.text = plain
    first_p = re.search(r"<p>(.*?)</p>", body, re.S)
    d = html.unescape(re.sub(r"<[^>]+>", "", first_p.group(1))) if first_p else plain
    page.desc = re.sub(r"\s+", " ", d).strip()[:200]
    page.headings = [t for _, _, t in toc]


def sidebar_html(groups, current: str, projects) -> str:
    out = ['<nav class="side" aria-label="Documentação">']
    out.append(f'<a class="side-home{" is-current" if current == HUB_SLUG else ""}" href="{HUB_SLUG}.html"{" aria-current=\"page\"" if current == HUB_SLUG else ""}>Índice da documentação</a>')
    for name, items in groups:
        out.append(f'<details class="side-group"{" open" if any(s == current for s, _ in items) else ""}><summary>{html.escape(name)}</summary><ul>')
        for slug, label in items:
            cur = slug == current
            out.append(f'<li><a href="{slug}.html"{" class=\"is-current\" aria-current=\"page\"" if cur else ""}>{html.escape(clean_title(label))}</a></li>')
        out.append("</ul></details>")
    out.append(f'<details class="side-group"{" open" if any(s == current for s, _ in projects) else ""}><summary>Projeto</summary><ul>')
    for slug, label in projects:
        cur = slug == current
        out.append(f'<li><a href="{slug}.html"{" class=\"is-current\" aria-current=\"page\"" if cur else ""}>{html.escape(label)}</a></li>')
    out.append("</ul></details></nav>")
    return "\n".join(out)


TEMPLATE = """<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0" />
<title>{title} · Codis</title>
<meta name="description" content="{desc}" />
<meta name="author" content="KillovSky" />
<meta name="color-scheme" content="dark" />
<meta name="theme-color" content="#080812" />
<link rel="canonical" href="{canon}" />
<meta property="og:type" content="article" />
<meta property="og:title" content="{title} · Codis" />
<meta property="og:description" content="{desc}" />
<meta property="og:url" content="{canon}" />
<meta property="og:image" content="{raw}.github/Banner.png" />
<meta name="twitter:card" content="summary_large_image" />
<link rel="stylesheet" href="shared.css" />
<link rel="stylesheet" href="docs.css" />
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E%3Ctext y='26' font-size='28'%3E%E2%9C%A6%3C/text%3E%3C/svg%3E" />
</head>
<body class="docs">
<a class="skip" href="#conteudo">Pular para o conteúdo</a>
<nav class="topbar" aria-label="Principal">
  <div class="nav-inner">
    <a class="nav-logo" href="index.html"><span class="star">✦</span> Codis <span class="version">v{version} · {codename}</span></a>
    <div class="nav-links">
      <a href="index.html#features">Recursos</a>
      <a href="{hub}.html"{hub_cur}>Documentação</a>
      <a href="ia.html">IA</a>
      <a href="plugins.html">Plugins</a>
      <a href="arquitetura.html">Arquitetura</a>
      <a href="primeiros-passos.html">Instalar</a>
      <a href="{repo}" target="_blank" rel="noopener" class="nav-cta">GitHub ↗</a>
    </div>
  </div>
</nav>
<div class="shell">
  <aside class="rail" id="rail">
    <div class="search" role="search">
      <label class="sr" for="q">Buscar na documentação</label>
      <input id="q" type="search" placeholder="Buscar na documentação ( / )" autocomplete="off" />
      <ul id="results" class="results" hidden></ul>
    </div>
    {sidebar}
  </aside>
  <main id="conteudo" class="doc" tabindex="-1">
    <button class="rail-toggle" type="button" aria-controls="rail" aria-expanded="false">☰ Menu da documentação</button>
    <article class="prose">
{body}
    </article>
    <footer class="pager">
{pager}
      <p class="edit"><a href="{edit}" target="_blank" rel="noopener">Editar esta página no GitHub</a> (<code>{srcpath}</code>)</p>
    </footer>
  </main>
  <aside class="toc" aria-label="Nesta página">
{toc}
  </aside>
</div>
<footer class="site-foot"><span>Codis v{version} · {codename} · MIT · © KillovSky</span><span><a href="{repo}" target="_blank" rel="noopener">GitHub</a> · <a href="changelog.html">Changelog</a> · <a href="roadmap.html">Roadmap</a></span></footer>
<script src="docs.js" defer></script>
</body>
</html>
"""


def build(out: Path) -> list[str]:
    facts_const = (ROOT / "src/constants.py").read_text(encoding="utf-8")
    version = re.search(r'APP_VERSION = "([^"]+)"', facts_const).group(1)
    codename = re.search(r'APP_CODENAME = "([^"]+)"', facts_const).group(1)
    groups = parse_groups()
    md = md_engine()
    wiki_files = {p.stem.lower(): p for p in WIKI.glob("*.md") if not p.name.startswith("_") and p.stem != "Home"}
    group_of = {s: g for g, items in groups for s, _ in items}
    unlisted = sorted(set(wiki_files) - set(group_of))
    if unlisted:
        raise SystemExit(f"Páginas da wiki fora de wiki/Home.md: {unlisted}")
    missing = sorted(set(group_of) - set(wiki_files))
    if missing:
        raise SystemExit(f"Home.md aponta para páginas inexistentes: {missing}")
    pages: list[Page] = []
    order = [s for _, items in groups for s, _ in items]
    for slug in order:
        pages.append(Page(slug, wiki_files[slug], "", group_of[slug]))
    hub = Page(HUB_SLUG, WIKI / "Home.md", "Documentação", "")
    proj = [Page(s, ROOT / f, l, "Projeto") for f, (s, l) in PROJECT_DOCS.items() if (ROOT / f).exists()]
    wiki_slugs = set(wiki_files)
    for pg in pages + [hub] + proj:
        render(pg, md, wiki_slugs, origin_in_wiki=pg.src.parent == WIKI)
        h1 = re.search(r"<h1[^>]*>(.*?)<a class=\"anchor\"", pg.body, re.S)
        pg.title = pg.title or (clean_title(html.unescape(re.sub(r"<[^>]+>", "", h1.group(1)))) if h1 else pg.slug)
    projects = [(p.slug, p.title) for p in proj]
    seq = pages + [hub] if False else [hub] + pages + proj
    out.mkdir(exist_ok=True)
    written: list[str] = []
    today = date.today().isoformat()
    for i, pg in enumerate(seq):
        prev_p = seq[i - 1] if i > 0 else None
        next_p = seq[i + 1] if i + 1 < len(seq) else None
        pager = ['      <nav class="pn" aria-label="Páginas vizinhas">']
        pager.append(f'        <a class="prev" href="{prev_p.slug}.html"><small>Anterior</small>{html.escape(prev_p.title)}</a>' if prev_p else "        <span></span>")
        pager.append(f'        <a class="next" href="{next_p.slug}.html"><small>Próxima</small>{html.escape(next_p.title)}</a>' if next_p else "        <span></span>")
        pager.append("      </nav>")
        toc = "    <p class=\"toc-t\">Nesta página</p>\n    <ul>" + "".join(
            f'<li class="l{lvl}"><a href="#{sid}">{html.escape(t)}</a></li>' for lvl, sid, t in pg.toc) + "</ul>" if pg.toc else ""
        src_rel = pg.src.relative_to(ROOT).as_posix()
        page_html = TEMPLATE.format(
            title=html.escape(pg.title), desc=html.escape(pg.desc, quote=True), canon=f"{SITE}{pg.slug}.html",
            raw=RAW, version=version, codename=codename, hub=HUB_SLUG, repo=REPO,
            hub_cur=' aria-current="page"' if pg.slug == HUB_SLUG else "",
            sidebar=sidebar_html(groups, pg.slug, projects), body=pg.body, pager="\n".join(pager), toc=toc,
            edit=f"{REPO}/blob/main/{src_rel}", srcpath=src_rel, today=today,
        )
        (out / f"{pg.slug}.html").write_text(page_html, encoding="utf-8")
        written.append(f"{pg.slug}.html")
    # índice de busca
    index = [{"t": p.title, "u": f"{p.slug}.html", "g": p.group, "h": p.headings, "x": p.text[:5000]} for p in seq]
    (out / "search-index.json").write_text(json.dumps(index, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    written.append("search-index.json")
    # redirecionamentos das URLs antigas
    for old, new in {"ci-cd.html": "builds.html#github-actions", "instalacao.html": "primeiros-passos.html"}.items():
        (out / old).write_text(
            f'<!DOCTYPE html><html lang="pt-BR"><head><meta charset="UTF-8"><title>Redirecionando…</title>'
            f'<meta http-equiv="refresh" content="0; url={new}"><link rel="canonical" href="{SITE}{new}">'
            f'<meta name="robots" content="noindex"></head><body><p>Esta página mudou: <a href="{new}">{new}</a>.</p></body></html>\n',
            encoding="utf-8")
        written.append(old)
    # 404
    (out / "404.html").write_text(TEMPLATE.format(
        title="Página não encontrada", desc="Página não encontrada na documentação do Codis.", canon=f"{SITE}404.html",
        raw=RAW, version=version, codename=codename, hub=HUB_SLUG, repo=REPO, hub_cur="",
        sidebar=sidebar_html(groups, "", projects),
        body='<h1 id="404">Página não encontrada</h1><p>O endereço não existe (ou mudou de lugar). Use a busca ao lado, volte ao <a href="index.html">início</a> ou abra o <a href="documentacao.html">índice da documentação</a>.</p>',
        pager="", toc="", edit=REPO, srcpath="-", today=today), encoding="utf-8")
    written.append("404.html")
    # sitemap/robots/.nojekyll
    urls = ["index.html"] + [f"{p.slug}.html" for p in seq]
    sm = ['<?xml version="1.0" encoding="UTF-8"?>', '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    sm += [f"  <url><loc>{SITE}{u}</loc></url>" for u in urls] + ["</urlset>"]
    (out / "sitemap.xml").write_text("\n".join(sm) + "\n", encoding="utf-8")
    (out / "robots.txt").write_text(f"User-agent: *\nAllow: /\nSitemap: {SITE}sitemap.xml\n", encoding="utf-8")
    (out / ".nojekyll").write_text("", encoding="utf-8")
    written += ["sitemap.xml", "robots.txt", ".nojekyll"]
    return written


def main() -> int:
    if "--check" in sys.argv:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            files = build(tmp)
            stale = []
            for f in files:
                a, b = tmp / f, OUT / f
                if not b.exists():
                    stale.append(f"{f} (ausente)")
                    continue
                ta, tb = a.read_text(encoding="utf-8"), b.read_text(encoding="utf-8")
                if ta != tb:
                    stale.append(f)
            if stale:
                print("docs/ desatualizado; rode `python tools/build_site.py`:\n  " + "\n  ".join(stale))
                return 1
            print("docs/ está atualizado.")
            return 0
    files = build(OUT)
    print(f"{len(files)} arquivos gerados em docs/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
