# 📚 KDocs - Documentação Nativa Universal

O **KDocs** (`src/editor/kdocs.py`) é o sistema de documentação integrado ao Codis. Ele analisa comentários e docstrings nativamente em 27 linguagens (mais um perfil genérico de fallback) e exibe um balloon de documentação rico ao passar o mouse sobre qualquer símbolo documentado - sem depender de LSP.

---

## O Que é o KDocs?

O KDocs é um motor de análise estática que funciona **diretamente sobre o texto do arquivo**, detectando automaticamente o estilo de documentação pela extensão do arquivo:

- **Detecção automática de linguagem** - `.py` → Python, `.rs` → Rust, `.go` → Go, etc.
- **Badge de linguagem colorido** no balloon - `PYTHON` azul `#3572a5`, `RUST` laranja `#dea584`, `GO` ciano `#00add8`
- **Múltiplos estilos por linguagem** - Python suporta Google Style, NumPy Style e Sphinx/reST simultaneamente
- **Anotações `@kdocs:`** em qualquer linguagem (comentários da própria linguagem)
- **Cache LRU de 64 entradas** para performance em arquivos grandes
- **Integração com o Painel Docs** - lista filtrável de todos os símbolos documentados do arquivo ativo

---

## Detecção Automática de Linguagem

O KDocs detecta a linguagem pela extensão do arquivo e seleciona o parser correto. Ao todo são **27 perfis de linguagem** (`LanguageProfile`) mais um perfil genérico de fallback:

| Extensão | Linguagem | Estilo detectado | Badge |
|----------|-----------|-----------------|-------|
| `.py`, `.pyw`, `.pyi` | Python | Google · NumPy · Sphinx/reST (auto) | `PYTHON` azul `#3572a5` |
| `.rs` | Rust | `///` doc comments com Markdown | `RUST` laranja `#dea584` |
| `.go` | Go | godoc - comentários antes de declarações | `GO` ciano `#00add8` |
| `.java` | Java | JSDoc / Javadoc | `JAVA` marrom `#b07219` |
| `.kt`, `.kts` | Kotlin | JSDoc / KDoc | `KOTLIN` roxo `#a97bff` |
| `.swift` | Swift | JSDoc | `SWIFT` laranja `#f05138` |
| `.cs` | C# | Doxygen-style (sintaxe XML: `<summary>`, `<param>`) | `CSHARP` verde `#178600` |
| `.cpp`, `.cc`, `.cxx`, `.hpp`, `.h` | C++ | Doxygen (`@param`, `\brief`, `@returns`) | `CPP` rosa `#f34b7d` |
| `.c` | C | Doxygen | `C` cinza `#555555` |
| `.rb`, `.rake` | Ruby | RDoc/YARD (`@param`, `@return`, `@raise`) | `RUBY` vermelho `#cc342d` |
| `.lua` | Lua | LDoc (`---`, `@tparam`, `@treturn`) | `LUA` azul marinho `#000080` |
| `.sql` | SQL | Comentários `--` estilo dash | `SQL` (cor padrão `jsdoc`) |
| `.sh`, `.bash`, `.zsh` | Shell | Comentários `#` estilo hash | `SHELL` (cor padrão `jsdoc`) |
| `.yml`, `.yaml` | YAML | Comentários `#` estilo hash | `YAML` (cor padrão `jsdoc`) |
| `.html`, `.htm` | HTML | Comentários `<!-- -->` | `HTML` (cor padrão `jsdoc`) |
| `.css` | CSS | JSDoc-style em `/* */` | `CSS` (cor padrão `jsdoc`) |
| `.scss`, `.sass` | SCSS | JSDoc-style em `/* */` | `SCSS` (cor padrão `jsdoc`) |
| `.js`, `.mjs`, `.cjs` | JavaScript | JSDoc / TSDoc | `JAVASCRIPT` amarelo `#f7df1e` |
| `.ts`, `.tsx`, `.d.ts` | TypeScript | JSDoc / TSDoc | `TYPESCRIPT` azul `#3178c6` |
| `.jsx` | JSX | JSDoc | `JSX` (cor padrão `jsdoc`) |
| `.php` | PHP | JSDoc | `PHP` lilás `#4f5d95` |
| `.dart` | Dart | JSDoc | `DART` ciano `#00b4ab` |
| `.ex`, `.exs` | Elixir | Python-style docstrings (`"""`) | `ELIXIR` roxo `#6e4a7e` |
| `.r`, `.R` | R | Roxygen2 (`#'`) | `R` azul `#198ce7` |
| `.jl` | Julia | Python-style docstrings (`"""`) | `JULIA` roxo `#a270ba` |
| `.hs`, `.lhs` | Haskell | Comentários `--` estilo dash, bloco `{- ... -}` | `HASKELL` roxo `#5e5086` |
| `.toml` | TOML | Comentários `#` estilo hash | `TOML` (cor padrão `jsdoc`) |
| outras extensões | Genérico (fallback) | JSDoc | `Doc` lilás `#cba6f7` (cor padrão) |

