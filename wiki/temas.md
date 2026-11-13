# 🎨 Temas

O Codis possui um sistema de temas completo com suporte a modos claro e escuro, além de suporte a temas criados por plugins.

---

## Temas Built-in

21 temas inclusos, todos definidos em `src/theme/base.py`. O nome exibido no menu **Exibir → Tema** é exatamente o nome interno (sem rebranding) - a tabela abaixo só capitaliza para leitura.

| Nome interno | Exibição | Modo |
|-------------|-----------------|------|
| `fluent_dark` | Fluent Dark | Escuro |
| `fluent_light` | Fluent Light | Claro |
| `catppuccin_mocha` | Catppuccin Mocha | Escuro |
| `tokyo_night` | Tokyo Night | Escuro |
| `dracula` | Dracula | Escuro |
| `gruvbox_dark` | Gruvbox Dark | Escuro |
| `gruvbox_light` | Gruvbox Light | Claro |
| `solarized_dark` | Solarized Dark | Escuro |
| `solarized_light` | Solarized Light | Claro |
| `one_dark_pro` | One Dark Pro | Escuro |
| `nord` | Nord | Escuro |
| `monokai_pro` | Monokai Pro | Escuro |
| `github_dark` | GitHub Dark | Escuro |
| `github_light` | GitHub Light | Claro |
| `palenight` | Palenight | Escuro |
| `rose_pine` | Rosé Pine | Escuro |
| `everforest_dark` | Everforest Dark | Escuro |
| `ayu_mirage` | Ayu Mirage | Escuro |
| `midnight_void` | Midnight Void | Escuro |
| `sepia` | Sepia | Claro |
| `high_contrast` | High Contrast | Escuro |

