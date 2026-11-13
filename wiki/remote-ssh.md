# 🖥️ Desenvolvimento Remoto via SSH

O Codis suporta edição de arquivos em servidores remotos diretamente pela interface do editor, sem precisar instalar nada no servidor além do SSH padrão.

---

## Requisitos

- Binário `ssh` disponível no PATH local (padrão em Linux, macOS; Git for Windows no Windows)
- Binário `scp` ou `sftp` para transferência de arquivos
- Acesso SSH ao servidor (chave privada ou senha)

Nenhuma instalação no servidor é necessária.

---

## Configurando um Perfil SSH

Acesse **Ferramentas → Remoto & Colaboração → SSH Remoto…**, pelo atalho `Ctrl+Shift+H`, ou pelo ícone 🖧 na barra de atividades (visível quando a chave de configuração **`ssh_enabled`** estiver definida como `true` - não existe por padrão em `config.json`, sendo necessário adicioná-la manualmente ou usar o menu/atalho).

Clique em **+ Novo Perfil** e preencha:

| Campo | Descrição |
|---|---|
| Nome do perfil | Identificador amigável (ex: "Servidor de Produção") |
| Host / IP | Endereço do servidor (ex: `192.168.1.10` ou `meu.servidor.com`) |
| Porta | Porta SSH (padrão: 22) |
| Usuário | Login SSH (ex: `ubuntu`, `root`) |
| Chave SSH | Caminho para `.pem` ou chave privada (deixe vazio para senha) |
| Diretório remoto | Pasta inicial ao navegar (padrão: `~`) |
| Jump Host | ProxyJump intermediário (ex: `bastion.empresa.com`) |

Os perfis são salvos em `~/.config/codis/ssh_profiles.json`.

---

## Testando a Conexão

Selecione um perfil e clique em **Testar**. O Codis executa `ssh ... echo ok` e exibe o resultado na barra de status. Erros comuns:

- `ssh: command not found` - instale o cliente SSH
- `Connection refused` - servidor inacessível ou porta errada
- `Permission denied` - chave incorreta ou usuário inválido

---

## Navegando Arquivos Remotos

O painel **Arquivos Remotos** funciona igual para SSH/SFTP, FTP/FTPS, HTTP(S) e WebDAV.

| Ação | Como |
|---|---|
| Abrir pasta / arquivo | Duplo clique ou `Enter` (arquivos abrem no editor com o **nome original**) |
| Subir um nível | `Backspace` ou botão ↑ |
| Voltar / avançar | `Alt+←` / `Alt+→` |
| Editar o caminho | `Ctrl+L` ou clique na área vazia dos breadcrumbs |
| Atualizar | `F5` |
| Filtrar | `Ctrl+F` (instantâneo) |
| Ordenar | Clique no cabeçalho da coluna (pastas sempre primeiro) |
| Renomear / excluir | `F2` / `Del` (SSH, FTP, WebDAV) |
| Nova pasta | `Ctrl+N` |

O menu de contexto (botão direito, em arquivo, pasta ou área vazia) oferece abrir,
baixar, enviar, nova pasta, renomear, excluir e copiar caminho. Baixar/enviar aceita
**seleção múltipla e pastas inteiras** (recursivo, com limite de segurança).
O painel lateral recolhe sozinho em janelas estreitas (botão ☰) e as colunas
secundárias somem conforme a largura.

### Salvar envia de volta (sync ao salvar)

Qualquer forma de salvar um arquivo aberto do servidor (`Ctrl+S`, *Salvar tudo* ou
autosave) o reenvia. Se o arquivo **mudou no servidor** desde que foi aberto, o
Codis pergunta antes de sobrescrever. Se o envio falhar, você é avisado e pode
tentar de novo (as alterações continuam salvas localmente). Desative com
`remote_sync_on_save: false`.

### Protocolos

| Protocolo | Listar | Baixar | Enviar | Criar pasta / renomear / excluir |
|---|:-:|:-:|:-:|:-:|
| SSH / SFTP | ✓ | ✓ | ✓ | ✓ |
| FTP / FTPS (explícito `:21`, implícito `:990`) | ✓ (MLSD ou LIST Unix/IIS) | ✓ | ✓ | ✓ (pasta vazia para excluir) |
| WebDAV / WebDAVS | ✓ | ✓ | ✓ | ✓ |
| HTTP / HTTPS (índice Apache/nginx) | ✓ | ✓ | – | – |

No cadastro da conexão você pode **colar uma URL** (`sftp://user@host:2200/var/www`) no campo
Host e os demais campos são preenchidos. O botão **Testar conexão** valida sem fechar o diálogo.

---

## Debug Remoto (DAP via Túnel SSH)

O Codis pode criar um túnel SSH para conectar o depurador local a um processo `debugpy` rodando no servidor.

No servidor, inicie o processo com debugpy:

```bash
python -m debugpy --listen 0.0.0.0:5678 meu_script.py
```

