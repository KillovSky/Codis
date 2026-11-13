# ⚙️ Sistema, Performance & Pacotes

Esta página cobre as ferramentas de **Ferramentas → Sistema & Processos**: Gerenciador de Pacotes Pip, Gerenciador de Processos e Modo Performance - além do Cursor Customizável e do Explorador de Arquivos Remotos, que vivem em outras partes do menu mas são, na essência, ferramentas de sistema. Também inclui uma seção técnica sobre a arquitetura de performance do editor, metas, benchmarks e como diagnosticar lentidão.

---

## 📦 Gerenciador de Pacotes Pip (`src/ui/pip_manager.py`)

Atalho: **Ferramentas → Sistema & Processos → 📦 Gerenciador Pip**.

Interface gráfica completa para `pip`, organizada em quatro abas:

| Aba | Conteúdo |
|---|---|
| **Instalados** | Cards visuais por pacote: versão, licença, links do projeto, badge de status |
| **PyPI** | Busca de pacotes no PyPI com cards ricos (descrição, autor, contagem de downloads, botão de instalar direto) |
| **Ambientes** | Lista, cria e ativa ambientes virtuais (`venv`) |
| **Outdated** | Lista pacotes com atualização disponível, com opção de atualização em massa |

### Funcionalidades

- Log de operações em tempo real, com cores distintas para sucesso/aviso/erro
- Instalar, atualizar e desinstalar pacotes com barra de progresso
- Exportar e importar `requirements.txt` diretamente da interface
- Criação de novo ambiente virtual sem sair do Codis

---

## 🖥️ Gerenciador de Processos (`src/ui/process_manager.py`)

Atalho: `Ctrl+Alt+M` ou **Ferramentas → Sistema & Processos → Gerenciador de Processos**.

Duas abas:

| Aba | Conteúdo |
|---|---|
| **Sistema** | Todos os processos do sistema operacional, com CPU, memória, status e usuário |
| **Codis** | Painéis internos abertos, threads internas e subprocessos gerados pelo próprio editor |

### Funcionalidades

- Gráficos de uso de CPU e memória em tempo real
- Painel de informações do sistema: RAM total, número de núcleos, uptime
- Filtro em tempo real por nome de processo ou PID
- Encerrar processo individual (com confirmação) ou encerrar a árvore completa de processos
- Detalhes expandidos por processo: linha de comando completa, threads, arquivos abertos
- Auto-atualização configurável (1s / 2s / 5s)

> **Dependência opcional:** requer `psutil` (`pip install psutil`). Sem ele, o painel degrada graciosamente - a aba **Sistema** fica indisponível, mas a aba **Codis** continua funcionando, já que não depende de `psutil`.

---

## 🚀 Modo Performance (`src/ui/performance_panel.py`)

Atalho: **Ferramentas → Sistema & Processos → Modo Performance**.

Permite controlar **individualmente** quais funcionalidades visuais são desativadas quando o monitor automático de travamentos é acionado, ou ao ativar o modo manualmente. Janela de 700×680.

### Funcionalidades controláveis

**Editor:**
Minimap, Syntax Highlighting (debounce), Sticky Scroll, Code Folding, Inline Blame, Color Preview, Inlay Hints, Ligatures, Edit Heatmap, Code Rhythm, Autocomplete.

**K-UI:**
Perfil de animações (dropdown de intensidade do motor holográfico).

**Linting:**
Lint automático ao salvar.

### Monitor automático

O Codis monitora o tempo de resposta da interface continuamente. Quando detecta um travamento acima de um limiar configurável (`perf_freeze_threshold_ms`, padrão **500ms**), aciona automaticamente o conjunto de desativações escolhido pelo usuário neste painel - sem precisar de intervenção manual.

O botão **Aplicar Agora** permite acionar manualmente o modo performance mesmo sem ter ocorrido um travamento, útil antes de abrir um arquivo muito grande.

---

## 📊 Performance Técnica, Benchmarks e Diagnóstico