Além desses, o modo **K-UI** aplica uma camada visual holográfica adicional sobre qualquer um dos 21 temas - ver [Tema K-UI](#tema-k-ui) mais abaixo.

O tema padrão é `fluent_dark`.

---

## Como Trocar de Tema

Existem quatro formas de trocar de tema:

1. **Menu:** `Exibir → Tema` - lista todos os temas disponíveis.
2. **Activity Bar:** clique no ícone 🎨 na parte inferior da Activity Bar - avança para o próximo tema.
3. **Configurações:** `Ctrl+,` → campo `theme`.
4. **Paleta de Comandos:** `Ctrl+Shift+P` → "Toggle Theme".

### Modos de Aplicação

Ao trocar de tema, um diálogo pergunta qual modo aplicar:

- **Modo Rápido** - repinta todos os widgets sem reconstruir a UI. Mais rápido, mas alguns elementos podem ficar com cores ligeiramente inconsistentes.
- **Modo Completo** - destrói e reconstrói toda a interface preservando o estado (workspace e arquivos abertos). Garante consistência total de cores.

---

## Pré-visualização antes de aplicar

O **Seletor de Temas** (`Exibir → Tema → Abrir Seletor de Temas...`) abre um painel dedicado de pré-visualização (`src/ui/theme_preview_panel.py`) antes de aplicar qualquer tema definitivamente:

- Grade de cartões com todos os temas disponíveis, mostrando nome e um ícone indicando se é dark ou light
- **Preview ao vivo**: cada cartão mostra uma miniatura realista de como ficariam o editor, a sidebar, a tab bar e a barra de status com aquele tema - sem precisar aplicá-lo antes para ver
- Filtro por nome e por polaridade (mostrar apenas dark, apenas light, ou todos)
- Navegação totalmente por teclado: setas para mover entre cartões, `Enter` para confirmar, `Escape` para cancelar
- Ao confirmar, ainda é perguntado se a aplicação deve ser **Completa** ou **Rápida** (ver seção anterior)

---

## Estrutura de um Tema

Um tema é um `@dataclass Theme` definido em `src/theme/base.py`. Os atributos são organizados por grupos:

### Grupo: Janela Principal

| Atributo | Descrição |
|----------|-----------|
| `name` | Identificador único do tema |
| `dark` | `True` para escuro, `False` para claro |
| `bg` | Cor de fundo da janela |
| `fg` | Cor de texto principal |
| `fg_muted` | Cor de texto secundário (menos visível) |

### Grupo: Editor

| Atributo | Descrição |
|----------|-----------|
| `editor_bg` | Fundo da área de código |
| `cursor` | Cor do cursor de texto |

> O texto principal do editor usa `fg` (não existe um `editor_fg` separado no dataclass `Theme`, apesar de mencionado no docstring interno de `base.py` - trata-se de uma referência desatualizada no comentário do código-fonte).

### Grupo: Sidebar e Activity Bar

| Atributo | Descrição |
|----------|-----------|
| `sidebar_bg` | Fundo da sidebar |
| `activity_bg` | Fundo da Activity Bar |

### Grupo: Abas

| Atributo | Descrição |
|----------|-----------|
| `tabbar_bg` | Fundo da barra de abas |
| `tab_act_bg` | Fundo da aba ativa |
| `tab_act_fg` | Texto da aba ativa |
| `tab_in_fg` | Texto das abas inativas |

### Grupo: Painéis e Flutuantes

| Atributo | Descrição |
|----------|-----------|
| `panel_bg` | Fundo do painel inferior |
| `floating_bg` | Fundo de janelas flutuantes (diálogos) |

### Grupo: Seleção e Inputs

| Atributo | Descrição |
|----------|-----------|
| `sel_bg` | Fundo de texto selecionado |
| `sel_fg` | Texto selecionado |
| `input_bg` | Fundo de campos de texto |
| `border` | Cor de bordas e divisores |

### Grupo: Botões

| Atributo | Descrição |
|----------|-----------|
| `btn_bg` | Fundo de botões padrão |
| `btn_hover` | Fundo de botões ao passar o mouse |
| `btn_fg` | Texto de botões padrão |
| `btn_primary` | Fundo de botões de ação primária |
| `btn_primary_fg` | Texto de botões de ação primária |

### Grupo: Scrollbar

| Atributo | Descrição |
|----------|-----------|
| `scroll_bg` | Trilha da scrollbar |
| `scroll_thumb` | Thumb (alça) da scrollbar |

### Grupo: Diagnósticos

| Atributo | Descrição |
|----------|-----------|
| `ok_fg` | Cor de status positivo |
| `warn_fg` | Cor de avisos |
| `err_fg` | Cor de erros |

### Grupo: Syntax Highlighting

| Atributo | Descrição |
|----------|-----------|
| `t_keyword` | Palavras-chave |
| `t_string` | Strings |
| `t_comment` | Comentários |
| `t_number` | Números |
| `t_func` | Funções |
| `t_type` | Tipos / classes |
| `t_operator` | Operadores |
| `t_decorator` | Decoradores / anotações |

---

## Criando um Tema Customizado

### Método 1: Via Plugin

A forma mais flexível é criar um plugin que registre o tema:

```python
# meu_plugin.py
from src.plugins.api import PluginAPI, PluginManifest
from src.theme.base import Theme

MANIFEST = PluginManifest(name="meu-tema", version="1.0.0")

def activate(api: PluginAPI) -> None:
    meu_tema = Theme(
        name="meu_tema",
        dark=True,
        bg="#1a1a2e",
        fg="#e0e0e0",
        fg_muted="#888888",
        editor_bg="#16213e",
        cursor="#00d4ff",
        sidebar_bg="#0f3460",
        activity_bg="#0a2540",
        tabbar_bg="#1a1a2e",
        tab_act_bg="#0f3460",
        tab_act_fg="#ffffff",
        tab_in_fg="#aaaaaa",
        panel_bg="#0f0f1a",
        floating_bg="#1a1a2e",
        sel_bg="#0f3460",
        sel_fg="#ffffff",
        input_bg="#0f1f35",
        border="#2d3f6e",
        btn_bg="#1e2d4a",
        btn_hover="#2d3f6e",
        btn_fg="#e0e0e0",
        btn_primary="#00d4ff",
        btn_primary_fg="#000000",
        scroll_bg="#0f1f35",
        scroll_thumb="#2d3f6e",
        ok_fg="#00ff88",
        warn_fg="#ffaa00",
        err_fg="#ff4444",
        t_keyword="#00d4ff",
        t_string="#00ff88",
        t_comment="#555577",
        t_number="#ff8855",
        t_func="#88aaff",
        t_type="#ffdd44",
        t_operator="#cc88ff",
        t_decorator="#ff88cc",
    )
    api.register_theme(meu_tema)
```

### Sobre `~/.codis/themes/`

Esse diretório é criado automaticamente na inicialização do Codis (junto com `plugins/`, `snippets/` etc.), mas **não é escaneado nem carregado automaticamente** nesta versão - não há nenhum loader de tema que leia arquivos `.py` soltos dessa pasta. O **Método 1 acima (via plugin)** é a única forma funcional e suportada de registrar temas customizados.

---

## Compatibilidade HiDPI (Windows)

No Windows, o Codis aplica automaticamente:

- `SetProcessDpiAwareness(1)` via `ctypes.windll.shcore`
- `tk scaling 1.5`
- Coloração da barra de título nativa (`DwmSetWindowAttribute`) para combinar com o tema ativo (Windows 11)

Nenhuma configuração manual é necessária.

---

## Tema K-UI

O tema `kui` é especial - ele é registrado dinamicamente ao ativar o **K-UI**. Não é um tema no sentido tradicional: a K-UI renderiza sua própria paleta de cores independente (`_pal()`), com void escuro (`#020209` escuro / `#0d0d2b` claro), texto luminoso e efeitos visuais que ignoram propositalmente as cores do `bg` para criar a estética cósmica.

Ver [K-UI](kui.md) para detalhes.