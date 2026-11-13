# ❓ FAQ - Perguntas Frequentes

> Para diagnóstico técnico mais profundo (comandos de log, causas detalhadas, coleta de informações para bug report), veja [🛠️ Solução de Problemas](troubleshooting.md).

---

## Instalação e Inicialização

### O Codis não abre. O que fazer?

1. Verifique o Python 3.12+: `python --version`
2. Verifique o Tkinter: `python -c "import tkinter"` (sem erro = OK)
3. Rode pelo terminal para ver erros: `python run.py`
4. Se houver erro de configuração corrompida, delete `~/.codis/config.json` e reinicie

> O Codis **não** requer nenhum `pip install` para abrir. Se ainda falhar após os passos acima, veja o diagnóstico completo em [troubleshooting.md#o-codis-não-abre--tela-preta](troubleshooting.md#o-codis-não-abre--tela-preta) ou abra uma issue no GitHub com a mensagem de erro completa.

### O wizard de primeiro uso não apareceu.

O wizard só aparece quando `first_run_done` não está em `config.json`. Para forçá-lo: delete `~/.codis/config.json` e reinicie.

### A janela abre mas está em branco ou travada.

Pode ser sessão corrompida. Delete `~/.codis/session.json` e reinicie.

---

## Editor

### O syntax highlighting não funciona para minha linguagem.

1. Verifique se a extensão está mapeada em `EXT_LANG` (`src/constants.py`)
2. Mude a linguagem manualmente clicando no nome na barra de status
3. Se a linguagem não existir no seletor, ela não tem parser implementado - contribuições são bem-vindas!

### O autocomplete não aparece.

- Verifique `autocomplete: true` em `config.json`
- Aguarde o `autocomplete_delay` após digitar (padrão: 200 ms)
- Requer pelo menos `autocomplete_min_chars` caractere (padrão: 1)

### O Ctrl+C / Ctrl+V não funciona em certos contextos.

O Codis tem um handler global de clipboard contextual:
- Foco em `Entry` → opera sobre o campo de texto
- Foco no editor `Text` → delega ao handler interno
- Outros casos → opera sobre o editor ativo

Se não funcionar, verifique qual widget tem foco. `Ctrl+A` também é contextual.

---

## Interface

### Cliquei num item de menu e parece que nada aconteceu.

Se a ação correspondente falhar internamente, o Codis mostra um diálogo de erro e registra o problema no log, em vez de simplesmente não fazer nada. Se você vir esse diálogo, copie a mensagem e [reporte um bug](#como-reportar-um-bug) - ele ajuda bastante a identificar a causa. Se nenhum diálogo aparecer e o item realmente não tiver efeito algum, isso também é um bug válido para reportar.

---

## Temas

### Troquei o tema mas partes ainda têm a cor antiga.

Use o **Modo Completo** de troca de tema (não o Modo Rápido) para reconstruir toda a interface com o novo tema.

### Como criar um tema?

Veja [Temas → Criando um Tema Customizado](temas.md#criando-um-tema-customizado).

---

## Linting

### O lint não funciona para Python.

1. `py_compile` (sempre disponível) funciona para erros de sintaxe
2. Para pylint/mypy/flake8: `pip install pylint mypy flake8`
3. Verifique os caminhos em `config.json`: `pylint_path`, `mypy_path`, `flake8_path`
4. Confira o Output Panel → canal `Python` para erros

### O ESLint não encontra a configuração do projeto.

O `LintManager` executa o ESLint no diretório raiz do workspace. O arquivo de configuração (`.eslintrc`, `eslint.config.js`, etc.) deve estar na raiz do workspace aberto.

### O lint está lento ou travando o editor.

O lint roda em thread separada e não deve travar a UI. Se perceber lentidão:
- Aumente `lint_delay` em `config.json` (ex: `1500` ms)
- Desative linters pesados: `"pylint_path": ""` ou `"lint_python": false`
- Para arquivos grandes: `"lint_enabled": false`

---

## Terminal

### O terminal não abre / shell não encontrado.

1. Verifique se o shell (`default_shell`) está no `PATH`
2. Se em branco, o Codis detecta o primeiro shell disponível automaticamente
3. No Windows, pelo menos `cmd.exe` deve estar disponível
4. Confira o Output Panel para mensagens de erro

### Como mudar o shell padrão?

`Ctrl+,` → campo `default_shell` → nome do shell (`"bash"`, `"zsh"`, `"PowerShell"`)  
Ou clique no nome do shell na barra de status do terminal.

---

## Git

### O painel Git não detecta meu repositório.

1. Verifique se o Git está instalado: `git --version`
2. Abra uma pasta que seja repositório Git (contém `.git/`)
3. Verifique se `git_enabled: true` em `config.json`

### Push/Pull falha com erro de autenticação.

O painel Git exibe um diálogo de credenciais ao detectar erros. Insira seu usuário e token de acesso pessoal (PAT). Para SSH, certifique-se de que a chave SSH está configurada no sistema.

---

## Debug

### Cliquei em Run no Painel de Debug mas o programa roda até o fim sem parar.

Isso acontece quando **nenhum breakpoint foi marcado** antes de clicar em Run - sem breakpoints, o `debugpy` libera a execução e o script roda normalmente do início ao fim. Não é um bug: clique na margem esquerda do editor (gutter), ao lado do número da linha, para marcar um breakpoint **antes** de iniciar a depuração. Veja o guia completo em [Debug](debug.md).

### Erro ao iniciar a depuração / "porta já em uso" / "Address already in use".

A porta configurada para o DAP (`debug_dap_port`, padrão `5678`) já está sendo usada por outro processo. As causas mais comuns:

- **Outra sessão de debug do Codis ainda está rodando** - clique em **⏹ Stop** no painel antes de iniciar uma nova.
- **VS Code aberto ao mesmo tempo**, depurando Python no mesmo projeto - veja a próxima pergunta.
- Algum outro processo manual (`python -m debugpy ...`) deixado rodando em segundo plano.

**Solução rápida:** mude `debug_dap_port` para outro valor (ex: `5690`) em **Configurações → Avançado**, ou `Ctrl+,` → Avançado.

### Uso o Codis e o VS Code juntos no mesmo projeto e a depuração de um deles para de funcionar.

Isso é esperado, não um defeito de nenhum dos dois editores: **o Codis e a extensão Python Debugger do VS Code usam, por padrão, exatamente a mesma porta (`5678`)** para conversar com o `debugpy`, porque os dois seguem a mesma convenção da ferramenta. Com ambos abertos e tentando depurar Python ao mesmo tempo, um deles falha ao abrir a porta, ou acaba conectando à sessão errada.

A solução é simples - não é preciso fechar nenhum dos dois editores:

1. No Codis, vá em **Configurações → Avançado** (`Ctrl+,`);
2. Mude `debug_dap_port` para um valor diferente do padrão (ex: `5690`, `15678`, qualquer porta livre);
3. Pronto. Os dois editores podem depurar Python ao mesmo tempo, cada um na sua porta, sem disputar o mesmo recurso.

Isso só importa se você for depurar **nos dois ao mesmo tempo**; usando um por vez nunca há conflito.

### `debugpy não encontrado` no console do painel de Debug.

Instale com `pip install debugpy`, no mesmo Python/ambiente virtual que o Codis usa para executar seus scripts (confira o interpretador na barra de status). Sem o `debugpy`, o painel ainda executa o arquivo, mas sem breakpoints/stepping reais.

### Os botões de Step Into/Over/Out/Continue não fazem nada.

Eles só têm efeito quando a execução está **parada num breakpoint** (status `⏸ Stopped`). Se não houver uma sessão parada (ela ainda não rodou até um breakpoint, já terminou, ou nunca foi iniciada), o painel avisa no console em vez de fingir que algo aconteceu.

---

## LSP

### O LSP não funciona para minha linguagem.

1. Instale o servidor LSP (ex: `pip install python-lsp-server` para Python)
2. O servidor deve estar no `PATH`
3. Verifique o Output Panel → canal `LSP` para erros
4. Confirme `lsp_enabled: true` em `config.json`

### Como verificar se o LSP está ativo?

Abra um arquivo Python e pressione `F12` sobre uma função (Ir para Definição). Se funcionar, o LSP está ativo. Mensagens também aparecem no canal `LSP` do Output Panel.

---

## K-UI

### O K-UI ativou mas a interface parece bugada.

1. Desative com `Ctrl+Alt+N`
2. Se não voltar ao normal, reinicie o Codis
3. Se persistir, delete `~/.codis/config.json` para resetar

### O K-UI está pesado / lento.

Mude o perfil de performance para `low` ou `minimal`:
- **Ferramentas → K-UI: Performance**
- Spotlight (`Ctrl+Space`) → "Perf"

| Perfil | CPU |
|---|---|
| `quantum` | Muito alto |
| `ultra` | Alto |
| `high` | Médio-alto |
| `balanced` | Médio |
| `windows` | Médio-baixo |
| `low` | Baixo |
| `minimal` | Mínimo (alias de `low` - ainda tem partículas/meteoros, não é "sem animações") |

> `ultra_plus` também existe como alias de `quantum`, e `power_saver` como alias de `windows` (mesmos perfis, nomes alternativos).

### As nebulosas não aparecem no K-UI.

O K-UI usa PIL/Pillow para renderizar as nebulosas em background. Instale com `pip install Pillow`. Sem PIL, o K-UI usa gradiente puro como fallback.

### Criei uma sequência mas o easter egg não ativou.

Os easter eggs são ativados via comando no terminal integrado: `/codis <código>`. Exemplo: `/codis nova`. Ver a lista completa em [K-UI → Easter Eggs](kui.md#easter-eggs).

---

## Plugins

### Meu plugin não está sendo carregado.

1. Verifique se está em `~/.codis/plugins/`
2. Output Panel → canal `Extensions` para erros
3. Verifique se a análise estática de segurança não bloqueou o plugin
4. Certifique-se de que o plugin define `MANIFEST` e `activate(api)`

### A análise de segurança bloqueou meu plugin por usar `os`.

O analisador de imports (`_sandbox_check_imports` em `src/plugins/api.py`) classifica `os`, `pathlib`, `shutil`, `requests`, `threading`, `pickle` e outros como **avisos**; já `subprocess`, `socket`, `ssl`, `ctypes`, `multiprocessing`, `winreg` e similares são classificados como **bloqueados** - mais arriscados.

O que acontece depois depende de como o plugin chegou ao Codis:

- **Instalando pela interface** (`Plugins → Instalar Plugin`): tanto avisos quanto bloqueios mostram um diálogo de confirmação. Aceitar adiciona o plugin a `~/.codis/trusted_plugins.json`, e ele passa a carregar normalmente nas próximas inicializações.
- **Arquivo já presente em `~/.codis/plugins/`** sem nunca ter passado pela instalação (ex: copiado manualmente): se contiver imports **bloqueados**, é recusado automaticamente, sem diálogo - só passa a carregar depois de ser instalado uma vez pela interface.

---

## Testes

### Como rodar os testes do Codis?

```bash
pip install pytest pytest-cov
pytest tests/ -v
```

A suíte roda headless (sem display gráfico). Ver [tests/README.md](../tests/README.md).

### Os testes falham com erros de importação do Tkinter.

O `conftest.py` instala stubs de Tkinter automaticamente antes de qualquer importação. Certifique-se de rodar `pytest` a partir da raiz do projeto (`cd Codis && pytest tests/`).

---

## Performance Geral

### O Codis está lento ao abrir arquivos grandes.

Para arquivos acima de 3 MB, o modo simplificado é ativado automaticamente (syntax highlighting e autocomplete desativados). O limite é configurável via `large_file_mb` em `config.json`.

### O Codis usa muita memória.

O conteúdo completo de todos os arquivos abertos fica em memória. Feche abas não utilizadas. O K-UI com perfis `ultra` ou `quantum` também consome mais CPU e memória.

---

## Outros

### Como reportar um bug?

Abra uma [issue no GitHub](https://github.com/KillovSky/Codis/issues) incluindo:
- Versão do Codis (`Ajuda → About Codis`)
- Sistema operacional e versão do Python
- Passos para reproduzir
- Mensagens de erro do terminal ou Output Panel

### Como contribuir?

Veja [CONTRIBUTING.md](../CONTRIBUTING.md).
