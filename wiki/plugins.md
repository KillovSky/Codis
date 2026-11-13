# 🧩 Plugins

O Codis possui um sistema de plugins completo com sandbox estática, API rica e instalação segura.

---

## Estrutura de um Plugin

Um plugin é um módulo Python (`.py`) ou um arquivo ZIP contendo `plugin.py`. A estrutura mínima:

```python
# meu_plugin.py
from src.plugins.api import PluginAPI, PluginManifest

MANIFEST = PluginManifest(
    name="meu-plugin",
    version="1.0.0",
    author="Seu Nome",
    description="O que o plugin faz.",
    icon="🔧",
    api_version="1.0",
)

def activate(api: PluginAPI) -> None:
    """Chamado quando o plugin é ativado."""
    api.show_message("Plugin ativado!")

def deactivate() -> None:
    """Chamado quando o plugin é desativado. Opcional."""
    pass
```

---

## Instalação de Plugins

### Método 1: Copiar para a pasta de plugins

Copie o arquivo `.py` (ou `.zip`) para `~/.codis/plugins/`. O Codis carrega os plugins nessa pasta automaticamente na inicialização.

### Método 2: Via Interface

1. Menu **Plugins → Instalar Plugin**
2. Selecione o arquivo `.py` ou `.zip` no diálogo.
3. O Codis executa a **análise estática de segurança** antes de instalar.

### Método 3: Via Painel de Plugins

Na sidebar → ícone 🔌 (Extensões), clique em **Instalar** e selecione o arquivo.

---

## Sandbox de Segurança

Antes de carregar qualquer plugin, o `PluginLoader` realiza uma **análise estática via AST** que verifica:

- Imports de módulos perigosos (`os`, `subprocess`, `sys`, `socket`, etc.) - classificados como `blocked` ou `warned`
- Uso de `eval`, `exec`, `__import__`
- Acesso a atributos internos do Python

O resultado da análise é classificado em:

| Classificação | Comportamento |
|--------------|--------------|
| `clean` | Plugin carregado automaticamente |
| `warned` | Diálogo de confirmação exibido ao usuário |
| `blocked` | Plugin bloqueado por padrão; usuário deve confirmar explicitamente |

Uma vez que o usuário aceita um plugin, ele é adicionado a `~/.codis/trusted_plugins.json` e carregado automaticamente nas próximas inicializações.

> O diálogo de confirmação só aparece ao **instalar pela interface** (`Plugins → Instalar Plugin` ou arrastar o arquivo). Um arquivo colocado diretamente em `~/.codis/plugins/` sem nunca ter passado pela instalação é verificado na inicialização automática do Codis - se contiver imports `blocked`, é recusado silenciosamente (sem diálogo), até que seja instalado uma vez pela interface e marcado como confiável.

---

## API do Plugin

O objeto `PluginAPI` é passado para a função `activate()` e fornece acesso controlado ao Codis.

### Informações e Notificações

```python
api.show_message("Mensagem")                          # Notificação na status bar (3s)
api.show_message("Mensagem", level="warning")          # Níveis: "info", "warning", "error"
api.show_message("Permanente", duration=0)              # 0 = não desaparece sozinha
api.show_info("Título", "Texto")                        # Diálogo modal informativo
api.show_warning("Título", "Texto")                      # Diálogo modal de aviso
api.show_error("Título", "Texto")                        # Diálogo modal de erro
```

> A versão do Codis está disponível como `APP_VERSION` em `src.constants` (não há um método `api.get_version()`).

### Editor

```python
editor = api.get_active_editor()           # EditorPane ativo (ou None)
content = api.get_text()                   # Texto completo do editor ativo
selection = api.get_selection()            # Texto selecionado
api.insert_at_cursor("texto")              # Insere no cursor
api.replace_selection("novo")              # Substitui a seleção
api.set_text("novo conteúdo")              # Substitui todo o conteúdo
api.get_cursor_position()                  # (linha, coluna)
api.set_cursor_position(42, 0)             # Move o cursor para linha 42, coluna 0
api.scroll_to_line(42)                     # Rola a view até a linha 42
api.get_active_file()                      # Path do arquivo ativo (ou None)
api.get_active_lang()                      # Linguagem do arquivo (ex: "python")
```

### Sintaxe e Temas