> Linguagens sem cor de badge própria no código (SQL, Shell, YAML, HTML, CSS, SCSS, JSX, TOML) usam a cor padrão lilás `#cba6f7` reservada para `jsdoc`/fallback.
---

## Estilos de Documentação Suportados

### JavaScript / TypeScript - JSDoc / TSDoc

```javascript
/**
 * Calcula o fatorial de um número.
 * @param {number} n - O número inteiro não negativo.
 * @returns {number} O fatorial de n.
 * @throws {Error} Se n for negativo.
 * @example
 * factorial(5); // 120
 */
function factorial(n) { ... }
```

Tags JSDoc reconhecidas: `@param`, `@returns`, `@throws`, `@example`, `@deprecated`, `@since`, `@author`, `@see`, `@type`, `@typedef`, `@callback`, `@template`, `@override`, `@abstract`.

### Python - Google Style

```python
def divide(a: float, b: float) -> float:
    """Divide dois números.

    Args:
        a: Numerador.
        b: Denominador. Não pode ser zero.

    Returns:
        O resultado da divisão.

    Raises:
        ValueError: Se b for zero.

    Example:
        >>> divide(10, 2)
        5.0
    """
```

### Python - NumPy Style

```python
def soma(a, b):
    """
    Soma dois valores.

    Parameters
    ----------
    a : int
        Primeiro operando.
    b : int
        Segundo operando.

    Returns
    -------
    int
        Resultado da soma.
    """
```

### Python - Sphinx / reST

```python
def greet(name: str) -> str:
    """Gera uma saudação.

    :param name: Nome da pessoa.
    :type name: str
    :returns: String de saudação.
    :rtype: str
    :raises TypeError: Se name não for string.
    """
```

### Rust - Doc Comments (`///`)

```rust
/// Calcula a raiz quadrada de um número.
///
/// # Arguments
///
/// * `x` - O número a calcular. Deve ser não negativo.
///
/// # Returns
///
/// A raiz quadrada de `x`.
///
/// # Panics
///
/// Entra em panic se `x` for negativo.
pub fn sqrt(x: f64) -> f64 { ... }
```

### Go - Godoc

```go
// Fetch realiza uma requisição HTTP GET para a URL fornecida.
// Retorna o corpo da resposta como string ou um erro.
// Exemplo:
//
//	body, err := Fetch("https://example.com")
func Fetch(url string) (string, error) { ... }
```

### Ruby - YARD

```ruby
# Calcula o IMC de uma pessoa.
#
# @param peso [Float] Peso em quilogramas.
# @param altura [Float] Altura em metros.
# @return [Float] O índice de massa corporal.
# @raise [ArgumentError] Se altura for zero.
def imc(peso, altura)
```

### Lua - LDoc

```lua
--- Faz login no servidor.
-- @param usuario string O nome de usuário.
-- @param senha string A senha do usuário.
-- @return boolean true se o login foi bem-sucedido.
-- @usage login("admin", "senha123")
function login(usuario, senha)
```

### Doxygen (C/C++/PHP/Java)

```cpp
/**
 * @brief Inicializa o módulo de rede.
 *
 * @param host Endereço do servidor.
 * @param port Porta de conexão (padrão: 8080).
 * @return int 0 em sucesso, código de erro negativo em falha.
 * @warning Esta função não é thread-safe.
 */
int net_init(const char* host, int port);
```

### C# - XML Documentation

