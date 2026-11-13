# ⌨️ Atalhos de Teclado

Referência completa de todos os atalhos padrão do Codis. Todos os atalhos operam sobre a janela principal.

---

## Geral

| Atalho | Ação |
|--------|------|
| `Ctrl+Shift+P` | Abrir Paleta de Comandos |
| `Ctrl+P` | Abertura Rápida de Arquivos |
| `Ctrl+,` | Configurações |
| `F11` | Tela cheia |
| `Ctrl+K Z` | Modo Zen (sem distrações) |

---

## Arquivo

| Atalho | Ação |
|--------|------|
| `Ctrl+N` | Novo arquivo |
| `Ctrl+O` | Abrir arquivo |
| `Ctrl+S` | Salvar |
| `Ctrl+Shift+S` | Salvar como |
| `Ctrl+W` | Fechar aba ativa |
| `Alt+F4` | Sair |

---

## Edição

| Atalho | Ação |
|--------|------|
| `Ctrl+Z` | Desfazer |
| `Ctrl+Y` | Refazer |
| `Ctrl+Shift+Z` | Refazer (alternativo) |
| `Ctrl+X` | Recortar |
| `Ctrl+C` | Copiar |
| `Ctrl+V` | Colar |
| `Ctrl+A` | Selecionar tudo |
| `Ctrl+L` | Selecionar linha atual |
| `Ctrl+D` | Duplicar linha |
| `Ctrl+Shift+K` | Apagar linha |
| `Alt+↑` | Mover linha para cima |
| `Alt+↓` | Mover linha para baixo |
| `Ctrl+/` | Alternar comentário |
| `Shift+Alt+F` | Formatar documento |
| `Ctrl+Shift+V` | Histórico de colagem |
| `Insert` | Alternar modo sobrescrita |

---

## Busca e Navegação

| Atalho | Ação |
|--------|------|
| `Ctrl+F` | Buscar no arquivo |
| `Ctrl+H` | Buscar e substituir |
| `Ctrl+Shift+F` | Buscar em todos os arquivos |
| `F3` | Próxima ocorrência |
| `Shift+F3` | Ocorrência anterior |
| `Escape` | Fechar barra de busca |
| `Ctrl+G` | Ir para linha |
| `Ctrl+Shift+O` | Ir para símbolo |
| `F8` | Próximo problema (lint) |
| `Shift+F8` | Problema anterior (lint) |
| `F7` | Próximo hunk de diff |
| `Shift+F7` | Hunk anterior de diff |

---

## Abas

| Atalho | Ação |
|--------|------|
| `Ctrl+Tab` | Próxima aba |
| `Ctrl+Shift+Tab` | Aba anterior |
| `Ctrl+PageDown` / `Ctrl+PageUp` | Próxima / aba anterior |
| `Ctrl+1` … `Ctrl+8` | Ir direto para a aba N |
| `Ctrl+9` | Ir para a última aba |
| `Ctrl+9` | Ir para a última aba |
| `Ctrl+Shift+T` | Reabrir a última aba fechada |

---

## Painéis e Sidebar

| Atalho | Ação |
|--------|------|
| `Ctrl+`` ` `` | Abrir/focar Terminal |
| `Ctrl+M`* | Abrir painel de Problemas |
| `Ctrl+Shift+A` | Abrir painel de IA |
| `Ctrl+Shift+N`** | Abrir Scratchpad |
| `Ctrl+Shift+J` | Abrir painel de Snippets |
| `Ctrl+Shift+R` | Abrir Regex Playground |
| `Ctrl+Shift+C` | Abrir Color Picker |

> \* O menu e o ícone da Activity Bar rotulam este atalho como "Ctrl+Shift+M", mas a binding registrada no código é `<Control-M>`. `Ctrl+Shift+M` também está vinculado à Macro Toolbar (`<Control-Shift-M>`), que é mais específica e tende a prevalecer; confirme no seu sistema (veja [linting.md](linting.md) para detalhes técnicos).

> O painel [Git](git.md) também exibe um rótulo "Ctrl+Shift+G" na Activity Bar sem um keybind funcional correspondente no código atual - abra-o pelo ícone 🌿, pela barra de status ou pelo menu Exibir.

