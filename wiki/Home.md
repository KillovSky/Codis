# 📖 Documentação do Codis

Documentação do Codis · Aether (v1.0.1). Os arquivos desta pasta alimentam a GitHub Wiki e o [site](https://killovsky.github.io/Codis/). CHANGELOG, ROADMAP, CONTRIBUTING e SECURITY ficam na raiz do repositório.


## Começando

| | Documento | Conteúdo |
| --- | --- | --- |
| 🚀 | [Primeiros Passos](primeiros-passos.md) | Instalação, requisitos, execução, wizard inicial |
| 🖥️ | [Interface](interface.md) | Layout geral: Activity Bar, Sidebar, painéis, barra de status |
| 📸 | [Previews](previews.md) | Tour visual: capturas do editor por dentro, painel por painel |
| ⌨️ | [Atalhos](atalhos.md) | Referência completa de todos os atalhos e cheat sheet |
| ⚙️ | [Configurações](configuracoes.md) | As 24 categorias de `config.json`, com tipo e valor padrão |
| 🎨 | [Temas](temas.md) | Temas built-in, pré-visualização, criação customizada e via plugin |
| ❓ | [FAQ](faq.md) | Perguntas frequentes e solução de problemas |
| 🛠️ | [Solução de Problemas](troubleshooting.md) | Diagnóstico técnico avançado: comandos, logs, causas |

## Editando

| | Documento | Conteúdo |
| --- | --- | --- |
| ✏️ | [Editor](editor.md) | Syntax highlighting, autocomplete, snippets, minimap, dobramento |
| 🧠 | [Inteligência do Editor](inteligencia-editor.md) | Multi-Cursor, Smart Tips, Complexity Lens, Code Rhythm, Edit Heatmap |
| 📚 | [KDocs](kdocs.md) | Documentação nativa (Python/Rust/Go/Ruby/Lua/C++/C#/JSDoc), hover balloon |
| 🔍 | [Linting & LSP](linting.md) | ESLint, Flake8, Mypy, Pylint, language servers externos |
| 🐞 | [Debug](debug.md) | Depuração real via DAP/debugpy: breakpoints, stepping, variáveis, console |
| 🌿 | [Git](git.md) | Painel Git completo, autenticação HTTPS/SSH, blame inline |
| 💻 | [Terminal](terminal.md) | Shell integrado, múltiplas sessões, REPLs |
| 🗂️ | [Workspace & Produtividade](workspace-projeto.md) | Workspace Switcher, Templates, Code Runner, Macros, Key Remapper, Trust |
| 🛠️ | [Utilitários de Desenvolvimento](utilitarios-dev.md) | Formatador, Variáveis de Ambiente, Grafo de Dependências |

## Assistente e conectividade

| | Documento | Conteúdo |
| --- | --- | --- |
| 🤖 | [Assistente de IA](ia.md) | Providers, Skills, 40 ferramentas, Approval Gate, Diff interativo |
| 💬 | [Chat ao Vivo](chat.md) | Cliente multi-protocolo: IRC, XMPP, Matrix, Slack, Discord, Telegram, Mattermost, MQTT e WebSocket |
| 🌐 | [SSH Remoto](remote-ssh.md) | Edição de arquivos em servidores remotos via SSH/SFTP |
| 👥 | [Colaboração em Tempo Real](colaboracao.md) | Edição simultânea do mesmo arquivo via WebSocket, cursores remotos |

## Ferramentas e extras

| | Documento | Conteúdo |
| --- | --- | --- |
| 💎 | [Recursos Avançados](recursos-avancados.md) | SQL Studio, Task Manager, LSP, Diff visual, Bookmarks |
| 🌟 | [Recursos Exclusivos](recursos-exclusivos.md) | Vim Mode imersivo, Zen Mode, Zen Media, easter eggs |
| ✦ | [K-UI](kui.md) | Engine holográfica: nebulosas, animações cósmicas, easter eggs |
| ⚡ | [Sistema & Performance](sistema-performance.md) | Pip Manager, Process Manager, Modo Performance, Cursor, Remote Browser |
| 🗄️ | [Backups, Lixeira & Arquivos](backups-lixeira-arquivos.md) | File Timeline, Lixeira, Visualizador de ZIP/RAR/7z |
| 📤 | [Painel de Saída](output.md) | Output Panel: canais, níveis de log e bridge de logging |
| ♿ | [Acessibilidade](acessibilidade.md) | Alto contraste, modos para daltonismo, navegação por teclado |
| 🖌️ | [Editor de Imagens (Paint)](paint.md) | Editor de imagens básico, embutido ou standalone |
| 📅 | [Utilitários Pessoais](utilitarios-pessoais.md) | Calendário, Calculadora, Relógio/Alarmes/Cronômetro |
| 🪟 | [Incorporar Programas e Preview Web](embed-e-preview.md) | `/codis app` (adotar/iniciar janelas) e navegador real embutido |
| 👹 | [DOOM Embutido](doom.md) | `/codis doom`: motor, IWAD e reparentamento da janela |

## Para quem desenvolve

| | Documento | Conteúdo |
| --- | --- | --- |
| 🧩 | [Plugins](plugins.md) | API: eventos, painéis, storage, sandbox, hot-reload |
| 🏗️ | [Arquitetura](arquitetura.md) | Estrutura de módulos, fluxo de inicialização, dependências |
| 📦 | [Builds & CI/CD](builds.md) | Empacotamento (PyInstaller/ZIP) e os 10 workflows do GitHub Actions (pipeline commit → build → release) |
| 🔄 | [Atualizador](updater.md) | Modos SMART/ZIP/REINSTALL/FORCE/BINARY, backup e rollback, caminhos protegidos |
| 🧪 | [Testes](testes.md) | Suíte de testes, fixtures, convenções e como contribuir com testes |

---

## Visão Geral

O **Codis** é um editor de código desktop completo construído sobre Python 3 e Tkinter. Apesar de usar uma stack de UI clássica, o projeto leva o Tkinter além dos seus limites:

- **K-UI** - engine de renderização cósmica com nebulosas, auroras e partículas animadas em thread separada (PIL)
- **Vim Mode** - janela CLI imersiva separada com 6 modos do Vim
- **Zen Media** - player de mídia integrado ao Modo Zen com backend VLC/MPV não-bloqueante
- **KDocs** - motor de documentação universal com suporte nativo a Python (Google/NumPy/Sphinx), Rust, Go, Ruby, Lua, C/C++, C# e mais
- **LSP** - integração nativa com servidores de linguagem externos (pylsp, tsserver, rust-analyzer…)
- **IA multi-provedor** - Anthropic, OpenAI, OpenAI-compatível, Gemini, Ollama e modelos GGUF locais, com 15 Skills e 40 ferramentas
- **SQL Studio** - banco de dados integrado com suporte a SQLite, PostgreSQL, MySQL e DuckDB
- **Task Manager** - quadro Kanban com agendador de comandos shell integrado
- **Plugins** - API completa com sandbox estática, eventos com prioridade e armazenamento persistente por plugin

---

## Licença

MIT - © KillovSky
