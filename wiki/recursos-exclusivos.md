# 🌟 Recursos Exclusivos

Esta página documenta os recursos únicos do Codis que o diferenciam de outros editores.

---

## Vim Mode

O Vim Mode do Codis é uma implementação imersiva que vai além de um simples emulador de atalhos. Ao ser ativado, cria uma **janela CLI separada** com aparência de terminal.

### Ativando o Vim Mode

- `Ctrl+Alt+V`
- Menu **Ferramentas → ⌨️ Vim Mode (toggle)**

> **Nota:** Vim Mode e K-UI são mutuamente exclusivos. Ativar um desativa o outro.

### O Que Acontece ao Ativar

1. Uma janela `Toplevel` separada é criada com aparência de terminal (fundo escuro, fonte monoespaçada).
2. O conteúdo do arquivo ativo é copiado para o editor interno da janela CLI.
3. A interface principal do Codis continua ativa em segundo plano.
4. O rodapé da janela CLI exibe: modo atual | nome do arquivo | linha:coluna | indicador de modificação.
5. A interface principal tem seu chrome ocultado (sidebar, Activity Bar, tabbar, breadcrumb, barra de status) para foco total.

### Modos Implementados

| Modo | Cursor | Como Entrar |
|------|--------|------------|
| `NORMAL` | Bloco (█) | Esc; padrão ao abrir |
| `INSERT` | Barra (\|) | `i`, `a`, `o`, `I`, `A`, `O` |
| `VISUAL` | Bloco (violeta) | `v` |
| `V-LINE` | Bloco (violeta) | `V` |
| `REPLACE` | Underline (vermelho) | `r` |
| `COMMAND` | Barra | `:` |

### Comandos NORMAL

| Tecla | Ação |
|-------|------|
| `h` `j` `k` `l` | Mover cursor |
| `w` `b` `e` | Mover por palavras |
| `gg` | Início do arquivo |
| `G` | Fim do arquivo |
| `0` | Início da linha |
| `$` | Fim da linha |
| `dd` | Apagar linha |
| `yy` | Copiar (yank) linha |
| `p` `P` | Colar depois / antes |
| `u` | Desfazer |
| `Ctrl+R` | Refazer |
| `x` | Apagar caractere |
| `~` | Alternar case do char |
| `>>` `<<` | Indentar / desindentar |

### Comandos COMMAND (`:`)

| Comando | Ação |
|---------|------|
| `:w` | Salvar arquivo |
| `:q` | Fechar janela Vim (volta ao Codis) |
| `:q!` | Fechar sem salvar |
| `:wq` | Salvar e fechar |
| `:<número>` | Ir para a linha |
| `:%s/busca/subst/g` | Substituição global (regex) |

### Saindo do Vim Mode

Use `:q`, `:q!` ou `:wq` dentro da janela CLI, ou `Ctrl+Alt+V` na janela principal do Codis.

---

## Zen Mode

O Zen Mode remove todas as distrações para foco total no código.

### Ativando

- `Ctrl+K Z`
- Menu **Ferramentas → 🌌 Zen Mode**

### O Que o Zen Mode Faz

- Oculta: sidebar, painel inferior, Activity Bar, botões flutuantes
- Ativa tela cheia (`-fullscreen`)
- Exibe uma label discreta no rodapé: "Zen Mode [Esc para sair]"
- Inicia o **Zen Media Player** automaticamente (se playlist configurada e `zen_media_autoplay: true`)

### Saindo do Zen Mode

Pressione `Esc` ou use `Ctrl+K Z` novamente.

---

## Zen Media Player

O **Zen Media** é um player de midia integrado, projetado para criar uma atmosfera imersiva durante sessões de código.

### Configurando

**Ferramentas → 🎵 Zen Media Config...** (ou via Spotlight no K-UI).

No diálogo de configuração:
- Adicione arquivos de midia à playlist
- Configure autoplay ao entrar no Zen Mode
- Ajuste volume e modo de repetição

### Zen Player (interface)

Abra com **Workspace → Zen Player** ou `Ctrl+Alt+P`. Funciona dentro e fora do Zen Mode.

