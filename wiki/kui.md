# ✦ K-UI - K-UI (Holográfica/Cósmica)

O **K-UI** (também chamado de K-UI) é uma engine de interface visual opcional que transforma o Codis em uma experiência cinematográfica com nebulosas, auroras, partículas quânticas e uma estética sci-fi imersiva, inspirada em Tron Legacy, Iron Man HUD e Cyberpunk 2077.

---

## Ativando o K-UI

| Método | Ação |
|---|---|
| `Ctrl+Alt+N` | Ativa/desativa o K-UI |
| Menu **Ferramentas → ✦ K-UI (toggle)** | Ativa/desativa |
| Paleta de Comandos (`Ctrl+Shift+P`) → "K-UI" | Ativa |

O estado é persistido em `config.json` (`kui_enabled: true/false`, padrão `false`). Ao reiniciar com `kui_enabled: true`, o K-UI reativa automaticamente após 300 ms.

> **Nota:** K-UI e Vim Mode são mutuamente exclusivos. Ativar um desativa o outro.

---

## O que Muda com o K-UI Ativo

### Visual

- **Fundo cósmico** - nebulosas e auroras renderizadas em thread separada via PIL, animadas continuamente
- **Partículas quânticas** - flutuantes e reativas à velocidade de digitação
- **Sistema solar de arquivos** - cada arquivo aberto recente orbita como planeta na tela de boas-vindas
- **Buracos negros** - crescem com a quantidade de erros de lint, exercem gravidade sobre meteoros e estrelas
- **Wormholes** - portais quânticos que teletransportam partículas entre dois pontos do espaço
- **Cometas** - cauda de plasma longa cruzando a tela em intervalos
- **Satélites** - objetos lentos atravessando o cosmos com painéis solares
- **Nascimento estelar** - nebulosas protoestelares surgindo no cosmos
- **Sash holográfico** - divisores do `PanedWindow` redesenhados com efeito de brilho

### Componentes Substituídos

| Componente clássico | Componente K-UI |
|---|---|
| Tab Bar (Tkinter Label) | `KTabBar` holográfica com indicadores de atividade |
| Top Bar (menu/toolbar) | `KTopBar` com glassmorphism |
| Status Bar | `KStatusBar` com displays digitais |
| Welcome Screen | `KWelcome` cósmica com sistema solar |

---

## Spotlight (Paleta de Comandos Holográfica)

Com o K-UI ativo, `Ctrl+Space` abre o **Spotlight** - paleta de comandos redesenhada com:

- Interface holográfica com glassmorphism
- Busca fuzzy em tempo real em 100+ comandos
- Ícones por categoria e animação de entrada/saída suave

**Categorias disponíveis:** Arquivo · Editar · Executar/Formatar · Exibir/Temas · Navegação (linha, símbolo) · Painéis · Ferramentas · Modos especiais · LSP · Painéis flutuantes

---

## Painéis Flutuantes (Detach)

No modo K-UI, qualquer aba do painel inferior pode ser "despluggada" para uma janela independente:

1. **Botão `⊞`** na barra de abas do painel inferior
2. **Menu de contexto** - clique direito em qualquer aba → "⊞ Despluggar 'nome'"
3. **Spotlight** - busque "Despluggar" + nome do painel

Painéis que podem ser despluggados: `terminal` · `output` · `git` · `debug` · `regex` · `problems`

Para devolver, clique direito na aba → "⊟ Reancoxar 'nome'".

---

## Perfis de Performance

Ajustáveis via **Ferramentas → K-UI: Performance** ou Spotlight → "Perf":

| Perfil | Efeitos | FPS | CPU |
|---|---|---|---|
| `quantum` (alias `ultra_plus`) | Tudo: física completa, subatômico, screensaver | 120 | Muito alto |
| `ultra` | Alto: nebulosas + partículas + aurora | 60 | Alto |
| `high` | Médio-alto: sem subatômico | 45 | Médio-alto |
| `balanced` | Padrão: nebulosas + partículas básicas, anéis, scan | 30 | Médio |
| `windows` (alias `power_saver`) | Reduzido: sem efeito de scan, otimizado para Windows | 30 | Médio-baixo |
| `low` (alias `minimal`) | Econômico: ainda com partículas, meteoros e sistema solar, mas sem anéis/scan/buracos negros | 24 | Baixo |

> Apesar do nome, `minimal` **não** remove todas as animações - é um alias exato de `low`, que ainda exibe partículas, meteoros e o sistema solar de arquivos, só com volume reduzido (30 estrelas, 12 partículas) e sem os efeitos mais pesados (anéis, scan, buracos negros, subatômico).

Os perfis `quantum` e `ultra` exibem confirmação antes de ativar. O perfil é salvo em `config.json` (`kui_perf`) e pode ser alterado em tempo real.

---

## Easter Eggs

O K-UI possui easter eggs ativados por sequências de teclado digitadas no terminal integrado (`/codis <código>`):

| Código | Efeito |
|---|---|
| `konami` | Explosão de partículas Konami |
| `nova` | Supernova + chuva de meteoros |
| `alien` | Naves alienígenas invadem o cosmos |
| `nier` | Artefatos da humanidade flutuam no vazio |
| `glitch` | Glitch cósmico máximo |
| `freeze` | Congela a animação (modo debug) |
| `reset` | Big Bang - reseta todo o cosmos |
| `horror` | Visões subliminares cósmicas |
| `space` | Nascimento estelar em massa |
| `codis` | Efeito especial do Codis |

---

## KHoverTooltip - Painel de Aprendizado Cósmico

Ao clicar em elementos da interface K-UI, um tooltip contextual (`KHoverTooltip`) exibe fatos de física quântica e cosmologia em 14 idiomas. Ativo no perfil `balanced` ou superior.

