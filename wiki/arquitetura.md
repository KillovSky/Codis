# 🏗️ Arquitetura

Esta página descreve a estrutura interna do Codis: módulos, responsabilidades e fluxo de execução.

> **Versão:** 1.0.0 · Para contribuir, veja [CONTRIBUTING.md](../CONTRIBUTING.md). Para testes, veja [tests/README.md](../tests/README.md).

---

## Visão Geral (Camadas)

O Codis é um editor de código desktop escrito inteiramente em Python 3.12+ com Tkinter como toolkit gráfico. A arquitetura é deliberadamente monolítica em sua camada de UI (sem servidor de UI separado) mas modular em suas funcionalidades - cada painel, ferramenta e sistema é um módulo independente que se comunica via event bus.

```
┌─────────────────────────────────────────────────────────────────┐
│                          run.py                                 │
│                     (entry point)                               │
└───────────────────────────┬─────────────────────────────────────┘
                            │
┌───────────────────────────▼─────────────────────────────────────┐
│                         src/app.py                              │
│              (App - orquestrador principal)                      │
│                                                                 │
│   ┌──────────────┐  ┌──────────────┐  ┌──────────────────────┐ │
│   │ _app_editor  │  │ _app_build   │  │   _app_settings      │ │
│   │ _app_lsp     │  │ _app_kui     │  │   _app_tools         │ │
│   └──────────────┘  └──────────────┘  └──────────────────────┘ │
└─────────────────────────────────────────────────────────────────┘
                            │
        ┌───────────────────┼───────────────────┐
        │                   │                   │
┌───────▼──────┐   ┌────────▼──────┐   ┌───────▼───────┐
│  src/editor/ │   │   src/ui/     │   │  src/events/  │
│  (Editor)    │   │  (Painéis)    │   │  (Event Bus)  │
└──────────────┘   └───────────────┘   └───────────────┘
        │                   │
┌───────▼──────┐   ┌────────▼──────┐
│  src/lsp/    │   │  src/ia/      │
│  src/lint/   │   │  src/syntax/  │
│  src/diff/   │   │  src/theme/   │
│  src/plugins/│   │  src/i18n.py  │
└──────────────┘   └───────────────┘
```

`run.py` é o único ponto de entrada (sem lógica de negócio própria) e `src/__main__.py` permite execução via `python -m src`. `App` (`src/app.py`) é a classe central: ela não faz nada diretamente, apenas delega para os mixins `_App*` listados na tabela abaixo, o que mantém `app.py` legível e permite testar cada domínio de forma isolada.

A estrutura completa de módulos, arquivo por arquivo, está detalhada logo abaixo.

---

## Estrutura de Módulos

