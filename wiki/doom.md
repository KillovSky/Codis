# DOOM embutido (`/codis doom`)

O DOOM roda como processo separado e **a janela nativa dele é reparentada** para dentro do editor
(`src/ui/window_embed.py` + `src/ui/doom_embed.py`).

| Plataforma | Mecanismo |
|---|---|
| Windows | `EnumWindows` (por PID) → `WS_CHILD` sem moldura → `SetParent` no HWND do frame |
| Linux/BSD (X11) | `_NET_WM_PID` → `XReparentWindow` no XID do frame (força `SDL_VIDEODRIVER=x11`) |
| macOS / Wayland puro | janela externa (fallback automático, com aviso) |

## Requisitos
1. **Motor**: gzdoom, uzdoom, crispy-doom, chocolate-doom, dsda-doom, woof ou prboom-plus (PATH, `~/.codis/doom/`,
   `<projeto>/doom/` ou pastas de instalação comuns no Windows).
2. **IWAD**: doom.wad, doom2.wad, freedoom1/2.wad, plutonia/tnt.wad ou doom1.wad (shareware). Procurado em
   `DOOMWADDIR`, `DOOMWADPATH`, pasta do motor, `~/.codis/doom/` e pastas padrão do Steam.

## Configuração (`~/.codis/config.json` ou variáveis de ambiente)
`doom_engine_path` / `CODIS_DOOM_ENGINE`, `doom_iwad_path` / `CODIS_DOOM_IWAD`, `doom_extra_args`.

## Uso
- **ESC** abre o menu do próprio DOOM.
- **Ctrl+Shift+F12** (global, funciona mesmo com o foco dentro do jogo) ou o botão **Voltar ao código** encerram.
- Se o jogo capturar o mouse, o atalho libera (`ClipCursor(NULL)` no Windows).
- Log do motor: `~/.codis/doom/last_run.log` (mostrado na mensagem se o motor falhar ao abrir).

## Testes
- Linux/X11 real (Xvfb + xterm como motor falso): `tests/manual/embed_x11_integration.py`.
- Win32: `tests/test_doom_embed.py` usa um `user32` falso que verifica a ordem exigida pela API
  (estilo `WS_CHILD` **antes** de `SetParent`, rollback em falha, restauração no `detach`).
  O caminho Win32 real só pode ser validado num Windows com gzdoom.
