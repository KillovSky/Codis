# 🛠️ Formatador, Variáveis de Ambiente e Grafo de Dependências

Esta página cobre três utilitários de desenvolvimento que ficam na categoria **Ferramentas → Utilitários de Desenvolvimento**: o Formatador/Validador de dados estruturados, o Gerenciador de Variáveis de Ambiente e o Visualizador de Grafo de Dependências.

> Os demais utilitários dessa mesma categoria - Color Picker, Regex Playground, SQL Studio, REST Client, Diff Viewer, Code Stats e Flowchart - já estão documentados em [Recursos Avançados](recursos-avancados.md).

---

## { } Formatador de Dados (`src/ui/formatter_panel.py`)

Atalho: `Ctrl+Alt+F` ou **Ferramentas → Utilitários de Desenvolvimento → Formatador**.

### Formatos suportados

JSON, YAML, TOML e XML.

### Funcionalidades

| Ação | O que faz |
|---|---|
| **Formatar** | Reindenta e organiza o conteúdo no formato escolhido, com indentação consistente |
| **Minificar** | Remove espaços e quebras de linha desnecessárias, gerando a versão mais compacta |
| **Validar** | Verifica a sintaxe e exibe erros com número de linha e coluna exatos |
| **Converter** | Converte entre formatos: JSON ↔ YAML ↔ TOML |
| **Comparar** | Abre o conteúdo formatado em duas versões no [Diff Viewer](recursos-avancados.md#diff-engine) |

### Fluxo típico

1. Cole ou digite o conteúdo no painel de entrada
2. Escolha o formato de origem (detectado automaticamente quando possível)
3. Use **Validar** antes de formatar, se quiser garantir que a sintaxe está correta
4. **Formatar**/**Minificar** processa o conteúdo no painel de saída
5. **Copiar resultado** ou **Salvar arquivo** para persistir o resultado

A árvore de estrutura (painel lateral) permite explorar o documento de forma hierárquica, útil para JSON/YAML profundamente aninhados.

---

## ⚙ Gerenciador de Variáveis de Ambiente (`src/ui/env_manager.py`)

Atalho: **Ferramentas → Utilitários de Desenvolvimento → Variáveis de Env**.

A janela tem três abas:

| Aba | Conteúdo |
|---|---|
| **Env Atual** | Todas as variáveis de ambiente do processo Codis em execução |
| **Perfis .env** | Arquivos `.env` salvos, importáveis/exportáveis |
| **Snippets** | Trechos prontos para exportar variáveis em diferentes shells |

### Funcionalidades

- Visualizar todas as variáveis do processo atual (`os.environ`)
- Adicionar, editar ou remover variáveis para a sessão corrente (afeta apenas o processo do Codis, não o sistema)
- **Perfis `.env`**: criar, importar e exportar arquivos `.env` completos, e aplicá-los à sessão atual
- Pesquisa e filtro por nome de variável
- **Geração de snippets** prontos em `export VAR=valor` (bash), `setx VAR valor` (cmd) e `$env:VAR = "valor"` (PowerShell)
- **Diff entre o env atual e um perfil salvo** - útil para descobrir rapidamente o que mudou entre ambientes (ex.: comparar `.env.production` com o ambiente local)

### Parsing de `.env`

O parser interno (`_parse_env_file`) lê o formato `.env` simples (`CHAVE=valor` por linha, com suporte a comentários `#` e linhas vazias ignoradas).

---

## 🔗 Dependency Graph (`src/ui/dependency_graph.py`)

Atalho: **Ferramentas → Utilitários de Desenvolvimento → Grafo de Deps**.

Analisa os imports de arquivos Python no workspace e renderiza um grafo interativo de dependências, desenhado inteiramente em `Canvas` Tkinter - sem nenhuma biblioteca de grafos externa.

### Como funciona

1. Faz o parse de `import X` e `from X import Y` em todos os arquivos `.py` do workspace, usando `ast` (com fallback para regex se o parse falhar)
2. Classifica cada módulo encontrado em três categorias coloridas:

| Categoria | Cor | Critério |
|---|---|---|
| **stdlib** | Verde | Está em `sys.stdlib_module_names` |
| **local** | - | Faz parte do próprio workspace |
| **third-party** | - | Pacote externo instalado (não é stdlib nem local) |

3. Aplica um **layout força-dirigida** (force-directed) implementado nativamente para posicionar os nós de forma legível
4. Renderiza o grafo com zoom e pan via mouse

### Funcionalidades adicionais

- Clique em um nó para abrir o arquivo correspondente diretamente no editor
- Filtro por nome de módulo
- Checkbox para mostrar/ocultar módulos stdlib (reduz ruído visual em projetos grandes)
- **Ciclos de dependência são destacados em vermelho** - útil para identificar imports circulares antes que causem erro em runtime
- **Exportação como DOT** (formato Graphviz), permitindo renderizar o grafo externamente com `dot -Tpng grafo.dot -o grafo.png` ou importar em outras ferramentas de visualização