| Recurso | Como funciona |
|---------|---------------|
| **Janela flutuante** | Layout completo: capa grande e abas **Tocando / Fila / Equalizador**. Botão de pin mantém no topo. Atalhos na janela: `Espaço` (play/pause), `←`/`→` (faixa anterior/próxima). |
| **Encaixar (attach)** | Botão de dock: o player vira uma barra no rodapé da janela principal (capa, título, controles, busca, volume; EQ e Fila abrem em popup). O botão ao lado destaca de volta. A música não é interrompida na troca. |
| **Capa** | 1) imagem com o **mesmo nome** do áudio na pasta; 2) `cover`/`folder`/`front`/`album`/`artwork` na pasta; 3) arte embutida (requer `mutagen`); 4) miniatura via `ffmpeg`, se instalado; 5) capa gerada. A cor de destaque da interface vem da capa. |
| **Metadados** | Título/artista/álbum via `mutagen`; sem tags, usa o nome do arquivo (`Artista - Título` é separado). |
| **Equalizador** | 10 bandas (31 Hz–16 kHz, ±12 dB), pré-amp automático contra clipping, 13 presets (Flat, Bass Boost, Treble Boost, Vocal, Rock, Pop, Jazz, Classical, Electronic, Hip-Hop, Acoustic, Lo-fi, Late Night) e presets próprios (**Salvar preset**). Arraste as bandas, use a roda do mouse (±0,5 dB) ou duplo clique para zerar uma banda. |
| **Minimizar** | Esconde o player e mostra a bolinha (`ZenMiniOrb`); clicar nela volta ao player. |

Preferências persistidas em `config.json`: `zen_player_mode` (`floating`/`docked`), `zen_player_geometry`, `zen_player_topmost`, `zen_eq_gains`, `zen_eq_enabled`, `zen_eq_custom`, `zen_media_volume`, `zen_media_shuffle`, `zen_media_repeat`, `zen_media_playlist`.

O equalizador é aplicado ao vivo no **mpv** (filtro `af` com `equalizer` do ffmpeg) e no **VLC** (`AudioEqualizer`).

### Componentes

| Componente | Arquivo | Descrição |
|------------|---------|-----------|
| `ZenMediaPlayer` | `zen_media.py` | Motor (mpv/VLC), playlist, EQ, vários ouvintes (`add_listener`) |
| `ZenPlayerHost` / `ZenPlayerView` | `zen_player.py` | Onde o player vive (janela/dock) e a interface em dois layouts |
| Widgets | `zen_player_widgets.py` | Botões vetoriais, barra de busca, `EqualizerPanel`, `QueuePanel` |
| Lógica | `zen_audio.py` | EQ/presets, capas, metadados (sem Tk, testável) |
| `ZenMiniOrb` | `zen_media.py` | Bolinha flutuante quando o player está minimizado |
| `ZenMediaDialog` | `zen_media.py` | Diálogo de configuração completo |

### Fluxo

1. Ao entrar no Zen Mode com faixas na playlist, o player é exibido no modo salvo.
2. Minimizar mostra a **ZenMiniOrb** no canto inferior direito; clicar nela reabre o player.
3. Ao sair do Zen Mode apenas a bolinha é ocultada; o player (janela ou dock) e a reprodução continuam.

---

## Focus Mode

O Focus Mode atenua visualmente as abas inativas para destacar o arquivo que está sendo editado.

### Ativando

Menu **Ferramentas → Focus Mode**.

**Efeito:** editores inativos recebem `bg = t.bg` (mais claro/escuro) em vez de `t.editor_bg`, criando contraste visual entre a aba ativa e as demais.

---

## Easter Eggs
- Er...nada aqui!
<!--O Codis possui easter eggs ocultos descobertos via comandos especiais no terminal integrado.

### Como Ativar

1. Abra o terminal (`Ctrl+`` ` ``)
2. Digite `/codis <código>` e pressione Enter

Os códigos são secretos por design - parte da diversão é descobri-los.

### Konami Code
O clássico Konami Code (↑↑↓↓←→←→BA) pode ser digitado no campo de input do terminal para ativar um efeito visual especial.

### Gerenciamento

Os easter eggs são gerenciados pela classe `EasterEggManager` (`src/ui/easter_eggs.py`). Os efeitos incluem animações visuais sobre a interface principal, partículas e mudanças temporárias de cores.-->

---

## Modo Somente Leitura

**Ferramentas → Modo Somente Leitura** alterna o editor ativo entre editável e `state="disabled"`.

No modo somente leitura:
- Nenhuma digitação é possível
- Atalhos de edição são ignorados
- A barra de status indica o estado

---

## Modo Sobrescrita

Pressione `Insert` para alternar entre modo de **inserção** (padrão) e **sobrescrita** (cada tecla substitui o caractere à frente do cursor em vez de inserir).

---

## Propriedade Exclusiva: Histórico de Colagem (Clipboard Ring)

O Codis mantém um histórico das últimas **50 entradas** copiadas/recortadas. `Ctrl+Shift+V` abre o histórico para escolher qual entrada colar.