Seção voltada a quem quer entender *como* o Codis atinge sua performance e como diagnosticar lentidão - não é sobre um painel específico, mas sobre a arquitetura de performance do editor como um todo.

### Metas de Performance

| Métrica | Meta | Medição |
|---|---|---|
| Tempo de inicialização (cold start) | < 1.5s | Da execução de `python run.py` até a janela visível |
| Abertura de arquivo (< 1 MB) | < 100ms | Do clique até o texto renderizado |
| Abertura de arquivo grande (1–10 MB) | < 500ms | Com modo de arquivo grande ativado |
| Syntax highlight (< 500 linhas) | < 16ms | Por frame (60fps) |
| Autocomplete (primeira sugestão) | < 200ms | Com LSP ativo |
| Busca global (projeto 10k arquivos) | < 2s | Com indexação em cache |
| Render de minimap | < 8ms | Por frame |

### Threading

O Codis usa threads para todas as operações que não tocam na UI:

```
Thread principal (Tkinter mainloop)
  ├── LSP I/O thread            ← leitura/escrita stdio com servidor LSP
  ├── Lint thread               ← execução de linters externos
  ├── Git thread                ← operações git (status, blame, log)
  ├── File watcher thread       ← inotify/FSEvents/ReadDirectoryChangesW
  ├── Plugin worker thread      ← execução de plugins via ProcessRunner
  └── IA inference thread       ← chamadas HTTP para APIs de IA
```

Resultados das threads são agendados de volta na thread principal via `root.after(0, callback)` - Tkinter não é thread-safe, então nenhum código de outra thread pode tocar widgets diretamente.

### Debounce e Throttle

| Operação | Estratégia | Delay |
|---|---|---|
| Syntax highlighting | Debounce | 50ms após última digitação |
| Lint | Debounce | 500ms após última digitação |
| LSP `textDocument/didChange` | Debounce | 100ms |
| Minimap render | Throttle | 60ms (≈16fps) |
| File tree refresh | Debounce | 200ms |
| Busca incremental | Debounce | 150ms |

### Modo Arquivo Grande

Arquivos com mais de **500 KB** ou **10.000 linhas** ativam automaticamente o modo de arquivo grande (`src/editor/large_file.py`):

- Syntax highlighting **desativado** (o Tkinter Tag system não escala para centenas de milhares de tags).
- Minimap em modo **low-res** (1 pixel por linha em vez de renderização completa).
- Code folding, Inline blame e Inlay hints **desativados**.

O arquivo ainda é completamente editável - apenas as features visuais pesadas são suspensas.

### Cache de Syntax Highlighting

O highlighter (`src/syntax/highlighter.py`) mantém um cache de resultados por hash do conteúdo. Re-highlight de um arquivo não modificado é um lookup de O(1). Qualquer edição invalida apenas o cache do arquivo editado, não o cache global.

### Diagnosticando lentidão manualmente

```bash
# Perfil de inicialização
python -m cProfile -o startup.prof -m src
python -m pstats startup.prof

# Perfil de operação específica (ex: abertura de arquivo grande)
# Adicione ao código:
import cProfile
cProfile.enable()
# ... operação ...
cProfile.disable()
cProfile.print_stats(sort='cumulative')
```

O `EventBus` loga automaticamente handlers que demoram mais de **50ms** em modo debug:

```bash
CODIS_DEBUG=1 python run.py
```

```
[EventBus] SLOW handler: git_panel.on_file_saved took 87ms for event editor:file:saved
```

### Limites conhecidos do Tkinter

| Limitação | Impacto | Mitigação no Codis |
|---|---|---|
| Tags no widget Text são O(n) para overlap | Highlight lento em arquivos grandes | Modo arquivo grande desativa tags |
| Canvas não usa aceleração GPU | Animações limitadas a ~60fps em CPU | K-UI usa deltas mínimos por frame |
| Font rendering depende do sistema | Ligatures inconsistentes entre SOs | `src/editor/ligatures.py` normaliza |
| Sem suporte a sub-pixel rendering | Texto levemente diferente entre SOs | Aceito como limitação da plataforma |
| `after()` não é tempo real | Delays variáveis sob carga de CPU | Throttle adaptativo baseado em frame time |

