# ⚙️ Configurações

As configurações do Codis são armazenadas em `~/.codis/config.json` e gerenciadas pela classe `ConfigManager` (`src/config.py`). A janela de Configurações (`src/ui/settings_window.py`) organiza todas as opções em **24 categorias** na sidebar lateral, cada uma documentada nesta página.

---

## Como Acessar as Configurações

| Caminho | O que abre |
|---|---|
| **Configurações → Abrir Configurações...** ou `Ctrl+,` | Janela gráfica com as 24 categorias, campos editáveis e busca |
| **Configurações → Atalhos de Teclado...** | Editor dedicado de remapeamento de teclas - ver [Key Remapper](workspace-projeto.md#key-remapper-srcuikeyremapperpy) |
| **Configurações → Redefinir / Wizard de Setup...** | Assistente de primeira configuração / reset completo |
| **Configurações → Avançado → Editar JSON Diretamente...** | Editor de `config.json` com syntax highlighting, para quem prefere editar o arquivo bruto |

> O menu **Configurações** foi reorganizado para não duplicar itens que já existem em outros menus: **Tema** vive em `Exibir → Tema` (ver [Temas](temas.md)) e **Acessibilidade** tem entrada própria aqui, mas seu conteúdo completo está em [Acessibilidade](acessibilidade.md).

Alterações feitas na janela de Configurações aplicam-se **imediatamente** à sessão atual, sem reiniciar o Codis - cada categoria é renderizada sob demanda e mantida em cache, então alternar entre elas é instantâneo mesmo em janelas com muitas opções.

---

## As 24 Categorias

### ✏ Editor

Opções fundamentais de edição de texto.

| Chave | Tipo | Padrão | Descrição |
|---|---|---|---|
| `font_family` | string | `"Consolas"` | Fonte monoespaçada do editor |
| `font_size` | int | `13` | Tamanho da fonte do editor (px) |
| `font_ligatures` | bool | `false` | Ativa ligaduras tipográficas - ver [Ligatures](inteligencia-editor.md#ligatures-srceditorligaturespy) |
| `ligature_mode` | string | `"pseudo"` | `"pseudo"` \| `"native"` \| `"both"` \| `"off"` |
| `tab_size` | int | `4` | Espaços por nível de indentação |
| `use_spaces` | bool | `false` | `true` = espaços; `false` = tabs |
| `word_wrap` | bool | `true` | Quebra de linha suave |
| `cursor_style` | string | `"line"` | `"line"` \| `"block"` \| `"underline"` |
| `cursor_blinking` | string | `"blink"` | `"blink"` \| `"solid"` \| `"smooth"` |
| `smooth_scroll` | bool | `true` | Scroll suave |
| `scroll_speed` | int | `3` | Velocidade do scroll |
| `multi_cursor` | bool | `true` | Suporte a múltiplos cursores - ver [Multi-Cursor](inteligencia-editor.md#multi-cursor-srceditormulticursorpy) |
| `multicursor_lines` | bool | `true` | Multicursor por linhas: `Alt+Clique` no gutter marca linhas e as teclas se aplicam a todas; `Esc` limpa |
| `render_whitespace` | string | `"none"` | `"none"` \| `"boundary"` \| `"all"` |
| `indent_guides` | bool | `true` | Guias verticais de indentação |
| `bracket_matching` | bool | `true` | Destaca pares de parênteses/colchetes/chaves |
| `highlight_current_line` | bool | `true` | Destaca a linha do cursor |
| `line_numbers` | bool | `true` | Exibe numeração de linhas |
| `relative_numbers` | bool | `false` | Numeração relativa ao cursor (estilo Vim) |
| `breadcrumbs` | bool | `true` | Exibe o breadcrumb de caminho |
| `minimap` | bool | `true` | Exibe o minimapa |
| `encoding` | string | `"utf-8"` | Codificação padrão de arquivos |
| `eol` | string | `"\n"` | Fim de linha: `"\n"` (LF) \| `"\r\n"` (CRLF) \| `"\r"` (CR) |

### − Editor Avançado

Recursos de produtividade documentados em profundidade em [Inteligência do Editor](inteligencia-editor.md).

| Chave | Tipo | Padrão | Descrição |
|---|---|---|---|
| `sticky_scroll` | bool | `true` | Cabeçalhos de escopo colados no topo |
| `code_folding` | bool | `true` | Dobramento de regiões de código |
| `inline_blame` | bool | `false` | Informações de `git blame` no editor |
| `inline_blame_mode` | string | `"cursor"` | `"cursor"` \| `"all"` |
| `color_preview` | bool | `true` | Amostras de cor para valores hexadecimais |
| `inlay_hints` | bool | `false` | Dicas de tipo injetadas pelo LSP |
| `smart_tips` | bool | `true` | Ativa as dicas contextuais - ver [Smart Tips](inteligencia-editor.md#smart-tips-srceditorsmarttipspy) |
| `smart_tips_interval` | int | `30` | Intervalo entre análises (ms) |
| `smart_tips_large_file_lines` | int | - | Limiar de linhas para considerar "arquivo grande" |
| `smart_tips_huge_file_lines` | int | - | Limiar de linhas para "arquivo enorme" |
| `smart_tips_big_function_lines` | int | - | Limiar de linhas para "função grande" |
| `smart_tips_long_line_chars` | int | - | Limiar de caracteres para "linha longa" |
| `smart_tips_deep_nesting` | int | - | Profundidade de aninhamento considerada excessiva |
| `smart_tips_todo_threshold` | int | - | Quantidade de TODOs antes de avisar |
| `smart_tips_generic_var_threshold` | int | - | Tolerância para nomes de variável genéricos |

### 💬 Autocomplete

| Chave | Tipo | Padrão | Descrição |
|---|---|---|---|
| `autocomplete` | bool | `true` | Ativa o autocomplete |
| `autocomplete_delay` | int | `200` | Delay para exibir sugestões (ms) |
| `snippets` | bool | `true` | Ativa expansão de snippets |
| `parameter_hints` | bool | `true` | Dicas de parâmetros (LSP) |
| `hover_docs` | bool | `true` | Documentação ao passar o mouse (KDocs/LSP) |

### A Aparência

| Chave | Tipo | Padrão | Descrição |
|---|---|---|---|
| `theme` | string | `"fluent_dark"` | Tema ativo - ver [Temas](temas.md) |
| `language` | string | `"pt-BR"` | Idioma da interface - ver [Idiomas](#idiomas) |
| `ui_font_family` | string | `"Segoe UI"` | Fonte da interface (menus, labels) |
| `ui_font_size` | int | `10` | Tamanho da fonte da interface |
| `ui_scale` | int | `0` | Escala geral da interface em %. `0` = automático (ajusta à resolução/DPI da tela); `50`–`250` = manual. Aplicado ao vivo |
| `remote_sync_on_save` | bool | `true` | Reenvia ao servidor arquivos remotos ao salvar - ver [Remoto](remote-ssh.md) |
| `activity_bar` | bool | `true` | Exibe a Activity Bar |
| `show_welcome` | bool | `true` | Exibe tela de boas-vindas na inicialização |

### 🖱 Cursor

Categoria especial: em vez de uma lista simples de chaves, embute diretamente o `CursorPickerPanel` (`src/ui/cursor_manager.py`) - o seletor visual de cursor customizado (emoji, texto ou imagem). Ver [Cursor Customizável](sistema-performance.md#cursor-customizável-srcuicursormanagerpy) para os detalhes completos de implementação.

### 🖥 Interface / Layout

| Chave | Tipo | Padrão | Descrição |
|---|---|---|---|
| `sidebar_width` | int | `320` | Largura inicial da sidebar (px) |
| `panel_height` | int | `240` | Altura inicial do painel inferior (px) |
| `minimap_width` | int | `110` | Largura do minimapa (px) |
| `minimap_scale` | int | `3` | Escala do minimapa |
| `window_width` / `window_height` | int | - | Dimensões da janela principal salvas entre sessões |
| `max_recent` | int | `20` | Máximo de entradas na lista de recentes |

### 💾 Salvamento

| Chave | Tipo | Padrão | Descrição |
|---|---|---|---|
| `autosave` | bool | `false` | Salva automaticamente após inatividade |
| `autosave_delay` | int | `30` | Intervalo de autosave em segundos (5–600) |
| `format_on_save` | bool | `false` | Formata o documento ao salvar |
| `trim_trailing_whitespace` | bool | `true` | Remove espaços no fim das linhas ao salvar |
| `insert_final_newline` | bool | `true` | Garante newline no final do arquivo |
| `session_restore` | bool | `false` | Restaura os arquivos abertos ao reiniciar |

### 🔍 Lint

| Chave | Tipo | Padrão | Descrição |
|---|---|---|---|
| `lint_enabled` | bool | `true` | Ativa o sistema de lint |
| `lint_python` | bool | `true` | Lint para Python (pylint, mypy, flake8...) |
| `lint_js` | bool | `true` | Lint para JS/TS (ESLint) |
| `lint_html` | bool | `true` | Lint para HTML |
| `lint_delay` | int | `750` | Delay após digitação para disparar lint (ms) |
| `eslint_path` / `pylint_path` / `flake8_path` / `mypy_path` / `prettier_path` | string | nome do binário | Caminho dos executáveis externos usados pelo lint |

### 🔤 LSP (Language Server Protocol)

Categoria própria, antes ausente da documentação - controla a integração com servidores de linguagem usados por Go to Definition, Find References, rename simbólico e mais (ver [Editor](editor.md) e [Arquitetura](arquitetura.md)).

| Chave | Tipo | Padrão | Descrição |
|---|---|---|---|
| `lsp_enabled` | bool | `true` | Habilita integração com servidores LSP |
| `lsp_hover_enabled` | bool | `true` | Documentação do LSP ao passar o mouse sobre símbolos |
| `lsp_format_enabled` | bool | `true` | *(presente na UI de configurações, mas sem efeito - veja nota abaixo)* |
| `lsp_diagnostics` | bool | `true` | Exibe diagnósticos do LSP no painel Problemas |

### 🌿 Git

| Chave | Tipo | Padrão | Descrição |
|---|---|---|---|
| `git_enabled` | bool | `true` | Ativa a integração Git |
| `git_decorations` | bool | - | Decorações visuais de status Git na file tree |
| `git_auto_fetch` | bool | `false` | Fetch automático periódico |
| `git_fetch_interval` | int | `180` | Intervalo de auto-fetch em segundos |
| `updater_url` | string | `"KillovSky/Codis"` | Repositório GitHub para verificação de atualizações (`owner/repo`) |

### 💻 Terminal

| Chave | Tipo | Padrão | Descrição |
|---|---|---|---|
| `default_shell` | string | detectado automaticamente | Shell padrão - ver [Terminal](terminal.md) |
| `terminal_font_size` | int | `12` | Tamanho da fonte do terminal |
| `terminal_max_lines` | int | - | Limite de linhas no scroll-back |

### 📁 Árvore de Arquivos (FileTree)

Categoria própria, antes sem cobertura na wiki.

| Chave | Tipo | Padrão | Descrição |
|---|---|---|---|
| `show_hidden_files` | bool | `false` | Exibe arquivos/pastas que começam com ponto (`.`) |
| `respect_ignore_files` | bool | `true` | Oculta arquivos listados no `.gitignore` da árvore e da busca |

### 🌐 Render

Categoria própria, antes sem cobertura na wiki.

| Chave | Tipo | Padrão | Descrição |
|---|---|---|---|
| `render_internal` | bool | `true` | Usa o renderizador interno para Markdown/HTML; quando desativado, usa o navegador padrão do sistema |

### 🕒 Timeline

Controla o sistema de snapshots automáticos - ver [File Timeline](backups-lixeira-arquivos.md#file-timeline-srcuifiletimelinepy).

| Chave | Tipo | Padrão | Descrição |
|---|---|---|---|
| `timeline_max_backups` | int | `20` | Máximo de snapshots mantidos por arquivo (5–100) |
| `timeline_max_age_days` | int | `30` | Backups mais antigos que isso são removidos automaticamente (1–365 dias) |

### 🔀 Flowchart

| Chave | Tipo | Padrão | Descrição |
|---|---|---|---|
| `flowchart_auto` | bool | `false` | Gera o diagrama de fluxo automaticamente ao editar o arquivo |
| `flowchart_max_depth` | int | `3` | Profundidade máxima de chamadas analisadas (1–8) |

### ⏱ Pomodoro

| Chave | Tipo | Padrão | Descrição |
|---|---|---|---|
| `pomodoro_work_min` | int | `25` | Duração de cada sessão de trabalho em minutos (5–90) |
| `pomodoro_break_min` | int | `5` | Duração da pausa curta em minutos (1–30) |
| `pomodoro_long_break_min` | int | `15` | Duração da pausa longa após um ciclo completo (5–60) |

### P Performance

| Chave | Tipo | Padrão | Descrição |
|---|---|---|---|
| `kui_perf` | string | `"balanced"` | Perfil de animações da K-UI |
| `hl_idle_ms` | int | `60` | Intervalo de atualização do highlighting em idle (ms) |
| `perf_auto_mode` | bool | `true` | Detecta travamentos e aplica modo performance automaticamente |
| `perf_freeze_threshold_ms` | int | `500` | Milissegundos sem resposta antes de considerar travamento |
| `perf_mode` | string | `"auto"` | `"auto"` \| `"balanced"` \| `"fast"` \| `"minimal"` |
| `perf_native_bindings` | bool | `false` | Extensões C nativas (requer compilação; desabilitado por padrão) |
| `perf_kui_fallback` / `perf_keep_minimap` / `perf_keep_sticky_scroll` / `perf_keep_heatmap` / `perf_keep_rhythm` / `perf_keep_lint` / `perf_keep_kui_anim` | bool | - | Toggles individuais do que **manter ativo** mesmo em modo performance - ver [Modo Performance](sistema-performance.md#modo-performance-srcuiperformancepanelpy) |
| `autocomplete_min_chars` | int | `1` | Mínimo de caracteres para disparar autocomplete |
| `autocomplete_max_items` | int | `24` | Máximo de itens na lista de sugestões |
| `large_file_mb` | int | `3` | Limiar em MB para ativar o modo de arquivo grande |
| `search_max_results` | int | `2000` | Máximo de resultados na busca em arquivos |

### 🔒 Security

| Chave | Tipo | Padrão | Descrição |
|---|---|---|---|
| `workspace_trust` | bool | `true` | Exibe diálogo de confiança ao abrir pastas desconhecidas - ver [Workspace Trust](workspace-projeto.md#workspace-trust-srcuiworkspacetrustpy) |

### ⚙ Avançado

| Chave | Tipo | Padrão | Descrição |
|---|---|---|---|
| `font_min` | int | `7` | Tamanho mínimo de fonte permitido pelo zoom |
| `font_max` | int | `36` | Tamanho máximo de fonte permitido pelo zoom |
| `debug_dap_enabled` | bool | `true` | Usa debugpy/DAP real (breakpoints, stepping, variáveis) quando disponível. Desative para forçar execução simples no Painel de Debug. |
| `debug_dap_port` | int | `5678` | Porta local usada pela conexão DAP com o debugpy. Mude se houver conflito com outra ferramenta usando a mesma porta (ex: VS Code). |

> O **Editor JSON Diretamente** (edição bruta do `config.json`) é acessado pelo menu **Configurações → Avançado**, não por esta categoria - ela trata apenas dos limites de zoom de fonte e do depurador.

Ver também: [Debug](debug.md)

### ♿ Acessibilidade

| Chave | Tipo | Padrão | Descrição |
|---|---|---|---|
| `a11y_high_contrast` | bool | `false` | Alto contraste (WCAG AA) |
| `a11y_colorblind_mode` | string | `""` | `""` \| `"protanopia"` \| `"deuteranopia"` \| `"tritanopia"` \| `"achromatopsia"` |
| `a11y_screen_reader` | bool | `false` | Anúncios TTS para leitores de tela |
| `a11y_keyboard_nav` | bool | `true` | Navegação aprimorada por Tab e setas |
| `a11y_large_ui_font` | bool | `false` | Força fonte de UI mínima de 13pt |
| `a11y_reduce_motion` | bool | `false` | Desativa animações e transições |

Ver também: [Acessibilidade](acessibilidade.md)

### 🖧 SSH Remoto

| Chave | Tipo | Padrão | Descrição |
|---|---|---|---|
| `ssh_default_port` | int | `22` | Porta padrão para novos perfis |
| `ssh_connect_timeout` | int | `15` | Timeout de conexão (segundos) |
| `ssh_keepalive_interval` | int | `30` | Intervalo keepalive em segundos (`0` desabilita) |
| `ssh_agent_forwarding` | bool | `false` | Forwarding do agente SSH |
| `ssh_strict_host_key` | bool | `true` | Verifica a chave do host estritamente |
| `ssh_sftp_buffer_kb` | int | `64` | Buffer SFTP em KB |
| `ssh_open_in_editor` | bool | `true` | Baixa e abre arquivo remoto diretamente no editor |

> O **Túnel de Debug SSH** (botão **Abrir Túnel Debug** no painel remoto) usa as portas informadas diretamente no próprio diálogo, não as chaves `debug_dap_*` - essas pertencem ao Painel de Debug **local** (categoria Avançado). São mecanismos independentes; veja [Debug Remoto](remote-ssh.md#debug-remoto-dap-via-túnel-ssh) e [Debug](debug.md).

Ver também: [SSH Remoto](remote-ssh.md)

### 👥 Colaboração

| Chave | Tipo | Padrão | Descrição |
|---|---|---|---|
| `collab_enabled` | bool | `false` | Habilita ícone de colaboração na Activity Bar |
| `collab_port` | int | `9001` | *(não utilizada pelo painel atual - veja nota abaixo)* |
| `collab_max_peers` | int | `8` | Máximo de participantes simultâneos |
| `collab_show_cursors` | bool | `true` | Exibe cursores dos participantes remotos |
| `collab_show_chat` | bool | `true` | Chat integrado visível |
| `collab_autosave_sync` | bool | `true` | Sincroniza auto-save entre participantes |

Ver também: [Colaboração](colaboracao.md)

### 🔌 Plugins

| Chave | Tipo | Padrão | Descrição |
|---|---|---|---|
| `plugin_isolation` | bool | `true` | Plugins marcados com `isolated=True` rodam em processo separado |
| `plugin_timeout_s` | int | `10` | Timeout de chamadas síncronas ao worker (segundos) |
| `plugin_heartbeat_s` | int | `30` | Timeout de heartbeat - processo sem resposta é reiniciado |
| `plugin_max_restarts` | int | `3` | Reinícios automáticos antes de desabilitar o plugin |
| `plugin_sandbox_network` | bool | `false` | Bloqueia acesso à rede em workers isolados (experimental) |

Ver também: [Plugins - Processo Isolado](plugins.md#plugins-em-processo-isolado)

### 🎨 Sintaxe Customizada

Esta categoria abre o **CustomKeywordsPanel** em vez de uma lista de chaves simples. Permite definir palavras-chave adicionais por linguagem com cor de destaque personalizada, expandindo o highlight de sintaxe além dos padrões embutidos.

As keywords são persistidas em `config.json` na chave `custom_keywords` como lista de objetos:

```json
"custom_keywords": [
  { "keyword": "minha_funcao", "color": "#ff79c6", "lang": "python" },
  { "keyword": "CONSTANTE",    "color": "#f1fa8c", "lang": "javascript" }
]
```

| Campo | Tipo | Descrição |
|---|---|---|
| `keyword` | string | Palavra-chave exata a ser destacada (sensível a maiúsculas) |
| `color` | string | Cor no formato `#rrggbb` ou `#rgb` |
| `lang` | string | Linguagem alvo (ex: `python`, `javascript`, `any` para todas) |

As keywords customizadas são aplicadas pelo highlighter de sintaxe com prioridade **após** os padrões embutidos, podendo sobrescrever a cor de termos já definidos. Alterações são aplicadas imediatamente ao arquivo ativo sem reiniciar o Codis.

---

## Chaves Complementares do `DEFAULT_CONFIG`

Chaves presentes em `DEFAULT_CONFIG` (`src/constants.py`) que complementam as categorias acima.

| Chave | Tipo | Padrão | Descrição |
|-------|------|--------|-----------|
| `menubar_position` | string | `"top"` | Posição da barra de menus: `"top"` ou `"bottom"` (menu Exibir) |
| `rulers` | lista | `[]` | Colunas das réguas verticais do editor |
| `lint_php` | bool | `true` | Lint para PHP |
| `recent_files` / `recent_folders` | lista | `[]` | Histórico de arquivos e pastas recentes (gravado automaticamente) |
| `exit_save_mode` | string | `"ask"` | Ao sair com alterações: `"ask"` pergunta, `"save"` sempre salva, `"discard"` descarta |
| `session_save_on_exit` | bool | `true` | Grava abas e pasta ao sair; desligado, o Codis abre limpo |
| `confirm_delete` | bool | `true` | Pede confirmação antes de excluir arquivos |
| `trash_backend` | string | `"workspace"` | `"workspace"` move para `.codis_trash/` do projeto; `"system"` usa a lixeira do SO (`send2trash`, com fallback para a do projeto) |
| `trash_retention_days` | int | `0` | Dias de retenção na lixeira (`0` = sem limite) |
| `trash_max_mb` | int | `0` | Tamanho máximo da lixeira em MB (`0` = sem limite) |
| `kui_enabled` | bool | `false` | Liga o [K-UI](kui.md) |
| `perf_keep_inline_blame` / `perf_keep_inlay_hints` | bool | `false` | Mantém blame inline / inlay hints ativos no modo performance |
| `perf_keep_color_preview` / `perf_keep_ligatures` / `perf_keep_autocomplete` / `perf_keep_folding` / `perf_keep_hl` | bool | `true` | Mantém cada recurso ativo no [modo performance](sistema-performance.md#modo-performance-srcuiperformancepanelpy) |
| `github_token` | string | `""` | PAT do GitHub (limite de API do [Atualizador](updater.md) e da loja de plugins) |
| `auto_check_updates` | bool | `true` | Verifica atualizações silenciosamente ao iniciar |

---

## Chaves Avançadas e de Estado

Estas chaves são lidas pelo código (com valor padrão no ponto de uso), mas **não aparecem na janela de Configurações**; várias são gravadas automaticamente pela interface. Edite `config.json` só se souber o que está fazendo.

| Chave | Tipo | Padrão | Descrição |
|-------|------|--------|-----------|
| `user_name` | string | `""` | Autor padrão preenchido nos [templates de projeto](workspace-projeto.md) |
| `actbar_visible_items` | lista \| null | `null` (todos) | Itens visíveis da Activity Bar (gravado pelo menu de personalização) |
| `cursor_preset` | string | `"system"` | Preset do [cursor customizado](sistema-performance.md) |
| `cursor_color` | string | `"#cdd6f4"` | Cor do cursor customizado |
| `cursor_size` | int | `22` | Tamanho do cursor customizado |
| `cursor_emoji` | string | `"✦"` | Emoji usado como cursor (preset emoji) |
| `terminal_scrollback` | int | `10000` | Linhas mantidas no scrollback |
| `terminal_conpty` | bool | `true` | Usa ConPTY no Windows quando disponível |
| `terminal_load_shell_rc` | bool | `true` | Carrega `.bashrc`/`.zshrc` ao abrir o shell |
| `ia_provedor` | string | primeiro provedor disponível | Provedor de IA ativo |
| `ia_provedores_cfg` | dict | `{}` | Configuração por provedor (modelo, URL, chave) |
| `ia_mode` | string | `"edit"` | Modo da IA (Perguntar / Editar / Agente) |
| `ia_context_tokens` | int | `24000` (mínimo `4000`) | Orçamento de tokens de contexto |
| `ia_auto_approve_levels` | lista | `["SAFE"]` | Níveis de risco aprovados automaticamente pelo [Approval Gate](ia.md#approval-gate-aprovação-de-ações) |
| `ia_privacy_warning_shown` | bool | `false` | Aviso de privacidade da IA já exibido |
| `plugin_store_repo` | string | `""` (usa o repositório oficial) | Repositório da loja de plugins |
| `plugin_store_branch` | string | `"main"` | Branch da loja de plugins |
| `kui_dock_pinned` | bool | `false` | Dock do K-UI fixado |
| `screensaver_idle_s` | int | `9999` | Segundos de inatividade para o protetor de tela do K-UI |

> Os padrões acima refletem os `.get(chave, padrão)` do código na versão 1.0.0; se divergirem, o código é a fonte da verdade.

---

## Idiomas

A interface do Codis é internacionalizada via `src/i18n.py`:

| Código | Idioma |
|---|---|
| `pt-BR` | Português (Brasil) - idioma base, usado como fallback |
| `en` | English |
| `es` | Español |
| `fr` | Français |
| `de` | Deutsch |
| `zh-CN` | 中文（简体） |
| `ja` | 日本語 |
| `ko` | 한국어 |
| `ru` | Русский |
| `uk` | Українська |
| `pl` | Polski |
| `nl` | Nederlands |
| `tr` | Türkçe |
| `it` | Italiano |

A troca de idioma reconstrói os menus imediatamente, sem reiniciar o Codis. Toda chave de tradução ausente em um idioma cai automaticamente para o texto em `pt-BR` (idioma base do sistema de i18n).

---

## Comportamento do ConfigManager

- **Escrita atômica:** salva primeiro em `.json.tmp` e depois renomeia para `config.json`, evitando corrupção em caso de falha durante a escrita.
- **Modo degradado:** se `~/.codis/` não puder ser criado (permissões), o ConfigManager opera em memória, sem persistência entre sessões.
- **Backup automático:** se `config.json` estiver corrompido, é renomeado para `.json.bak` e os padrões são usados.
- **Coerção de tipos:** valores numéricos lidos como string são convertidos automaticamente.
- **Callbacks de mudança:** componentes podem se registrar para receber notificações quando uma chave específica muda, via `cfg.on_change("theme", callback)`.
