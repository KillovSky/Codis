# 🧪 Testes

O Codis possui uma suíte de testes automatizados projetada para rodar completamente **headless** (sem display gráfico), permitindo que todo o CI/CD execute em runners Linux sem configuração especial. **1.683 casos** em 70 arquivos cobrem os subsistemas mais críticos - eventos, transformações de texto, IA, sintaxe das 25 linguagens, LSP, providers, plugins, diff, atualizador, chat, terminal e roteamento standalone.

---

## Executar os Testes

### Instalação

```bash
pip install pytest pytest-cov
```

Todas as outras dependências são as mesmas do Codis (`requirements.txt`).

### Comandos

```bash
# Todos os testes
pytest tests/

# Com relatório de cobertura no terminal
pytest tests/ --cov=src --cov-report=term-missing

# Com cobertura em HTML (abre em browser)
pytest tests/ --cov=src --cov-report=html
open htmlcov/index.html

# Arquivo específico
pytest tests/test_syntax_patterns.py -v

# Módulo específico por palavra-chave
pytest tests/ -k "TestDiffEngine"

# Parar no primeiro falho
pytest tests/ -x

# Ignorar testes de providers (mais rápido, sem I/O de rede simulado)
pytest tests/ --ignore=tests/test_ia_providers.py

# Saída mínima (apenas erros)
pytest tests/ -q
```

---

## Inventário por Arquivo

Lista tirada da coleta do pytest. A coluna de módulos mostra os `src.*` que cada arquivo importa.

| Arquivo de teste | Módulos exercitados | Casos |
|---|---|---:|
| `test_syntax_patterns.py` | `src.syntax.patterns` | 92 |
| `test_updater.py` | `src.updater`, `src.updater.backup`, `src.updater.result` | 87 |
| `test_text_transforms.py` | `src.editor.text_transforms` | 79 |
| `test_collab_ot.py` | `src.collab.ot` | 56 |
| `test_events_bus.py` | `src.events`, `src.events.bus`, `src.events.kinds` | 54 |
| `test_snippets.py` | `src.editor.snippets` | 50 |
| `test_paint_engine.py` | `src.ui` | 49 |
| `test_constants.py` | `src.constants` | 48 |
| `test_kui_colors.py` | `src.ui.k_ui._colors` | 46 |
| `test_suggest.py` | `src.editor` | 46 |
| `test_plugins.py` | `src.constants`, `src.plugins.api` | 45 |
| `test_theme_utils.py` | `src.theme.utils` | 43 |
| `test_events_pipeline_correlator_window.py` | `src.events.bus`, `src.events.correlator`, `src.events.kinds` | 41 |
| `test_remote_adapters.py` | `src.ui` | 40 |
| `test_ia_providers.py` | `src.ia.providers` | 38 |
| `test_approval_gate.py` | `src.ia.approval_gate` | 37 |
| `test_lsp_protocol.py` | `src.lsp.protocol` | 37 |
| `test_terminal_keys.py` | `src.terminal.keys` | 37 |
| `test_standalone.py` | `src.standalone` | 35 |
| `test_kui_world.py` | `src.ui.k_ui._world` | 30 |
| `test_i18n.py` | `src.i18n` | 28 |
| `test_kui_engine.py` | `src.ui.k_ui._colors`, `src.ui.k_ui._perf`, `src.ui.k_ui._engine` | 28 |
| `test_pyexec.py` | `src.terminal.backends`, `src.lsp` | 28 |
| `test_events_stream.py` | `src.events.bus`, `src.events.kinds` | 27 |
| `test_doom_embed.py` | `src.ui` | 26 |
| `test_theme_preview_panel.py` | `src.theme.base` | 25 |
| `test_terminal_vt.py` | `src.terminal.palette`, `src.terminal.vt` | 24 |
| `test_config.py` | `src.constants`, `src.config` | 23 |
| `test_calculator_safe_eval.py` | `src.ui.calculator_panel` | 22 |
| `test_diff_engine.py` | `src.diff.engine` | 22 |
| `test_theme.py` | `src.theme.base`, `src.theme.manager` | 21 |
| `test_trash_core.py` | - | 21 |
| `test_ia_context_builder.py` | `src.ia.context_builder` | 19 |
| `test_ia_pair.py` | `src.ia`, `src.ia.toolbox` | 18 |
| `test_lint_diag.py` | `src.lint.manager` | 18 |
| `test_wizard_options.py` | `src.theme.base`, `src.constants` | 18 |
| `test_toolbox_security.py` | `src.ia.toolbox` | 17 |
| `test_kui_perf.py` | `src.ui.k_ui._perf` | 16 |
| `test_collab_net.py` | `src.collab.net`, `src.collab.ot`, `src.collab.session` | 15 |
| `test_ia_session.py` | `src.ia` | 15 |
| `test_terminal_session.py` | `src.terminal.backends`, `src.terminal.session` | 15 |
| `test_ia_checkpoints.py` | `src.ia.checkpoints`, `src.ia` | 14 |
| `test_menu_bar_switching.py` | `src.ui` | 14 |
| `test_utils_and_sync.py` | `src.editor._pane_utils` | 14 |
| `test_sql_studio_helpers.py` | `src.ui.sql_studio` | 13 |
| `test_toolbox_checkpoints.py` | `src.ia.toolbox` | 13 |
| `test_zen_audio.py` | `src.ui` | 13 |
| `test_terminal_renderer.py` | `src.terminal.backends`, `src.terminal.session`, `src.terminal.view` | 12 |
| `test_collab_security.py` | `src.ui.collab_panel` | 11 |
| `test_exit_save.py` | - | 11 |
| `test_terminal_panel_logic.py` | `src.ui.terminal` | 11 |
| `test_terminal_shells.py` | `src.terminal.shells` | 11 |
| `test_webdev.py` | `src.ui` | 11 |
| `test_chat_irc.py` | `src.chat.adapters.irc`, `src.chat.models`, `src.chat.session` | 10 |
| `test_kui_bgrenderer.py` | `src.ui.k_ui._bgrenderer`, `src.ui.k_ui._perf` | 10 |
| `test_persistence_utils.py` | `src.ui._persistence_utils` | 10 |
| `test_app_embed.py` | `src.ui` | 7 |
| `test_chat_ws_adapters.py` | `src.chat.models`, `src.chat.session`, `src.chat.adapters.slack` | 6 |
| `test_ia_provider_races.py` | `src.ia` | 6 |
| `test_task_manager_helpers.py` | `src.ui.task_manager` | 6 |
| `test_workspace_trust_enforcement.py` | `src.ia.toolbox` | 6 |
| `test_chat_mqtt_ws.py` | `src.chat.adapters`, `src.chat.models`, `src.chat.session` | 5 |
| `test_font_safety.py` | - | 5 |
| `test_chat_http_adapters.py` | `src.chat.models`, `src.chat.session` | 4 |
| `test_chat_render.py` | `src.ui.chat_render` | 4 |
| `test_chat_xmpp.py` | `src.chat.models`, `src.chat.session` | 4 |
| `test_diag_hover_throttle.py` | `src.editor` | 4 |
| `test_ia_skills.py` | `src.ia.actions` | 4 |
| `test_lsp_shutdown.py` | `src.lsp`, `src.lsp.client` | 4 |
| `test_stall_trace.py` | `src.stall_trace` | 4 |
| **Total (70 arquivos)** | | **1.683** |

