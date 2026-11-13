# 🗄️ Backups, Lixeira & Arquivos Compactados

Três recursos do Codis lidam com a segurança e o histórico de arquivos fora do controle de versão Git: **File Timeline** (snapshots automáticos), o **Gerenciador de Lixeira** (exclusão segura) e o **Visualizador de Arquivos Compactados** (ZIP/RAR/7z/TAR).

---

## 🕒 File Timeline (`src/ui/file_timeline.py`)

Acesso: menu **Exibir → Painéis → File Timeline**.

Exibe o histórico de snapshots automáticos de um arquivo aberto, permitindo restaurar versões anteriores **sem depender do Git**. Útil em arquivos fora de um repositório, ou quando você quer voltar a um estado anterior sem criar um commit.

### Como funciona

- O Codis salva snapshots automáticos do conteúdo do arquivo em `~/.codis/backups/` (organizados por nome de arquivo sanitizado)
- A janela do Timeline lista os snapshots em ordem cronológica, com timestamp de cada um
- Cada snapshot pode ser comparado com o conteúdo atual via diff (usando `difflib`), mostrando exatamente o que mudou
- Restaurar um snapshot substitui o conteúdo do editor pela versão escolhida - a substituição **não é salva automaticamente em disco**, dando a chance de revisar antes de confirmar com `Ctrl+S`

### Quando usar

- Recuperar uma versão anterior depois de uma edição em massa indesejada (find & replace errado, por exemplo)
- Comparar o estado atual de um arquivo com como ele estava há algumas horas, sem precisar de um repositório Git
- Funciona em qualquer arquivo de texto, mesmo fora de um workspace versionado

---

## 🗑️ Gerenciador de Lixeira (`src/ui/trash_manager.py`)

A exclusão de arquivos pelo Codis (no Explorer, por exemplo) usa um destes destinos, definido em `trash_backend` (Configurações → Destino da Lixeira):

1. **`workspace`** (padrão): move o item para `<projeto>/.codis_trash/` com um `rename` atômico no mesmo volume - instantâneo mesmo para arquivos enormes e nunca vira cópia entre discos. A pasta tem seu próprio `.gitignore` e `.meta.json` com os metadados (caminho de origem, data de exclusão).
2. **`system`**: delega à lixeira nativa do sistema operacional via `send2trash` (opcional). Se falhar ou o pacote não estiver instalado, o Codis usa a lixeira do projeto.

`trash_max_mb` e `trash_retention_days` limitam o tamanho e a idade dos itens; ao exceder, os mais antigos são removidos.

### Painel da lixeira

O painel `TrashPanel` (Activity Bar) lista os itens de todas as lixeiras do projeto, com opções de:

- Restaurar para o caminho original
- Excluir permanentemente

### Uso programático (para quem desenvolve plugins)

```python
from src.ui.trash_manager import TrashManager

TrashManager.delete(path)                    # envia para a lixeira
TrashManager.delete(path, permanent=True)    # exclusão definitiva
```

---

## 📦 Visualizador de Arquivos Compactados (`src/ui/archive_panel.py`)

Permite explorar o conteúdo de arquivos compactados sem precisar extraí-los manualmente primeiro.

### Formatos suportados

| Formato | Suporte |
|---|---|
| **ZIP** | Nativo, via módulo `zipfile` da biblioteca padrão |
| **TAR / GZip / BZip2** | Nativo, via módulo `tarfile` da biblioteca padrão |
| **RAR** | Requer `pip install rarfile` - exibe aviso se ausente |
| **7-Zip** | Requer `pip install py7zr` - exibe aviso se ausente |

### Interface

- Árvore (`TreeView`) com a estrutura de pastas do arquivo compactado, sem extrair nada ainda
- Duplo-clique em um arquivo, ou o botão **Abrir no editor**, extrai apenas aquele arquivo para um diretório temporário e o abre diretamente no Codis
- **Extrair selecionado...** permite escolher o destino e extrair arquivos/pastas específicos, sem precisar descompactar o pacote inteiro
- Barra de status mostra o tamanho comprimido comparado ao tamanho real de cada item, útil para identificar arquivos grandes dentro do pacote antes de extrair

---

## 🎨 Visualizador de Pré-Tema (`src/ui/theme_preview_panel.py`)

Painel de pré-visualização de temas estilo wizard, usado antes de confirmar a troca de tema - ver detalhes completos em [Temas](temas.md#pré-visualização-antes-de-aplicar).

- Grade de cartões com todos os temas disponíveis (nome + ícone indicando dark/light)
- Preview ao vivo com miniatura de código, sidebar, tab bar e barra de status já no tema selecionado
- Botão de aplicar com escolha entre troca **Completa** ou **Rápida**
- Navegação por teclado (setas, `Enter`, `Escape`)
- Filtro por nome e por polaridade (dark / light / todos)
