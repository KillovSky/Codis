# 🛠️ Solução de Problemas

Guia de diagnóstico técnico (comandos, logs, causas) para os problemas mais comuns. Para respostas rápidas em formato pergunta-resposta, veja também a [FAQ](faq.md).

---

## Índice

- [O Codis não abre / tela preta](#o-codis-não-abre--tela-preta)
- [Erro: "No module named tkinter"](#erro-no-module-named-tkinter)
- [Fontes distorcidas ou ilegíveis](#fontes-distorcidas-ou-ilegíveis)
- [Tela em branco no Linux (Wayland)](#tela-em-branco-no-linux-wayland)
- [Antivírus bloqueia o executável](#antivírus-bloqueia-o-executável)
- [LSP não funciona / sem autocomplete](#lsp-não-funciona--sem-autocomplete)
- [Editor muito lento em arquivos grandes](#editor-muito-lento-em-arquivos-grandes)
- [Git panel mostra "git não encontrado"](#git-panel-mostra-git-não-encontrado)
- [Terminal integrado não abre (Windows)](#terminal-integrado-não-abre-windows)
- [Erro de encoding ao abrir arquivo](#erro-de-encoding-ao-abrir-arquivo)
- [Plugins não carregam](#plugins-não-carregam)
- [Configurações corrompidas / reset](#configurações-corrompidas--reset)
- [Coletar informações para reportar bug](#coletar-informações-para-reportar-bug)

---

## O Codis não abre / tela preta

**Sintomas:** Processo Python inicia e termina sem abrir janela, ou janela aparece vazia/preta.

**Diagnóstico:**

```bash
# Execute pelo terminal para ver erros
python run.py

# Com log detalhado
CODIS_DEBUG=1 python run.py
```

**Causas comuns:**

1. **Python < 3.12** - o Codis usa f-strings com aspas aninhadas (PEP 701), sintaxe válida apenas em 3.12+.
   ```bash
   python --version   # deve ser 3.12.x ou superior
   ```

2. **Tkinter não instalado** - veja a seção [Erro: "No module named tkinter"](#erro-no-module-named-tkinter).

3. **Configuração corrompida** - veja [Configurações corrompidas / reset](#configurações-corrompidas--reset).

4. **Conflito de display no Linux** - veja [Tela em branco no Linux (Wayland)](#tela-em-branco-no-linux-wayland).

---

## Erro: "No module named tkinter"

O Tkinter não está incluído em algumas instalações Python, especialmente no Linux.

**Ubuntu / Debian:**
```bash
sudo apt install python3-tk
```

**Fedora / RHEL:**
```bash
sudo dnf install python3-tkinter
```

**Arch Linux:**
```bash
sudo pacman -S tk
```

**macOS (via Homebrew):**
```bash
brew install python-tk@3.12
```

**Windows:** O instalador oficial do Python inclui Tkinter por padrão. Se estiver faltando, reinstale Python com a opção "tcl/tk and IDLE" marcada.

---

## Fontes distorcidas ou ilegíveis

**Linux:** Instale as fontes DejaVu e configure o fontconfig:
```bash
sudo apt install fonts-dejavu-core fonts-dejavu-extra fontconfig
fc-cache -fv
```

**Windows:** Certifique-se de que ClearType está ativado:
- Configurações → Personalização → Fontes → Configurações de ClearType

**macOS:** Fontes borradas em telas não-Retina são uma limitação do Tkinter no macOS. Use **Configurações → Editor → Fonte** e escolha uma fonte com hinting manual (ex: Menlo, Courier New).

**Todas as plataformas:** Em **Configurações → Aparência → Escala de Interface**, ajuste o DPI multiplier para corresponder à sua tela.

---

## Tela em branco no Linux (Wayland)

O Tkinter usa X11. No Wayland, é necessário o XWayland.

**Verificar se XWayland está disponível:**
```bash
echo $WAYLAND_DISPLAY   # deve mostrar wayland-0 ou similar
echo $DISPLAY           # deve mostrar :0 ou similar (XWayland)
```

**Forçar X11:**
```bash
GDK_BACKEND=x11 python run.py
```

**GNOME (Wayland):** Instale `xwayland` se não estiver presente:
```bash
sudo apt install xwayland   # Ubuntu/Debian
```

**KDE (Wayland):** XWayland é instalado por padrão com o Plasma. Se não estiver funcionando:
```bash
sudo apt install kwin-wayland-backend-x11
```

---

## Antivírus bloqueia o executável

Executáveis gerados por PyInstaller frequentemente disparam falsos positivos em antivírus heurísticos. Isso afeta especialmente binários Windows.

**Antivírus comuns afetados:** Windows Defender, Bkav, Gridinsoft, SentinelOne, Skyhigh.

**Causa:** O bootstrap do PyInstaller (código que carrega o Python runtime) tem padrões comportamentais similares a droppers. Não há malware no Codis.

**Soluções:**

1. **Adicionar exceção no antivírus** - adicione a pasta do Codis como exceção/exclusão.

2. **Verificar integridade** - os hashes SHA-256 dos binários oficiais estão em cada [GitHub Release](https://github.com/KillovSky/Codis/releases):
   ```bash
   # Windows (PowerShell)
   Get-FileHash Codis.exe -Algorithm SHA256
   
   # Linux / macOS
   sha256sum Codis
   ```

3. **Executar pelo Python** - a opção mais segura para quem desconfia do binário:
   ```bash
   git clone https://github.com/KillovSky/Codis.git
   pip install -r requirements.txt
   python run.py
   ```

4. **Reportar falso positivo** - submeta o binário para análise no [VirusTotal](https://www.virustotal.com) e reporte como falso positivo diretamente ao seu antivírus.

---

## LSP não funciona / sem autocomplete

**Verificar se o servidor LSP está instalado:**
```bash
# Para Python
pylsp --version   # ou: python-language-server --version

# Instalar
pip install python-lsp-server[all]
```

**Verificar configuração:**
- Abra **Configurações → LSP → Servidores**
- Confirme que o caminho do servidor está correto
- Clique em "Testar Conexão"

**Log do LSP:**
```bash
CODIS_DEBUG=1 python run.py 2>&1 | grep -i lsp
```

**Problemas comuns:**

| Sintoma | Causa | Solução |
|---|---|---|
| "LSP: servidor não encontrado" | `pylsp` não está no PATH | `pip install python-lsp-server[all]` |
| "LSP: timeout na inicialização" | Servidor lento para iniciar | Aumentar timeout em Configurações → LSP |
| Autocomplete funciona mas hover não | Capacidade não suportada pelo servidor | Normal para alguns servidores LSP |
| LSP trava ao abrir arquivo grande | Análise estática pesada | Desativar LSP para arquivos > X KB em Configurações |

---

## Editor muito lento em arquivos grandes

O Codis ativa automaticamente o **modo arquivo grande** para arquivos com mais de 500 KB. Se não estiver ocorrendo:

1. Abra **Configurações → Performance**
2. Reduza `large_file_threshold_kb` (padrão: 500)
3. Desative `minimap_enabled` para arquivos grandes
4. Desative `animations_enabled`

Para arquivos extremamente grandes (> 50 MB), recomendamos usar `less` ou `vim` no terminal integrado do Codis.

---

## Git panel mostra "git não encontrado"

**Linux / macOS:**
```bash
which git   # deve mostrar /usr/bin/git ou similar
# Se não encontrado:
sudo apt install git    # Ubuntu/Debian
brew install git        # macOS
```

**Windows:** Instale o [Git for Windows](https://git-scm.com/download/win) e certifique-se de que está no PATH:
```cmd
where git
```

Se o Git está instalado mas o Codis não encontra, configure o caminho manualmente:
- **Configurações → Git → Caminho do executável git**

---

## Terminal integrado não abre (Windows)

O terminal integrado no Windows usa `pywinpty` para suporte a PTY completo.

```bash
pip install pywinpty
```

Se `pywinpty` não estiver disponível, o terminal cai para modo `subprocess` simples (sem cores ANSI, sem interatividade completa). Isso é esperado - o editor ainda funciona.

**Alternativa:** Use o PowerShell ou CMD externo via **Ferramentas → Abrir terminal externo**.

---

## Erro de encoding ao abrir arquivo

O Codis detecta encoding automaticamente via `charset-normalizer` ou `chardet`. Se não estiver instalado:

```bash
pip install charset-normalizer chardet
```

Para forçar encoding manualmente ao abrir um arquivo:
- Clique com o botão direito no arquivo na file tree
- **Abrir com encoding → escolha o encoding**

Ou via menu **Arquivo → Reabrir com encoding**.

---

## Plugins não carregam

**Verificar log de plugins:**
```bash
CODIS_DEBUG=1 python run.py 2>&1 | grep -i plugin
```

**Causas comuns:**

1. **Erro de sintaxe no plugin** - o plugin tem um bug que impede o carregamento. Verifique o log.
2. **Dependência faltando** - o plugin requer um pacote não instalado. Veja a documentação do plugin.
3. **Versão incompatível** - o plugin foi escrito para uma versão anterior do Codis. Verifique a versão mínima exigida.

**Desativar todos os plugins temporariamente:**
```bash
CODIS_NO_PLUGINS=1 python run.py
```

---

## Configurações corrompidas / reset

As configurações ficam em `~/.codis/config.json`.

**Resetar para padrão:**
```bash
# Backup
cp ~/.codis/config.json ~/.codis/config.json.bak

# Reset
rm ~/.codis/config.json
python run.py   # recria com valores padrão
```

**Windows:** A pasta fica em `%USERPROFILE%\.codis\`.

**Editar manualmente** (para usuários avançados):
```bash
# O arquivo é JSON padrão - editável em qualquer editor de texto
nano ~/.codis/config.json
```

---

## Coletar informações para reportar bug

Antes de abrir uma issue, colete:

```bash
# Versão do Python e Tkinter
python -c "import sys, tkinter; print(sys.version); print('Tk:', tkinter.TkVersion)"

# Sistema operacional
python -c "import platform; print(platform.platform())"

# Versão do Codis
python -c "from src.constants import APP_VERSION; print(APP_VERSION)"

# Dependências instaladas
pip list | grep -E "Pillow|markdown|psutil|websockets|send2trash|charset|Pygments"

# Log com debug ativado (últimas 100 linhas)
CODIS_DEBUG=1 python run.py 2>&1 | tail -100
```

Cole essas informações no template de bug report:
[🐛 Abrir bug report](https://github.com/KillovSky/Codis/issues/new?template=bug_report.yml)

---

## Codis compilado: pip, venv, LSP e Git

O executável compilado (PyInstaller) **não inclui um interpretador Python** para os
subprocessos. O Codis procura um Python 3.12+ instalado (PATH, `py -3` no Windows,
pastas padrão) e o valida executando-o. Se o painel pip mostrar *"Python não
encontrado"*:

1. Instale o Python em python.org (marque *Add to PATH*), ou
2. defina `CODIS_PYTHON` com o caminho do `python`/`python.exe` e reinicie.

Garantias contra cascata de processos: o Codis nunca executa a si mesmo como Python;
quando chamado como `Codis -m modulo` ou `Codis -c codigo` ele se comporta como
interpretador (módulos embutidos apenas) em vez de abrir a interface, e recusa
iniciar com profundidade de spawn acima de 4 (`CODIS_SPAWN_DEPTH`). O ambiente
dos subprocessos é limpo das variáveis do bundle (`LD_LIBRARY_PATH`, `TCL_LIBRARY`,
`TK_LIBRARY`).

