# 🔍 Linting & LSP

O Codis possui um sistema de diagnósticos em dois níveis: **Lint Nativo** (executado internamente) e **LSP** (servidor de linguagem externo). Ambos convergem para o mesmo sistema de exibição.

---

## Como Funciona

1. Ao abrir ou modificar um arquivo, o `LintManager` agenda a execução do lint com um debounce de `lint_delay` ms (padrão: 750 ms).
2. Os linters são executados em **threads daemon** separadas - nunca bloqueiam a UI.
3. Os resultados são convertidos em objetos `Diag` e entregues ao `EditorPane` ativo, ao `ProblemsPanel` (painel inferior e sidebar) e à barra de status.
4. O minimapa exibe marcadores coloridos nas linhas com diagnósticos.

---

## Linters por Linguagem

### Python

Os linters Python são executados em sequência (os disponíveis no `PATH`):

| Ferramenta | O que verifica |
|------------|---------------|
| `py_compile` | Sintaxe (embutido no Python - sempre disponível) |
| `pyflakes` | Erros semânticos simples (variáveis indefinidas, imports não usados) |
| `flake8` | Estilo (PEP 8) + erros |
| `mypy` | Checagem de tipos estática |

Configuração no `config.json`:

```json
"lint_python": true,
"pylint_path": "pylint",
"mypy_path": "mypy",
"flake8_path": "flake8"
```

### JavaScript e TypeScript

| Ferramenta | O que verifica |
|------------|---------------|
| ESLint | Erros de sintaxe, estilo, boas práticas |
| Prettier | Verificação de formatação |

O Codis detecta automaticamente a configuração do ESLint no workspace (`.eslintrc`, `.eslintrc.json`, `eslint.config.js`, etc.). O lint JS também cobre `.jsx`, `.tsx`, `.mjs`, `.cjs`, e os arquivos de componentes **Vue** e **Svelte** (`.vue`, `.svelte`) são roteados para o mesmo pipeline ESLint.

```json
"lint_js": true,
"eslint_path": "eslint",
"prettier_path": "prettier"
```

### HTML / SVG / XML

Validação via **tidy-html5** (`tidy`), se disponível no `PATH`. Sem `tidy` instalado, o Codis cai automaticamente para um **validador heurístico interno** (sem dependências externas) que detecta tags não fechadas e tags de fechamento inesperadas, percorrendo o conteúdo linha a linha.

> A chave `lint_html` existe em `config.json` (padrão `true`), mas atualmente não é lida pelo dispatcher de lint - o lint de HTML roda sempre que `lint_enabled` global está ativo, independente do valor de `lint_html`.

### PHP

Validação de sintaxe via `php -l` (recurso nativo do próprio interpretador PHP, instalado junto com o PHP - sem ferramenta adicional). Detecta apenas erros de parse/fatais, não faz análise de estilo.

> Assim como `lint_html`, a chave `lint_php` (padrão `true`) não é lida pelo dispatcher de lint - roda sempre que `lint_enabled` global está ativo.

### CSS / SCSS / Less

O lint de CSS é feito via **stylelint** (executado como subprocesso, no mesmo padrão do ESLint). Diferente de pylint/mypy/flake8/eslint, o caminho do `stylelint` **não é configurável** via `config.json` - o Codis busca apenas por `shutil.which("stylelint")` no `PATH` do sistema e, como segunda tentativa, em `node_modules/.bin/stylelint` do workspace atual.

Sem o `stylelint` instalado e acessível em uma dessas duas formas, nenhum diagnóstico de CSS/SCSS é gerado (a função retorna lista vazia silenciosamente, sem erro visível).

---

## Painel de Problemas

O painel de Problemas (`Ctrl+M`) exibe todos os diagnósticos dos arquivos abertos.

Cada entrada mostra:
- **Ícone de severidade:** ✖ (erro) | ⚠ (aviso) | ℹ (info) | 💡 (hint)
- **Mensagem** do diagnóstico
- **Regra** aplicada (ex: `E501`, `no-unused-vars`)
- **Fonte** (ex: `pylint`, `eslint`, `LSP`)
- **Localização:** arquivo + linha + coluna

