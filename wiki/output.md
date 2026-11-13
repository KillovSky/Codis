# 📤 Painel de Saída

O **Output Panel** (`src/ui/output_panel.py`) é o painel de log centralizado do Codis. Ele exibe mensagens de todos os subsistemas em canais separados, com filtragem por nível.

---

## Abrindo o Output Panel

- Painel inferior → aba **Output**
- Menu **Exibir → Output**

---

## Canais

O Output Panel usa um sistema de canais para separar mensagens por origem:

| Canal | O que registra |
|-------|---------------|
| `Codis` | Mensagens internas do editor (ações do usuário, eventos importantes) |
| `Python` | Resultados do lint Python (pyflakes, pylint, mypy, flake8) |
| `ESLint` | Resultados do lint JavaScript/TypeScript |
| `LSP` | Mensagens do servidor LSP ativo |
| `Extensions` | Logs do sistema de plugins (carregamento, erros) |
| `Git Clone` | Progresso e resultado de operações de clone |
| `<nome do plugin>` | Canal exclusivo criado por cada plugin via `api.create_output_channel()` |

Plugins podem criar canais adicionais arbitrários.

---

## Níveis de Mensagem

Cada mensagem tem um nível que determina sua cor de exibição:

| Nível | Cor | Uso |
|-------|-----|-----|
| `info` | Texto padrão do tema | Informações gerais |
| `warn` | Amarelo | Avisos não críticos |
| `error` | Vermelho | Erros e falhas |
| `ok` | Verde | Operações concluídas com sucesso |

---

## Bridge com o Sistema de Logging Python

O Codis instala dois handlers no sistema de logging padrão do Python (`logging`) que encaminham mensagens para o Output Panel:

1. **Handler do logger `Codis`** (nível `WARNING` e acima) → canal `Codis`
2. **Handler do logger raiz** (nível `ERROR` e acima) → canal `Codis`

Isso significa que qualquer chamada a `logger.warning(...)` ou `logger.error(...)` em qualquer módulo do Codis aparece automaticamente no Output Panel.

Plugins que usam `api.get_logger()` obtêm um logger prefixado com o ID do plugin, e suas mensagens aparecem no canal do próprio plugin.

---

## Adicionando Mensagens ao Output

Via código Python (por exemplo, em um plugin):

```python
# Via OutputChannel (preferido para plugins)
canal = api.create_output_channel("Meu Plugin")
canal.append("Operação concluída.")
canal.appendLine("Outra mensagem.")

# Via logger padrão (aparece no canal "Codis")
import logging
logger = logging.getLogger("Codis.MeuModulo")
logger.warning("Algo inesperado aconteceu.")
logger.error("Falha crítica: %s", str(exc))
```

---

## Mensagens Automáticas

O Codis escreve automaticamente no Output Panel nas seguintes situações:

| Evento | Canal | Mensagem |
|--------|-------|---------|
| Arquivo aberto | `Codis` | `📂 Aberto: <caminho>` |
| Workspace aberto | `Codis` | `Workspace: <caminho>` |
| Lint completado (com diagnósticos) | `Python` / `ESLint` | Lista de erros e avisos |
| Diagnósticos LSP recebidos | `LSP` | Contagem de diagnósticos |
| Plugin carregado | `Extensions` | `Plugins carregados: N` |
| Erro de carregamento de plugin | `Extensions` | `Plugins com falha: N` |
| Mensagem de log do servidor LSP | `LSP` | Mensagem do servidor |

---

## Formato das Mensagens de Lint

Para cada arquivo analisado, o Output Panel exibe:

```
── nome_do_arquivo.py ──────────────────────────────────
  ✖ L42:8  Undefined variable 'foo'  [undefined-variable]
  ⚠ L55:1  Line too long (82 > 79)   [line-too-long]
  2 problema(s) - 1 erro(s), 1 aviso(s)
```

---

## Timestamp

Mensagens podem incluir timestamp (`timestamp=True`). O formato é `[HH:MM:SS]`. A maioria das mensagens automáticas inclui timestamp; mensagens de lint não incluem para economizar espaço.

---

## Filtragem

O Output Panel permite filtrar mensagens por canal (dropdown) e por nível mínimo. Mensagens de canais não selecionados são ocultadas da visualização mas não descartadas.
