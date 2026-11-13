# 🚀 Primeiros Passos

Este guia cobre tudo que você precisa para instalar e rodar o Codis pela primeira vez.

---

## Requisitos

| Componente | Versão mínima | Notas |
|---|---|---|
| Python | 3.12+ | Latest recomendado |
| Tkinter | Incluso no Python | `sudo apt install python3-tk` no Ubuntu/Debian |
| Git | qualquer | Opcional - necessário apenas para o painel Git |
| Node.js | qualquer | Opcional - necessário para linting JS/TS |

> **O Codis não possui dependências Python obrigatórias.** Funciona com a stdlib pura.
> Todos os pacotes em `requirements.txt` são opcionais e ativam funcionalidades extras.

### Dependências opcionais

| Ferramenta | Instalação | Funcionalidade |
|---|---|---|
| `send2trash` | `pip install send2trash` | Lixeira nativa do SO (sem ela: lixeira do projeto em `.codis_trash/`) |
| `Pillow` | `pip install Pillow` | Nebulosas/auroras no K-UI (recomendado) |
| `pylsp` | `pip install python-lsp-server` | LSP para Python |
| `pyright` | `npm i -g pyright` | LSP TypeScript/Python (alternativa) |
| `eslint` | `npm i -g eslint` | Linting JavaScript/TypeScript |
| `prettier` | `npm i -g prettier` | Formatação JS/TS/CSS/HTML/JSON |
| `pylint` | `pip install pylint` | Análise aprofundada de Python |
| `mypy` | `pip install mypy` | Checagem de tipos Python |
| `flake8` | `pip install flake8` | Estilo + erros Python |
| `typescript-language-server` | `npm i -g typescript-language-server` | LSP JS/TS |
| `rust-analyzer` | via rustup | LSP Rust |
| `gopls` | `go install golang.org/x/tools/gopls@latest` | LSP Go |
| `psycopg2-binary` | `pip install psycopg2-binary` | SQL Studio - PostgreSQL |
| `pymysql` | `pip install pymysql` | SQL Studio - MySQL / MariaDB |
| `duckdb` | `pip install duckdb` | SQL Studio - DuckDB |
| `psutil` | `pip install psutil` | Monitor de CPU/RAM no gerenciador de processos |
| `websockets` | `pip install websockets` | Colaboração em tempo real, painéis de chat |
| `Pygments` | `pip install Pygments` | Exportação de código com highlight em HTML |
| `charset-normalizer` | `pip install charset-normalizer` | Detecção automática de encoding (senão usa `chardet` como fallback) |
| `chardet` | `pip install chardet` | Fallback de detecção de encoding (sem ele, UTF-8 é assumido para tudo) |
| `py7zr` | `pip install py7zr` | Suporte a arquivos `.7z` no painel de arquivos |
| `rarfile` | `pip install rarfile` | Suporte a arquivos `.rar` no painel de arquivos |
| `pywinpty` | `pip install pywinpty` | Terminal PTY completo no Windows (senão usa `subprocess` simples) |
| `tomli` | `pip install tomli` | Parser TOML em Python < 3.11 (desnecessário em 3.11+) |

Nenhuma dessas ferramentas é obrigatória. O Codis funciona sem elas, mas recursos específicos ficam indisponíveis.

> **Lixeira:** Por padrão o Codis move os arquivos excluídos para `.codis_trash/`
> dentro do projeto. Um ícone 🗑 na barra lateral permite restaurar ou excluir
> permanentemente os arquivos deletados.

---

## Matriz de Compatibilidade

### Python

| Versão | Suporte | Observação |
|---|---|---|
| 3.12.x | ✅ Oficial | Versão de desenvolvimento e CI |
| 3.13.x | ✅ Testado | CI executa em 3.13 a cada push |
| 3.11.x | ❌ Não suportado | f-strings com aspas aninhadas (PEP 701) requerem 3.12+ |
| 3.10.x e anteriores | ❌ Não suportado | - |
| PyPy | ⚠️ Não testado | Tkinter no PyPy tem comportamentos diferentes |

### Windows

| Versão | Arquitetura | Suporte | Binário oficial |
|---|---|---|---|
| Windows 11 | x64 | ✅ | ✅ |
| Windows 11 | ARM64 | ✅ | ✅ |
| Windows 10 (21H2+) | x64 | ✅ | ✅ |
| Windows 10 | x86 (32-bit) | ✅ | ✅ |
| Windows 10 < 21H2 | x64 | ⚠️ | ✅ (não testado sistematicamente) |
| Windows 8.1 e anteriores | - | ❌ | ❌ |

Requisitos: Python 3.12+ com Tkinter (incluso no instalador oficial) e Visual C++ Redistributable 2015–2022 (para algumas dependências opcionais).

