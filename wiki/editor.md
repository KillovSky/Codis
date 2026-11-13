# ✏️ Editor

O núcleo do Codis é o `EditorPane` - um editor de texto rico construído sobre o widget `tk.Text`. Esta página descreve todas as funcionalidades disponíveis no editor.

---

## Syntax Highlighting

O destaque de sintaxe é baseado em expressões regulares compiladas em `src/syntax/patterns.py`. É importante distinguir dois níveis de suporte a linguagem no Codis:

- **Highlight de sintaxe real** (`LANG_RE`, 25 linguagens): JavaScript, TypeScript, Python, HTML, CSS, SCSS, Less, JSON, Markdown, YAML, TOML, INI, SQL, Shell, Rust, Go, C, C++, Java, Makefile, Dockerfile, GraphQL, XML, Vue e Svelte (estas duas últimas só acessíveis trocando a linguagem manualmente, sem detecção automática por extensão).
- **Reconhecimento por extensão sem highlight** (`EXT_LANG`, mais 23 linguagens): Kotlin, Swift, C#, F#, VB.NET, Ruby, PHP, Lua, Perl, R, Julia, Haskell, Elixir, Erlang, Scala, Clojure, CoffeeScript, Groovy, HCL/Terraform, Protocol Buffers, Nix, Batch e PowerShell - o Codis identifica corretamente a linguagem (ícone, nome de exibição na barra de status, formatador associado), mas o texto é exibido **sem coloração de sintaxe**, já que essas linguagens não têm uma entrada em `LANG_RE`.

No total são **48 linguagens reconhecidas por extensão**, das quais **25 têm highlight de sintaxe colorido**.

A linguagem é detectada automaticamente pela extensão do arquivo. Para alterar manualmente, clique no nome da linguagem na **Barra de Status**.

> Não confundir com o [KDocs](kdocs.md), que reconhece **27 linguagens diferentes** para fins de parsing de docstring/comentário - uma lista parcialmente distinta da de highlight, já que KDocs e o highlighter de sintaxe são sistemas independentes.

---

## Autocompletar

O autocomplete combina três fontes:

1. **Palavras do arquivo atual** - análise lexical do buffer.
2. **Keywords da linguagem** - lista curada por linguagem (definida em `src/constants.py`).
3. **Símbolos LSP** - funções, classes e variáveis do servidor de linguagem ativo.

**Configuração:**

| Opção | Padrão | Descrição |
|-------|--------|-----------|
| `autocomplete` | `true` | Ativa/desativa |
| `autocomplete_delay` | `200` ms | Tempo de espera após digitação |
| `autocomplete_min_chars` | `1` | Mínimo de caracteres para disparar |
| `autocomplete_max_items` | `24` | Máximo de sugestões exibidas |

A lista de autocomplete é navegável por `↑` / `↓` e fechada com `Esc`. `Tab` ou `Enter` confirma a sugestão selecionada.

---

## Snippets

Snippets são trechos de código reutilizáveis armazenados em `~/.codis/snippets/`. O painel de Snippets (`Ctrl+Shift+J` ou Activity Bar → ✂️) permite gerenciar e inserir snippets.

Ao digitar o prefixo de um snippet e pressionar `Tab`, o snippet é expandido no cursor. Snippets suportam variáveis `${1}`, `${2}` para navegação entre campos de edição.

