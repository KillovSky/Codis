# 🤖 Assistente de IA

O Codis integra um painel de chat com IA diretamente na sidebar. Suporta múltiplos provedores, streaming de respostas, ferramentas de contexto e skills personalizáveis.

---

## Abrindo o Painel de IA

- **Activity Bar** → ícone `[IA]`
- **Menu Exibir** → "Assistente IA"
- `Ctrl+Shift+A`

---

## Provedores Suportados

| Provedor | Tipo | Requer Chave |
|----------|------|-------------|
| **Anthropic (Claude)** | API Anthropic | Sim |
| **OpenAI (ChatGPT)** | API OpenAI | Sim |
| **Google Gemini** | API Gemini | Sim |
| **OpenAI Compatible** | Qualquer `/v1/chat/completions` | Opcional |
| **Ollama** | HTTP local (`localhost:11434`) | Não |
| **LocalLib** | `llama-cpp-python` direto | Não |

O provedor **OpenAI Compatible** funciona com: LM Studio, Jan, Groq, Together AI, OpenRouter, Perplexity, e qualquer servidor compatível com a API OpenAI.

### Configurando um Provedor

1. No painel de IA, clique no ícone de configuração (⚙) ao lado do seletor de provedor.
2. Insira a chave de API, modelo e URL base (quando aplicável).
3. Clique em **Testar** para verificar a conectividade.

As configurações de cada provedor são salvas em `config.json`.

---

## Contexto de Código

O assistente pode receber contexto automático do editor ativo. Selecione o tipo de contexto no seletor:

| Opção | Descrição |
|-------|-----------|
| **Nenhum** | Sem contexto de código |
| **Arquivo** | Conteúdo completo do arquivo ativo |
| **Seleção** | Apenas o texto selecionado no editor |
| **Manual** | Você escreve o contexto manualmente |

Além do que é selecionado manualmente, o `ContextBuilder` (`src/ia/context_builder.py`) monta automaticamente um bloco de contexto bem mais rico, injetado no system prompt antes de cada requisição - toda a coleta é projetada para falhar silenciosamente caso alguma informação não esteja disponível, sem nunca travar o envio da mensagem:

**Nível de projeto:**
nome, caminho e tipo de projeto (detectado por arquivos de configuração como `pyproject.toml`, `package.json` etc.), linguagens predominantes com contagem de arquivos, árvore de arquivos relevante (filtrada e limitada em tamanho), branch e último commit Git, status de arquivos modificados, dependências declaradas e um resumo das primeiras linhas do `README`.

**Nível de arquivo ativo:**
caminho relativo, linguagem, imports/includes do arquivo (para entender suas dependências internas), e o conteúdo da janela ao redor do cursor.

Com esse contexto automático, o modelo normalmente **não precisa perguntar** em qual workspace está, qual arquivo foi aberto, qual linguagem é usada ou como o projeto se estrutura - ele já recebe isso de forma implícita em cada requisição.

---

## Skills (Ações Rápidas)

Skills são prompts pré-configurados que fornecem instruções especializadas ao modelo antes da mensagem do usuário. O seletor de skills fica na barra de ferramentas do painel de IA.

### Skills Padrão

| Skill | Finalidade |
|-------|-----------|
| `ask` | Pergunta geral de programação |
| `generate` | Gerar novo código |
| `explain` | Explicar código selecionado |
| `fix` | Corrigir bugs identificados |
| `refactor` | Refatorar código |
| `review` | Revisão de código (code review) |
| `document` | Gerar documentação (docstrings, JSDoc) |
| `tests` | Gerar testes unitários |
| `optimize` | Otimizar performance |
| `translate` | Traduzir código entre linguagens |
| `commit` | Gerar mensagem de commit |
| `security` | Análise de segurança |
| `sql` | Auxiliar com queries SQL |
| `regex` | Construir/explicar expressões regulares |
| `architecture` | Discussão de arquitetura de software |

### Skills Personalizadas

Crie skills próprias em `~/.codis/skills/`:

```markdown
---
name: minha-skill
description: Descrição da skill
i18n_key: ia.skill_minha_skill
prompt_prefix: "Você é um especialista em..."
---

Instruções detalhadas para o modelo...
```

Skills do usuário aparecem no seletor após as skills padrão.

---

## Ferramentas de Contexto (Tool Use)