> Os 191 testes de `tests_gui/` (18 arquivos) exigem display real e **não** entram no total acima. `test_chat_mqtt_ws.py` e `test_chat_ws_adapters.py` (11 casos) só rodam com `websockets` instalado; sem ele, o pytest coleta 1.672 casos.

---

## Cobertura por Subsistema

> Medição de referência (`pytest --cov`), **não** regenerada automaticamente - rode `pytest tests/ --cov=src` para valores atuais.

| Subsistema | Stmts | Cobertos | Cobertura |
|---|---:|---:|---:|
| `events/` | 971 | 917 | **94%** |
| `diff/` | 101 | 95 | **94%** |
| `theme/` | 559 | 455 | **81%** |
| `lsp/` | 1 458 | 429 | 29% |
| `ia/` | 3 283 | 441 | 13% |
| `plugins/` | 1 955 | 294 | 15% |
| `lint/` | 539 | 116 | 21% |
| `syntax/` | 476 | 43 | 9% |
| `core` (config, i18n, standalone…) | 5 506 | 374 | 7% |
| `editor/` | 10 234 | 189 | 2% |
| `ui/` | 33 244 | 691 | 2% |
| **TOTAL** | **58 326** | **4 044** | **6,9%** |

> **Por que a cobertura global é baixa?** O projeto tem ~50 000 statements de código UI/Tkinter que só executam com display gráfico real. A cobertura dos módulos de **lógica pura** - events (94%), diff (94%), theme (81%), LSP, IA, plugins - é significativamente maior e reflete a qualidade real da suíte headless.

---

## Como a Suíte Funciona

### Headless sem display gráfico

O `conftest.py` instala um stub completo de Tkinter **antes** de qualquer importação do projeto. Isso permite que todos os módulos com `import tkinter` carreguem normalmente em runners CI sem display (sem `$DISPLAY` nem Xvfb).

O stub cobre: `tkinter`, `tkinter.ttk`, `tkinter.font`, `tkinter.filedialog`, `tkinter.messagebox`, `tkinter.simpledialog` e `tkinter.colorchooser`.

O stub `_W` (widget base) salva todos os kwargs recebidos em `__init__` e os retorna corretamente via `cget()`, permitindo testar widgets como `Chip`, `Toast` e `ProgressBar` sem display.

