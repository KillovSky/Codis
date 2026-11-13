# 📅 Utilitários Pessoais

O Codis embute três utilitários de produtividade pessoal que não dependem de projeto ou workspace aberto: **Calendário**, **Calculadora** e **Relógio**. Os três abrem como janelas independentes (`Toplevel`) e podem ficar abertos em paralelo ao editor.

Acesso: menu **Ferramentas → Calendário & Pessoal**.

---

## 📅 Calendário (`src/ui/calendar_panel.py`)

Atalho de menu: **Ferramentas → Calendário & Pessoal → 📅 Calendário**.

### Funcionalidades

- Grade mensal navegável com visual polido e indicador do dia atual
- Mini-mapa de eventos: dias com eventos marcados ficam destacados no grid
- Anotações e lembretes associados a uma data específica
- Lista lateral de eventos com horário, cor e descrição
- Pesquisa de eventos por texto
- Lembretes com notificação popup quando a hora chega

### Fluxo de uso

1. Abra o calendário pelo menu (janela de 900×640, redimensionável)
2. Navegue entre meses pelos controles de seta no topo do grid
3. Clique em um dia para ver/criar eventos daquela data
4. Cada evento pode ter horário, cor de destaque e descrição livre
5. Use a busca para localizar eventos por palavra-chave em qualquer mês
6. Eventos com lembrete disparam um popup de notificação no horário configurado

A persistência dos eventos é local (não há sincronização em nuvem).

---

## 🔢 Calculadora (`src/ui/calculator_panel.py`)

Atalho: **Ferramentas → Calendário & Pessoal → 🔢 Calculadora** · `Ctrl+Alt+C`.

### Modos disponíveis

| Modo | O que oferece |
|------|---------------|
| **Básico** | Operações aritméticas comuns com display grande |
| **Científico** | Funções trigonométricas, logarítmicas, potências, constantes (π, e) |
| **Conversão de unidades** | Comprimento, peso e temperatura |

### Funcionalidades

- Display elegante com histórico de expressão e resultado lado a lado
- Histórico persistente de cálculos (salvo em `calc_history.json` na pasta de configuração do usuário), navegável com `↑`/`↓` no campo de expressão
- Anotações livres associadas a cada entrada do histórico
- Campo de entrada aceita **expressões Python completas** (não apenas a sintaxe da calculadora), avaliadas em um sandbox restrito de builtins seguros (`abs`, `round`, funções de `math`, etc.) - nenhuma chamada arbitrária do Python é permitida
- Clique duplo em uma entrada do histórico para reutilizá-la

### Sandbox de avaliação

A calculadora **não** usa `eval()` aberto. Apenas um conjunto fixo de builtins matemáticos seguros é exposto ao avaliador, então não é possível executar código arbitrário do sistema através do campo de expressão.

---

## 🕐 Relógio, Alarmes e Cronômetro (`src/ui/clock_panel.py`)

Atalho: **Ferramentas → Calendário & Pessoal → 🕐 Relógio** · `Ctrl+Alt+K`.

A janela do relógio tem três abas:

| Aba | Conteúdo |
|-----|---------|
| **Relógio** | Relógio analógico desenhado em `Canvas` com ponteiros suaves + relógio digital com fuso horário configurável |
| **Alarmes** | Múltiplos alarmes com rótulo próprio e som opcional, criados/editados em diálogo dedicado |
| **Cronômetro** | Start/stop/reset com registro de voltas (*laps*) e exportação dos tempos |

### Alarmes

- Cada alarme tem horário, rótulo de texto e um som opcional associado
- Vários alarmes podem coexistir e são listados na própria aba
- O diálogo de criação/edição de alarme é uma janela própria (`_AlarmDialog`)

### Cronômetro

- Registra voltas individuais durante a contagem
- Os tempos de volta podem ser exportados para análise externa
- Independente do relógio principal - pode rodar em paralelo com o relógio analógico/digital visível

---

## Relação com o menu

Os três utilitários ficam agrupados na categoria **Calendário & Pessoal** dentro do menu **Ferramentas**, separados deliberadamente dos utilitários de desenvolvimento (Diff, Formatador, Regex etc.) e das ferramentas de sistema (Pip, Processos), já que não têm relação com o código do projeto aberto - ver [Atalhos](atalhos.md) para a lista completa de combinações de teclado.
