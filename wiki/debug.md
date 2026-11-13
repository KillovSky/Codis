# 🐞 Debug (DAP via debugpy)

O Codis tem um painel de depuração real para Python: breakpoints, step into/over/out, inspeção de variáveis e um console com avaliação de expressões no frame parado - tudo isso conversando de fato com o protocolo **DAP** (Debug Adapter Protocol) através do [`debugpy`](https://github.com/microsoft/debugpy).

Diferente de ferramentas que só abrem uma porta e esperam algum cliente externo conectar, **o próprio Codis é o cliente DAP**. Você não precisa instalar nem configurar nenhum IDE adicional - basta ter `debugpy` disponível no Python que você usa.

---

## Como usar (passo a passo)

1. **Instale o `debugpy`**, se ainda não tiver:
   ```bash
   pip install debugpy
   ```
2. Abra um arquivo `.py` **salvo em disco** no editor (arquivos não salvos não podem ser depurados).
3. **Clique na margem esquerda do editor** (o gutter, ao lado do número da linha) na linha onde você quer parar. Um marcador de breakpoint aparece ali.
4. Abra o **Painel de Debug** (painel inferior → aba **Debug**).
5. Clique em **▶ Run**.

O Codis vai:
- Iniciar o `debugpy` no arquivo ativo, numa porta local (padrão `5678`);
- Conectar a ele como cliente DAP automaticamente;
- Enviar os breakpoints marcados no gutter;
- Liberar a execução.

Quando a execução chegar numa linha marcada, ela **para** (`⏸ Stopped: breakpoint`), a linha correspondente é destacada no editor, e o painel mostra a pilha de chamadas e as variáveis locais/globais daquele ponto.

> Sem nenhum breakpoint marcado, o script roda do início ao fim sem parar - você só verá a saída normal no console do painel. Isso é esperado, não é um bug: marque pelo menos um breakpoint antes de clicar em Run se quiser inspecionar o estado do programa.

### Controles da toolbar

| Botão | Ação | Atalho sugerido |
|---|---|---|
| ▶ Run | Inicia uma nova sessão de depuração no arquivo ativo | F5 |
| ⏹ Stop | Encerra a sessão e o processo depurado | - |
| ↻ Restart | Para e inicia novamente (com os mesmos breakpoints) | - |
| ⤵ Step Into | Entra na próxima função chamada na linha atual | F11 |
| ⤴ Step Over | Executa a linha atual sem entrar em chamadas de função | F10 |
| ⤴⤴ Step Out | Sai da função atual, voltando ao chamador | Shift+F11 |
| ⏩ Continue | Segue a execução até o próximo breakpoint | F5 |

Os botões de step/continue só têm efeito quando a execução está de fato parada num breakpoint (`⏸ Stopped`). Fora disso, o painel avisa que não há sessão parada para avançar.

### Breakpoints

- **Adicionar/remover:** clique na margem esquerda do editor, ao lado do número da linha.
- A lista de breakpoints no painel reflete sempre o **arquivo/aba ativa** - trocar de aba atualiza a lista automaticamente.
- Se você adicionar ou remover um breakpoint **enquanto uma sessão de debug já está rodando**, a mudança é enviada ao vivo ao `debugpy` (não é preciso parar e reiniciar a sessão).
- Breakpoints não persistem entre reinicializações do Codis nem entre fechar/reabrir o arquivo.

### Console e avaliação de expressões

O campo de entrada do painel funciona como um REPL:

- **Sem sessão de debug parada:** avalia num namespace Python local isolado, sem relação com o programa em execução - útil só para cálculos rápidos.
- **Com a execução parada num breakpoint:** a expressão é avaliada de fato **no contexto do frame parado**, com acesso real às variáveis locais e globais daquele ponto do programa - o mesmo que um `evaluate` do DAP faria num IDE completo. Você pode inclusive atribuir valores (`x = 10`) para alterar o estado em tempo real.

### Variáveis e Call Stack

Quando a execução para num breakpoint, o painel busca automaticamente:
- A pilha de chamadas (`stackTrace`) - o topo da pilha é usado para posicionar o cursor no editor;
- Os escopos `Locals` e `Globals` do frame selecionado, com seus respectivos valores.

---

## Detecção automática

Por padrão, o Codis **detecta e usa o `debugpy` automaticamente** sempre que ele estiver disponível no Python/ambiente em uso - não é necessário habilitar nada manualmente. Se `debugpy` não estiver instalado, o Codis avisa no console e cai para uma execução simples (sem breakpoints reais, só captura de saída).

Se quiser **desativar** o debug real e forçar sempre a execução simples (por exemplo, para evitar a sobrecarga do debugpy em scripts muito curtos), use a configuração abaixo.

## Configurações

Acesse em **Configurações → Avançado** (ou `Ctrl+,` → categoria correspondente):

| Chave | Tipo | Padrão | Descrição |
|---|---|---|---|
| `debug_dap_enabled` | bool | `true` | Usa debugpy/DAP real quando disponível. Desative para forçar execução simples sem breakpoints. |
| `debug_dap_port` | int | `5678` | Porta local usada pelo `debugpy` para a conexão DAP. |

> **Dica:** se você também usa o VS Code (ou outro editor com depurador Python) **aberto ao mesmo tempo** no mesmo projeto, veja a seção de conflito de porta abaixo - é só mudar `debug_dap_port` no Codis e o problema desaparece.

---

## Limitações atuais

- **Suporte real (DAP completo) apenas para Python.** Outras linguagens executam no painel de forma simples (saída capturada do processo), sem breakpoints/stepping reais.
- Cada sessão de debug depura **um arquivo por vez** - multi-threading/multi-processo do programa depurado funciona, mas múltiplas sessões simultâneas do Codis não são suportadas.
- Breakpoints condicionais, breakpoints de função e exception breakpoints ainda não têm UI dedicada no painel.
- Depuração remota (em outra máquina) é um recurso separado - veja [SSH Remoto → Debug Remoto](remote-ssh.md#debug-remoto-dap-via-túnel-ssh), que usa um mecanismo de túnel independente deste painel local.

---

## Resolução de problemas

### "Nenhuma sessão de depuração ativa" ao clicar em Step/Continue

A sessão anterior já terminou (processo encerrado ou rodou até o fim sem breakpoints). Clique em **▶ Run** para iniciar uma nova sessão.

### O programa roda até o fim e nada para

Você não marcou nenhum breakpoint antes de clicar em Run. Clique na margem esquerda do editor, na linha desejada, **antes** de iniciar a depuração.

### Erro ao iniciar / a porta já está em uso

Normalmente significa que outro processo já está escutando na porta configurada (`debug_dap_port`, padrão `5678`) - o caso mais comum é **ter o VS Code aberto ao mesmo tempo** depurando Python no mesmo projeto (veja a seção dedicada abaixo). Mude `debug_dap_port` para outro valor (ex: `5690`) em **Configurações → Avançado** e tente novamente.

### Uso simultâneo com VS Code (ou outro IDE) causa conflito

O Codis e a extensão Python Debugger do VS Code usam, **por padrão, exatamente a mesma porta** (`5678`) para o `debugpy`, porque ambos seguem a mesma convenção da ferramenta. Se os dois estiverem abertos e tentando depurar Python ao mesmo tempo, um deles vai falhar ao abrir a porta, ou o cliente errado pode acabar conectando à sessão do outro.

Isso não é um bug de incompatibilidade entre os editores - é só os dois competindo pelo mesmo número de porta nos bastidores. A solução é simples:

1. Abra **Configurações → Avançado** no Codis (ou `Ctrl+,`);
2. Mude `debug_dap_port` para um valor diferente do que o VS Code usa (ex: `5690`, `15678`, qualquer porta livre);
3. Pronto - as duas ferramentas podem depurar ao mesmo tempo, sem conflito.

> Isso só é necessário se você realmente for depurar Python **nos dois editores ao mesmo tempo**, no mesmo projeto. Usar só um dos dois por vez nunca tem esse problema.

### `debugpy não encontrado`

Instale com `pip install debugpy` no mesmo ambiente/interpretador Python usado pelo Codis para executar seus scripts (verifique se é o mesmo `python`/`venv` que aparece na barra de status).

### A saída do programa (`print`) não aparece no console

Se isso acontecer, confirme que está usando uma versão atualizada do Codis - versões antigas tinham um bug em que a saída do processo depurado não era redirecionada de volta corretamente. Atualizar resolve.

---

Ver também: [Recursos Avançados](recursos-avancados.md) · [Configurações](configuracoes.md) · [SSH Remoto](remote-ssh.md)
