# 💻 Terminal

O Codis possui um terminal integrado completo com suporte a múltiplos shells e REPLs interativos.

---

## Abrindo o Terminal

| Método | Ação |
|--------|------|
| `Ctrl+`` ` `` | Abre o painel inferior na aba Terminal |
| Menu **Terminal → Novo Terminal** | Abre um novo terminal adicional *(o menu exibe o rótulo "Ctrl+Shift+`", mas não há bind de teclado funcional para essa combinação no código atual)* |
| Menu **Terminal → Novo Terminal** | Cria nova instância |
| Painel inferior → aba **Terminal** | Clique direto |

---

## Shells Suportados

O Codis detecta automaticamente os shells disponíveis no sistema e os lista no seletor da barra de status do terminal.

### Windows

| Shell | Executável |
|-------|-----------|
| PowerShell | `powershell.exe` |
| PowerShell Core | `pwsh.exe` |
| CMD | `cmd.exe` |
| Bash (Git/Cygwin) | `bash.exe --login -i` |
| WSL (padrão) | `wsl.exe` |
| BusyBox ash | `busybox.exe ash` |
| Dash | `dash.exe` |
| Korn Shell | `ksh.exe` |
| Tcsh | `tcsh.exe` |
| Csh | `csh.exe` |
| Elvish | `elvish.exe` |
| Nushell | `nu.exe` |
| Xonsh | `xonsh.exe` |
| Oil | `osh.exe` |

### Linux / macOS

| Shell | Executável |
|-------|-----------|
| Bash | `bash --norc` |
| Zsh | `zsh --no-rcs` |
| Fish | `fish --no-config` |
| Dash | `dash` |
| Korn Shell | `ksh` |
| Tcsh | `tcsh` |
| Csh | `csh` |
| Ash | `ash` |
| Elvish | `elvish` |
| Nushell | `nu` |
| Xonsh | `xonsh` |
| Oil | `osh` |
| sh (padrão do sistema) | `$SHELL` |

### REPLs de Linguagens (Multiplataforma)

| REPL | Condição |
|------|----------|
| Python REPL | `python3`/`python` disponível (`-i -u`) |
| IPython | `ipython` disponível |
| BPython | `bpython` disponível |
| PTPython | `ptpython` disponível |
| Node.js REPL | `node` disponível (`--interactive`) |
| Ruby (irb) | `irb` disponível |
| Perl debugger | `perl` disponível (`perl -de 1`) |
| PHP interativo | `php -a` disponível |
| Lua REPL | `lua` disponível |
| R REPL | `R --quiet` disponível |
| Julia REPL | `julia` disponível |
| Elixir (iex) | `iex` disponível |
| Erlang | `erl` disponível |
| Clojure REPL | `clojure` disponível |
| Scala REPL | `scala` disponível |
| Groovy (groovysh) | `groovysh` disponível |
| Tcl | `tclsh` disponível |
| Gauche Scheme | `gosh` disponível |
| Guile | `guile` disponível |
| Racket | `racket -i` disponível |
| SWI-Prolog | `swipl` disponível |

Se nenhum shell ou REPL estiver disponível, o fallback final é `cmd.exe` no Windows ou `/bin/sh` no Linux/macOS.

---

## Configurações do Terminal

| Opção | Padrão | Descrição |
|-------|--------|-----------|
| `default_shell` | detectado automaticamente | Shell inicial ao abrir o terminal |
| `terminal_font_size` | `12` | Tamanho da fonte |

O shell padrão pode ser alterado via `Ctrl+,` ou clicando no nome do shell na barra de status do terminal.

---

## Operações Disponíveis

### Via Menu Terminal

| Comando | Atalho | Descrição |
|---------|--------|-----------|
| Novo Terminal | - *(rótulo "Ctrl+Shift+`" no menu, sem bind funcional)* | Cria uma nova instância de terminal |
| Limpar Terminal | - | Limpa o buffer de saída do terminal ativo |
| Matar Terminal | - | Encerra o processo do terminal ativo |
| Executar Seleção | - | Envia o texto selecionado no editor para o terminal |

### Executar Arquivo (F5)

Pressionar `F5` executa o arquivo ativo no terminal usando o **Code Runner** (`src/ui/code_runner.py`), que suporta 22 linguagens com comando pré-configurado (customizável via **Ferramentas → Code Runners**):

| Linguagem | Comando |
|-----------|---------|
| Python | `<python do Codis> <arquivo>` |
| JavaScript | `node <arquivo>` |
| TypeScript | `ts-node <arquivo>` |
| Ruby | `ruby <arquivo>` |
| PHP | `php <arquivo>` |
| Go | `go run <arquivo>` |
| Rust | `cargo script <arquivo>` (requer `cargo-script`) |
| Java | `java <nome-sem-extensão>` (requer compilação prévia) |
| Kotlin | `kotlin <arquivo>` |
| Swift | `swift <arquivo>` |
| Lua | `lua <arquivo>` |
| Perl | `perl <arquivo>` |
| Bash | `bash <arquivo>` |
| PowerShell | `powershell -File <arquivo>` |
| R | `Rscript <arquivo>` |
| Julia | `julia <arquivo>` |
| Dart | `dart run <arquivo>` |
| Scala | `scala <arquivo>` |
| Elixir | `elixir <arquivo>` |
| Haskell | `runhaskell <arquivo>` |
| C (gcc) | `gcc <arquivo> -o <nome> && ./<nome>` |
| C++ (g++) | `g++ <arquivo> -o <nome> && ./<nome>` |

Para outras linguagens sem runner configurado, a barra de status exibe um aviso sugerindo configurar um runner customizado, ou use o terminal diretamente.

### Executar Seleção

Selecione um trecho de código e use **Terminal → Executar Seleção** para enviar diretamente ao terminal ativo. Se nenhum texto estiver selecionado, envia a linha atual.

---

## Diretório de Trabalho

Ao abrir um workspace, o terminal é iniciado no diretório raiz do workspace. Se nenhum workspace estiver aberto, o terminal inicia no diretório `$HOME`.

Ao abrir um workspace após o terminal já estar ativo, o diretório de trabalho é atualizado automaticamente (`set_cwd`).

Em alguns casos, pode ser preciso confirmar o comando para entrar no diretório (embora nos testes isso só tenha ocorrido algumas vezes em powershell).

---

## Múltiplas Instâncias

O Codis suporta múltiplas instâncias de terminal simultâneas. Cada instância mantém seu próprio processo de shell, histórico e diretório de trabalho.

Navegue entre instâncias usando as abas internas do terminal ou criando novas via **Terminal → Novo Terminal**.

---

## Easter Eggs via Terminal

O Codis possui easter eggs ocultos que podem ser ativados via comandos especiais no terminal. Use o prefixo `/codis` seguido de um código secreto. Os easter eggs ativam efeitos visuais e animações especiais na interface.
Dica Direta: Konami Code.

---

## Modo K-UI: Terminal Flutuante

Quando o **K-UI** está ativo, o painel de Terminal pode ser "despluggado" para uma janela flutuante independente:

1. Clique com botão direito na aba "Terminal" no painel inferior
2. Selecione **⊞ Despluggar 'terminal'**

A janela flutuante pode ser movida e redimensionada livremente. Para reancorá-la, clique com botão direito na aba novamente.

Ver [K-UI](kui.md) para mais detalhes sobre painéis flutuantes.