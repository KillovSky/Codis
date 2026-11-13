# 🤝 Guia de Contribuição - Codis

Obrigado por considerar contribuir com o Codis! Este guia explica tudo que você precisa saber para ter sua contribuição aceita.

---

## Formas de Contribuir

| Forma | Link |
|---|---|
| 🐛 **Reportar bug** | [Issue (Bug)](https://github.com/KillovSky/Codis/issues/new?template=bug_report.yml) |
| 💡 **Sugerir feature** | [Feature Request](https://github.com/KillovSky/Codis/issues/new?template=feature_request.yml) |
| 🖼️ **Screenshots** | Capturas do editor em diferentes SOs, temas e painéis para a wiki |
| 🌍 **Traduções** | Edite em `src/locales/` ou via plugin |
| 🎨 **Criar tema** | Veja [Criando um Tema](#criando-um-tema) |
| 🧩 **Criar plugin** | Veja [wiki/plugins.md](wiki/plugins.md) |
| 📝 **Melhorar docs** | Edite os arquivos em `wiki/` |
| 🧪 **Testes** | Expanda `tests/` para `src/ui/` e integração |
| 💻 **Código** | Veja o [Fluxo de Contribuição](#fluxo-de-contribuição) |

---

## Fluxo de Contribuição

### 1. Fork e Clone

```bash
git clone https://github.com/SEU_USUARIO/Codis.git
cd Codis
git remote add upstream https://github.com/KillovSky/Codis.git
```

### 2. Crie uma Branch

```bash
git checkout -b fix/sql-thread-lock          # correções
git checkout -b feature/new-panel            # features
git checkout -b docs/update-plugins-wiki     # documentação
git checkout -b theme/nord-variant-light     # temas
git checkout -b test/ui-editor-coverage      # testes
git checkout -b i18n/update-fr-locale        # traduções
```

### 3. Configure o Ambiente

```bash
pip install -r requirements.txt
pip install pytest pytest-cov flake8 pylint
```

Linux - instale o Tkinter:

```bash
sudo apt install python3-tk   # Ubuntu/Debian
sudo pacman -S tk              # Arch
sudo dnf install python3-tkinter  # Fedora
```

### 4. Faça as Mudanças

- Siga as [Convenções de Código](#convenções-de-código)
- Commits pequenos e descritivos
- Uma mudança por commit (não misture feature com bugfix)

### 5. Verifique Antes de Submeter

```bash
# Sintaxe de todos os arquivos Python
find src/ -name "*.py" -print0 | xargs -0 python -m py_compile

# Testes headless (sem display gráfico)
pytest tests/ -v

# Com cobertura
pytest tests/ --cov=src --cov-report=term-missing

# Módulo específico
pytest tests/test_config.py -v

# Para no primeiro falho
pytest tests/ -x

# Estilo
flake8 src/ run.py --max-line-length=180

# Imports principais
python -c "from src.config import ConfigManager; print('OK')"
python -c "from src.app import CodisApp; print('OK')"
```

→ [Documentação completa dos testes](tests/README.md)

### 6. Sincronize com o Upstream

```bash
git fetch upstream
git rebase upstream/main
```

### 7. Submeta o Pull Request

```bash
git push origin sua-branch
```

Abra o PR no GitHub preenchendo o template - especialmente a seção **"Como Testar"**.

---

## Convenções de Código

### Estilo Geral

- **Python 3.12+** - use f-strings, `match`, `dataclass`, type hints
- **Type hints** em todas as assinaturas públicas
- **Docstrings** em funções e classes públicas (Google Style em PT-BR)

### Docstrings

```python
def minha_funcao(param1: str, param2: int = 0) -> bool:
    """Descrição concisa do que a função faz.

    Args:
        param1: Descrição do parâmetro.
        param2: Descrição do parâmetro opcional.

    Returns:
        bool: O que o valor de retorno significa.

    Raises:
        ValueError: Quando acontece X.
    """
```

### Thread Safety - CRÍTICO

O Tkinter **não é thread-safe total**. Toda manipulação de widget deve ocorrer na main thread:

```python
# ❌ ERRADO - pode crashar ou corromper o estado da UI
def minha_thread():
    self._label.config(text="novo texto")

# ✅ CORRETO - agenda na main thread
def minha_thread():
    self.after(0, lambda: self._label.config(text="novo texto"))

# ✅ CORRETO - com dados de retorno
def minha_thread():
    result = calcular_algo()
    self.after(0, lambda r=result: self._apply_result(r))
```

### Internacionalização (i18n)

Toda string visível ao usuário deve usar `_t()`:

```python
from src.i18n import _t

# ❌ ERRADO
tk.Label(self, text="Salvar arquivo")

# ✅ CORRETO
tk.Label(self, text=_t("ui.save_file", "Salvar arquivo"))
```

**Fluxo para novas strings:**

1. Adicione a chave **apenas em `src/locales/pt-BR.json`** com o valor em português.
2. Execute `python sync_locales.py` - propaga para todos os outros idiomas marcando com `[TODO]` para tradução futura.
3. Nunca edite os outros arquivos de locale manualmente para novas chaves - sempre via sync.

```bash
# Após adicionar chaves em pt-BR.json:
python sync_locales.py

# Automaticamente traduzir em todos os outros idiomas
# Apesar de permitido, evite quando puder; Sempre revise!
python translator.py
```

---

## Criando um Tema

Temas ficam em `src/theme/base.py`. Para criar um novo, adicione dentro de `_define_themes()`:

```python
_add(Theme(
    name="meu_tema",
    dark=True,
    bg="#1a1a2e",
    fg="#e0e0ff",
    editor_bg="#12122a",
    cursor="#6c63ff",
    accent="#6c63ff",
    # ... todos os campos obrigatórios
))
```

Adicione `"meu_tema"` na lista `THEME_ORDER` na posição desejada. Para temas distribuídos como plugins, veja [wiki/plugins.md](wiki/plugins.md#temas-via-plugins).

---

## Criando uma Tradução

Os arquivos de locale ficam em `src/locales/<código>.json`. O Codis usa 4.633 chaves de i18n (pt-BR e en são completos; `sync_locales.py` propaga as novas para os demais):

```bash
# Copiar o template do inglês para um novo idioma
cp src/locales/en.json src/locales/pl.json
# Abra o arquivo e traduza os valores - nunca as chaves!
```

Para adicionar um idioma via plugin (sem PR), veja [wiki/plugins.md](wiki/plugins.md#plugins-de-locale).

---

## Adicionando um Novo Painel

Os painéis do Codis seguem um padrão de integração definido:

1. Crie o módulo em `src/ui/meu_painel.py` com uma classe que estenda `tk.Frame`.
2. Implemente `apply_theme(tmgr)` para recoloração dinâmica de tema.
3. Registre na Activity Bar em `src/_app_build.py` (`_actbar.add_plugin_item(...)`).
4. Adicione ao `panel_map` em `src/_app_editor.py` (`_on_activity`).
5. Se for painel inferior: adicione a aba em `_build_bottom_panel` e ao `panel_map` em `src/_app_lsp.py` (`_show_panel`).
6. Adicione ao bloco `apply_theme` em `src/app.py`.
7. Adicione as chaves i18n em `pt-BR.json` e execute `sync_locales.py`.

---

## Modos Standalone - como testar

Úteis para isolar bugs sem iniciar o editor completo. Há **29 modos** disponíveis:

```bash
# Editor e visualizadores
python run.py scratch       # Bloco de notas multi-aba
python run.py paint         # Editor de imagens
python run.py diff A.py B.py # Diff Viewer
python run.py render doc.md # Preview HTML/Markdown/SVG
python run.py flowchart src/app.py # Fluxograma AST

# Banco de dados e dados
python run.py sql           # SQL Studio
python run.py formatter     # Formatador JSON/YAML/TOML/XML

# Ferramentas de dev
python run.py regex         # Regex Playground
python run.py rest          # REST Client
python run.py git .         # Painel Git
python run.py terminal .    # Terminal integrado
python run.py stats src/    # Estatísticas de código

# Sistema e ambiente
python run.py pip           # Gerenciador de pacotes Pip
python run.py processes     # Gerenciador de processos
python run.py env           # Variáveis de ambiente
python run.py ssh           # Conexão SSH

# Produtividade
python run.py player        # Zen Media Player
python run.py pomodoro      # Timer Pomodoro
python run.py todo          # Lista de tarefas
python run.py tasks         # Kanban de tarefas
python run.py calendar      # Calendário
python run.py clock         # Relógio / Alarmes
python run.py calculator    # Calculadora científica

# Utilitários
python run.py snippets      # Gerenciador de snippets
python run.py color-picker  # Seletor de cores
python run.py archive arq.zip # Visualizador de arquivos compactados
python run.py output        # Painel de output
python run.py accessibility # Painel de acessibilidade
python run.py plugins       # Gerenciador de plugins

# Também via python -m src
python -m src paint
python -m src sql
```

---

## Documentação

Cada informação fica em um único lugar, para os textos não divergirem entre si.

| Onde | O que contém | Observação |
|---|---|---|
| `README.md` | Vitrine do projeto e início rápido | Só resumo; detalhes ficam na wiki |
| `wiki/*.md` | Guias de usuário e de desenvolvedor | Fonte dos guias. `wiki/Home.md` é o índice (grupos e ordem) e `wiki/_Sidebar.md` é o menu da GitHub Wiki |
| `CHANGELOG.md`, `ROADMAP.md`, `CONTRIBUTING.md`, `SECURITY.md`, `AUTHORS.md` | Documentos do projeto | Ficam na raiz (convenção do GitHub) |
| `docs/` | Site (GitHub Pages) | `index.html` é escrito à mão; as demais páginas são geradas, não edite direto |
| `tools/docs_facts.py` | Extrai números do código e confere os docs | `python tools/docs_facts.py --check` |
| `tools/build_site.py` | Gera `docs/*.html` a partir dos `.md` | `make docs` (precisa de `pip install -r tools/requirements-docs.txt`) |

Ao adicionar uma página na wiki: crie `wiki/<nome-em-minusculas>.md`, liste-a em `wiki/Home.md` e em `wiki/_Sidebar.md`, rode `make docs` e faça commit dos arquivos gerados. O job `check` do `docs.yml` falha no PR se `docs/` estiver desatualizado ou se um número dos docs não bater com o código.

---

## Áreas que Precisam de Contribuição

| Área | O que precisamos |
|---|---|
| 🖼️ **Screenshots** | Capturas do editor em diferentes SOs e temas para a wiki |
| 🌍 **Traduções** | Todos os idiomas podem ter strings com `[TODO]` |
| 🎨 **Temas** | Gruvbox Hard, Tokyo Storm, Catppuccin Latte |
| 🧪 **Testes** | Expandir cobertura para `src/ui/` e testes de integração |
| 📝 **Docs** | Exemplos práticos na wiki, especialmente para plugins e IA |
| 🐛 **Bugs** | [Issues com label `bug`](https://github.com/KillovSky/Codis/issues?q=label%3Abug) |
| ✨ **Features** | [Issues com label `help wanted`](https://github.com/KillovSky/Codis/issues?q=label%3A%22help+wanted%22) |

---

## Perguntas?

- [💬 Discussões](https://github.com/KillovSky/Codis/discussions) para dúvidas gerais
- Leia a [Governança](.github/GOVERNANCE.md) para entender o processo de decisão
