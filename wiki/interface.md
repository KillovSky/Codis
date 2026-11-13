# 🖥️ Interface

O Codis segue um layout inspirado em editores modernos, composto por cinco regiões principais.

---

## Layout Geral

```
┌──────────────────────────────────────────────────────────────┐
│  Menu Bar (Arquivo · Editar · Seleção · Exibir · ...)          │
├────┬────────────┬─────────────────────────────────────────────┤
│    │            │  Tab Bar                                    │
│    │            ├─────────────────────────────────────────────┤
│  A │  Sidebar   │  Breadcrumb                                 │
│  c │  (Explorer │  ─────────────────────────────────────────  │
│  t │   Git      │                                             │
│  i │   Busca    │          Área do Editor                     │
│  v │   IA       │                                             │
│  i │   etc.)    │                                             │
│  t │            ├─────────────────────────────────────────────┤
│  y │            │  Painel Inferior (Terminal · Output · ...)    │
│  B │            │                                             │
│  a ├────────────┴─────────────────────────────────────────────┤
│  r │            Barra de Status                               │
└────┴─────────────────────────────────────────────────────────┘
```

---

## Activity Bar (Barra de Atividades)

A barra vertical de 48 px à esquerda é fixa e contém os ícones de navegação principal.

A Activity Bar é composta por itens **estáticos** (lista fixa em `ITEMS_TOP`/`ITEMS_BOTTOM`) e itens **dinâmicos**, adicionados via `add_plugin_item()` durante a inicialização. A maior parte dos itens fica **oculta por padrão** - o usuário ativa via clique direito na barra (personalização).

### Itens estáticos (`ITEMS_TOP`)

| Ícone | Painel | Tecla | Visível por padrão |
|-------|--------|-------|---|
| 📁 | Explorer (árvore de arquivos) | `Ctrl+Shift+E` | Sim |
| 🔍 | Buscar em Arquivos | `Ctrl+Shift+F` | Sim |
| 🌿 | Source Control (Git) | *(rótulo "Ctrl+Shift+G", sem bind real - veja [Git](git.md))* | Sim |
| ▶ | Executar/Depurar | `Ctrl+Shift+D` | Sim |
| 🔌 | Extensões (Plugins) | `Ctrl+Shift+X` | Não |
| `[IA]` | Assistente de IA | `Ctrl+Shift+A` | Não |
| 🔷 | Outline | - | Não |
| 🔖 | Bookmarks | `F2` | Não |
| 📝 | Scratchpad | `Ctrl+Shift+N`* | Não |
| ⚠ | Problemas | *(rótulo "Ctrl+Shift+M", bind real é `Ctrl+M`)* | Não |
| 🗂 | SSH Remoto | `Ctrl+Shift+H` | Não |
| 💬 | Chat ao Vivo | `Ctrl+Shift+T` | Sim |
| 🤝 | Colaboração | - | Não |
| ♿ | Acessibilidade | `Ctrl+Shift+U` | Não |

\* Com o foco no editor de código, `Ctrl+Shift+N` é capturado primeiro pelo [Sticky Note Gutter](inteligencia-editor.md).

### Itens estáticos (`ITEMS_BOTTOM`)

| Ícone | Painel | Tecla |
|-------|--------|-------|
| ⊟ | Terminal | `` Ctrl+` `` |
| ⚙ | Configurações | `Ctrl+,` |
| `Th` | Tema (cicla para o próximo) | - |

### Itens dinâmicos (registrados via `add_plugin_item()`)

| Ícone | Painel | Tecla |
|-------|--------|-------|
| 🗄 | SQL Studio | `Ctrl+Shift+Q` |
| 📊 | Code Stats | - |
| ✅ | Tarefas (Kanban + Agendador) | - *(sem atalho dedicado, apesar do que sugerem outras fontes)* |
| 🌍 | REST Client | - |
| 🌐 | Preview / Render | - |
| ⏱ | Pomodoro | `Ctrl+Alt+P` |
| 🔀 | Flowchart | - |
| ✂️ | Snippets | `Ctrl+Shift+J` |
| ⏱ | Time Tracker | - |
| 🔗 | Dependency Graph | - |
| 📚 | Outline/Docs | - |

Clicar no mesmo ícone que está ativo **fecha** a sidebar. O ícone de **Tema** (`Th`) na parte inferior alterna para o próximo tema ciclicamente.

Plugins podem registrar novos itens na Activity Bar via `api.add_sidebar_panel()` (não `register_panel()` - veja [Plugins](plugins.md)).

---

## Sidebar

A sidebar (painel lateral) exibe o conteúdo correspondente ao item selecionado na Activity Bar.

**Redimensionamento:** arraste o divisor entre a sidebar e a área do editor.

**Ocultação rápida:** clique no botão flutuante `◀` / `▶` que aparece na borda esquerda da área do editor, ou pressione `Ctrl+B` (se configurado via plugin).

### Explorer

Exibe a árvore de arquivos do workspace. Suporta:

- Ícones por tipo de arquivo
- Indicadores de status Git (modificado, novo, ignorado)
- Menu de contexto (clicar com botão direito): renomear, excluir, novo arquivo/pasta
- Duplo clique para abrir; clique simples para pré-visualizar (peek)

Se nenhum workspace estiver aberto, um *placeholder* com botão **Abrir Pasta** é exibido no lugar da árvore.

### Busca em Arquivos

Pesquisa de texto completo em todos os arquivos do workspace. Suporta:

- Filtros de inclusão/exclusão de paths (`*.py`, `!node_modules`)
- Resultado clicável que abre o arquivo na linha correspondente
- Respeita `.gitignore` quando `respect_ignore_files = true`

### Source Control (Git)

Ver [Git](git.md).

### Assistente de IA

Ver [Assistente de IA](ia.md).

---

## Área do Editor

A região central contém:

1. **Tab Bar** - abas de arquivos abertos, com indicador `●` para arquivos modificados.
2. **Breadcrumb** - caminho completo do arquivo em camadas clicáveis. Pode ser ocultado via **Exibir → Breadcrumbs**. A breadcrumb é limpa automaticamente ao fechar a última aba aberta, não exibindo o caminho do arquivo fechado.
3. **Search Bar** - barra de busca/substituição flutuante, ativada por `Ctrl+F` ou `Ctrl+H`.
4. **Container do Editor** - onde os `EditorPane`s são exibidos.

### Múltiplas Abas

- `Ctrl+Tab` - próxima aba
- `Ctrl+Shift+Tab` - aba anterior
- `Ctrl+W` - fechar aba ativa
- Ao fechar uma aba com modificações, o Codis pergunta se deseja salvar.

### Split Editor

Use **Exibir → Split Editor** (ou o comando na Paleta de Comandos) para abrir o mesmo arquivo em uma segunda aba lado a lado. Cada aba mantém seu próprio estado de cursor e scroll.

---

## Painel Inferior

O painel inferior (bottom panel) contém oito abas:

| Aba | Conteúdo |
|-----|----------|
| ⚠ Problemas | Diagnósticos de lint e LSP |
| ⬛ Terminal | Terminal integrado com múltiplos shells |
| 📤 Saída | Log de eventos do editor e plugins |
| 🌿 Git | Instância adicional do painel Git |
| 📊 Diff | Comparação lado a lado de arquivos |
| 🐛 Debug | Painel de depuração |
| 🎯 Regex | Regex Playground interativo |
| 🗄 SQL | SQL Studio - banco de dados integrado |

**Abrir/fechar:** botão flutuante `▲` / `▼` no canto inferior direito, ou `` Ctrl+` `` para o Terminal, ou `Ctrl+Shift+Q` para o SQL Studio.