```
src/
├── __init__.py
├── __main__.py          ← Ponto de entrada: python -m src
├── main.py              ← Inicialização completa (main())
├── app.py               ← CodisApp: orquestrador principal
├── config.py            ← ConfigManager: configuração persistente
├── constants.py         ← Constantes globais (paths, fontes, linguagens)
├── i18n.py              ← Sistema de internacionalização (I18n)
├── standalone.py        ← Roteamento dos 29 modos --flag standalone + 4 aliases (sem o editor completo)
│
├── _app_build.py        ← _AppBuildMixin: construção dos painéis principais da UI
├── _app_editor.py       ← _AppEditorMixin: abrir/salvar/fechar arquivos, tabs, recentes
├── _app_lsp.py          ← _AppLspMixin: ciclo de vida dos language servers e diagnósticos
├── _app_settings.py     ← _AppSettingsMixin: janela de config, fonte, tema, idioma em runtime
├── _app_tools.py        ← _AppToolsMixin: terminal, colaboração, SQL Studio, Pip Manager…
├── _app_kui.py          ← _AppKuiMixin: ativação e bridge da interface K-UI
│
├── editor/              ← Componentes do editor de código
│   ├── pane.py          ← EditorPane: widget principal de edição
│   ├── _pane_lsp.py     ← _PaneLSPMixin: integração LSP do EditorPane (hover, autocomplete, go-to-def…)
│   ├── _pane_utils.py   ← Utilitários compartilhados entre pane.py e _pane_lsp.py
│   ├── constants.py     ← Constantes do módulo de edição (paths, limites, timeouts)
│   ├── autocomplete.py  ← AutocompletePopup: sugestões de código
│   ├── code_folding.py  ← Dobramento de regiões
│   ├── color_preview.py ← Amostras de cor em linha
│   ├── complexity_lens.py ← ComplexityLens: badge de complexidade ciclomática em tempo real
│   ├── diff_pane.py     ← DiffPane: diff lado a lado
│   ├── file_watcher.py  ← FileWatcher: detecta modificações externas via polling (sem inotify/FSEvents)
│   ├── focus_block.py   ← FocusBlock: dimming de tudo fora do bloco estrutural atual
│   ├── heatmap.py       ← EditHeatmap: mapa de calor de edições por linha
│   ├── inlay_hints.py   ← Hints de tipo injetados (LSP)
│   ├── inline_blame.py  ← Git blame inline
│   ├── kdocs.py         ← KDocs: motor de documentação
│   ├── large_file.py    ← Virtual file buffer em 3 camadas para arquivos gigantes
│   ├── ligatures.py     ← Ligatures: ligaturas pseudo via destaque visual (->, !=, >= etc.)
│   ├── line_numbers.py  ← Painel de numeração de linhas
│   ├── minimap.py       ← Minimapa do código
│   ├── multi_cursor.py  ← Edição com múltiplos cursores simultâneos
│   ├── rhythm.py        ← CodeRhythm: análise de ritmo de digitação em tempo real
│   ├── smart_selection.py ← Seleção inteligente
│   ├── smart_tips.py    ← SmartTips: dicas flutuantes contextuais não-intrusivas
│   ├── snippets.py      ← SnippetEngine: expansão de snippets
│   ├── sticky_note_gutter.py ← StickyNoteGutter: notas adesivas (💬) por linha no gutter
│   ├── sticky_scroll.py ← Cabeçalhos colados no topo
│   ├── text_transforms.py ← Transformações de texto puras sobre seleção/palavra sob cursor
│   └── vim_mode.py      ← VimMode: janela CLI imersiva
│
├── diff/
│   └── engine.py        ← DiffEngine: algoritmo de Myers
│
├── ia/                  ← Assistente de IA
│   ├── iaspace.py       ← PainelIA: UI de chat
│   ├── providers.py     ← Provedores (Anthropic, OpenAI, Gemini, Ollama, etc.)
│   ├── context_builder.py ← Construção do bloco de contexto
│   ├── dialdiff.py      ← DialDiff: diálogo de diff para aplicar código da IA
│   ├── actions.py       ← Registry de actions e skills
│   ├── approval_gate.py ← ApprovalGate: classificação de risco e portal de aprovação
│   ├── toolbox.py       ← ToolExecutor: ferramentas MCP-style (40 ferramentas)
│   └── skills_default/  ← Skills padrão em Markdown
│
├── lint/
│   └── manager.py       ← LintManager + Diag: execução assíncrona de linters
│
├── locales/             ← Arquivos de tradução JSON (14 idiomas)
│
├── lsp/                 ← Language Server Protocol e Debug Adapter Protocol
│   ├── client.py        ← LSPManager: gerencia processos de servidor LSP
│   ├── protocol.py      ← Tipos e estruturas LSP (LSPMessage, Range, etc.)
│   └── dap_client.py    ← DAPClient: cliente DAP real sobre TCP, usado pelo Painel de Debug para falar com debugpy (initialize/attach/setBreakpoints/stepping/variables)
│
├── plugins/             ← Sistema de plugins
│   ├── api.py           ← PluginAPI + PluginManifest
│   ├── loader.py        ← PluginLoader: carregamento com sandbox AST
│   ├── process_runner.py ← Execução de plugins isolados em processo separado (multiprocessing)
│   └── store.py         ← Plugin Store: busca/instalação a partir do repositório GitHub
│
├── syntax/              ← Syntax highlighting
│   ├── highlighter.py   ← Motor de highlight via tags Tkinter
│   └── patterns.py      ← Padrões regex por linguagem (compilados)
│
├── theme/               ← Sistema de temas
│   ├── base.py          ← Theme dataclass + definições dos 21 temas built-in
│   ├── manager.py       ← ThemeManager: troca, patches e TTK theming
│   └── utils.py         ← Funções utilitárias de cor (contraste, interpolação, hex)
│
└── ui/                  ← Todos os painéis e widgets de UI
    ├── accessibility.py ← AccessibilityManager + AccessibilityPanel: alto contraste, navegação por teclado
    ├── activity_bar.py  ← ActivityBar: barra de atividades vertical
    ├── archive_panel.py ← ArchivePanel: visualizador de ZIP/RAR/7z
    ├── bookmarks.py     ← BookmarkManager + BookmarksPanel
    ├── breadcrumb.py    ← BreadCrumb: caminho do arquivo
    ├── calculator_panel.py ← CalculatorPanel: calculadora científica integrada
    ├── calendar_panel.py ← CalendarPanel: calendário e lembretes
    ├── chat_panel.py    ← ChatPanel: cliente multi-protocolo (IRC/XMPP/Matrix/Slack/Discord/Telegram)
    ├── clock_panel.py   ← ClockPanel: relógio, alarmes e cronômetro
    ├── code_outline.py  ← CodeOutline: estrutura do arquivo
    ├── code_runner.py   ← CodeRunnerDialog + CodeRunnerStore: execução configurável por linguagem
    ├── code_stats.py    ← CodeStatsPanel: estatísticas do workspace
    ├── collab_panel.py  ← CollabPanel: edição colaborativa em tempo real via WebSocket
    ├── color_picker.py  ← ColorPickerPopup: obtenha uma cor com o mouse
    ├── command_palette.py ← CommandPalette: paleta de comandos fuzzy
    ├── custom_keywords_panel.py ← CustomKeywordsPanel: palavras-chave de sintaxe customizadas por linguagem
    ├── cursor_manager.py ← CursorManager + CursorPickerPanel: cursores customizáveis do editor
    ├── debug_panel.py   ← DebugPanel: depuração real de Python (breakpoints, stepping, variáveis) via cliente DAP próprio (src/lsp/dap_client.py) conectado ao debugpy
    ├── dependency_graph.py ← DependencyGraphPanel: grafo de dependências entre módulos do projeto
    ├── diff_panel.py    ← DiffPanel: visualizador de diff standalone
    ├── docs_panel.py    ← DocsPanel: browser de KDocs na sidebar
    ├── dropdown.py      ← DropDown: widget dropdown customizado
    ├── easter_eggs.py   ← EasterEggManager: ???
    ├── env_manager.py   ← EnvManagerPanel: gerenciador visual de variáveis de ambiente
    ├── file_timeline.py ← FileTimeline: histórico de versões
    ├── file_tree.py     ← FileTree: árvore de arquivos
    ├── find_in_files.py ← FindInFilesPanel: busca em workspace
    ├── flowchart_panel.py ← FlowchartPanel: python apenas
    ├── formatter_panel.py ← FormatterPanel: formatador/validador de JSON, YAML e afins
    ├── git_auth.py      ← GitAuthManager: autenticação Git
    ├── git_clone_dialog.py ← Diálogo de clone de repositório
    ├── git_panel.py     ← GitPanel: controle de versão completo
    ├── grab_safe.py     ← safe_grab_set(): utilitário para grab_set() seguro em Toplevels
    ├── key_remapper.py  ← KeyRemapperManager + KeyRemapperDialog: remapeamento de atalhos
    ├── macro_recorder.py ← MacroRecorder + MacroStore: gravação e repetição de macros de teclado
    ├── menu_bar.py      ← MenuBar: barra de menus customizada holográfica
    ├── output_panel.py  ← OutputPanel: log com canais
    ├── paint_panel.py   ← PaintPanel: editor de imagens integrado (estilo Paint)
    ├── panel_detach.py  ← PanelDetachManager: destacamento de painéis em janelas flutuantes
    ├── performance_panel.py ← PerformancePanel: 13 features de performance + presets
    ├── pip_manager.py   ← PipManagerPanel: gerenciador visual de pacotes pip e venvs
    ├── plugin_panel.py  ← PluginPanel: gerenciador de plugins
    ├── pomodoro_panel.py ← PomodoroPanel: timer Pomodoro
    ├── problems.py      ← ProblemsPanel: diagnósticos de lint/LSP
    ├── process_manager.py ← ProcessManagerPanel: gerenciador de processos do sistema
    ├── project_templates.py ← ProjectTemplateDialog: criação de projetos a partir de templates
    ├── regex_playground.py ← RegexPlayground: testes de regexp
    ├── remote_browser.py ← RemoteBrowserPanel: explorador de arquivos remotos (SSH/SFTP)
    ├── render_panel.py  ← RenderPanel: preview HTML/Markdown/SVG
    ├── rest_client.py   ← RestClientPanel: cliente HTTP
    ├── scratchpad.py    ← ScratchpadPane: rascunho de edição
    ├── search_bar.py    ← SearchBar: busca/substituição inline
    ├── settings_editor.py ← SettingsEditor: editor JSON de config
    ├── settings_window.py ← SettingsWindow: janela de config gráfica
    ├── snippets_panel.py ← SnippetsPanel: autocomplete manual
    ├── sql_studio.py    ← SQLStudioPanel: banco de dados integrado (SQLite/PostgreSQL/MySQL/DuckDB)
    ├── status_bar.py    ← StatusBar: barra de status inferior
    ├── tab_bar.py       ← TabBar: barra de abas
    ├── task_manager.py  ← TaskManagerPanel: Kanban + Agendador de comandos
    ├── terminal.py      ← TerminalPanel: terminal integrado
    ├── theme_preview_panel.py ← ThemePreviewPanel: pré-visualização de temas
    ├── time_tracker.py  ← TimeTrackerPanel: Code Time Tracker, tempo gasto por arquivo/projeto
    ├── todo_panel.py    ← TodoPanel: scanner de TODOs/HACK/FIXME etc
    ├── trash_manager.py ← TrashManager + TrashPanel: lixeira interna do Codis
    ├── ux_enhancements.py ← ModernTooltip e demais micro-interações de UX
    ├── welcome.py       ← WelcomeScreen: tela inicial
    ├── wizard.py        ← FirstRunWizard: wizard de primeiro uso
    ├── workspace_switcher.py ← WorkspaceSwitcherDialog: troca rápida entre workspaces
    ├── workspace_trust.py ← WorkspaceTrust: apenas plugins confiaveis rodam
    ├── zen_media.py     ← ZenMediaPlayer + HUD + Orb + Dialog
    ├── zen_player.py    ← ZenPlayerHost/View (janela flutuante ou dock)
    ├── zen_player_widgets.py ← botões vetoriais, EQ gráfico, fila
    ├── zen_audio.py     ← EQ/presets, capas, metadados (sem Tk)
    └── k_ui/            ← K-UI: engine holográfica
        ├── __init__.py  ← KUI: controlador + re-exports
        ├── _bgrenderer.py ← BgRenderer: nebulosa/aurora em thread PIL
        ├── _colors.py   ← Paletas e utilitários de cor
        ├── _easter.py   ← KUEasterEggs: ???
        ├── _engine.py   ← KEngine + KWelcome
        ├── _perf.py     ← Perfis de performance
        ├── _tooltip.py  ← KHoverTooltip
        └── _widgets.py  ← KGlassPane, KTopBar, KTabBar, KStatusBar, KPortal, etc.
```