No Codis, abra o painel remoto, conecte ao perfil e clique em **Abrir Túnel Debug**. O Codis criará um túnel `localhost:5678 → servidor:5678`, e o cliente DAP local (o mesmo do [Painel de Debug](debug.md)) conecta normalmente nesse túnel, como se o processo estivesse rodando na própria máquina.

> As portas usadas pelo túnel são informadas diretamente no diálogo **Abrir Túnel Debug**, e são independentes da configuração `debug_dap_port` (usada apenas para depuração local, sem SSH). Se a porta local do túnel colidir com outra ferramenta de debug já em uso na sua máquina (ex: VS Code também escutando em `5678`), basta escolher outra porta local no diálogo do túnel.

---

## Configuração Avançada com Jump Host

Para servidores atrás de um bastion/jump host:

```
Perfil: Servidor Interno
Host: 10.0.0.50
Porta: 22
Usuário: dev
Jump Host: bastion.empresa.com
```

O Codis usa `ssh -J bastion.empresa.com dev@10.0.0.50` automaticamente.

---

## Autenticação

Ordem: **chave privada** → **senha** → **ssh-agent**. A senha vai para o `ssh` por
`sshpass -e` (Linux/macOS, se instalado) ou por um `SSH_ASKPASS` temporário, o que
faz o login por senha funcionar também **no Windows**, sem `sshpass`. A senha nunca
aparece na linha de comando. Se a chave tiver passphrase, informe-a no campo Senha.
Em Linux/macOS a conexão é reutilizada (ControlMaster): uma única autenticação para
listar, baixar e enviar; defina `CODIS_SSH_NOMUX=1` para desativar.

## Limitações

- O servidor SSH precisa de shell POSIX (`ls`, `cd`, `mkdir`, `rm`, `mv`); servidores Windows (cmd/PowerShell) não são suportados para listagem.
- Progresso de transferência é detalhado em FTP/HTTP/WebDAV; em SSH a barra fica indeterminada (usa o `sftp` do sistema).
- Não há watch remoto de arquivos (mudanças externas só são detectadas ao salvar, pela checagem de conflito).
- Senhas ficam em base64 (ofuscação, **não** criptografia) em arquivo `0600`; prefira chaves ou o ssh-agent.
- O caminho SSH_ASKPASS no Windows ainda não foi validado em máquina Windows real.

---

## Configurações

Acesse em **Configurações → SSH Remoto** (ou `Ctrl+,` → categoria SSH Remoto):

| Chave | Padrão | Descrição |
|---|---|---|
| `ssh_default_port` | `22` | Porta SSH padrão para novos perfis |
| `ssh_connect_timeout` | `15` | Timeout de conexão (segundos) |
| `ssh_keepalive_interval` | `30` | Intervalo keepalive em segundos (0 = off) |
| `ssh_agent_forwarding` | `False` | Habilita forwarding do agente SSH |
| `ssh_strict_host_key` | `True` | Verifica chave do host strictamente |
| `ssh_sftp_buffer_kb` | `64` | Tamanho do buffer SFTP em KB |
| `ssh_open_in_editor` | `True` | Baixa e abre arquivo remoto no editor |
| `ssh_enabled` | `False` | Exibe ícone SSH na barra de atividades |

> As configurações `debug_dap_enabled`/`debug_dap_port` pertencem à categoria **Avançado** (depuração local, sem SSH) - veja [Debug](debug.md) e [Configurações → Avançado](configuracoes.md#-avançado). O túnel de debug SSH descrito acima usa portas informadas diretamente no diálogo, não essas chaves.

---

## Segurança e robustez

- **Caminhos escapados.** O caminho navegado é escapado com `shlex.quote` antes de ir ao shell remoto (preservando a expansão de `~`), de modo que nomes com `;`, crases ou `$()` não são executados.
- **Senha SSH fora da linha de comando.** Com `sshpass`, a senha é transmitida por variável de ambiente (`sshpass -e` + `SSHPASS`), nunca como argumento visível em `ps`.
- **Lote do `sftp`.** Caminhos são citados no estilo do parser do `sftp` e quebras de linha embutidas são rejeitadas.
- **Caminhos iniciados por `-`.** O terminador de opções `--` é usado antes dos caminhos no `scp`.
- **Permissões.** Perfis de conexão (`remote_browser_profiles.json`) e demais arquivos com credenciais ofuscadas são salvos com permissão `0600` (Linux/macOS).
- **Listagem SSH.** O parser ancora no tamanho/data de `ls --time-style=long-iso` e funciona com GNU, BSD e BusyBox.
- **Erros visíveis.** Permissão negada e pasta inexistente exibem a mensagem real e um botão *Tentar novamente*.
- **Sync ao salvar.** O editor notifica `src/editor/save_hooks.py` em qualquer gravação; falhas de upload/download são reportadas como erro.
- **Arquivos sem extensão conhecida** (`Makefile`, `Dockerfile`, dotfiles) são inspecionados (byte nulo) em vez de recusados.

Os testes estão em `tests/test_remote_adapters.py` e `tests_gui/test_remote_browser_panel.py`.
