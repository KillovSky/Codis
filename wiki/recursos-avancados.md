# 🚀 Recursos Avançados

Esta página documenta funcionalidades avançadas do Codis que vão além do editor básico.

---

## LSP - Language Server Protocol

O LSP integra servidores de linguagem externos para fornecer inteligência de código avançada. Gerenciado pela classe `LSPManager` (`src/lsp/client.py`).

### Servidores Suportados (Auto-detectados)

| Linguagem | Servidores candidatos (em ordem de preferência) | Instalação | Status |
|-----------|------------------------------------------------|------------|--------|
| Python | `pylsp`, `pyright-langserver`, `jedi-language-server` | `pip install python-lsp-server[all]` | ✅ Completo |
| JavaScript | `typescript-language-server`, `tsserver` | `npm install -g typescript-language-server` | ✅ |
| TypeScript | `typescript-language-server` | `npm install -g typescript-language-server` | ✅ |
| Rust | `rust-analyzer` | [rust-analyzer.github.io](https://rust-analyzer.github.io) | ✅ |
| Go | `gopls` | `go install golang.org/x/tools/gopls@latest` | ✅ |
| C/C++ | `clangd` | `sudo apt install clangd` | ✅ |
| JSON | `vscode-json-languageserver` | `npm install -g vscode-langservers-extracted` | ✅ |
| HTML | `vscode-html-languageserver` | `npm install -g vscode-langservers-extracted` | ✅ |
| CSS | `vscode-css-languageserver` | `npm install -g vscode-langservers-extracted` | ✅ |
| Markdown | `marksman` | [github.com/artempyanykh/marksman](https://github.com/artempyanykh/marksman) | ✅ |
| Java | `eclipse.jdt.ls` | [eclipse.org/jdt-ls](https://github.com/eclipse/eclipse.jdt.ls) | ⚠️ Limitações conhecidas |
| PHP | `intelephense` | `npm install -g intelephense` | ⚠️ Limitações conhecidas |
| Ruby | `solargraph` | `gem install solargraph` | ⚠️ Limitações conhecidas |
| Lua | `lua-language-server` | [github.com/LuaLS/lua-language-server](https://github.com/LuaLS/lua-language-server) | ✅ |
| Bash | `bash-language-server` | `npm install -g bash-language-server` | ✅ |

> **Legenda:** ✅ Testado e funcional · ⚠️ Funcional com limitações conhecidas

### Funcionalidades LSP

| Funcionalidade | Atalho | Requisição LSP |
|---------------|--------|---------------|
| Hover documentation | Passar mouse | `textDocument/hover` |
| Autocomplete | Automático | `textDocument/completion` |
| Ir para definição | `F12` | `textDocument/definition` |
| Encontrar referências | `Shift+F12` | `textDocument/references` |
| Hints de parâmetros | Ao digitar `(` | `textDocument/signatureHelp` |
| Outline do documento | Activity Bar → 📚 | `textDocument/documentSymbol` |
| Busca de símbolo no workspace | `Ctrl+Shift+O` | `workspace/symbol` |
| Formatar via LSP | `Shift+Alt+F` | `textDocument/formatting` |
| Renomear símbolo | `F6` | `textDocument/rename` |
| Diagnósticos | Automático | `textDocument/publishDiagnostics` |

### Configuração

```json
"lsp_enabled": true
```

O LSP é ativado automaticamente ao abrir um arquivo de uma linguagem suportada. Cada linguagem tem seu próprio processo de servidor LSP independente.

---

## Code Outline

O painel **Code Outline** (Activity Bar → 📚) exibe a estrutura hierárquica do arquivo ativo: funções, classes, variáveis, imports e outros símbolos. Atualiza automaticamente quando o arquivo é modificado.

Clicar em qualquer símbolo no outline navega diretamente para a definição no editor.

---

## Bookmarks

O sistema de bookmarks permite marcar posições específicas nos arquivos para navegação rápida.

### Criando Bookmarks

- `Ctrl+F2` ou **Ferramentas → Adicionar Bookmark**
- Ao criar, um diálogo solicita uma nota opcional para identificar o marcador.

### Navegando por Bookmarks

| Ação | Atalho |
|------|--------|
| Próximo bookmark | `F2` |
| Bookmark anterior | `Shift+F2` |
| Listar todos | Activity Bar → 🔖 |

### Painel de Bookmarks

O painel de Bookmarks (sidebar → 🔖) lista todos os marcadores de todos os arquivos, com:
- Arquivo, linha, coluna e snippet da linha
- Nota associada ao bookmark
- Clique para navegar diretamente

---

## Snippets

### Painel de Snippets

O painel de Snippets (`Ctrl+Shift+J`) exibe todos os snippets disponíveis, filtrados pela linguagem do arquivo ativo.

### Estrutura de um Snippet

Snippets são arquivos JSON em `~/.codis/snippets/`:

```json
{
  "name": "função Python",
  "prefix": "def",
  "language": "python",
  "body": "def ${1:nome}(${2:parâmetros}):\n    ${3:pass}",
  "description": "Criar uma nova função Python"
}
```

Campos:
- `prefix` - gatilho de expansão (digitar + Tab)
- `body` - corpo do snippet com `${N:placeholder}` para campos de edição
- `language` - linguagem específica ou `"*"` para todas

### Inserindo Snippets

- Digite o prefixo e pressione `Tab` para expandir.
- Navegue entre campos com `Tab` / `Shift+Tab`.
- O painel de Snippets também permite inserção por clique duplo.

---

## Diff Engine

### Diff: Arquivo vs. Versão Salva

**Ferramentas → Diff: Arquivo vs. Versão Salva** compara o conteúdo atual (com modificações não salvas) com a última versão em disco.

### Diff: Dois Arquivos

**Ferramentas → Diff: Dois Arquivos** abre um seletor de arquivo duplo e exibe o diff lado a lado.

### Navegação no Diff

| Ação | Atalho |
|------|--------|
| Próximo hunk | `F7` |
| Hunk anterior | `Shift+F7` |

O diff engine (`src/diff/engine.py`) usa o algoritmo de Myers para gerar diffs com linha de contexto.

---

## Find in Files (Busca em Arquivos)

A busca em arquivos (`Ctrl+Shift+F`) pesquisa texto em todo o workspace.

**Funcionalidades:**
- Suporte a expressões regulares
- Filtros de inclusão: `*.py`, `src/**`
- Filtros de exclusão: `!node_modules`, `!*.min.js`
- Respeita `.gitignore` quando `respect_ignore_files: true`
- Máximo de `search_max_results` resultados (padrão: 2000)

**Atalho no resultado:** clique em qualquer resultado para abrir o arquivo na linha correspondente.

---

## Debug Panel

O Painel de Debug (painel inferior → aba **Debug**) oferece depuração real de Python via DAP/`debugpy`: breakpoints (clique na margem do editor), step into/over/out, inspeção de variáveis e call stack, e um console com avaliação de expressões no frame parado.

> Guia completo, configurações e resolução de problemas (incluindo conflitos de porta com VS Code): [Debug](debug.md).

Para outras linguagens além de Python, o painel executa o arquivo de forma simples (captura de saída), sem breakpoints/stepping reais por enquanto.

---

## Regex Playground

`Ctrl+Shift+R` ou painel inferior → aba **Regex** abre um playground interativo para testar expressões regulares:

- Campo de padrão regex com syntax highlighting
- Flags: `i` (case-insensitive), `m` (multiline), `s` (dotall)
- Resultado em tempo real: matches destacados no texto de teste
- Botão **Aplicar no Editor** - substitui no arquivo ativo via `_regex_replace()`

---

## Color Picker

`Ctrl+Shift+C` abre o seletor de cores. Detecta automaticamente a cor sob o cursor (valores `#rrggbb`, `rgb()`, etc.) e inicializa o picker com ela.

Ao confirmar:
1. A cor selecionada é inserida no cursor.
2. A cor é copiada para o clipboard.

---

## Scratchpad

O Scratchpad (`Ctrl+Shift+N`) é um editor de texto temporário persistente - ideal para notas rápidas, código de rascunho ou qualquer texto que não precisa de um arquivo.

O conteúdo é salvo automaticamente e sobrevive ao reinício do Codis.

---

## Code Stats

O painel **📊 Code Stats** (Activity Bar → 📊) exibe estatísticas do workspace:
- Contagem de arquivos por linguagem
- Total de linhas de código
- Tamanho total dos arquivos
- Distribuição por tipo de arquivo (gráfico de barras)

---

## Todo Tree

O painel **✅ Todo Tree** (Activity Bar → ✅) varre todos os arquivos do workspace em busca de comentários `TODO`, `FIXME`, `HACK`, `NOTE` e exibe uma lista navegável. Clicar em um item abre o arquivo na linha correspondente.

---

## REST Client

O painel **🌍 REST Client** (Activity Bar → 🌍) permite fazer requisições HTTP diretamente do editor:
- Métodos: GET, POST, PUT, PATCH, DELETE, HEAD, OPTIONS
- Headers customizáveis
- Body (JSON, form-data, raw)
- Exibição formatada da resposta

---

## Preview / Render Panel

O painel **🌐 Preview** (Activity Bar → 🌐) renderiza o arquivo ativo:
- **HTML** - renderiza como página web com suporte a CSS e JavaScript local
- **Markdown** - converte e exibe com formatação rica (detecta tema claro/escuro pelo brilho do tema ativo)
- **SVG** - renderiza o gráfico diretamente

Atualiza automaticamente ao salvar o arquivo.

---

## Flowchart Panel

O painel **🔀 Flowchart** (Activity Bar → 🔀) gera um fluxograma visual do código ativo automaticamente, usando análise de fluxo de controle.

---

## Pomodoro Timer

O painel **⏱ Pomodoro** (`Ctrl+Alt+P`) implementa a técnica Pomodoro:
- Temporizadores configuráveis para sessão de trabalho e pausa
- Notificações na barra de status ao fim de cada intervalo

---

## Notas de Sessão

**Ferramentas → Notas de Sessão** abre uma janela flutuante de notas persistentes vinculadas ao workspace atual. O conteúdo é salvo automaticamente em `~/.codis/notes_<hash>.txt` a cada 600 ms de inatividade.

---

## Histórico de Colagem

`Ctrl+Shift+V` abre o histórico das últimas 50 entradas do clipboard. Clique duplo ou pressione Enter para colar o item selecionado no editor ativo.

---

## Ferramentas de Texto

Via menu **Ferramentas**:

| Ferramenta | Ação |
|-----------|------|
| Contador de Palavras | Conta palavras, linhas, chars (seleção ou arquivo) |
| Base64 Encode | Codifica a seleção em Base64 |
| Base64 Decode | Decodifica a seleção de Base64 |
| URL Encode | Percent-encoding da seleção |
| URL Decode | Decodifica percent-encoding |
| MD5 Hash | Calcula MD5 da seleção (copia para clipboard) |
| SHA-256 Hash | Calcula SHA-256 da seleção |
| Inserir Sequência | Insere sequência numérica com passo configurável |

---

## SQL Studio

O **SQL Studio** é um ambiente completo de banco de dados integrado ao Codis, disponível na Activity Bar (`🗄`), na aba inferior (`🗄 SQL`) e pelo atalho `Ctrl+Shift+Q`.

### Drivers suportados

| Driver | Instalação | Notas |
|--------|-----------|-------|
| **SQLite** | Embutido no Python | Zero dependências, padrão |
| **PostgreSQL** | `pip install psycopg2-binary` | Host, porta, usuário, senha, database |
| **MySQL / MariaDB** | `pip install pymysql` | Mesmos campos do PostgreSQL |
| **DuckDB** | `pip install duckdb` | Arquivo `.duckdb` ou `:memory:` |

### Gerenciamento de Conexões

Conexões são salvas em `~/.codis/sql_connections.json`. Cada perfil inclui:
- Nome, driver, caminho (SQLite/DuckDB) ou host/porta/database/usuário/senha (outros)
- Flag somente leitura para conexões SQLite/DuckDB em modo seguro
- Botão **🔗 Testar** antes de salvar verifica se a conexão funciona

### Editor SQL

- Syntax highlighting: palavras-chave em azul, strings, números e comentários `--` / `/* */`
- Números de linha integrados
- Autocomplete `Ctrl+Space` - keywords SQL + tabelas + colunas do esquema ativo
- `F5` ou `Ctrl+Enter` - executa a query completa
- Botão **▶ Seleção** - executa apenas o texto selecionado
- Botão **📊 EXPLAIN** - exibe o plano de execução sem rodar a query
- Botão **⬛ Formatar** - UPPER-CASE em keywords e quebras de linha semânticas
- Salvar / abrir arquivos `.sql` diretamente

### Browser de Esquema

A coluna lateral esquerda exibe a árvore de tabelas do banco conectado:
- `📋 nome_tabela` - nó expansível
- `🔑 coluna` - chave primária
- `coluna TIPO!` - `!` indica `NOT NULL`
- Duplo clique em tabela → insere `SELECT * FROM "tabela" LIMIT 100;` no editor
- Duplo clique em coluna → insere o nome da coluna no cursor
- Botão direito → menu de contexto com `SELECT *`, `COUNT(*)`, `DROP TABLE`, `Ver Esquema DDL`
- Campo de busca no topo para filtrar tabelas por nome

### Grid de Resultados

- Clique no cabeçalho de qualquer coluna para ordenar
- **📤 CSV** - exporta os resultados para arquivo `.csv`
- **📤 JSON** - exporta para arquivo `.json` com objetos por linha
- Máximo de 5.000 linhas exibidas por query (configurável em `src/ui/sql_studio.py`)

### Histórico

Até 500 queries salvas automaticamente em `~/.codis/sql_history.json`, acessíveis pelo botão **⏱ Histórico**. A janela de histórico mostra timestamp, prévia da query e permite inserção por duplo clique.

---

## Gerenciador de Tarefas

O **Gerenciador de Tarefas** (Activity Bar → `✅`, sem atalho de teclado dedicado) oferece três visões:

### Kanban

Quadro visual com quatro colunas configuráveis:

```
Backlog  →  Em Andamento  →  Revisão  →  Concluído
```

Cada cartão exibe:
- Faixa de cor por prioridade (🔴 Crítico · 🟠 Alto · 🟡 Médio · 🟢 Baixo)
- Título, preview da descrição
- Badge de vencimento com cores por urgência (atraso em vermelho, hoje em amarelo, próximo em laranja)
- Tags coloridas (cor calculada automaticamente por hash do nome)

Duplo clique em qualquer cartão abre o editor. Botão direito → mover para coluna, arquivar, deletar.

### Lista

Tabela com todas as tarefas filtráveis por texto e prioridade, com opção de mostrar tarefas arquivadas.

### Agendador de Comandos

Aba **⏰ Agendador** para execução automática de comandos shell:

| Modo | Comportamento |
|------|--------------|
| **Uma vez** | Executa em data/hora específica e desabilita após |
| **A cada N minutos** | Executa a cada N minutos a partir da última execução |
| **A cada N horas** | Executa a cada N horas |
| **Todo dia** | Executa diariamente no horário `HH:MM` configurado |
| **Toda semana** | Executa no dia da semana + horário `HH:MM` configurados |

O **Scheduler** é um thread daemon que verifica as tarefas a cada 10 segundos. Suporta comandos simples (`python backup.py`) e shell complexos com pipes e redirecionamentos (`echo hi | tee log.txt`). O botão **▶ Executar Agora** força execução imediata da tarefa selecionada.

Agendamentos salvos em `~/.codis/scheduled_tasks.json`. O log de execuções exibe saída real com cores verde (sucesso) / vermelho (erro).