---

## CodisApp e os Mixins

`app.py` define a classe `CodisApp`, o orquestrador principal da aplicação, mas a maior parte de sua lógica vive em seis mixins importados de módulos irmãos na raiz de `src/`. Essa divisão existe para manter cada arquivo focado em uma responsabilidade única, em vez de um único arquivo monolítico com milhares de linhas:

```python
class CodisApp(
    _AppBuildMixin,     # src/_app_build.py    - construção da UI
    _AppEditorMixin,    # src/_app_editor.py   - abrir/salvar/fechar arquivos
    _AppLspMixin,       # src/_app_lsp.py      - language servers
    _AppSettingsMixin,  # src/_app_settings.py - configurações em runtime
    _AppToolsMixin,     # src/_app_tools.py    - painéis opcionais (terminal, SQL, etc.)
    _AppKuiMixin,       # src/_app_kui.py      - bridge com a engine K-UI
):
    ...
```

| Mixin | Responsabilidade |
|---|---|
| `_AppBuildMixin` | Instancia e conecta todos os painéis visuais (editor, terminal, Git, IA…) que compõem a janela principal |
| `_AppEditorMixin` | Abertura, salvamento, fechamento e ciclo de vida dos arquivos, tabs e histórico recente |
| `_AppLspMixin` | Ciclo de vida dos language servers, roteamento de diagnósticos, painel LSP |
| `_AppSettingsMixin` | Janela de configurações, aplicação de preferências de fonte/tema/indentação/idioma sem reiniciar |
| `_AppToolsMixin` | Abertura e gerenciamento dos painéis auxiliares: terminal, colaboração, Pip Manager, Code Runner, SQL Studio, entre outros |
| `_AppKuiMixin` | Ativação, configuração e comunicação entre a UI Tkinter tradicional e a engine K-UI |