O painel de IA usa *function calling* para que o modelo possa agir sobre o workspace automaticamente, em vez de depender só de texto colado pelo usuário. As ferramentas são executadas via `ToolExecutor` (`src/ia/toolbox.py`) em thread separada, e são organizadas em níveis de risco - o Approval Gate define cinco (`SAFE`, `LOW`, `MEDIUM`, `HIGH` e `CRITICAL`) e as ferramentas atuais usam `SAFE`, `MEDIUM`, `HIGH` e `CRITICAL`; cada nível tem um tratamento diferente pelo [Approval Gate](#approval-gate-aprovação-de-ações), descrito na próxima seção.

### Leitura - SAFE (executam sem confirmação)

`read_file`, `read_active_file`, `get_cursor_context`, `list_files`, `search_in_files`, `find_definition`, `get_project_summary`, `read_diagnostics`, `read_symbols`, `read_git_diff`, `read_git_log`, `find_references`, `preview_rename`, `workspace_symbols`, `get_env_info`, `check_dependency`, `diff_files`, `read_file_tree`, `get_clipboard`.

### Escrita - MEDIUM (pedem aprovação)

`write_file`, `apply_edit`, `insert_after_line`, `format_code`, `patch_files`.

### Execução - HIGH (bloqueiam e aguardam aprovação explícita)

`run_command`, `run_script`, `run_tests`, `create_directory`, `delete_file`, `move_file`, `copy_file`, `init_project`, `install_dependency`, `git_commit`, `git_stage`, `git_branch`, `open_terminal_tab`.

### Crítico - CRITICAL (alerta vermelho, exige confirmação extra)

`delete_directory`, `git_reset`, `git_clean`.

No total, são **40 ferramentas** disponíveis ao agente, cobrindo leitura de contexto, edição, execução de comandos, integração Git, inicialização de projetos e instalação de dependências - um nível de capacidade comparável a assistentes como Cursor ou Zed Agent.

---

## Approval Gate (Aprovação de Ações)

O **Approval Gate** (`src/ia/approval_gate.py`) pausa a execução da IA e exige confirmação explícita do usuário antes de qualquer ação com efeitos colaterais. Ele é o que torna seguro deixar a IA operar com 40 ferramentas, incluindo exclusão de arquivos e comandos shell.

| Categoria de risco | Exemplos de ferramenta | Comportamento |
|---|---|---|
| **SAFE** | Leitura, busca, análise | Executa imediatamente, sem interromper o fluxo |
| **LOW** | Análises pesadas | Confirmação opcional, configurável pelo usuário |
| **MEDIUM** | Escrita de arquivo, formatação | Pede aprovação antes de aplicar |
| **HIGH** | Comandos shell, criar/deletar arquivos, operações Git | Bloqueia a execução e aguarda decisão do usuário |
| **CRITICAL** | Delete recursivo, `git reset --hard` | Alerta vermelho - exige confirmação extra, mesmo tendo aprovado HIGH anteriormente |

Quando uma ação cai em MEDIUM ou acima, o painel de IA exibe um card de aprovação descrevendo exatamente o que será feito (arquivo afetado, comando exato, diff da escrita) antes de qualquer execução real. O usuário pode aprovar, rejeitar, ou - no **Modo Agente** (tarefas multi-passo autônomas) - configurar um nível de confiança que aprova automaticamente certas categorias por sessão.

---

## Streaming

As respostas do modelo são exibidas em streaming - o texto aparece progressivamente enquanto o modelo gera a resposta. Um indicador de carregamento animado é exibido durante a geração.

Para cancelar uma resposta em andamento, clique no botão **✕ Cancelar**.

---

## Inserir Código no Editor

Quando a resposta do modelo contém blocos de código (` ```linguagem ... ``` `), o Codis extrai esses blocos automaticamente.

Dois botões aparecem após a resposta:

- **📋 Copiar** - copia o código para o clipboard.
- **⤵ Inserir no Editor** - abre um diálogo de diff (`DialDiff`) mostrando as diferenças entre o código atual e o código sugerido pela IA. Você pode aceitar, rejeitar ou editar antes de aplicar.

---

## Histórico de Chat

Cada sessão mantém um histórico de mensagens que é enviado ao modelo a cada turno (contexto acumulado). O histórico é mantido em memória durante a sessão e **não é persistido** entre reinicializações.

Para iniciar uma nova conversa do zero, clique em **🗑 Limpar Histórico**.

---

## Configuração Inline de Provedor

Clique no botão **⚙ Configurar** ao lado do seletor de provedor para abrir um painel inline de configuração. Os campos disponíveis variam por provedor:

- **Anthropic / OpenAI / Gemini:** chave de API + modelo
- **OpenAI Compatible:** chave de API (opcional) + URL base + modelo
- **Ollama:** URL base (padrão: `http://localhost:11434`) + modelo
- **LocalLib:** caminho do modelo GGUF + parâmetros de inferência

---

## Prompt de Sistema

O assistente de IA usa um prompt de sistema padrão otimizado para programação:

- Responde no mesmo idioma que o usuário escreve.
- Usa sempre blocos de código com a linguagem correta.
- Utiliza o contexto automático fornecido sem pedir informações repetidas.
- Usa as ferramentas proativamente quando o contexto é insuficiente.

O prompt de sistema padrão pode ser customizado via configuração avançada (campo `sistema` em `config.json`, lido por todos os 6 provedores em `src/ia/providers.py`).

---

## Disponibilidade

O painel de IA é carregado de forma **condicional**: se a importação de `src.ia.iaspace` falhar por qualquer motivo (dependências ausentes, erro de importação), o Codis inicia normalmente sem o painel, registrando apenas um aviso no log. Nenhuma funcionalidade do editor é afetada.