O histórico é mantido em memória durante a sessão.

---

## Barra de Título Nativa Colorida (Windows 11)

No Windows 11, o Codis colore automaticamente a barra de título nativa da janela para combinar com o tema ativo, usando `DwmSetWindowAttribute` (DWMWA_CAPTION_COLOR).

Isso dá ao Codis uma aparência nativa integrada, sem necessidade de configuração.

---

## Novas Janelas

**Arquivo → Nova Janela** lança uma nova instância independente do Codis no mesmo processo Python (`python -m src`). Múltiplas instâncias são totalmente independentes e compartilham apenas as configurações em `config.json`.

---

## Modos Standalone

O Codis oferece **29 modos standalone** (mais 4 aliases) que permitem abrir qualquer painel isoladamente, sem carregar o editor completo. São úteis para scripts de automação, launchers de sistema e uso rápido de ferramentas específicas.

Cada modo standalone carrega apenas o subconjunto mínimo de dependências necessário: tema, i18n, configuração e o painel em questão. Fechar a janela encerra o processo.

### Como Usar

```bash
# Subcomandos diretos (preferido - sem hífens)
python run.py sql
python run.py git /meu/projeto

# Flags longas (compatibilidade)
python run.py --sql
python run.py --git /meu/projeto

# Ver lista completa
python run.py --help
```

O `run.py` normaliza automaticamente `python run.py sql` para `python run.py --sql`, então ambos os formatos funcionam de forma idêntica.

### Lista Completa de Modos

| Modo | Alias | Descrição | Argumento |
|------|-------|-----------|-----------|
| `--sql` | - | SQL Studio (SQLite/PostgreSQL/MySQL/DuckDB) | - |
| `--tasks` | - | Gerenciador de Tarefas: Kanban + Agendador | - |
| `--diff` | - | Diff Viewer lado a lado | `[A] [B]` |
| `--paint` | - | Editor de Imagens básico | `[img]` |
| `--rest` | - | REST Client (HTTP com headers e corpo) | - |
| `--regex` | - | Regex Playground com grupos e flags | - |
| `--terminal` | - | Terminal integrado | `[dir]` |
| `--git` | - | Painel Git completo | `[dir]` |
| `--pip` | - | Gerenciador de Pacotes Pip | - |
| `--scratch` | - | Bloco de Notas multi-aba, persistido | - |
| `--flowchart` | - | Gerador de Fluxograma (AST Python) | `[py]` |
| `--render` | `--preview` | Preview HTML / Markdown / SVG | `[file]` |
| `--stats` | - | Estatísticas de Código (LOC, complexidade) | `[dir]` |
| `--pomodoro` | - | Timer Pomodoro 25/5 configurável | - |
| `--todo` | - | Lista de Tarefas Rápidas | - |
| `--output` | - | Painel de Output (log viewer) | - |
| `--processes` | `--procs` | Gerenciador de Processos do sistema | - |
| `--calculator` | `--calc` | Calculadora Científica | - |
| `--clock` | - | Relógio / Alarmes / Cronômetro | - |
| `--calendar` | - | Calendário com Lembretes | - |
| `--env` | - | Variáveis de Ambiente do shell | - |
| `--formatter` | - | Formatador JSON / YAML / TOML / XML | - |
| `--snippets` | - | Gerenciador de Snippets | - |
| `--color-picker` | - | Seletor de Cores com picker visual | - |
| `--player` | - | Zen Media Player (VLC/MPV backend) | - |
| `--accessibility` | `--a11y` | Painel de Acessibilidade | - |
| `--plugins` | - | Gerenciador de Plugins | - |
| `--ssh` | - | Conexão SSH / SFTP Remota | - |
| `--archive` | - | Visualizador de ZIP / RAR / 7z | `[file]` |

### Exemplos Práticos

```bash
# Abrir diff entre dois arquivos diretamente do terminal
python run.py diff antes.py depois.py

# Iniciar o SQL Studio com o projeto ativo
python run.py sql

# Abrir o flowchart de um arquivo específico
python run.py flowchart src/app.py

# Preview de um relatório Markdown
python run.py render relatorio.md

# Estatísticas do projeto inteiro
python run.py stats src/
```

---

## Reinício do Processo

**Arquivo → Reiniciar** salva todos os arquivos modificados e reinicia o processo do Codis:
- No **Linux/macOS**: usa `os.execv()` para substituir o processo atual.
- No **Windows**: lança um novo processo com `subprocess.Popen` e destrói a janela atual.

O workspace e a sessão são salvos antes do reinício para que tudo seja restaurado.
