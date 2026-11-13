# 🧠 Inteligência do Editor

Esta página documenta os recursos de produtividade do editor que vão além do realce de sintaxe básico: edição multi-cursor, dicas inteligentes, ferramentas de foco visual e os dois recursos **exclusivos do Codis** - Code Rhythm e Edit Heatmap, que não existem em nenhum outro editor mainstream.

> Para Syntax Highlighting, Autocomplete, Snippets, Minimap, Code Folding e demais fundamentos, ver [Editor](editor.md).

---

## 🖊️ Multi-Cursor (`src/editor/multi_cursor.py`)

Edição simultânea em múltiplos pontos do texto, no estilo VS Code / Sublime Text / JetBrains.

| Ação | Atalho |
|---|---|
| Adicionar cursor no ponto clicado | `Alt+Click` |
| Adicionar cursor acima/abaixo | `Ctrl+Alt+↑` / `Ctrl+Alt+↓` |
| Selecionar próxima ocorrência da palavra sob o cursor | `Ctrl+D` |
| Selecionar **todas** as ocorrências | `Ctrl+Shift+L` |
| Cancelar todos os cursores extras | `Escape` |

> **Importante:** `Ctrl+D` tem dois comportamentos diferentes dependendo do estado do Multi-Cursor. Com o Multi-Cursor **inativo** (estado padrão), `Ctrl+D` duplica a linha atual (veja [Editor](editor.md)). Assim que o Multi-Cursor é ativado, o mesmo atalho passa a selecionar a próxima ocorrência da palavra - o handler do Multi-Cursor intercepta o evento antes do handler de duplicar linha.

Internamente, cada cursor extra mantém sua própria "âncora" (um índice do Tk armazenado como tag). Ao digitar ou apagar texto, a ação é replicada em todos os cursores ativos simultaneamente. Os cursores extras são desenhados como marcas coloridas diretamente no widget `Text`, e um indicador na barra de status (`MultiCursorStatusIndicator`) mostra quantos cursores estão ativos no momento.

Pode ser ligado/desligado via **Ferramentas → Workspace & Editor → Multi-Cursor** ou `Ctrl+Alt+M`.

> No código-fonte existem dois binds tecnicamente distintos: `<Control-Alt-m>` (minúsculo) para o toggle do Multi-Cursor e `<Control-Alt-M>` (maiúsculo, ou seja, com Shift também pressionado) para abrir o Gerenciador de Processos. Tkinter trata essas sequências como eventos diferentes, mas a forma como o sistema operacional gera maiúscula/minúscula com `Alt` pressionado pode variar entre plataformas e layouts de teclado. Se `Ctrl+Alt+M` abrir o Gerenciador de Processos em vez de alternar o Multi-Cursor (ou vice-versa), use o menu **Ferramentas → Workspace & Editor → Multi-Cursor** como alternativa confiável.

---

## 🔭 Smart Selection (`src/editor/smart_selection.py`)

Expansão e contração de seleção semântica, equivalente ao "Expand/Shrink Selection" do VS Code.

| Ação | Atalho |
|---|---|
| Expandir seleção (palavra → expressão → linha → bloco → função → arquivo) | `Alt+Shift+→` ou `Ctrl+Shift+A` |
| Contrair para o nível anterior | `Alt+Shift+←` |

A expansão é feita por análise de tokens e delimitadores no texto puro - **não depende de AST**, então funciona em qualquer linguagem, mesmo sem suporte LSP. A contração restaura exatamente a seleção anterior à última expansão (não recalcula do zero).

---

## 🎯 Focus Block (`src/editor/focus_block.py`)

Escurece (dim) todas as linhas que **não** fazem parte do bloco estrutural atual sob o cursor - função, método, classe ou bloco de alto nível - guiando o olhar para o que está sendo editado agora.

- Detecta automaticamente o bloco via indentação + AST
- Aplica uma tag de "esmaecimento" nas linhas fora do bloco
- Atualiza em tempo real conforme o cursor se move
- Compatível com o syntax highlighting normal - sobrepõe a cor sem quebrar a renderização existente