```csharp
/// <summary>
/// Converte uma string para Base64.
/// </summary>
/// <param name="input">A string a ser codificada.</param>
/// <returns>A representação Base64 da string.</returns>
/// <exception cref="ArgumentNullException">Se input for null.</exception>
public static string ToBase64(string input) { ... }
```

---

## Anotações KDocs Especiais

As anotações `@kdocs:<tipo>` funcionam em **qualquer linguagem**, independente do estilo de comentário (`//`, `#`, `--`, `<!-- -->`). O `<tipo>` não é uma lista fechada pré-definida - o parser aceita qualquer palavra após `@kdocs:`, mas os tipos abaixo têm tratamento e renderização especiais no balloon:

```python
# @kdocs:since 1.0.0
# @kdocs:author KillovSky
# @kdocs:stability stable
# @kdocs:warn [fs] Não use de forma síncrona em produção! [@alice] [!high] [#nosync]
# [!due:2025-12-31]
# [!ticket:PROJ-123]
def minha_funcao():
    ...
```

| Anotação | Descrição |
|----------|-----------|
| `@kdocs:since <versão>` | Versão em que o símbolo foi introduzido |
| `@kdocs:author <nome>` | Autor do símbolo |
| `@kdocs:stability <status>` | Texto livre - convenção sugerida: `stable`, `experimental`, `deprecated` |
| `@kdocs:see <referência>` / `@kdocs:link <referência>` | Referência cruzada para outro símbolo ou arquivo |
| `@kdocs:type <nome>` | Anota o tipo de uma variável/parâmetro explicitamente |
| `@kdocs:warn <texto>` | Aviso destacado no balloon (ex: alertas de uso) |
| `@kdocs:note <texto>` | Nota informativa adicional |
| `@kdocs:deprecated <texto>` | Marca o símbolo como obsoleto, com o motivo |

### Modificadores entre colchetes

Podem ser combinados livremente dentro do texto de qualquer anotação `@kdocs:`:

| Modificador | Descrição |
|---|---|
| `[!due:YYYY-MM-DD]` | Data limite vinculada ao símbolo |
| `[!ticket:ID]` | Ticket de issue tracker vinculado |
| `[!low]` `[!normal]` `[!high]` `[!critical]` | Nível de prioridade (case-insensitive) |
| `[@nome.de-usuario]` | Pessoa responsável/atribuída |
| `[#tag1,tag2]` | Tags livres separadas por vírgula |

URLs em qualquer anotação são convertidas em links clicáveis no balloon.

---

## Balloon de Documentação

Ao passar o mouse sobre um símbolo documentado, um balloon é exibido com:

- **Badge de linguagem** - identifica visualmente a linguagem do arquivo
- **Nome do símbolo** e tipo (função, classe, variável)
- **Descrição** extraída do comentário
- **Parâmetros** com tipos e descrições
- **Retorno** com tipo e descrição
- **Exceções / Erros** documentados
- **Exemplos de uso** com syntax highlighting
- **Metadados KDocs** (`@since`, `@author`, `[!ticket:]`, `[!due:]`)
- **Locais de uso** do símbolo no arquivo (até 200 ocorrências escaneadas)

O balloon usa debounce para evitar disparos acidentais e fecha automaticamente ao mover o cursor.

---

## Cache LRU

O KDocs mantém um cache LRU (`_DocCache`) com capacidade de **64 entradas**. Arquivos grandes com muitas definições são parseados uma única vez e os resultados ficam em cache até que o conteúdo do arquivo mude.

---

## Configuração

| Opção | Padrão | Descrição |
|-------|--------|-----------|
| `hover_docs` | `true` | Exibe o balloon ao passar o mouse |

---

## Painel KDocs na Sidebar

O painel **📚 KDocs** (Activity Bar → 📚) exibe uma navegação estruturada de todas as definições documentadas do arquivo ativo. Clique em qualquer entrada para ir à definição.

---

## Compatibilidade

O KDocs é compatível com JSDoc. Nenhuma migração de comentários é necessária - o motor detecta automaticamente o estilo usado.

O KDocs opera de forma **complementar ao LSP**: quando um servidor de linguagem está ativo e fornece hover documentation, o LSP tem prioridade. O KDocs é o fallback quando o LSP não está disponível ou não possui documentação para o símbolo.
