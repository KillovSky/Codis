# 🗂️ Workspace & Produtividade de Projeto

Esta página cobre o conjunto de ferramentas voltadas à organização do **workspace** (pasta de projeto) e à produtividade ao alternar entre projetos: troca rápida de workspace, templates de novo projeto, execução de código multi-linguagem, gravação de macros, remapeamento de atalhos e o modelo de confiança de pastas (Workspace Trust).

Acesso à maioria delas: menu **Ferramentas → Workspace & Editor**.

---

## 🔁 Workspace Switcher (`src/ui/workspace_switcher.py`)

Atalho: `Ctrl+Shift+W` ou **Ferramentas → Workspace & Editor → Alternar Workspace**.

Painel flutuante (700×480) para trocar rapidamente entre workspaces recentemente abertos.

- Lista de workspaces recentes com ícone indicando o tipo detectado (Git, projeto Python, projeto JS/Node...)
- Pré-visualização da árvore de diretórios ao passar o mouse sobre um item
- Busca em tempo real por nome de pasta
- Ações: abrir, remover da lista de recentes, abrir no explorador de arquivos do sistema

---

## 📋 Project Templates (`src/ui/project_templates.py`)

Atalho: **Ferramentas → Workspace & Editor → Novo Projeto a partir de Template**.

Cria projetos novos a partir de templates pré-definidos ou customizados pelo usuário. Cada template é uma pasta com arquivos e um `template.json` de metadados.

### Templates embutidos

| ID | Descrição |
|---|---|
| `python_basic` | Script Python simples |
| `python_package` | Pacote Python instalável (com `pyproject.toml`) |
| `fastapi` | API REST com FastAPI |
| `flask` | Aplicação web com Flask |
| `cli_tool` | Ferramenta de linha de comando |
| `node_basic` | Projeto Node.js básico |
| `typescript_basic` | Projeto TypeScript básico |
| `data_science` | Estrutura para notebooks/análise de dados |
| `mkdocs` | Site de documentação com MkDocs |
| `makefile_c` | Projeto C/C++ com Makefile |

### Funcionalidades

- Janela de criação (820×580) com preview da estrutura de arquivos antes de confirmar
- **Variáveis de substituição** nos arquivos do template: `{{project_name}}`, `{{author}}`, `{{year}}` e outras, substituídas no momento da criação
- Templates customizados: qualquer pasta pode ser salva como template novo
- Suporte a **comandos pós-criação** (ex.: `git init`, `pip install -r requirements.txt`) executados automaticamente após gerar os arquivos

---

## ▶️ Code Runner Configurável (`src/ui/code_runner.py`)

Atalho: **Ferramentas → Workspace & Editor → Configurar Code Runner**. A execução em si é via `F5` (Executar Arquivo).

Permite rodar arquivos em qualquer linguagem com comando, argumentos e variáveis de ambiente customizáveis por linguagem.

### Linguagens pré-configuradas

Python, JavaScript, TypeScript, Ruby, PHP, Go, Rust, Java, Kotlin, Swift, Lua, Perl, Bash, PowerShell, R, Julia, Dart, Scala, Elixir, Haskell, C e C++ - mais de 30 linguagens no total, incluindo variações de runtime.

### Funcionalidades

- Editar o comando de execução de qualquer linguagem (por exemplo, trocar `python` por `python3.12` ou adicionar flags)
- Argumentos extras e variáveis de ambiente específicas por linguagem
- Timeout configurável por execução (evita que um script travado prenda o terminal indefinidamente)
- Histórico de execuções com timestamp

---

## 🎬 Macro Recorder (`src/ui/macro_recorder.py`)

Atalhos: `Ctrl+Shift+M` (iniciar/parar gravação) · `Ctrl+Alt+M` (reproduzir).

Grava e reproduz sequências de teclas e ações do editor.

### Funcionalidades

- Toolbar flutuante com botões Gravar / Parar / Reproduzir
- Captura texto digitado, atalhos de teclado e navegação de cursor
- Múltiplas macros nomeadas, com persistência em JSON
- Reprodução com delay configurável (útil para visualizar a macro rodando passo a passo)
- Importar/exportar macros como arquivo JSON, para compartilhar com outra instância do Codis

---

## ⌨️ Key Remapper (`src/ui/key_remapper.py`)

Acesso: **Configurações → Atalhos de Teclado...**

Permite remapear qualquer atalho de teclado da aplicação. Os atalhos customizados são persistidos separadamente da configuração principal e aplicados em toda a sessão.

### Arquitetura interna

| Componente | Responsabilidade |
|---|---|
| `KeyRemapperStore` | Lê/grava `keybindings.json`, separado do `config.json` |
| `KeyRemapperDialog` | Janela de edição (760×560) com lista pesquisável e captura interativa de tecla |
| `KeyRemapperManager` | Aplica os bindings customizados na janela raiz do Tk; usado pelo `CodisApp` na inicialização |

Para redefinir um atalho: localize a ação na lista pesquisável, clique em **Capturar**, pressione a nova combinação desejada - a janela de captura (320×120) registra a combinação e a salva imediatamente.

---

## 🔒 Workspace Trust (`src/ui/workspace_trust.py`)

Sistema de segurança acionado automaticamente ao abrir uma pasta **nunca aberta antes** nesta máquina.

### Como funciona

Ao abrir um workspace novo, o Codis exibe um diálogo de confiança perguntando se o conteúdo da pasta é confiável. Pastas marcadas como **não confiáveis** têm os seguintes recursos restringidos automaticamente:

| Recurso | Comportamento em pasta não confiável |
|---|---|
| Plugins | Carregados em modo somente-leitura, sem execução de código |
| Execução de tarefas/scripts | Bloqueada |
| LSP | Desabilitado (servidores de linguagem podem executar código arbitrário) |
| Terminal integrado | Exibe aviso visual de modo restrito |

### Persistência

A lista de pastas confiáveis fica em `~/.codis/trusted_workspaces.json`. Uma vez confiada, a pasta (e qualquer subpasta dela) não pergunta novamente. Para revogar a confiança de uma pasta, edite ou remova a entrada correspondente nesse arquivo.

> Este modelo é equivalente ao "Workspace Trust" do VS Code: protege contra a abertura descuidada de repositórios desconhecidos que poderiam, por exemplo, embutir um `.codis/plugins/malicioso.py` ou configurar um LSP apontando para um binário malicioso.

---

## 🪟 Panel Detach (`src/ui/panel_detach.py`)

Gerencia o **destacamento** de painéis (Terminal, Output, Git, Debug, Regex, Problems) para janelas flutuantes independentes, arrastáveis e redimensionáveis.

Antes da versão atual, o destacamento só funcionava com o modo K-UI ativo, pois a lógica vivia dentro do controlador holográfico. O `PanelDetachManager` foi extraído como uma classe independente, instanciada uma única vez na inicialização do `CodisApp`, e funciona tanto no modo clássico quanto no modo K-UI - incluindo a janela flutuante com moldura holográfica (`KFloatWindow`), que é reaproveitada de [K-UI](kui.md).

Compartilhar uma única instância entre os dois modos resolve um problema de UX: painéis destacados **não são fechados à força** quando o usuário alterna o K-UI ligado/desligado - eles continuam abertos normalmente.