```python
# Registrar um novo tema (veja todos os campos obrigatórios em temas.md)
from src.theme.base import Theme
api.register_theme(Theme(name="meu-tema", dark=True, bg="#1a1a2e", fg="#e0e0e0", **outros_campos))

# Destacar uma região no editor (coordenadas linha/coluna, não strings tipo "1.0")
api.add_highlight(line_start=1, col_start=0, line_end=1, col_end=10,
                   tag="erro", bg="#ff000033")

# Adicionar hint inline (texto fantasma após a posição)
api.add_inlay_hint(line=10, col=5, text="→ string")

# Adicionar ícone no gutter (margem esquerda)
api.add_gutter_icon(line=10, icon="⚡", tooltip="Linha quente")
```

### Comandos e Menus

```python
# Registrar um comando (disponível na Paleta de Comandos)
api.register_command("meu.comando", "Descrição do Comando", minha_funcao)

# Adicionar item de menu
api.add_menu_item("Plugins/Meu Plugin", "Fazer Algo", minha_funcao)

# Registrar atalho de teclado
api.add_keybinding("<Control-Alt-m>", "meu.comando")
```

### Painéis e Sidebar

```python
# Adicionar um painel na sidebar com botão na Activity Bar
def criar_painel(parent):
    import tkinter as tk
    frame = tk.Frame(parent, bg="black")
    tk.Label(frame, text="Meu Painel", fg="white", bg="black").pack()
    return frame

api.add_sidebar_panel("meu-painel", "🔧", criar_painel, "Meu Painel", position="bottom")
```

### Eventos (Hooks)

O nome do evento é uma string; os eventos abaixo formam o conjunto `CORE_EVENTS` (`src/plugins/api.py`). Qualquer outro nome continua válido para eventos entre plugins (`api.emit`).

```python
# Registrar handler para um evento
def ao_salvar(path):
    print(f"Arquivo salvo: {path}")

api.on("file_save", ao_salvar)
api.on("file_open", lambda path: ...)
api.on("text_change", lambda editor: ...)
api.on("tab_change", lambda idx: ...)
api.on("lint_complete", lambda diags: ...)
api.on("cursor_move", lambda pos: ...)
api.on("theme_change", lambda theme: ...)
api.on("lang_change", lambda lang: ...)
api.on("before_format", lambda editor: ...)
api.on("after_format", lambda editor: ...)

api.once("app_ready", lambda: ...)   # dispara uma única vez
api.off("file_save", ao_salvar)       # remove o handler
api.emit("meu_plugin.evento", dados)  # evento customizado entre plugins
```

Eventos do núcleo: `file_open`, `file_save`, `file_close`, `text_change`, `cursor_move`, `theme_change`, `lang_change`, `app_ready`, `app_exit`, `before_save`, `after_save`, `before_format`, `after_format`, `lint_complete`, `terminal_line`, `git_commit`, `tab_change`, `workspace_set`, `workspace_close`, `plugin_loaded`, `plugin_unloaded`, `selection_change`, `fold_change`.

> O barramento interno `EventKind` (93 eventos em `src/events/kinds.py`, usado por pipelines, correlator e streams) é uma camada separada, voltada ao código do Codis; plugins usam os nomes de hook acima.

### Canal de Saída

```python
# Canal de saída próprio do plugin
canal = api.create_output_channel("Meu Plugin")
canal.append("Linha de log")
canal.appendLine("Outra linha")
canal.clear()
```

### Armazenamento Persistente

```python
# Armazenamento por plugin (persistido em ~/.codis/plugins/<id>/storage.json)
storage = api.get_storage()
storage.set("chave", "valor")
valor = storage.get("chave", "valor_padrao")   # 2º argumento é o valor padrão (posicional)
storage.delete("chave")
storage.keys()      # Lista todas as chaves armazenadas
storage.clear()      # Apaga todo o armazenamento do plugin
storage.flush()       # Força gravação síncrona imediata em disco
```

### Configurações

```python
valor = api.get_config("font_size")        # Lê config do Codis
api.set_config("font_size", 14)            # Altera config do Codis
```

### Workspace e Arquivos

```python
workspace = api.get_workspace_folder()     # Path da raiz do workspace (ou None)
api.open_file(Path("arquivo.py"))          # Abre arquivo no editor
files = api.get_workspace_files("**/*.py", exclude=["**/.git/**"])  # Busca por padrão glob
```

### Formatadores e Provedores

