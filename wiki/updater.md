# 🔄 Atualizador

O sistema de atualização do Codis (`src/updater/`) é modular e usa apenas a stdlib: faz **backup antes de qualquer mudança**, aceita cancelamento a qualquer momento e restaura o backup se algo falhar. O diálogo de atualização (`src/updater/dialog.py`, aberto por `open_update_dialog`) mostra progresso, changelog e a escolha do modo.

---

## Modos de Atualização

Todos os modos passam por `UpdateManager.update(mode)` e devolvem um `UpdateResult`.

| Modo | O que faz |
|------|-----------|
| `CHECK_ONLY` | Consulta a API do GitHub e compara a versão instalada com a mais recente. Nenhum arquivo é modificado (usado na verificação silenciosa da inicialização) |
| `SMART` | Usa a Compare API do GitHub para baixar só os arquivos que mudaram entre as duas versões; cai para ZIP se a Compare API falhar |
| `ZIP` | Baixa o ZIP completo da release e sobrescreve a instalação atual |
| `REINSTALL` | Remove os arquivos não protegidos e reinstala do zero via ZIP (corrige instalações corrompidas) |
| `FORCE` | Igual a `REINSTALL`, mas ignora a checagem de versão: reinstala mesmo já estando na última versão |
| `BINARY` | Substitui o executável PyInstaller. Em Linux/macOS usa rename atômico; no Windows agenda a troca por um script `.bat` após o reinício. Só funciona em builds empacotados |

Os estados possíveis de um resultado (`UpdateStatus`) são `UP_TO_DATE`, `UPDATE_AVAILABLE`, `SUCCESS`, `PENDING_RESTART`, `ERROR` e `CANCELLED`.

---

## Arquitetura

| Componente | Arquivo | Responsabilidade |
|------------|---------|------------------|
| `UpdateManager` | `manager.py` | Orquestra os modos. Expõe `check()`, `update(mode, *, target_tag="", dry_run=False)`, `cancel()`, `run_in_thread(...)` e `restart_app()`. Thread-safe via `threading.Lock` |
| `UpdateChannel` | `channel.py` | Toda a comunicação com a API do GitHub, só com `urllib`. Aceita token pessoal (PAT) para elevar o rate limit |
| `BackupManager` | `backup.py` | Snapshot do diretório do app antes de modificar, restauração em falha e limpeza após sucesso. Funciona como context manager |
| `UpdateResult` / `ReleaseInfo` / `UpdateMode` / `UpdateStatus` | `result.py` | Dataclasses e enums. `UpdateResult.ok` e `.update_available` são propriedades calculadas |
| `UpdateDialog` | `dialog.py` | Interface Tkinter com visual consistente com o K-UI |

---

## Proteção de Dados

Os caminhos em `_PROTECTED` (`manager.py`) são verificados antes de qualquer escrita ou remoção; nenhum modo os modifica.

| Caminho protegido | Conteúdo |
|-------------------|----------|
| `config.json` | Configurações do usuário |
| `.codis` | Diretório de dados do editor |
| `session.json` | Estado da sessão (arquivos abertos) |
| `keybindings.json` | Atalhos customizados |
| `plugins` | Plugins instalados |
| `themes` | Temas customizados |
| `snippets` | Snippets personalizados |
| `logs` | Arquivos de log |

---

## API

Verificação silenciosa na inicialização (não bloqueia a UI; roda em thread daemon):

```python
from src.updater import UpdateManager, UpdateMode
from src.constants import APP_VERSION

mgr = UpdateManager(APP_VERSION)

def _on_result(result):
    if result.update_available:
        self.after(0, lambda: self._notify_update(result))

mgr.run_in_thread(mode=UpdateMode.CHECK_ONLY, callback=_on_result)
```

Atualização com progresso:

```python
def _on_progress(message: str, percent: float) -> None:
    progressbar.set(percent)
    status_label.config(text=message)

mgr = UpdateManager(
    current_version="1.0.0",
    github_token=config.get("github_token", ""),
    on_progress=_on_progress,
)

result = mgr.update(UpdateMode.SMART)

if result.ok and result.restart_required:
    if messagebox.askyesno("Reiniciar?", "Atualização aplicada. Reiniciar agora?"):
        UpdateManager.restart_app()
```

Simulação (`dry_run`), sem modificar nada:

```python
result = mgr.update(UpdateMode.SMART, dry_run=True)
print(f"Arquivos que seriam atualizados: {result.files_updated}")
```

`BackupManager` como context manager:

```python
from src.updater.backup import BackupManager
from pathlib import Path

with BackupManager(Path("/caminho/para/codis")) as bm:
    bm.create(label="pre-update")
    # Se uma exceção ocorrer aqui, restore() é chamado automaticamente
    perform_risky_operation()
# Sem exceção -> cleanup() remove o backup temporário
```

---

## Segurança: Backup → Aplicar → Confirmar → Limpar

1. **Backup automático:** `BackupManager.create()` copia o diretório do app para um diretório temporário (sem `__pycache__` nem `.pyc`).
2. **Aplicação com checagem de cancelamento:** o evento `_cancelled` é verificado a cada arquivo; se cancelado, a operação para e o backup é restaurado.
3. **Confirmação:** só após todas as operações terminarem, `backup.cleanup()` remove o snapshot.
4. **Rollback em falha:** qualquer exceção dispara `restore()` + `cleanup()` e o erro vira `UpdateStatus.ERROR`.

---

## Configuração

Sem token, a API do GitHub permite 60 requisições por hora por IP; com um PAT, o limite sobe para 5.000 por hora.

| Chave em `config.json` | Tipo | Padrão | Descrição |
|------------------------|------|--------|-----------|
| `github_token` | string | `""` | PAT do GitHub. Vazio = sem autenticação |
| `auto_check_updates` | bool | `true` | Verifica silenciosamente ao iniciar |

---

## Testes

`tests/test_updater.py` (87 casos) roda 100% offline: o canal é mockado com `unittest.mock` e os ZIPs são gerados em memória.

```bash
pytest tests/test_updater.py -v
```

| Classe de teste | Casos |
|-----------------|------:|
| `TestReleaseInfo` | 6 |
| `TestUpdateResult` | 10 |
| `TestVersionTuple` | 7 |
| `TestFmtBytes` | 4 |
| `TestIsProtected` | 5 |
| `TestCheck` | 6 |
| `TestUpdateSmart` | 7 |
| `TestUpdateZip` | 6 |
| `TestUpdateReinstall` | 4 |
| `TestUpdateForce` | 2 |
| `TestTargetTag` | 2 |
| `TestCancel` | 2 |
| `TestRunInThread` | 3 |
| `TestBackupManager` | 8 |
| `TestUpdateChannelHeaders` | 11 |
| `TestOverwriteFromDir` | 2 |
| `TestClearPycache` | 2 |

Veja também [Builds & Empacotamento](builds.md) e [Arquitetura](arquitetura.md).
