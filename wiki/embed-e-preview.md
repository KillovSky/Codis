# Incorporar programas e Preview Web

## 1. Incorporar qualquer programa (`/codis app`)
No terminal do Codis:

| Comando | O que faz |
|---|---|
| `/codis app` | Abre o seletor: **Adotar janela em execução** (lista tipo Gerenciador de Tarefas, filtro, campo "nome do .exe") ou **Iniciar programa** (caminho + argumentos, com recentes). |
| `/codis app notepad.exe` | Adota a janela do processo `notepad.exe` já aberto (inclui processos filhos). |
| `/codis app C:\caminho\prog.exe` | Inicia e incorpora. |

* **Adotado** = o programa continua rodando; ao devolver (botão ou `Ctrl+Shift+F12`) a janela volta ao desktop no lugar original. Se o programa fechar a própria janela, o painel fecha.
* **Iniciado** = ao fechar o painel o programa é encerrado (como o DOOM, veja [doom.md](doom.md)).
* Limites do Windows: programas **como administrador** (UIPI), apps da **Microsoft Store/UWP** e jogos em **tela cheia exclusiva** recusam o reparentamento; o Codis mostra o motivo.
* Código: `src/ui/app_embed.py` (+ `window_embed.py`: `list_windows`, `find_window_in`, `detach(restore=True)`).

## 2. Preview Web (navegador real embutido)
`Ferramentas > Sistema > Preview Web` (`Ctrl+Alt+Y`) ou botão **🚀 Preview Web** no painel Preview.

* Usa **Edge / Chrome / Brave / Chromium já instalados** (nenhuma API key, nenhum download). HTML, CSS e JS completos (grid, módulos ES, fetch, WebGL…); **F12** abre o DevTools.
* Servidor local `127.0.0.1` com **live reload**: CSS troca sem recarregar; o que você digita **sem salvar** já aparece.
* Markdown e SVG também; segue a aba ativa (`web_preview_follow`).
* Console do navegador dentro do Codis (erros JS, `console.log`, recursos 404) com clique para abrir `arquivo:linha`.
* Emulação de dispositivo (desktop/tablet/mobile), URL/voltar/avançar/recarregar, divisão 1/3, 1/2, 2/3 ou tela toda.
* Sem Chromium (ou macOS): abre no navegador padrão, ainda com live reload.
* Config: `web_browser_path`, `web_extra_args`, `web_preview_split`, `web_preview_follow`.
* Segurança: servidor só em loopback, bloqueia path traversal e `Host` estranho (DNS rebinding); a porta de depuração do navegador é loopback, efêmera e existe só enquanto o painel está aberto.

## 3. Startup
* `CODIS_STARTUP_PROFILE=1 python run.py` imprime o tempo de cada fase.
* A localização de executáveis usa um índice único do PATH (`src/_shells.py`), preguiçoso e pré-aquecido em thread.
* Painéis raros da sidebar são criados só no primeiro uso (`src/ui/lazy_panel.py`); `tkinterweb` só é importado quando necessário.