```python
# Registrar formatador para uma linguagem
def formatar_python(content: str) -> str:
    return content.upper()  # exemplo trivial

api.register_formatter("python", formatar_python)

# Registrar provedor de símbolos
def obter_simbolos(content: str, lang: str) -> list[str]:
    return ["MinhaClasse", "minha_funcao"]

api.register_symbol_provider("python", obter_simbolos)

# Registrar provedor de quick-fix / refatoração para uma linguagem
def provedor_quick_fix(texto, linha, col):
    return [{"title": "Adicionar docstring", "command": "meu.add_docstring", "arguments": [linha]}]

api.register_code_action_provider("python", provedor_quick_fix)
```

### Temporizadores

```python
# Executar após delay (ms) - note: delay_ms vem antes da função
handle = api.set_timeout(1000, minha_funcao)
api.clear_timer(handle)               # clear_timer cancela tanto timeout quanto interval

# Executar periodicamente
handle = api.set_interval(5000, minha_funcao)
api.clear_timer(handle)               # Para automaticamente também na desativação do plugin

# Debounce: executa apenas após inatividade (delay_ms antes da função)
debounced = api.debounce(300, minha_funcao)

# Throttle: executa no máximo uma vez por intervalo (interval_ms antes da função)
throttled = api.throttle(1000, minha_funcao)
```

### Indicador de Progresso

```python
# Gerenciador de contexto que exibe progresso (opcionalmente cancelável)
with api.with_progress("Processando...", cancelavel=True) as prog:
    for i in range(100):
        if prog.is_cancelled:
            break
        prog.report(i, f"Item {i}/100")
```

### IPC entre Plugins

```python
# Obter instância de outro plugin pelo ID
outro = api.get_plugin("outro-plugin-id")
if outro:
    resultado = api.call_plugin("outro-plugin-id", "minha_funcao", arg1, arg2)
```

### Logger Estruturado

```python
logger = api.get_logger()   # Logger Python com prefixo do plugin
logger.info("Mensagem informativa")
logger.warning("Aviso")
logger.error("Erro crítico")
```

### Referência Complementar da API

Métodos públicos de `PluginAPI` que complementam as seções anteriores (assinaturas conforme `src/plugins/api.py`).

| Método | Descrição |
|--------|-----------|
| `register_language(lang_id, patterns=None, keywords=None, label=None, extensions=None, comment=None, comment_block=None, *, indent_rules=None, bracket_pairs=None, lsp_command=None)` | Registra uma nova linguagem (highlight, extensões, comentários, indentação, LSP) |
| `register_decorator(name, factory)` | Registra um decorador visual do editor (anotações inline) |
| `remove_highlight(tag="plugin_highlight")` | Remove o highlight da tag no editor ativo |
| `add_status_item(item_id, text_fn, click_fn=None, side="right", *, tooltip="", priority=0)` | Adiciona item na barra de status |
| `update_status_item(item_id)` / `remove_status_item(item_id)` | Atualiza / remove um item da barra de status |
| `add_context_menu_item(context, label, handler, *, when=None)` | Adiciona item a um menu de contexto |
| `add_settings_section(category, settings)` | Adiciona uma seção do plugin na janela de Configurações |
| `once(event, handler)` / `off(event, handler)` | Assina um evento uma única vez / remove o handler |
| `emit(event, *args, **kwargs)` | Dispara um evento entre plugins (use nomes com namespace, ex.: `"meu_plugin.dados_prontos"`) |
| `get_all_editors()` | Lista todos os painéis de editor abertos |
| `get_line(line)` / `get_line_count()` / `get_visible_range()` | Texto de uma linha (base 1), total de linhas e faixa de linhas visíveis no editor ativo |
| `save_file()` | Salva o arquivo ativo |
| `find_files(pattern, *, root=None, max_results=1000)` | Busca arquivos por glob sob `root` (padrão: workspace) |
| `ask_input(prompt, title="Input", *, initial="")` | Diálogo de texto; retorna `str` ou `None` |
| `ask_yesno(question, title="Confirmar")` | Diálogo sim/não; retorna `bool` |
| `ask_choice(title, prompt, choices)` | Diálogo de escolha; retorna o item ou `None` |
| `open_file_dialog(**kw)` / `open_files_dialog(**kw)` / `save_file_dialog(**kw)` / `ask_directory(**kw)` | Diálogos de arquivo/pasta; retornam `Path`, lista de `Path` ou `None` |
| `t(key, **kwargs)` | Traduz uma chave no idioma atual |
| `watch_config(key, callback)` / `migrate_config(migrations)` | Observa mudanças de uma chave / renomeia chaves de configuração |
| `read_clipboard()` / `write_clipboard(text)` | Lê / grava a área de transferência |

