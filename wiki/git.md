# 🌿 Git

O Codis possui integração Git nativa completa através do `GitPanel` (`src/ui/git_panel.py`). Nenhuma extensão adicional é necessária.

---

## Requisitos

- Git instalado e disponível no `PATH`.
- Um workspace aberto que seja (ou contenha) um repositório Git.

O Codis detecta automaticamente se o workspace é um repositório Git. Se não for, o painel exibe um botão para inicializar (`git init`).

---

## Abrindo o Painel Git

Existem três formas:

1. **Activity Bar** → ícone 🌿 (Source Control)
2. **Barra de Status** → clique no nome da branch (ex: `🌿 main · 3↑`)
3. **Menu Exibir → Git**

> O ícone 🌿 na Activity Bar exibe o rótulo "Ctrl+Shift+G", mas esse atalho de teclado não tem keybind funcional vinculado no código atual (mesma situação de [Problemas](linting.md#nota-técnica) e [SSH Remoto](remote-ssh.md) - o rótulo da Activity Bar nem sempre reflete um bind real). Use o menu ou clique no ícone diretamente.

O painel Git existe em dois lugares:
- **Sidebar** - painel principal de controle de versão.
- **Painel Inferior → aba Git** - instância adicional (útil quando a sidebar está com o Explorer aberto).

---

## Seções do Painel

### Staged Changes (Mudanças em Stage)

Arquivos adicionados à área de staging (`git add`). Para cada arquivo:
- **Unstage** - remove do staging (⊖)
- **Discard** - descarta as mudanças (⊘)
- **Ver diff** - clique no arquivo para abrir o diff no painel Diff

### Changes (Mudanças)

Arquivos modificados mas não staged. Para cada arquivo:
- **Stage** - adiciona ao staging (⊕)
- **Discard** - reverte ao estado do último commit (⊘)
- **Ver diff** - clique no arquivo

### Untracked (Novos Arquivos)

Arquivos novos não rastreados pelo Git. Para cada arquivo:
- **Stage** - adiciona ao tracking e staging (⊕)
- **Ignorar** - adiciona ao `.gitignore`

---

## Operações Disponíveis

### Commit

1. Preencha a mensagem de commit no campo de texto.
2. Clique em **✓ Commit** ou pressione `Ctrl+Enter` dentro do campo.

O Codis valida que a mensagem não está vazia antes de commitar.

### Stage / Unstage em Massa

- **Stage All** - adiciona todos os arquivos modificados ao staging.
- **Unstage All** - remove todos os arquivos do staging.

### Push / Pull / Fetch

Botões na barra superior do painel. Executados em thread separada para não bloquear a UI. Um indicador de progresso é exibido durante a operação.

- **Fetch** - atualiza as informações do remote sem mesclar.
- **Pull** - `git pull` com o remote configurado.
- **Push** - `git push` para o remote e branch configurados.

### Stash

O botão **Stash** abre o **Gerenciador de Stash**, um painel completo com:

- **Campo de mensagem** - descreva o stash antes de guardar (opcional).
- **Guardar** - executa `git stash push` com a mensagem informada.
- **Lista de stashes** - exibe todos os stashes do repositório com referência (`stash@{N}`) e mensagem.
- **Pop (aplicar + remover)** - restaura o stash selecionado e o remove da pilha (`git stash pop`).
- **Aplicar** - aplica o stash selecionado sem removê-lo da pilha (`git stash apply`).
- **Ver Diff** - exibe o diff do stash selecionado no painel de diff integrado.
- **Remover** - descarta o stash selecionado com confirmação (`git stash drop`).

### Branch

O seletor de branch na parte superior do painel permite:
- **Trocar de branch** - selecione uma branch existente.
- **Criar nova branch** - insira um nome e pressione Enter.
- **Deletar branch** - clique com botão direito em uma branch.

### Git Log

Uma seção de log compacto exibe os commits mais recentes com:
- Hash curto (7 chars)
- Mensagem do commit
- Autor e data

Clicar em um commit mostra os detalhes no painel de Output.

---

## Diff Inline

Ao clicar em um arquivo modificado (staged ou unstaged), o diff é exibido no **Painel Inferior → aba Diff**.

O diff mostra lado a lado:
- **Original** - conteúdo salvo no disco (ou HEAD)
- **Modificado** - conteúdo atual

Navegação por hunks: `F7` (próximo) e `Shift+F7` (anterior).

### Diff entre Dois Arquivos

Via **Ferramentas → Diff: Dois Arquivos** ou Paleta de Comandos, selecione dois arquivos quaisquer para compará-los lado a lado.

### Diff: Arquivo vs. Salvo

Via **Ferramentas → Diff: Arquivo vs. Versão Salva** mostra a diferença entre o conteúdo atual do editor (com modificações não salvas) e a última versão salva em disco.

---

## Indicadores na Árvore de Arquivos

Quando um workspace Git está aberto, a árvore de arquivos (Explorer) exibe indicadores visuais:

| Indicador | Significado |
|-----------|------------|
| `M` | Modificado |
| `U` | Não rastreado (Untracked) |
| `A` | Adicionado ao staging |
| `D` | Deletado |
| `R` | Renomeado |
| `C` | Copiado |

---

## Badge na Activity Bar

O ícone de Git na Activity Bar exibe um badge com o número de arquivos com mudanças (staged + unstaged + untracked). O badge é atualizado automaticamente após cada operação.

---

## Autenticação Git

Para repositórios remotos que requerem autenticação (HTTPS), o painel exibe um diálogo de autenticação quando detecta erros de credenciais (`AuthManager`).

Suporta:
- Usuário + senha
- Tokens de acesso pessoal (PAT)

As credenciais são armazenadas de forma segura no arquivo de configuração do Codis (em Base64) e associadas ao host do repositório.

### Gerenciar Credenciais

As credenciais podem ser gerenciadas diretamente em **Configurações → Git**, na seção **Credenciais Git (HTTPS)**:

- **Lista de hosts** - exibe os hosts para os quais há credenciais salvas.
- **＋ Adicionar / Atualizar credenciais** - abre um diálogo para informar o host (ex: `github.com`), usuário e token/senha.
- **Remover** - apaga as credenciais de um host específico.

> As credenciais são gerenciadas exclusivamente na janela de Configurações, categoria **Git**.

---

## Clone de Repositório

Na **Tela de Boas-Vindas**, há um campo para clonar repositórios diretamente:

1. Cole a URL do repositório (HTTPS ou SSH).
2. Selecione o diretório de destino.
3. Clique em **Clone**.

O progresso do clone é exibido no **Painel de Saída → canal "Git Clone"**. Ao concluir, o workspace é automaticamente aberto na pasta clonada.

---

## Fetch Automático

Com `git_auto_fetch: true`, o Codis executa `git fetch` automaticamente a cada `git_fetch_interval` segundos (padrão: 180).

---

## Dependência: Comando `git`

Todas as operações Git são feitas via chamadas ao executável `git` em subprocessos. O Codis não usa nenhuma biblioteca Python para Git. Isso garante compatibilidade com qualquer versão do Git e respeita as configurações locais do usuário (`.gitconfig`, credencial store, etc.).

---

## Segurança

O `GIT_ASKPASS` (`src/ui/git_auth.py`) usa um script Python **estático**, sem credenciais embutidas, que lê usuário e token de variáveis de ambiente do subprocesso Git. A credencial nunca passa por um parser de shell (aspas, `` ` ``, `$()` e `&` são tratados como texto literal) e o helper é gerado uma única vez e reaproveitado, sem acumular arquivos com senha em disco.

`git_credentials` (dentro de `config.json`) é salvo com permissão `0600` (Linux/macOS).