Cada mixin é um arquivo independente e pode ser lido isoladamente; juntos, formam a API pública completa de `CodisApp`. Essa estrutura também facilita testes e revisão de código, já que uma mudança em "como o terminal abre" só toca `_app_tools.py`.

---

## Fluxo de Inicialização

```
run.py / python -m src
    └── main.py → main()
        1. Criar diretórios de runtime (~/.codis/*)
        2. Compilar padrões de sintaxe (_compile_all_patterns)
        3. Definir e patchar temas (_define_themes, _patch_all_themes)
        4. Criar janela raiz Tk (oculta)
        5. Monkey-patch global de ttk.Combobox (fix de dropdown)
        6. Instalar handler global de exceções não tratadas
        7. HiDPI Windows (SetProcessDpiAwareness, tk scaling)
        8. Instanciar ConfigManager + ThemeManager
        9. Wizard de primeiro uso OU _init_app() direto
       10. root.mainloop()

_init_app()
    └── CodisApp(root)
        1. Instanciar sistemas: ConfigManager, ThemeManager, SnippetEngine, LintManager
        2. Instanciar LSPManager (opcional, se lsp_enabled)
        3. Instanciar PluginAPI + PluginLoader
        4. Inicializar estado interno (editors, workspace, sidebar, etc.)
        5. _setup_window() - título, geometria, protocolo WM_DELETE
        6. _build_layout() - Activity Bar, Sidebar, área do editor, painel inferior
        7. _build_menus() - barra de menus completa
        8. _bind_shortcuts() - todos os atalhos de teclado
        9. Conectar callbacks: theme change, locale change, config change
       10. lint_mgr.attach(root), _load_plugins()
       11. _restore_session_or_welcome()
       12. _setup_autosave()
       13. _init_exclusive_features() - Zen Media, K-UI theme, Easter Eggs, Vim/KUI restore
```