Ativar/desativar: **Ferramentas → Modo Foco** (não confundir com o [Focus Mode](recursos-exclusivos.md#focus-mode) de tela cheia - Focus Block é um esmaecimento de linhas, não um modo de janela).

---

## 💡 Smart Tips (`src/editor/smart_tips.py`)

Dicas contextuais não intrusivas, baseadas em gatilhos automáticos que analisam o arquivo conforme você edita. Aparecem como balões flutuantes no canto inferior direito, com fade-in e auto-dismiss.

| Gatilho | O que detecta |
|---|---|
| Arquivo grande | Contagem de linhas acima do limite configurado |
| Complexidade | Funções/classes aninhadas profundamente |
| Convenção de nome | Variáveis com nome genérico (`x`, `tmp`, `data`...) |
| Dados sensíveis | Padrões que sugerem senhas/tokens em texto literal |
| Linhas longas | Excesso de linhas acima de 100 caracteres |
| Funções grandes | Funções com número excessivo de linhas |
| Imports duplicados | Imports repetidos no topo do arquivo |
| TODO acumulado | Volume alto de comentários `TODO`/`FIXME` |
| Trailing whitespace | Excesso de espaço em branco ao final de linhas |
| Encoding especial | Caracteres invisíveis/incomuns no arquivo |

Cada dica pode ser dispensada manualmente ou desabilitada globalmente em **Configurações**.

---

## 🔢 Complexity Lens (`src/editor/complexity_lens.py`)

Badge flutuante no canto superior direito do editor mostrando, em tempo real, a **complexidade ciclomática** (McCabe) da função sob o cursor.

| Faixa | Classificação | Cor |
|---|---|---|
| 1–5 | Simples | Verde |
| 6–10 | Moderada | Amarelo |
| 11–15 | Alta | Laranja |
| 16+ | Muito alta | Vermelho |

A análise é feita via AST, com debounce de 300ms após cada movimento de cursor - nunca bloqueia a interface. Disponível apenas para Python (a análise depende de AST nativa da linguagem).

---

## 💬 Sticky Note Gutter (`src/editor/sticky_note_gutter.py`)

Permite anexar notas de texto livre a linhas específicas do código, exibidas como ícones 💬 no gutter (barra de números de linha).

- Passar o mouse sobre o ícone mostra a nota em um tooltip rico
- Clicar no ícone abre um popup de edição multiline
- `Ctrl+Shift+N` adiciona/edita a nota na linha atual - **com o cursor dentro do editor de código**, este atalho é capturado pelo Sticky Note Gutter antes de chegar ao Scratchpad (veja nota abaixo)
- Notas são persistidas por arquivo (chave `sticky_notes` na configuração)
- Podem ser exportadas como comentários inline (`# NOTE: ...`)
- Se linhas forem inseridas/removidas, a nota "acompanha" a linha original corretamente

> **Colisão de atalho:** `Ctrl+Shift+N` também está vinculado à abertura do [Scratchpad](recursos-exclusivos.md). Como o bind do Sticky Note está no próprio widget de texto do editor (mais específico) e retorna `"break"`, ele intercepta o evento primeiro - então, com o foco dentro do editor de código, `Ctrl+Shift+N` sempre abre o Sticky Note, não o Scratchpad. Para abrir o Scratchpad, use o menu ou a Activity Bar diretamente.

---

## ✨ Ligatures (`src/editor/ligatures.py`)

Sistema de ligaturas tipográficas para sequências comuns de código (`->`, `!=`, `>=`, etc.), com três modos:

| Modo | Comportamento |
|---|---|
| `pseudo` | Detecta as sequências e aplica destaque visual colorido, sinalizando onde uma ligatura apareceria - funciona com qualquer fonte |
| `native` | Delega às ligaturas reais da fonte (requer fonte com suporte a ligaturas, ex. Fira Code, JetBrains Mono) |
| `both` | Aplica o destaque pseudo **depois** de ativar as ligaturas nativas da fonte |
| `off` | Nenhuma substituição visual |

Importante: o texto bruto em ASCII é **sempre preservado** - é salvo e copiado normalmente, independentemente do modo visual escolhido. O processamento opera apenas nas linhas visíveis, para impacto mínimo de performance.

---

## 🌡️ Edit Heatmap - recurso exclusivo do Codis (`src/editor/heatmap.py`)

> Funcionalidade que não existe em nenhum outro editor mainstream.

Rastreia quantas vezes cada linha foi editada durante a sessão atual e exibe um gradiente de calor no gutter - azul frio (poucas edições) até vermelho quente (muitas edições).

### Para que serve

- Identificar linhas problemáticas que exigem refatoração constante
- Localizar *hot spots* de complexidade onde bugs tendem a se concentrar
- Revelar padrões de edição que indicam dívida técnica acumulada

### Como funciona

O `EditHeatmap` escuta o `EventBus` (eventos `editor.change` com payload de linha) e acumula contagens por linha durante a sessão. O gutter consulta `get_line_color()` para colorir cada linha proporcionalmente à intensidade de edição.

---

## 🎵 Code Rhythm - recurso exclusivo do Codis (`src/editor/rhythm.py`)

> Funcionalidade que não existe em nenhum outro editor mainstream.

Analisa o ritmo de digitação do desenvolvedor em tempo real e expõe estatísticas de produtividade:

- **WPM** (palavras por minuto) de código
- **Detector de "flow state"**: identifica quando a digitação está regular e sem longas pausas, sinalizando estado de fluxo
- Mapa de padrões de pausa - onde o desenvolvedor hesita mais
- Detecção de sequências de edições repetitivas (sinal de refatoração em loop)
- Estimativa de tempo restante para terminar o arquivo, baseada no ritmo atual

O `CodeRhythm` recebe eventos de tecla via `record_keystroke()` e expõe as métricas calculadas via `metrics()`, integradas à [barra de status](interface.md) e ao `EventBus` interno.