### Linux

| Distribuição | Versão | Arquitetura | Suporte |
|---|---|---|---|
| Ubuntu | 22.04 LTS (Jammy) | x64 | ✅ Testado no CI |
| Ubuntu | 24.04 LTS (Noble) | x64, ARM64 | ✅ Testado no CI |
| Debian | 12 (Bookworm) | x64 | ✅ |
| Fedora | 39+ | x64 | ✅ |
| Arch Linux | Rolling | x64 | ✅ |
| openSUSE Leap | 15.5+ | x64 | ⚠️ Não testado sistematicamente |
| Alpine Linux | - | - | ❌ musl libc incompatível com binário PyInstaller |

```bash
# Ubuntu / Debian
sudo apt install python3-tk python3.12 libx11-6 libxft2 libfontconfig1

# Fedora
sudo dnf install python3-tkinter python3.12 libX11 libXft fontconfig
```

**Display:** X11 tem suporte completo; Wayland funciona via XWayland com `GDK_BACKEND=x11`; Wayland puro (sem XWayland) não funciona, pois o Tkinter requer X11.

### macOS

| Versão | Arquitetura | Suporte | Binário oficial |
|---|---|---|---|
| macOS 14 Sonoma | ARM64 (Apple Silicon) | ✅ | ✅ |
| macOS 13 Ventura | ARM64 (Apple Silicon) | ✅ | ✅ |
| macOS 12 Monterey | ARM64 | ⚠️ | ✅ (não testado sistematicamente) |
| macOS (qualquer) | Intel x64 | ⚠️ Sem binário | Execute via Python local |
| macOS < 12 | - | ❌ | ❌ |

> **macOS Intel:** os runners `macos-13` (Intel) foram descontinuados pelo GitHub Actions em janeiro de 2025, portanto não há binário oficial para Intel. Usuários Intel podem compilar localmente com `pip install pyinstaller && python package_codis.py pyinstaller`.

Requisitos: `brew install python@3.12 tcl-tk`.

### Funcionalidades por plataforma

| Funcionalidade | Windows | Linux | macOS |
|---|---|---|---|
| Editor de código completo | ✅ | ✅ | ✅ |
| Terminal integrado (PTY) | ✅ (com pywinpty) | ✅ | ✅ |
| Terminal integrado (subprocess) | ✅ (fallback) | ✅ (fallback) | ✅ (fallback) |
| Git panel completo | ✅ | ✅ | ✅ |
| LSP (autocomplete, hover, etc.) | ✅ | ✅ | ✅ |
| DAP (debug adapter) | ✅ | ✅ | ✅ |
| File watcher (mudanças externas) | ✅ | ✅ | ✅ |
| Lixeira nativa (send2trash) | ✅ | ✅ | ✅ |
| DPI awareness automático | ✅ | ⚠️ (parcial) | ⚠️ (parcial) |
| Notificações do sistema | ⚠️ (via messagebox) | ⚠️ (via messagebox) | ⚠️ (via messagebox) |
| Suporte a .rar / .7z | ✅ (com deps) | ✅ (com deps) | ✅ (com deps) |
| Player de vídeo (mpv/vlc) | ✅ (manual) | ✅ (manual) | ✅ (manual) |

> **Legenda:** ✅ Suportado/testado · ⚠️ Funcional com limitações · ❌ Não suportado

---

## Instalação

```bash
# Clonar o repositório
git clone https://github.com/KillovSky/Codis.git
cd Codis

# Executar direto (sem nenhuma dependência extra)
python run.py

# Opcional: instalar pacotes que habilitam funcionalidades extras
pip install -r requirements.txt        # todos os opcionais
pip install send2trash                 # só a lixeira nativa
pip install Pillow markdown            # só K-UI + preview Markdown
```

> **Windows:** `pythonw run.py` (sem janela de console)  
> **Linux/macOS:** `python3 run.py`  
> **Alternativo:** `python -m src`

---

## Primeiro Uso - Wizard de Configuração

Na primeira execução, o **Wizard de Primeiro Uso** é exibido automaticamente. Ele permite:

- Escolher o tema inicial (Dark, Light, Monokai, Catppuccin etc.)
- Definir o idioma da interface (14 idiomas disponíveis)
- Configurar o shell padrão do terminal integrado
- Ajustar tamanho de fonte e preferências básicas do editor

Após o wizard, as configurações são salvas em `~/.codis/config.json`. Para reexibir o wizard, delete `config.json` e reinicie o Codis.

---

## Abrindo via Linha de Comando

```bash
python run.py caminho/para/arquivo.py           # um arquivo
python run.py caminho/para/minha-pasta/         # workspace
python run.py arquivo1.py arquivo2.js           # múltiplos arquivos
```

---

## Tela de Boas-Vindas