---

## Hierarquia de Widgets (Layout)

```
root (tk.Tk)
├── Menu (barra de menus)
├── StatusBar (pack side=bottom)
└── PanedWindow H (h_paned)
    ├── ActivityBar (width=48, stretch=never)
    ├── Frame sidebar (width=cfg.sidebar_width, stretch=never)
    │   └── sidebar_stack (Frame)
    │       ├── FileTree (explorer)
    │       ├── FindInFilesPanel (search)
    │       ├── GitPanel (git)
    │       ├── PluginPanel (extensions)
    │       ├── SettingsEditor (settings)
    │       ├── CodeOutline (outline)
    │       ├── BookmarksPanel (bookmarks)
    │       ├── ScratchpadPane (scratchpad)
    │       ├── SnippetsPanel (snippets)
    │       ├── DocsPanel (docs)
    │       ├── CodeStatsPanel (codestats)
    │       ├── TodoPanel (todo)
    │       ├── RestClientPanel (restclient)
    │       ├── RenderPanel (preview)
    │       ├── PomodoroPanel (pomodoro)
    │       ├── FlowchartPanel (flowchart)
    │       ├── ProblemsPanel (problems - sidebar instance)
    │       ├── PainelIA (ia)
    │       └── placeholder (Frame vazio)
    └── Frame content_frame (stretch=always)
        └── PanedWindow V (v_paned)
            ├── Frame editor_area
            │   ├── TabBar
            │   ├── BreadCrumb
            │   ├── SearchBar (empacotada/desempacotada sob demanda)
            │   └── Frame editor_container
            │       ├── WelcomeScreen (ou)
            │       └── EditorPane[0..N] (empilhados, apenas um visível)
            └── Frame bottom_frame
                ├── panel_tabbar (problems | terminal | output | git | diff | debug | regex | sql)
                └── panel_content
                    ├── ProblemsPanel
                    ├── TerminalPanel
                    ├── OutputPanel
                    ├── GitPanel (bottom)
                    ├── DiffPane
                    ├── DebugPanel
                    ├── RegexPlayground
                    └── SQLStudioPanel (bottom, opcional)
```

---

## Padrões de Design

### Observer (Callbacks)

O Codis usa extensivamente callbacks para comunicação entre componentes:

- `ConfigManager.on_change(key, cb)` - notifica quando uma chave muda
- `ThemeManager.on_change(cb)` - notifica mudança de tema
- `I18n.on_change(cb)` - notifica mudança de idioma
- `EditorPane.on_change`, `on_cursor`, `on_title` - eventos do editor

### Event Bus (Hooks de Plugin)

Plugins se comunicam com o editor via `api.on(event, handler)`. O CodisApp dispara eventos via `_fire_hook(event, data)`. Erros em handlers de plugin são capturados e registrados no Output Panel sem derrubar o editor.

### Lazy Loading Condicional

Módulos opcionais (IA, LSP) são importados com `try/except ImportError`. Se a importação falhar, o Codis continua funcionando normalmente sem os recursos opcionais.

### Escrita Atômica

Tanto `ConfigManager` quanto `_save_session()` usam o padrão de escrita atômica: escrever em `.tmp` e depois `rename()` para o arquivo final, evitando corrupção em caso de crash.

---

## Constantes Globais

Definidas em `src/constants.py`:

| Constante | Valor | Descrição |
|-----------|-------|-----------|
| `APP_NAME` | `"Codis"` | Nome da aplicação |
| `APP_VERSION` | `"1.0.0"` | Versão |
| `APP_CODENAME` | `"Aether"` | Codinome |
| `CONFIG_DIR` | `~/.codis` | Diretório de dados |
| `CONFIG_FILE` | `~/.codis/config.json` | Arquivo de configuração |
| `PLUGINS_DIR` | `~/.codis/plugins` | Diretório de plugins |
| `SNIPPETS_DIR` | `~/.codis/snippets` | Diretório de snippets |
| `THEMES_DIR` | `~/.codis/themes` | Diretório de temas |
| `SESSIONS_FILE` | `~/.codis/session.json` | Arquivo de sessão |
| `WINDOW_SIZE` | `(1200, 800)` | Tamanho inicial da janela |
| `WINDOW_MIN` | `(640, 400)` | Tamanho mínimo |
| `LINT_DELAY` | `750` | Debounce do lint (ms) |
| `LARGE_FILE_BYTES` | `3 * 1024 * 1024` | Limite para modo large file |
| `EXT_LANG` | dict | Mapeamento extensão → linguagem |
| `LANG_LABEL` | dict | Mapeamento linguagem → nome de exibição |
| `LANG_COMMENT` | dict | Caractere de comentário por linguagem |
| `LANG_KEYWORDS` | dict | Palavras-chave por linguagem |
| `SHELL_OPTIONS` | dict | Shells detectados no sistema |

---

## Decisões de Design

### Por que Tkinter?

Tkinter está presente em toda instalação padrão do Python, em todas as plataformas. Zero dependências obrigatórias significa que qualquer usuário com Python 3.12+ pode executar o Codis com `python run.py` sem nenhum `pip install`. Isso torna a distribuição e adoção muito mais simples.

### Por que não threads para a UI?

Tkinter não é thread-safe. Todo código que toca widgets deve rodar na thread principal. Operações pesadas (LSP, lint, git, IA) são executadas em threads separadas, mas seus resultados são agendados de volta na thread principal via `root.after(0, callback)`.

### Por que mixins em vez de herança profunda?

`App` precisa de funcionalidades de múltiplos domínios (editor, LSP, build, settings...). Herança profunda tornaria o código impossível de navegar. Mixins permitem dividir a implementação em arquivos temáticos mantendo uma única classe coesa na superfície.

### Por que o EventBus é síncrono?

O Codis não tem operações que precisem de pub/sub assíncrono na camada de UI. Um bus síncrono é mais simples de debugar (stack trace completo), não tem risco de memory leaks por handlers não coletados, e a performance é mais que suficiente para a frequência de eventos de uma aplicação desktop.

### Empacotamento com PyInstaller

Para a estratégia atual de empacotamento (`--onefile` vs `--onedir`, compressão UPX por plataforma) e o motivo da mudança para `--onefile` (correção de segfault no Linux), veja [wiki/builds.md](builds.md).

---

## Referências Externas

- [Documentação do protocolo LSP](https://microsoft.github.io/language-server-protocol/)
- [Documentação do protocolo DAP](https://microsoft.github.io/debug-adapter-protocol/)
- [Tkinter docs (Python 3.12)](https://docs.python.org/3.12/library/tkinter.html)
- [PyInstaller docs](https://pyinstaller.org/en/stable/)
