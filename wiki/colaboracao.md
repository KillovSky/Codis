# 👥 Edição Colaborativa

Edição simultânea do mesmo arquivo por várias pessoas em tempo real, via servidor WebSocket embutido no Codis. A convergência dos textos é garantida por **Operational Transformation (OT)** com servidor autoritativo.

---

## Requisitos

```bash
pip install websockets
```

Sem o pacote, o painel mostra o comando de instalação. Depois de instalar, reinicie o Codis.

---

## Hospedando uma sessão

1. Abra o arquivo (ou aba) que quer compartilhar.
2. Abra o painel **Colaboração** em **Ferramentas → Remoto & Colaboração → Colaboração…** ou pelo ícone 👥 da barra de atividades (visível com `collab_enabled = true`).
3. Informe seu nome e clique em **🌐 Hospedar**. A **senha é obrigatória**: se o campo estiver vazio, o Codis gera uma automaticamente e a mostra.
4. Clique em **📋 Copiar convite** e envie aos participantes (IP, porta, ID da sala e senha).

O painel fica **preso à aba escolhida** ao hospedar. Trocar de aba depois não mistura arquivos; se a aba compartilhada for fechada, a sessão é encerrada com um aviso.

Para descobrir o IP manualmente: `hostname -I` (Linux) ou `ipconfig` (Windows). O convite já traz o IP local.

## Entrando em uma sessão

1. Em **🔗 Entrar**, informe host, porta, ID da sala, senha e seu nome.
2. O Codis abre uma **aba nova** com o documento da sala. O seu arquivo aberto não é sobrescrito.
3. Mensagens de erro claras: sala não encontrada, senha incorreta, nome já em uso, sala cheia.

> Só o host cria salas. Digitar um ID inexistente retorna "Sala não encontrada" (antes criava uma sala vazia silenciosamente).

---

## Participantes, cursores e chat

- A lista mostra cada participante com cor própria; entradas e saídas aparecem no chat.
- O cursor de cada pessoa aparece no seu editor com uma **etiqueta com o nome**. Desative com `collab_show_cursors = false`.
- O chat entrega a mensagem a todos, inclusive ao autor, **uma única vez**. O histórico existe só durante a sessão.

---

## Como a sincronização funciona

```
 digitação local ──► diff mínimo ──► ClientOT (1 mudança em voo + buffer) ──► servidor
 servidor ──► ack (sua mudança foi aceita)        ──► libera a próxima do buffer
 servidor ──► op de outro ──► transforma contra o pendente ──► aplica no editor
```

- Cada mudança aceita pelo servidor é uma **revisão**. O autor recebe `ack`; os demais recebem o `op` já transformado.
- O cliente nunca perde edições: antes de integrar qualquer operação remota, o painel captura o que você digitou desde o último ciclo (50 ms).
- Operações remotas são aplicadas **de forma incremental** no editor: cursor, seleção, rolagem e realce são preservados.
- Inserção de outra pessoa dentro de um trecho que você apagou **é preservada** (a deleção é dividida em duas).
- Mudança inválida, ou de um cliente muito atrasado (histórico descartado), faz o servidor reenviar o documento completo para aquele cliente; o painel avisa que edições pendentes foram descartadas.
- Emoji e demais caracteres fora do BMP são tratados corretamente (o Tk os conta como 2 colunas).

A convergência é verificada por testes de propriedade (dezenas de milhares de pares de operações, simulações com vários clientes) e por testes com sockets reais.

---

## Segurança

- WebSocket simples, **sem TLS**. Em redes que não sejam de confiança, use um túnel SSH: `ssh -L 9743:localhost:9743 usuario@host`.
- A senha é comparada em tempo constante; sem senha correta o documento não é enviado.
- O servidor escuta em `0.0.0.0` (LAN). Limites: 100 salas, 50 pessoas por sala (ou `collab_max_peers`), mensagens de até 32 MiB, documento de até 20 milhões de caracteres, 10 mil operações por mudança.
- Cada sala existe apenas enquanto o host está com a sessão aberta.

## Limitações conhecidas

- Funciona melhor em LAN; pela internet use túnel SSH ou `ngrok tcp 9743`.
- Se a conexão cair, a sessão termina (sem reconexão automática com mesclagem). O texto local permanece no editor.
- Edições simultâneas em vários pontos distantes no mesmo ciclo de 50 ms viram uma única mudança contígua; funciona, mas é mais grossa do que operações por tecla.
- O desfazer (`Ctrl+Z`) do editor também pode desfazer texto aplicado por outra pessoa.
- Configurações `collab_show_chat` e `collab_autosave_sync` existem na tela de Configurações, mas **ainda não** têm efeito.

---

## Configurações

**Configurações → Colaboração**:

| Chave | Padrão | Efeito |
|---|---|---|
| `collab_enabled` | `False` | Mostra o ícone de colaboração na barra de atividades |
| `collab_port` | `9743` | Porta padrão sugerida nos campos de hospedar e entrar |
| `collab_max_peers` | `8` | Máximo de participantes por sala (limite absoluto 50) |
| `collab_show_cursors` | `True` | Exibe cursores e nomes dos outros participantes |
| `collab_show_chat` | `True` | *(ainda não implementado)* |
| `collab_autosave_sync` | `True` | *(ainda não implementado)* |
| `collab_username` | usuário do sistema | Nome padrão |

---

## Arquitetura (para contribuidores)

| Módulo | Papel |
|---|---|
| `src/collab/ot.py` | `TextOp`, `transform` (TP1), `RoomDoc` (servidor), `ClientOT` (cliente), `diff_change` |
| `src/collab/net.py` | `CollabServer` e `CollabClient` (WebSocket, ACK, validação, fila de eventos) |
| `src/collab/session.py` | `CollabSession`: captura local → OT → editor (sem Tk, testável) |
| `src/collab/tk_adapter.py` | `TkTextAdapter`: aplicação incremental em `tk.Text` |
| `src/ui/collab_panel.py` | Interface (`CollabPanel`) e overlay de cursores |

Regras de projeto: nenhum callback de rede toca o Tk (tudo passa por uma fila drenada na thread de UI); o painel coleta o lixo ao ser destruído para que variáveis Tk não sejam finalizadas em threads de rede.

Testes: `tests/test_collab_ot.py` (propriedades), `tests/test_collab_net.py` (sockets reais), `tests_gui/test_collab_adapter.py` e `tests_gui/test_collab_panel.py` (dois painéis ponta a ponta).

## Plugins

O evento `collab_peer_join`, citado em versões anteriores desta página, **nunca existiu**: nenhum código o emite. Plugins ainda não conseguem observar a sessão de colaboração.
