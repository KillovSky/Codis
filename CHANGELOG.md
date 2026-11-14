# Changelog - Codis

Formato baseado em [Keep a Changelog](https://keepachangelog.com/pt-BR/1.0.0/),
seguindo o [Versionamento Semântico](https://semver.org/lang/pt-BR/).

## [1.0.1]

Correções de segurança apontadas pelo CodeQL e ajustes de CI.

### Adicionado

- **Documentação:** página [Previews](wiki/previews.md) com um tour visual do editor, painel por painel, e a pasta `docs/img/previews/` para as capturas (com checklist de nomes e o script `tools/check_previews.py` que lista as que faltam).

### Modificado

- **LSP:** o `clientInfo` enviado aos servidores passa a informar a versão real do Codis (`APP_VERSION`) em vez de um valor fixo.

### Corrigido

- **CI:** a análise CodeQL falhava na extração Python (`NotADirectoryError: 'run.py'`) porque `paths` do `codeql.yml` listava um arquivo; agora lista só diretórios (`src`).
- **CI:** a sincronização da wiki deixa de quebrar o workflow de documentação quando a GitHub Wiki ainda não foi inicializada; o passo é ignorado com um aviso explicando como criá-la.

### Segurança

- **Terminal:** expressões regulares das sequências DCS/SOS/PM/APC reescritas em tempo linear, eliminando o backtracking exponencial (ReDoS) com entrada maliciosa.
- **Chat:** conexões IRC, MQTT, XMPP e a sonda de servidor exigem no mínimo TLS 1.2.
- **Preview/Render:** `<script>`, `<style>` e `<head>` agora são removidos mesmo com fechamento do tipo `</script >`; o sticky scroll reconhece tags HTML em maiúsculas.
- **Zen Media:** a detecção de links do YouTube compara o host da URL em vez de uma substring (`evil.com/?youtube.com` não é mais aceito).
- **Navegador Remoto:** o cache local de arquivos remotos usa SHA-256 no lugar de SHA-1.

## [1.0.0]

Primeira versão pública e completamente floss.

### Adicionado

- **Editor:** 25 linguagens com highlight (48 reconhecidas por extensão), autocomplete e sugestões contextuais, snippets, Vim Mode, minimap, multi-cursor, seleção inteligente, code folding, LSP e DAP.
- **Interface:** 21 temas, K-UI (engine cósmica animada), 15+ painéis, Activity Bar responsiva, 14 idiomas.
- **IA:** Anthropic (Claude), OpenAI, OpenAI-compatível, Gemini, Ollama e GGUF local; modos Perguntar, Editar e Agente; skills, checkpoints e diff interativo.
- **SQL Studio:** SQLite, PostgreSQL, MySQL e DuckDB.
- **KDocs:** documentação nativa para 27 linguagens.
- **Git:** stage, commit, push/pull, branches, diff visual, blame inline e autenticação integrada.
- **Colaboração e chat:** edição colaborativa em tempo real; chat com IRC, Matrix, Slack, Discord, Mattermost, Telegram, XMPP, MQTT e WebSocket.
- **Navegador Remoto:** SSH/SFTP, FTP/FTPS e WebDAV.
- **Ferramentas:** terminal integrado, Paint, Preview Web, REST Client, Regex Playground, Gerenciador de Tarefas, lixeira por projeto.
- **Plugins:** API com eventos, sandbox estática e execução isolada em processo separado.
- **Distribuição:** empacotamento com PyInstaller para Linux, Windows e macOS e atualizador integrado.

## [0 -> 0.9]
- Inicio do projeto: 01/2025
- Betas fechados e desenvolvimento privado.