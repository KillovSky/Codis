# 🎨 Editor de Imagens (Paint)

Editor de imagens integrado, inspirado no Paint clássico. Funciona embutido no Codis ou standalone.

**Arquitetura** (separada para ser testável): `src/ui/paint_engine.py` (motor, sem Tk) · `src/ui/paint_canvas.py` (canvas) · `src/ui/paint_panel.py` (barra de ferramentas, paleta, arquivos).

## Abrindo
- Menu **Ferramentas → Editor de Imagens**, ou duplo clique em `.png/.jpg/.bmp/.gif/.webp` na árvore de arquivos.
- Linha de comando: `python run.py --paint [imagem.png]`

## Ferramentas
| Ferramenta | Comportamento |
|---|---|
| Pincel | Antialiasing real; a opacidade **não acumula** dentro do mesmo traço |
| Pixel | Traço sem suavização (pixel art) |
| Spray | Aerógrafo acumulativo |
| Borracha | Apaga com a **cor de fundo** (botão direito: cor primária) |
| Balde | Preenchimento com tolerância; **Ctrl+clique** preenche todas as áreas parecidas (não só a conectada) |
| Linha / Retângulo / Elipse | Com antialiasing; **Shift** = 45° / quadrado / círculo; "Preencher" preenche |
| Polígono | Clique adiciona pontos; duplo clique/Enter fecha; Esc cancela |
| Texto | Fonte do sistema (DejaVu/Arial/…) com antialiasing |
| Conta-gotas | Botão esquerdo = cor primária, direito = secundária |
| Seleção | Retângulo; arraste de dentro para **mover** (deixa a cor de fundo no lugar); Ctrl+C/X/V, Delete, Ctrl+A |
| Pan (mão) | Arrasta a imagem |

## Atalhos
| Atalho | Ação |
|---|---|
| Ctrl+Z / Ctrl+Y (ou Ctrl+Shift+Z) | Desfazer / refazer |
| Ctrl+S / Ctrl+Shift+S / Ctrl+O / Ctrl+N | Salvar / salvar como / abrir / novo |
| Ctrl+C / Ctrl+X / Ctrl+V / Ctrl+A | Copiar / recortar / colar (aceita imagem da área de transferência do sistema, quando a plataforma permite) / selecionar tudo |
| Roda | Rola na vertical · **Shift+roda** horizontal · **Ctrl+roda** zoom no cursor |
| Botão do meio ou **Espaço**+arrastar | Pan |
| `+` / `-` / Ctrl+0 | Zoom / ajustar à janela |
| Esc | Cancela o traço, a seleção flutuante ou o polígono |

O zoom vai de 6% a 3200%; a grade de pixels aparece a partir de 800%. O título mostra **•** quando há alterações não salvas e **Novo/Abrir** perguntam antes de descartar.

## Desempenho (por que não trava mais)
Medido no mesmo script antes/depois (800×600 | 4000×3000):

| Operação | Antes | Depois |
|---|---|---|
| Evento de pincel | 22 ms \| 650 ms | 2 ms \| 1,6 ms |
| Evento de pincel com zoom 8× (grade) | 844 ms | ~1 ms |
| Balde numa imagem em branco | 1.180 ms \| minutos | 37 ms \| 560 ms |
| Zoom 8× em 4000×3000 | **estourava a memória** | 17 ms |
| Undo | 33 ms (+PNG inteiro por ação) | 8 ms (só blocos alterados) |

Como: renderiza **só o retângulo visível/sujo** (sem reescalar a imagem inteira nem criar milhares de itens de canvas para a grade/xadrez); eventos de mouse são coalescidos em um desenho por ciclo ocioso; o pincel acumula uma máscara e compõe só a caixa afetada; o balde trabalha por trechos horizontais; o histórico guarda blocos de 256×256 (limite de ~384 MB, 200 ações).

## Salvando
PNG/WebP preservam transparência. **JPEG/BMP achatam sobre branco** (antes ficava preto onde era transparente). A gravação é atômica (arquivo temporário + `replace`): uma falha não corrompe o original. Imagens com orientação EXIF são giradas ao abrir.

## Testes
`tests/test_paint_engine.py` (motor: undo contra snapshots, flood fill contra BFS de referência, renderização parcial == total, orçamentos de tempo) e `tests_gui/test_paint_canvas.py` (compara os pixels **exibidos** com o modelo).