---

## PluginManifest - Campos Disponíveis

```python
@dataclass
class PluginManifest:
    name:           str              # Obrigatório. ID único (slug)
    version:        str = "1.0.0"
    author:         str = ""
    description:    str = ""
    homepage:       str = ""
    repository:     str = ""         # URL do repositório fonte
    icon:           str = "🔌"
    api_version:    str = "1.0"
    tags:           list[str] = []
    license:        str = ""
    category:       str = "Geral"
    min_codis:      str = "1.0.0"    # Versão mínima do Codis requerida
    trusted:        bool = False     # Marca como confiável por padrão
    readme:         str = ""         # Markdown de documentação inline
    keywords:       list[str] = []
    capabilities:   list[str] = []   # Ex: ["editor", "git", "lsp"]
    dependencies:   list[str] = []   # IDs de outros plugins requeridos
    engines:        dict = {}        # Ex: {"python": ">=3.12"}
    changelog:      str = ""
    funding:        str = ""
    preview:        bool = False     # Marca como experimental
```

---

## Plugins em ZIP

Plugins podem ser distribuídos como `.zip` contendo:

```
meu-plugin.zip
├── plugin.py        ← Código principal
├── README.md        ← Documentação (opcional)
├── assets/          ← Recursos (opcional)
└── requirements.txt ← Dependências Python (opcional)
```

---

## Gerenciador de Plugins

O painel de Extensões (sidebar → 🔌) exibe:
- Lista de plugins instalados com versão, autor e status (ativo/inativo)
- Botão para ativar/desativar cada plugin
- Botão para abrir a pasta de plugins no explorador do sistema
- Botão para instalar novo plugin
- Erros de carregamento de plugins com traceback

---

## Limpeza Automática (`_teardown`)

Quando um plugin é desativado, a API chama automaticamente `_teardown()`, que:
- Cancela todos os temporizadores registrados
- Fecha todos os canais de saída
- Remove todos os hooks de evento
- Limpa o armazenamento em memória

O plugin não precisa implementar limpeza manualmente - apenas declare os recursos via API e eles serão limpos automaticamente.

---

## Plugins em Processo Isolado

Plugins que realizam operações pesadas, chamadas de rede ou que acessam recursos externos podem declarar `isolated: True` no `MANIFEST`. O Codis os executará em um **processo Python separado**, garantindo que um travamento ou crash no plugin não afete o editor.

```python
MANIFEST = PluginManifest(
    name="meu-plugin-pesado",
    version="1.0.0",
    author="Autor",
    description="Faz algo intensivo.",
    isolated=True,    # ← executa em processo separado
)
```

### Como funciona

- O `PluginLoader` detecta `MANIFEST.isolated = True` na instalação.
- O `IsolatedPluginManager` lança um processo filho via `multiprocessing`.
- Toda comunicação entre o plugin e o editor é feita por filas JSON - sem acesso direto ao Tkinter ou ao estado interno do `CodisApp`.
- Se o processo travar, o monitor detecta ausência de heartbeat e reinicia automaticamente (até o limite `plugin_max_restarts`).

### Configurações relevantes

| Chave | Padrão | Descrição |
|---|---|---|
| `plugin_isolation` | `True` | Habilita/desabilita o isolamento por processo |
| `plugin_timeout_s` | `10` | Timeout para chamadas síncronas ao worker (s) |
| `plugin_heartbeat_s` | `30` | Tempo sem resposta antes de reiniciar (s) |
| `plugin_max_restarts` | `3` | Reinícios automáticos antes de desistir |
| `plugin_sandbox_network` | `False` | Bloqueia rede nos workers (experimental) |

Acesse em **Configurações → Plugins (Isolamento)**.

### Restrições do processo isolado

- Sem acesso ao Tkinter (não importar `tkinter` no plugin isolado)
- Sem acesso direto ao editor, abas ou arquivos abertos
- Toda interação com o editor é via mensagens da `PluginAPI`
- Imports de `multiprocessing`, `threading` são permitidos dentro do worker