Clicar em um diagnóstico abre o arquivo e posiciona o cursor na linha correspondente.

O painel de Problemas existe em dois lugares:
1. **Painel inferior** (sempre visível quando aberto)
2. **Sidebar** - uma instância espelhada

---

## Navegação por Diagnósticos

| Atalho | Ação |
|--------|------|
| `F8` | Ir para o próximo diagnóstico no arquivo ativo |
| `Shift+F8` | Ir para o diagnóstico anterior |

A navegação é circular - ao chegar ao último diagnóstico, volta ao primeiro.

---

## Diagnósticos no Editor

Os diagnósticos são exibidos diretamente no editor com sublinhados coloridos:

| Severidade | Cor | Sublinhado |
|------------|-----|-----------|
| Erro (1) | vermelho | ondulado |
| Aviso (2) | amarelo | ondulado |
| Info (3) | azul | pontilhado |
| Hint (4) | cinza | pontilhado |

---

## Estrutura do Objeto `Diag`

```python
@dataclass
class Diag:
    line:     int                  # Linha do diagnóstico (base 1)
    col:      int                  # Coluna de início
    end_col:  int                  # Coluna de fim
    severity: int                  # 1=erro, 2=aviso, 3=info, 4=hint
    message:  str                  # Texto do diagnóstico
    rule:     str = ""             # Identificador da regra (ex: "E501")
    source:   str = ""             # Origem (ex: "pylint", "eslint", "LSP")
    fix:      dict | None = None   # Sugestão de correção automática (opcional)
    related:  list = field(default_factory=list)  # Locais relacionados (opcional)
```

---

## Correção Automática

`Executar → Corrigir Tudo` (ou via Paleta de Comandos → "Fix All") aplica todas as correções automáticas disponíveis no arquivo ativo. Apenas diagnósticos com objeto `fix` são corrigidos.

---

## LSP como Fonte de Diagnósticos

Servidores de linguagem (LSP) também enviam diagnósticos via `textDocument/publishDiagnostics`. O Codis converte esses diagnósticos no formato interno `Diag` e os exibe nos mesmos painéis.

Diagnósticos LSP e de lint nativo são exibidos juntos no painel de Problemas, diferenciados pelo campo `source`.

Ver [Recursos Avançados → LSP](recursos-avancados.md#lsp---language-server-protocol) para configuração dos servidores.

---

## Barra de Atividades - Badge de Problemas

O ícone na Activity Bar que controla o painel de Problemas exibe um badge numérico com a contagem total de erros + avisos no workspace atual.

---

## Saída do Lint no Output Panel

Os resultados do lint são também enviados ao **Painel de Saída** (Output) nos canais:
- `Python` - diagnósticos de linters Python
- `ESLint` - diagnósticos do ESLint
- `LSP` - mensagens do servidor de linguagem
- `Codis` - logs internos

Ver [Painel de Saída](output.md) para mais detalhes.

---

## Configurações de Lint

| Opção | Padrão | Descrição |
|-------|--------|-----------|
| `lint_enabled` | `true` | Ativa/desativa o lint globalmente - única chave que afeta CSS, HTML e PHP |
| `lint_delay` | `750` | Debounce após digitação (ms) |
| `lint_js` | `true` | Habilita lint JS/TS/JSX/TSX/Vue/Svelte (efetivamente checada) |
| `lint_python` | `true` | Habilita lint Python (efetivamente checada) |
| `lint_css` | - | Existe na config, mas não é lida pelo lint atual (veja nota acima) |
| `lint_html` | `true` | Existe na config, mas não é lida pelo lint atual (veja nota acima) |
| `lint_php` | `true` | Existe na config, mas não é lida pelo lint atual (veja nota acima) |

Todas as opções podem ser ajustadas via `Ctrl+,`.