### Isolamento de configuração

A fixture `isolated_config_dir` (com `autouse=True`) redireciona `CONFIG_DIR` e `CONFIG_FILE` para um diretório temporário único por teste. Isso garante que:

- Os testes nunca leem nem escrevem na configuração real do usuário (`~/.codis/`)
- Cada teste começa com estado limpo
- Testes paralelos não interferem entre si

### Sem dependências de rede

Testes de provedores de IA verificam apenas comportamento de erro, cancelamento e configuração - sem fazer chamadas reais para Anthropic, OpenAI, Google ou Ollama. Testes de LSP verificam apenas a estrutura das mensagens JSON-RPC, não se comunicam com servers reais.

---

## Fixtures Disponíveis

Documentadas em `conftest.py`, disponíveis em todos os arquivos de teste via injeção do pytest:

| Fixture | Escopo | O que provê |
|---|---|---|
| `cfg` | `function` | Instância limpa de `ConfigManager` em diretório temporário |
| `tmp_py_file` | `function` | Arquivo `.py` temporário com código Python de exemplo |
| `tmp_js_file` | `function` | Arquivo `.js` temporário com código JavaScript de exemplo |
| `mock_workspace` | `function` | Pasta temporária com `main.py`, `utils.py`, `requirements.txt` e `README.md` |

---

## Convenções de Código de Teste

- **Classes de teste** agrupam casos relacionados: `class TestGetSet`, `class TestPersistence`.
- **Fixtures de escopo de função** garantem isolamento total entre testes.
- **Mocks mínimos** - `unittest.mock.MagicMock` apenas quando necessário, preferindo testar a lógica real.
- **Mensagens de asserção em português**, consistentes com o padrão do projeto.
- **Nomes descritivos**: `test_diff_empty_strings_returns_empty_hunks` é melhor que `test_edge_case_1`.
- **threading.Event** para sincronizar testes assíncronos com timeout explícito.

---

## Adicionar Novos Testes

1. Crie `tests/test_seu_modulo.py`
2. Importe o stub de Tkinter via `conftest.py` automaticamente (não precisa fazer nada)
3. Use as fixtures disponíveis para isolamento
4. Agrupe casos em classes por funcionalidade
5. Execute localmente: `pytest tests/test_seu_modulo.py -v`

Pull requests que adicionam features devem incluir testes cobrindo o caminho feliz e pelo menos um caso de erro esperado.

---

## Áreas Sem Cobertura Atual

As seguintes áreas ainda não têm testes automatizados e são alvo do roadmap:

- `src/ui/` - painéis e widgets de UI (exceto K-UI e alguns painéis como calculadora e embed)
- `src/ia/iaspace.py` - UI do painel de IA
- `src/ia/toolbox.py` - execução das 40 ferramentas (requer mocks de I/O complexos)
- `src/standalone.py` - funções `run_*` individuais (abrem janelas Tkinter)
- Testes de integração (múltiplos subsistemas juntos)
- Testes de performance (tempo de carregamento, uso de memória)

Contribuições de testes para essas áreas são especialmente bem-vindas. Veja [CONTRIBUTING.md](../CONTRIBUTING.md) para o fluxo de pull request.

---

## CI/CD

A suíte roda automaticamente no GitHub Actions (job `quality` do `pipeline.yml`) a cada push e pull request que mude código, em paralelo ao build. Veja [wiki/builds.md](builds.md) para detalhes dos workflows.

```
pytest tests/ --cov=src --cov-report=xml --junit-xml=test-results.xml
```

O CI **falha** se qualquer teste falhar - isso é intencional. Relatórios de cobertura são publicados como artefatos de cada run para acompanhamento de tendência ao longo do tempo.

---

## Testes do Navegador Remoto e da escala da interface

- `tests/test_remote_adapters.py` - parsers puros (ls, LIST Unix/IIS, índices Apache/nginx, URLs) e protocolos contra **servidores reais** levantados em threads: `http.server`, `pyftpdlib` (FTP) e `wsgidav`+`cheroot` (WebDAV). São pulados se as bibliotecas não estiverem instaladas (`pip install pyftpdlib wsgidav cheroot`).
- SSH real é opcional: com um `sshd` local de teste, exporte `CODIS_TEST_SSH="127.0.0.1:2222:usuario:senha"` (a pasta `~/proj` do usuário precisa ter `a.txt`, `2024` e `dir with space/`) antes de rodar o pytest.
- `tests_gui/test_remote_browser_panel.py` - painel com servidor em memória (respostas fora de ordem, conflito, falha de upload, sync ao salvar) e um teste ponta a ponta com FTP real.
- `tests_gui/test_ui_scale.py` - escala da interface (sem acúmulo, widgets novos, barras fixas, Treeview).