> \*\* Com o cursor dentro do editor de código, `Ctrl+Shift+N` é capturado pelo [Sticky Note Gutter](inteligencia-editor.md#sticky-note-gutter) antes de chegar ao Scratchpad. Use o menu ou a Activity Bar para abrir o Scratchpad de forma confiável.

---

## Multi-Cursor e Ferramentas de Sistema

| Atalho | Ação |
|--------|------|
| `Ctrl+Alt+M` (minúsculo, `<Control-Alt-m>`) | Ativar/desativar Multi-Cursor |
| `Ctrl+Alt+M` (com Shift, `<Control-Alt-M>`) | Abrir Gerenciador de Processos |
| `Ctrl+Alt+↑` / `Ctrl+Alt+↓` | Adicionar cursor acima/abaixo (com Multi-Cursor ativo) |
| `Ctrl+D` | Duplicar linha - ou selecionar próxima ocorrência se o Multi-Cursor estiver ativo |
| `Ctrl+Shift+L` | Selecionar todas as ocorrências (com Multi-Cursor ativo) |

> Os dois atalhos `Ctrl+Alt+M` são tecnicamente sequências diferentes no código (`m` minúsculo vs `M`/Shift), mas dependendo do sistema operacional e layout de teclado, podem não se comportar como esperado. Veja [Inteligência do Editor](inteligencia-editor.md#multi-cursor) para detalhes. Se um não funcionar, use o menu **Ferramentas → Workspace & Editor → Multi-Cursor**.

---

## Zoom

| Atalho | Ação |
|--------|------|
| `Ctrl+=` | Aumentar zoom |
| `Ctrl+-` | Diminuir zoom |
| `Ctrl+0` | Resetar zoom (100%) |
| `Ctrl+Alt+=` | Aumentar a interface (+10%) |
| `Ctrl+Alt+-` | Diminuir a interface (-10%) |
| `Ctrl+Alt+0` | Interface em tamanho automático |

> O zoom acima afeta só o texto do editor; os atalhos `Ctrl+Alt` ajustam a **escala da interface** inteira (a mesma opção `ui_scale` das Configurações).

---

## Execução e Lint

| Atalho | Ação |
|--------|------|
| `F5` | Executar arquivo atual |
| `Shift+Alt+F` | Formatar documento |

---

## LSP

| Atalho | Ação |
|--------|------|
| `F12` | Ir para definição (LSP) |
| `Shift+F12` | Encontrar referências (LSP) |
| `F6` | Renomear símbolo (LSP) |

---

## Bookmarks

| Atalho | Ação |
|--------|------|
| `Ctrl+F2` | Adicionar bookmark |
| `F2` | Próximo bookmark |
| `Shift+F2` | Bookmark anterior |

---

## Recursos Exclusivos

| Atalho | Ação |
|--------|------|
| `Ctrl+Alt+V` | Alternar Modo Vim |
| `Ctrl+Alt+N` | Alternar K-UI |
| `Ctrl+Alt+P` | Abrir painel Pomodoro (padrão do Key Remapper). **Atenção:** `_app_build.py` também liga `Ctrl+Alt+P` a mostrar/ocultar o Zen Player; se o atalho abrir o player, remapeie um dos dois em Ferramentas → Key Remapper |
| `Ctrl+Alt+Y` | Abrir o [Preview Web](embed-e-preview.md#2-preview-web-navegador-real-embutido) |
| `Ctrl+Shift+W` | Abrir o Workspace Switcher ([Workspace](workspace-projeto.md)) |
| `Ctrl+\` | Alternar foco entre os dois painéis do editor dividido |
| - *(sem atalho dedicado; use a Activity Bar → ✅)* | Abrir Gerenciador de Tarefas |
| `Ctrl+Shift+Q` | Abrir SQL Studio (painel inferior) |

---

## Remoto & Colaboração

| Atalho | Ação |
|--------|------|
| `Ctrl+Shift+H` | Abrir [SSH Remoto](remote-ssh.md) |
| `Ctrl+Shift+U` | Abrir [Acessibilidade](acessibilidade.md) |

> O painel de [Chat ao Vivo](chat.md) exibe o rótulo "Ctrl+Shift+T" na Activity Bar, mas `Ctrl+Shift+T` está vinculado a **Reabrir aba fechada** (veja [Abas](#abas)); abra o Chat pelo ícone 💬 ou pelo menu Ferramentas.

> O painel de [Colaboração](colaboracao.md) não possui atalho de teclado dedicado - acesse via menu **Ferramentas → Remoto & Colaboração → Colaboração…** ou pelo ícone 👥 na barra de atividades.

---

## Atalhos Específicos do Vim Mode

Ao entrar no Vim Mode (`Ctrl+Alt+V`), uma janela CLI separada é aberta. Os seguintes comandos Vim estão disponíveis nela:

### Modo NORMAL

| Tecla | Ação |
|-------|------|
| `h` `j` `k` `l` | Mover cursor (esq, baixo, cima, dir) |
| `w` `b` `e` | Mover por palavra (frente, trás, fim) |
| `gg` | Início do arquivo |
| `G` | Fim do arquivo |
| `dd` | Apagar linha |
| `yy` | Copiar linha (yank) |
| `p` `P` | Colar depois / antes |
| `u` | Desfazer |
| `Ctrl+R` | Refazer |
| `i` `I` | Entrar em INSERT (cursor / início da linha) |
| `a` `A` | Entrar em INSERT (após cursor / fim da linha) |
| `o` `O` | Nova linha abaixo / acima e INSERT |
| `v` | Entrar em VISUAL |
| `V` | Entrar em V-LINE |
| `r` | Entrar em REPLACE (um char) |
| `:` | Entrar em COMMAND |
| `Esc` | Voltar a NORMAL |

### Modo COMMAND

| Comando | Ação |
|---------|------|
| `:w` | Salvar arquivo |
| `:q` | Sair do Vim Mode |
| `:q!` | Sair sem salvar |
| `:wq` | Salvar e sair |

---

## K-UI Spotlight (K-UI)

Quando o K-UI está ativo, `Ctrl+Space` abre o **Spotlight** - uma paleta de comandos holográfica que lista todos os comandos disponíveis com busca fuzzy instantânea.

`Ctrl+E` alterna o explorador de arquivos holográfico.

---

## Observação sobre Atalhos de Clipboard

Os atalhos `Ctrl+C`, `Ctrl+X` e `Ctrl+V` funcionam de forma inteligente:

- Se o foco estiver em um campo de texto (`Entry`): operam sobre esse campo.
- Se o foco estiver no editor (`Text`): delegam ao handler interno do editor (que também registra o histórico de colagem).
- Em qualquer outro caso: operam sobre o editor ativo.
