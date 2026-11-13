# ♿ Acessibilidade

O Codis oferece um sistema de acessibilidade modular e totalmente opcional. Nenhuma funcionalidade afeta a experiência padrão enquanto não for explicitamente ativada.

---

## Como Ativar

Acesse pelo menu **Ferramentas → Remoto & Colaboração → Acessibilidade…**, pelo atalho `Ctrl+Shift+U`, pelo ícone ♿ na barra de atividades (visível por padrão, já que `a11y_screen_reader` é `true` por padrão em `config.json`), ou via linha de comando:

```bash
python run.py --a11y
python run.py --accessibility
```

---

## Modos Disponíveis

### Alto Contraste

Aplica uma paleta de cores com fundo preto, texto branco e bordas nítidas, seguindo as diretrizes WCAG AA. Ideal para ambientes com luz intensa ou baixa acuidade visual.

A ativação é persistida entre sessões no arquivo de configuração. Para desativar, basta desmarcar a opção no painel ou usar o mesmo atalho.

### Modos para Daltonismo

Quatro paletas de cores otimizadas para diferentes tipos de daltonismo:

| Modo | Deficiência | Estratégia |
|---|---|---|
| Protanopia | Dificuldade com vermelho | Azul no lugar de vermelho, laranja para avisos |
| Deuteranopia | Dificuldade com verde | Laranja-vermelho, amarelo-ouro, azul |
| Tritanopia | Dificuldade com azul/amarelo | Vermelho puro, teal, laranja |
| Acromasia | Sem percepção de cor | Escala de cinza por luminosidade |

As paletas substituem as cores semânticas (erro, aviso, sucesso, info) por alternativas perceptivelmente distintas dentro da limitação de cada condição.

---

## Leitor de Tela / TTS

O Codis detecta automaticamente o motor de síntese de voz disponível no sistema:

| Sistema | Motor usado |
|---|---|
| Linux | `spd-say` (Speech Dispatcher), `espeak` |
| macOS | `say` (nativo) |
| Windows | SAPI via PowerShell |

Quando um motor é detectado, os widgets anotados anunciam automaticamente seu rótulo e atalho ao receber foco ou ao passar o mouse sobre eles (`<Enter>`). Use o botão **Testar leitor de tela** no painel para verificar o funcionamento.

Para anotar widgets customizados em plugins:

```python
# No activate(api) do plugin
api.annotate_widget(meu_botao,
                    role="button",
                    label="Executar script",
                    keyshortcut="F5")
```

---

## Navegação por Teclado

Toda a interface pode ser operada sem mouse:

| Tecla | Ação |
|---|---|
| `Tab` | Próximo widget no grupo ativo |
| `Shift+Tab` | Widget anterior |
| `Enter` / `Space` | Ativar elemento focado |
| `Escape` | Fechar modal / sair do grupo |
| Setas | Navegação contextual (listas, árvores) |

O gerenciador de navegação é inicializado automaticamente. Plugins podem registrar grupos de navegação customizados:

```python
api.register_nav_group("meu_painel", [btn1, btn2, entry1])
```

---

## Configuração Persistente

As preferências são salvas automaticamente em `~/.config/codis/config.json`:

```json
{
  "a11y_high_contrast": false,
  "a11y_colorblind_mode": "deuteranopia"
}
```

---

## Suporte a Voz (experimental)

Navegação por comandos de voz requer `speech_recognition` e um microfone:

```bash
pip install SpeechRecognition
```

Quando disponível, o botão **🎙 Ativar voz** aparece no painel de acessibilidade. Comandos básicos: "abrir arquivo", "fechar", "desfazer", "salvar".

---

## Contribuindo com Acessibilidade

Se você usa tecnologia assistiva e encontrou problemas de acessibilidade no Codis, abra uma issue com a tag `a11y`. Descreva:

- O modo de acessibilidade utilizado
- O sistema operacional e motor TTS
- O comportamento esperado vs. o observado
- Se possível, um log do painel de Output

Contribuições de paletas para daltonismo adicional (ex: tritanopia melhorada para temas claros) são muito bem-vindas via Pull Request.

---

## Navegação e Motor

### Navegação Aprimorada por Teclado

Com `a11y_keyboard_nav: true` (padrão), o Codis registra grupos de widgets para navegação por **Tab / Shift+Tab** e **setas direcionais** em painéis, listas e árvores.

### Fonte de UI Grande

Com `a11y_large_ui_font: true`, força a fonte de interface para no mínimo **13pt**, melhorando legibilidade em monitores de baixa resolução ou com zoom reduzido.

### Reduzir Movimento

Com `a11y_reduce_motion: true`, desabilita animações, transições e efeitos visuais (piscar, fade, deslizamentos). Útil para usuários com sensibilidade a movimento ou epilepsia fotossensível.

---

## Configurações

Acesse em **Configurações → Acessibilidade** (ou `Ctrl+,` → categoria Acessibilidade):

| Chave | Padrão | Descrição |
|---|---|---|
| `a11y_high_contrast` | `False` | Alto contraste (WCAG AA) |
| `a11y_colorblind_mode` | `""` | Modo daltonismo: protanopia / deuteranopia / tritanopia / achromatopsia |
| `a11y_screen_reader` | `True` | Habilita anúncios via TTS/leitor de tela |
| `a11y_keyboard_nav` | `True` | Navegação aprimorada por Tab e setas |
| `a11y_large_ui_font` | `False` | Fonte de UI mínima de 13pt |
| `a11y_reduce_motion` | `False` | Desativa animações e efeitos visuais |