Após o wizard (ou nas execuções seguintes), a **Tela de Boas-Vindas** exibe:

- Ações rápidas: Novo Arquivo, Abrir Arquivo, Abrir Pasta
- Lista de arquivos e pastas recentes (até 20 entradas armazenadas em `config.json`, mas a Welcome Screen exibe apenas as 12 mais recentes)
- Opção de clonar repositório Git diretamente

Com o **K-UI ativo**, a tela de boas-vindas transforma-se em um cosmos completo com sistema solar de arquivos, cada arquivo recente orbitando como planeta. Ver [K-UI](kui.md).

A tela pode ser reexibida a qualquer momento via **Ajuda → Tela de Boas-Vindas**.

---

## Estrutura de Diretórios

Ao iniciar, o Codis cria automaticamente em `~/.codis/`:

```
~/.codis/
├── config.json          ← Configurações do usuário
├── session.json         ← Sessão (arquivos abertos, workspace)
├── keybindings.json     ← Atalhos personalizados
├── plugins/             ← Plugins instalados
├── snippets/            ← Snippets personalizados por linguagem
├── themes/              ← Criado na inicialização, reservado para uso futuro (não escaneado automaticamente; temas customizados são registrados via plugin com `api.register_theme()`)
├── skills/              ← Skills de IA personalizadas (.md)
├── plugin_data/         ← Storage persistente por plugin
├── trash/               ← Pseudo-lixeira (usada quando send2trash ausente)
├── notes_*.txt          ← Notas de sessão por workspace
└── trusted_plugins.json ← Lista de plugins confiáveis
```

---

## Restauração de Sessão

A restauração automática está **desativada por padrão**. Para ativar:

1. Abra **Arquivo → Configurações** (`Ctrl+,`)
2. Ative a opção `session_restore`

Com ela ativa, o Codis reabrirá automaticamente o workspace e os arquivos da sessão anterior ao reiniciar.

---

## Executando os Testes

```bash
pip install pytest pytest-cov
pytest tests/ -v
pytest tests/ --cov=src --cov-report=term-missing
```

A suíte roda **headless** (sem display gráfico), adequada para CI/CD. Ver [tests/README.md](../tests/README.md) para documentação completa.

---

## Próximos Passos

- [Interface](interface.md) - conheça o layout completo da UI
- [Editor](editor.md) - recursos do editor de código
- [Atalhos](atalhos.md) - referência rápida de atalhos
- [K-UI](kui.md) - ative o modo visual holográfico
- [Temas](temas.md) - personalize a aparência
- [Configurações](configuracoes.md) - todas as opções disponíveis

---

## Modos Standalone

O Codis permite abrir qualquer módulo individualmente sem iniciar o editor completo. São **29 modos** disponíveis (mais 4 aliases).

```bash
# Subcomandos diretos (preferido)
python run.py sql              # SQL Studio
python run.py tasks            # Gerenciador de Tarefas (Kanban)
python run.py diff A.py B.py   # Diff Viewer
python run.py paint img.png    # Editor de Imagens
python run.py rest             # REST Client
python run.py regex            # Regex Playground
python run.py terminal .       # Terminal integrado
python run.py git .            # Painel Git
python run.py pip              # Gerenciador de Pacotes Pip
python run.py scratch          # Bloco de Notas (persistido)
python run.py flowchart src/   # Fluxograma AST Python
python run.py render doc.md    # Preview HTML/Markdown/SVG
python run.py stats src/       # Estatísticas de Código
python run.py pomodoro         # Timer Pomodoro
python run.py todo             # Lista de Tarefas Rápidas
python run.py output           # Painel de Output
python run.py processes        # Gerenciador de Processos
python run.py calculator       # Calculadora Científica (alias: calc)
python run.py clock            # Relógio / Alarmes / Cronômetro
python run.py calendar         # Calendário com Lembretes
python run.py env              # Variáveis de Ambiente
python run.py formatter        # Formatador JSON/YAML/TOML/XML
python run.py snippets         # Gerenciador de Snippets
python run.py color-picker     # Seletor de Cores
python run.py player           # Zen Media Player
python run.py accessibility    # Painel de Acessibilidade (alias: a11y)
python run.py plugins          # Gerenciador de Plugins
python run.py ssh              # Conexão SSH Remota
python run.py archive arq.zip  # Visualizador de ZIP/RAR/7z

# Também via python -m src
python -m src sql
python -m src paint

# Flags legadas (ainda funcionam)
python run.py --sql
python run.py --paint
```

Cada modo carrega apenas as dependências necessárias (tema, i18n, configuração) e abre uma janela autônoma. Fechar a janela encerra o processo.

→ [Documentação completa dos modos standalone](recursos-exclusivos.md#modos-standalone)