Os fatos cobrem: emaranhamento quântico, singularidades, dilatação temporal, nebulosas, tunelamento, dualidade onda-partícula, Higgs, matéria escura, energia escura, evaporação de buracos negros, wormholes, estrelas de nêutrons, fundo cósmico de micro-ondas, multiverso, espaguetificação, relatividade e constante de Planck.

---

## Integração com Temas

O K-UI usa sua própria paleta de cores (`_pal()`) que **ignora propositalmente** os atributos do tema ativo para garantir a estética cósmica:

- `void = #020209` (dark) ou `#0d0d2b` (light)
- `text_bg`, `text_fg`, `text_cur`, `text_sel` são cores otimizadas para legibilidade sobre o void

Ao trocar o tema com o K-UI ativo, `apply_theme()` reconstrói a paleta e reaplicar os efeitos visuais automaticamente.

---

## Compatibilidade

O K-UI é completamente **opcional e não-destrutivo**: desativá-lo restaura a interface clássica exatamente como estava, incluindo workspace e arquivos abertos.

**Não compatível com:**
- Vim Mode - ativar um desativa o outro automaticamente
- Sistemas sem suporte a transparência nativa de janela

**PIL/Pillow** é necessário para renderizar nebulosas em background. Sem ele, o K-UI usa um fundo de gradiente puro como fallback.

---

## Referências Técnicas

| Arquivo | Responsabilidade |
|---|---|
| `src/ui/k_ui/_engine.py` | `KEngine` + `KWelcome` - motor principal de animação |
| `src/ui/k_ui/_colors.py` | Paletas, interpolação de cor, utilitários de widget |
| `src/ui/k_ui/_perf.py` | Definição dos 6 perfis de performance |
| `src/ui/k_ui/_easter.py` | Detecção de sequências e disparo de easter eggs |
| `src/ui/k_ui/_bgrenderer.py` | Renderização PIL de nebulosas/auroras em thread separada |
| `src/ui/k_ui/_tooltip.py` | `KHoverTooltip` - painel de aprendizado contextual |
| `src/ui/k_ui/_widgets.py` | `KGlassPane`, `KTopBar`, `KTabBar`, `KStatusBar`, `KPortal` |
| `src/ui/k_ui/__init__.py` | `KUI` - controlador principal e re-exports |

## Barra lateral (K-UI Dock)

No modo K-UI a barra lateral (Explorador, Busca, Git, etc.) é um painel **flutuante** sobre o conteúdo - a mesma sidebar do modo clássico, sem duplicação.

| Ação | Como |
|------|------|
| Abrir/fechar | `Ctrl+E`, ícones da barra de atividades ou botão ▶/◀ |
| Fechar | `✕`, clique fora do painel ou clicar de novo no ícone ativo |
| Manter aberto | `◇` no cabeçalho (vira `◆`); o painel só fecha pelo `✕` |
| Redimensionar | arraste a borda direita (240-520 px) |

O painel permanece acima do editor, da tela inicial e do **modo cosmos**. Abrir um arquivo pelo Explorador em modo cosmos volta ao editor automaticamente. Ao desativar o K-UI a sidebar volta ao layout clássico mantendo o estado (aberta/fechada).

## Cosmos vivo (interações)

Os fenômenos do cosmos não vivem mais isolados: uma camada de mundo (`_world.py`) roda antes de cada frame e faz com que eles se influenciem.

| Causa | Efeito |
|-------|--------|
| Sol, buracos negros, binárias e pulsares | Gravidade: cometas ganham órbitas e estilingues; poeira cai nos buracos negros |
| Cometa se aproxima do Sol | Cauda aponta para longe do Sol e cresce; perto demais, sublima e se desintegra |
| Impacto no Sol ou num planeta (arquivo) | Faíscas, anel de choque, fragmentos; no Sol, uma erupção |
| Buraco negro engole algo | Cresce, espirais de acreção; após vários, emite uma onda gravitacional |
| Supernova | Onda de choque empurra poeira, detritos, cometas e nuvens; estrelas cintilam; aliens fogem |
| Fim da supernova | Deixa um pulsar ou uma nebulosa; a nebulosa comprimida forma uma nova estrela |
| Feixe de pulsar cruza um alvo | Ilumina planetas, cometas e satélites; aliens "recebem o sinal" |
| Mouse | Arrastar leva a poeira; parado vira um poço (vagalumes); clique cria uma ondulação; aliens curiosos se aproximam se você está calmo |
| Digitar | Vento solar: faíscas saem do Sol; ritmo alto aumenta erupções |
| Avisos e erros do editor | Erupções mais frequentes e tempestades solares (ondas de choque) |
| Salvar arquivo | Nova solar: onda suave que acalma o cosmos |
| Abrir arquivo | Um cometa chega, mirando o Sol |

O movimento é baseado em tempo (não em frames): a velocidade se mantém mesmo quando um frame atrasa. A camada se desliga sozinha, sem afetar a animação, se falhar repetidamente.

## Solução de problemas

**A interface está lenta ou travada.** Rode com o diagnóstico de travamentos:

```
CODIS_STALL_TRACE=1 python run.py          # Linux/macOS
$env:CODIS_STALL_TRACE=1; python run.py    # Windows (PowerShell)
```

Quando o laço principal fica parado por mais de 1,5 s (ajustável com `CODIS_STALL_MS`), o log `Codis.Stall` mostra a pilha do que estava bloqueando, e as fases de inicialização aparecem com seus tempos. Em telas muito grandes, o perfil `power_saver` ou `low` reduz ainda mais o custo do cosmos.