Ver [Recursos Avançados → Snippets](recursos-avancados.md#snippets) para detalhes sobre criação.

---

## Fechamento Automático de Pares

O editor fecha automaticamente os pares:

| Abre | Fecha |
|------|-------|
| `(` | `)` |
| `[` | `]` |
| `{` | `}` |
| `"` | `"` |
| `'` | `'` |
| `` ` `` | `` ` `` |

Pressionar o caractere de fechamento sobre um par existente apenas move o cursor para fora, sem duplicar.

---

## Minimapa

O minimapa é uma representação em miniatura do código à direita do editor.

- **Largura:** 110 px (configurável via `minimap_width`)
- **Escala:** 1:3 (configurável via `minimap_scale`)
- Marcadores coloridos de diagnósticos de lint são sobrepostos no minimapa.
- Para ocultar: `Exibir → Minimapa` ou `Ctrl+,` → `minimap: false`.

---

## Numeração de Linhas

Exibida à esquerda do editor. Pode exibir números absolutos ou **relativos** (estilo Vim):

- `line_numbers: true` - exibe números de linha (padrão)
- `relative_numbers: true` - exibe números relativos ao cursor

---

## Dobramento de Código (Code Folding)

Regiões de código definidas por indentação ou delimitadores de bloco podem ser dobradas/desdobradas. Clique no triângulo `▸` na margem esquerda ou use `Ctrl+Shift+[` / `Ctrl+Shift+]` (se configurado via plugin).

- `code_folding: true` - ativa o dobramento (padrão).

---

## Sticky Scroll

Ao rolar o código, os cabeçalhos de escopo (funções, classes, loops) ficam "colados" no topo do editor até que o bloco inteiro saia da tela.

- `sticky_scroll: true` - ativa (padrão).

---

## Highlight da Linha Atual

A linha onde o cursor se encontra recebe um destaque visual diferenciado.

- `highlight_current_line: true` - ativa (padrão).

---

## Correspondência de Parênteses

Ao posicionar o cursor próximo a um parêntese, colchete ou chave, o par correspondente é destacado.

- `bracket_matching: true` - ativa (padrão).
- Para **navegar** ao par correspondente: `Ir → Colchete Correspondente`.

---

## Guias de Indentação

Linhas verticais sutis indicam os níveis de indentação do código.

- `indent_guides: true` - ativa (padrão).

---

## Renderização de Espaços em Branco

Controla como espaços e tabs são exibidos:

- `render_whitespace: "none"` - não exibe (padrão)
- `render_whitespace: "boundary"` - exibe apenas nos limites de palavras
- `render_whitespace: "all"` - exibe todos os espaços e tabs

---

## Preview de Cores

Quando `color_preview: true` (padrão), o editor exibe uma pequena amostra colorida ao lado de valores de cor no código (ex: `#4a9eff`, `rgb(255, 0, 0)`). Clique na amostra para abrir o **Color Picker** (`Ctrl+Shift+C`).

---

## Hints Embutidas (Inlay Hints)

Quando `inlay_hints: true`, o LSP pode injetar dicas de tipo diretamente no código, exibidas em fonte menor e cor diferente.

---

## Inline Blame (Git Blame)

Exibe a informação de `git blame` diretamente no editor:

- `inline_blame: false` - desativado por padrão
- `inline_blame_mode: "cursor"` - mostra blame apenas na linha do cursor
- `inline_blame_mode: "all"` - mostra blame em todas as linhas visíveis

---

## Modo de Sobrescrita

Pressione `Insert` para alternar entre **inserção** (padrão) e **sobrescrita** (substituição de caracteres à medida que digita).

---

## Modo Somente Leitura

Via `Ferramentas → Modo Somente Leitura`, o editor fica em modo read-only - o texto não pode ser editado. Ideal para revisar código sem risco de alterações acidentais.

---

## Formatação de Documento

`Shift+Alt+F` ou `Executar → Formatar Documento` invoca o formatador configurado para a linguagem ativa. A formatação chama a ferramenta CLI diretamente via subprocess (não usa o protocolo LSP):

| Linguagem | Ferramenta |
|-----------|-----------|
| Python | Black (preferido, `--line-length 120`) → autopep8 (fallback se Black ausente) |
| JavaScript / TypeScript | Prettier |
| JSON | Prettier |
| CSS / SCSS | Prettier |
| HTML | Prettier |
| Markdown | Prettier |

> Apenas essas linguagens têm formatação automática nativa. Go, Rust e demais linguagens não suportadas exibem um diálogo informando que não há formatador disponível para a linguagem - não há integração com `gofmt` ou `rustfmt` nesta versão. Se a ferramenta CLI (Prettier, Black ou autopep8) não estiver instalada no PATH, o mesmo diálogo é exibido.

---

## Manipulação de Linhas

Atalhos de manipulação de linhas disponíveis:

| Ação | Atalho |
|------|--------|
| Duplicar linha | `Ctrl+D` |
| Apagar linha | `Ctrl+Shift+K` |
| Mover linha para cima | `Alt+↑` |
| Mover linha para baixo | `Alt+↓` |
| Juntar linhas | via menu Editar |
| Ordenar linhas (A-Z) | via menu Editar |
| Ordenar linhas (Z-A) | via menu Editar |
| Remover linhas duplicadas | via menu Editar / Ferramentas |
| Remover espaços no fim | via menu Editar |

---

## Transformações de Texto

Via `Editar → Transformar`:

- **Maiúsculas** - converte seleção para UPPER CASE
- **Minúsculas** - converte seleção para lower case
- **Title Case** - Converte Seleção Para Title Case

---

## Zoom

| Ação | Atalho |
|------|--------|
| Aumentar zoom | `Ctrl+=` |
| Diminuir zoom | `Ctrl+-` |
| Resetar zoom (100%) | `Ctrl+0` |

O zoom atual é exibido na barra de status e pode ser ajustado clicando nele.

---

## Quebra de Linha

`Exibir → Quebra de Linha` alterna entre linha reta e quebra suave (`word_wrap`). Estado salvo em `config.json`.

---

## Arquivos Grandes

Para arquivos acima de 3 MB (configurável via `large_file_mb`), o Codis ativa um modo simplificado que desativa syntax highlighting e autocomplete para manter a performance.

---

## File Timeline (Histórico de Versões)

`Exibir → Painéis → File Timeline` abre o histórico de versões automáticas do arquivo atual. O Codis salva snapshots em `~/.codis/backups/` enquanto o arquivo está aberto, e é possível comparar (via diff) e restaurar qualquer versão anterior com um clique - sem depender do Git. Detalhes completos do mecanismo de snapshot em [Backups, Lixeira & Arquivos Compactados](backups-lixeira-arquivos.md#file-timeline-srcuifiletimelinepy).