No modo **K-UI**, cada aba pode ser "despluggada" (`⊞`) para uma janela flutuante independente. Clique com o botão direito em qualquer aba para ver a opção.

---

## Barra de Status

A barra de status inferior exibe (da esquerda para a direita):

| Elemento | Ação ao clicar |
|----------|---------------|
| 🌿 Branch Git + contagem de mudanças | Abre o painel Git |
| ⚠ Erros / ✔ Avisos | Abre o painel de Problemas |
| Nome da linguagem | Abre seletor de linguagem |
| Codificação (UTF-8, etc.) | Abre seletor de codificação |
| Fim de linha (LF / CRLF) | Abre seletor de EOL |
| Indentação (Tabs / Espaços: N) | Abre seletor de indentação |
| Posição do cursor (Ln X, Col Y) | - |
| Seleção (N chars, N linhas) | - |
| Zoom (100%) | Abre seletor de zoom |

Notificações temporárias aparecem no centro da barra de status (ex: "Arquivo salvo", "Lint em execução"). Clicar na área de notificação abre um popup com o **histórico de notificações** recentes da sessão.

---

## Botões de Alternância Flutuantes

Dois pequenos botões flutuantes ficam sempre visíveis sobre a interface:

- **`▶` / `◀`** - na borda esquerda da área do editor, alterna a sidebar.
- **`▲` / `▼`** - no canto inferior direito (acima da barra de status), alterna o painel inferior.

---

## Barra de Menus

A barra de menus contém os seguintes menus:

| Menu | Conteúdo principal |
|------|--------------------|
| **Arquivo** | Novo, Abrir, Salvar, Recentes, Autosave, Fechar, Reiniciar, Sair |
| **Editar** | Undo/Redo, Copiar/Colar, Busca/Substituição, Manipulação de linhas, Transformações, Histórico de colagem, Formatar |
| **Seleção** | Selecionar tudo, linha, copiar linha acima/abaixo |
| **Exibir** | Paleta de Comandos, painéis, temas, quebra de linha, minimapa, breadcrumbs, zoom, tela cheia, split |
| **Ir** | Ir para linha, símbolo, próximo/anterior problema/aba/colchete |
| **Executar** | Executar arquivo, formatar, lint, corrigir tudo |
| **Terminal** | Novo terminal, limpar, matar, executar seleção |
| **Plugins** | Gerenciar, instalar, abrir pasta de plugins |
| **Ferramentas** | Contador de palavras, codificação Base64/URL, hash, inserir sequência, painéis nativos, LSP, modos especiais |
| **Ajuda** | Boas-vindas, documentação, atalhos, Sobre |

### Navegação por Teclado nos Menus

Os dropdowns da barra de menus suportam navegação completa por teclado:

| Tecla | Ação |
|-------|------|
| `↑` / `↓` | Move o destaque entre itens do dropdown aberto |
| `←` / `→` | Alterna entre menus de nível superior (ex: Arquivo → Editar) |
| `Enter` | Ativa o item destacado |
| `Escape` | Fecha o dropdown |
| `Alt + letra` | Abre o menu correspondente à letra sublinhada |

Os dropdowns também fecham automaticamente quando a janela principal é minimizada.

---

## Paleta de Comandos

Pressione `Ctrl+Shift+P` para abrir a **Paleta de Comandos** - um campo de busca fuzzy que lista todos os comandos disponíveis (incluindo os de plugins). A paleta exibe o atalho de teclado associado a cada comando.

`Ctrl+P` abre a **Abertura Rápida de Arquivos**, uma versão da paleta focada em arquivos recentes.
