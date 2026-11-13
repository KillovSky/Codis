# 💬 Chat ao Vivo (Painel de Chat Universal)

Cliente de chat multi-protocolo embutido no Codis. A arquitetura é dividida em **núcleo sem Tk** (`src/chat/`) e **interface** (`src/ui/chat_panel.py`):

| Camada | Arquivos | Responsabilidade |
|---|---|---|
| Modelos | `src/chat/models.py` | `ChatProfile`, tabelas de campos por protocolo (`FieldSpec`), selos/cores |
| Adaptadores | `src/chat/adapters/*.py` | Um por protocolo; ciclo de vida, reconexão com backoff e fila de envio ficam em `base.py` |
| Sessão | `src/chat/session.py` | Estado dos canais (thread-safe), menções, comandos, eco, histórico |
| Histórico | `src/chat/history.py` | JSONL por canal, permissão 0600, tamanho limitado |
| Interface | `src/ui/chat_panel.py`, `chat_render.py`, `chat_profile_dialog.py` | Renderização incremental, composer, busca, diálogo de conexão |

Acesso: **Configurações → Remoto & Colaboração → Chat ao Vivo...** ou o ícone 💬 da barra de atividades.

> Não confundir com [Colaboração em Tempo Real](colaboracao.md) (edição compartilhada). O Chat é um cliente de mensageria genérico para conversar com a equipe **enquanto** você programa.

---

## Protocolos

| Protocolo | O que está implementado | Limitações conhecidas |
|---|---|---|
| **IRC / IRCS** | Parser RFC 1459 + tags IRCv3, `CAP` (sasl, server-time, multi-prefix), SASL PLAIN, TLS, CTCP (`ACTION`, `VERSION`, `PING`), NAMES/prefixos, PING/keepalive, divisão de mensagens longas, servidores com fim de linha `\n` ou `\r\n` | - |
| **XMPP / Jabber** | Parser XML incremental, STARTTLS ou TLS direto, SASL PLAIN/ANONYMOUS, bind, MUC (roster, assunto, histórico com horário original, conflito de apelido), DMs, `/me` | SCRAM-SHA-1 ainda não; sem SRV lookup (informe o host se o domínio não resolver) |
| **Matrix** | Login por senha ou `access_token` (validado por `whoami`), long-poll `/sync`, nomes de exibição, salas por alias/ID, convites, imagens/arquivos como link, `m.emote`, tópico | Sem E2EE (salas criptografadas não são legíveis) |
| **Slack** | **Socket Mode** (App Token `xapp-`, recomendado), RTM (apps clássicos) ou **polling** automático; nomes de canais/usuários resolvidos, menções/links desescapados | Apps novos não têm RTM: sem App Token o Codis usa polling (alguns segundos de atraso) |
| **Discord** | Gateway v10 com heartbeat/ACK e intents corretos, filtro de canais do perfil, DMs, menções e anexos, limite de 2000 caracteres, `allowed_mentions` desligado (sem `@everyone` acidental) | Exige *Message Content Intent* habilitado no Developer Portal; token de usuário (self-bot) viola os ToS |
| **Telegram** | Long-polling, grupos/canais/DMs, texto puro (sem erros por `<`), mídia como rótulo | Bot só vê chats onde foi adicionado |
| **Mattermost** | Login (token no cabeçalho `Token`) ou PAT, WebSocket v4, equipes, DMs, membros | - |
| **MQTT 3.1.1** | Remaining-length completo, usuário com/sem senha, keepalive com `PINGREQ`, TLS, curingas na assinatura, formato `plain` ou `json` | Publica só em tópico exato (sem curinga) |
| **WebSocket / Codis** | `ws`, `wss` e servidor colaborativo do Codis (join com senha, lista de peers, entrada/saída) | Senha errada é erro definitivo (não fica tentando) |

### Garantias comuns
- **Reconexão automática** com backoff exponencial (1 s → 60 s) e re-entrada nos canais; erros definitivos (senha/token inválido) **não** são repetidos.
- Todo envio sai por **fila em thread própria**: a interface nunca trava em rede lenta; falhas aparecem no canal.
- Texto enviado é saneado contra **injeção de comandos** (CR/LF/NUL) nos protocolos orientados a linha.

---

## Interface

- **Barra lateral** com selo/cor do protocolo, status (online / conectando / reconectando / offline) e *badges* de não lidas (vermelho = menção ou DM). Recolher servidor pela seta.
- **Mensagens** com recuo suspenso, agrupamento por autor, separador de dia, linha *"novas mensagens"* após o histórico, destaque de menções, `` `código` ``, blocos ```` ``` ````, `**negrito**`, links clicáveis (só `http/https`), cores de nick com contraste garantido no tema claro/escuro.
- **Rolagem preservada**: se você está lendo o histórico, mensagens novas não puxam a tela; aparece o botão **↓ N novas**.
- **Composer** multilinha (Enter envia, Shift+Enter quebra linha), ↑/↓ histórico, **Tab** completa nicks e comandos, confirmação ao colar mais de 5 linhas.
- **Busca** no canal (`Ctrl+F`, Enter/Shift+Enter navegam), **alternador rápido** (`Ctrl+K`), `Alt+↑/↓` troca de conversa.
- **Membros** (alternável): ops primeiro; duplo clique menciona; menu com DM/Whois (IRC), ignorar, copiar.
- **Diálogo de conexão** adaptativo, validação inline, botão **Testar conexão** (alcance/TLS e credencial quando o protocolo permite).

### Comandos
`/help` lista tudo. Genéricos: `/join`, `/part`, `/close`, `/query nick [msg]`, `/me`, `/clear`, `/clearhistory`, `/ignore`, `/unignore`, `/reconnect`, `/disconnect`, `//texto` (envia `/texto`). IRC: `/msg /nick /topic /whois /who /names /list /mode /kick /op /deop /voice /ban /ns /cs /identify /away /invite /notice /ctcp /raw`. XMPP: `/topic /nick`. Matrix: `/topic`.

---

## Perfis e privacidade
- `~/.config/codis/chat_profiles.json` (0600). Senhas/tokens ficam em **base64 (ofuscação, não é criptografia)** - não reutilize senhas importantes.
- **Histórico** em `.../chat_history/<perfil>/<canal>.jsonl` (0600, ~1 MB por canal), ligado por padrão; desative por perfil ("Salvar histórico") ou apague com `/clearhistory`.
- Opções por perfil: reconectar automaticamente, conectar ao abrir, verificar certificado TLS.

## Testes
`tests/test_chat_*.py` sobem **servidores falsos em sockets reais** (IRC, MQTT, XMPP, HTTP, WebSocket) e o servidor colaborativo real do Codis; `tests_gui/test_chat_panel.py` exercita a interface completa sob Xvfb.