### Benchmarks de referência

Medidos em: Ryzen 5 3600, 16GB RAM, SSD NVMe, Python 3.12.3, Ubuntu 22.04.

| Operação | Tempo médio | Desvio padrão |
|---|---|---|
| Cold start | 890ms | ±45ms |
| Abertura arquivo 100KB Python | 42ms | ±8ms |
| Abertura arquivo 5MB JSON | 310ms | ±25ms |
| Highlight 500 linhas Python | 11ms | ±2ms |
| Highlight 2000 linhas Python | 38ms | ±5ms |
| Busca regex em projeto 1000 arquivos | 680ms | ±90ms |
| LSP completion (pylsp local) | 85ms | ±30ms |
| Git status (repo 5000 commits) | 120ms | ±20ms |

> Benchmarks reproduzíveis via `python -m pytest tests/ -m slow -v`

### Tuning pelo usuário

Em **Configurações → Performance**, o usuário pode ajustar:

| Configuração | Padrão | Impacto |
|---|---|---|
| `highlight_delay_ms` | 50 | Latência vs. CPU |
| `lint_delay_ms` | 500 | Frequência de lint |
| `large_file_threshold_kb` | 500 | Quando ativar modo arquivo grande |
| `minimap_enabled` | true | Desativar reduz CPU em ~5% |
| `animations_enabled` | true | Desativar reduz CPU em ~3% |
| `git_auto_refresh_ms` | 2000 | Frequência do git status automático |

---

## 🖱️ Cursor Customizável (`src/ui/cursor_manager.py`)

Acesso: integrado ao seletor de tema/aparência (categoria **Cursor** dentro de [Configurações](configuracoes.md)).

Substitui o ponteiro do mouse do sistema por um cursor de software: emoji, texto, imagem (PNG/SVG via Pillow) ou combinações.

### Como funciona internamente

- O cursor nativo do sistema é ocultado na janela (`root.config(cursor="none")`)
- Um `Toplevel` transparente e clicável segue o mouse em tempo real
- Esse `Toplevel` exibe o cursor escolhido como `Label` ou `Canvas`
- Os cliques atravessam normalmente o overlay: no Windows via `SetWindowLong` (passthrough nativo) e configuração de `-transparentcolor`; no Linux/macOS via `Toplevel` ultra-fino de 1px com `wm_attributes("-alpha", 0.99)`

### Limitações conhecidas

- Emojis dependem da fonte disponível no sistema operacional
- Cursores de imagem requerem Pillow instalado (`pip install Pillow`)
- Existe uma latência de até ~1 frame (≤16ms) entre o cursor real e o de software, imperceptível na maioria dos casos

---

## 🌐 Explorador de Arquivos Remotos (`src/ui/remote_browser.py`)

Suporta múltiplos protocolos de acesso remoto a arquivos em uma única interface unificada:

| Protocolo | Implementação |
|---|---|
| SSH / SFTP | Via cliente SSH do próprio sistema (`scp`/`sftp`) |
| FTP | Via `ftplib` (biblioteca padrão) |
| FTPS | FTP sobre TLS, via `ftplib` |
| HTTP/HTTPS | Listagem de índices Apache/Nginx + download |
| WebDAV | Via `requests` + `xml.etree` (requer `pip install requests`) |
| Local/SMB | Montagens de rede já visíveis no sistema de arquivos local |

### Funcionalidades

- Gerenciamento de conexões com perfis persistentes em JSON
- Navegação em árvore com duplo-clique para entrar em pastas
- Download e upload bidirecional
- Abertura direta de arquivos remotos no editor do Codis (texto, código, imagem ou notebook)
- **Sync automático ao salvar**: ao editar um arquivo aberto remotamente e salvar, o Codis faz upload de volta ao servidor automaticamente
- Menu de contexto com ações rápidas e filtro de busca

Cada protocolo é implementado como um `_Adapter` próprio, seguindo o mesmo padrão arquitetural usado no [Chat Panel](chat.md).
