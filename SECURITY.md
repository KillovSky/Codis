# 🔐 Política de Segurança - Codis

Este documento descreve como reportar vulnerabilidades de segurança e como o projeto as trata.

---

## Versões Suportadas

| Versão | Suporte de Segurança |
|---|---|
| `1.0.0` | ✅ Ativa - patches enviados assim que possível |
| Forks, distribuições não oficiais, versões modificadas | ❌ Fora do escopo deste repositório |

---

## Como Reportar uma Vulnerabilidade

> ⚠️ **Não abra issues públicas para vulnerabilidades de segurança.**  
> Issues públicas expõem usuários antes que haja uma correção disponível.

### Canal preferencial

Use o **GitHub Private Security Advisories**:

1. Acesse [Security → Advisories](https://github.com/KillovSky/Codis/security/advisories)
2. Clique em **"Report a vulnerability"**
3. Preencha o formulário - a conversa fica privada entre você e o maintainer

### Alternativa

Contato direto pelos links no perfil [@KillovSky](https://github.com/KillovSky).

---

## O que Incluir no Relatório

| Campo | Descrição |
|---|---|
| **Descrição** | Qual é a vulnerabilidade e como ela funciona |
| **Impacto** | O que um atacante pode fazer com ela |
| **Passos de reprodução** | Sequência mínima e reproduzível |
| **Versão afetada** | Versão do Codis e do Python |
| **Prova de conceito** | Código, script ou screenshot (se possível) |
| **Correção sugerida** | Opcional, mas muito bem-vinda |

---

## Escopo

### ✅ Em escopo

- Execução de código arbitrário via plugins sem confirmação do usuário
- Exfiltração de dados - credentials, tokens, chaves de API armazenadas
- Corrupção de arquivos do usuário causada por bug no editor
- Path traversal / directory traversal em operações de arquivo
- Bypass do sistema de confiança de plugins (`trusted_plugins.json`)
- Injeção de comandos via inputs do usuário que chegam a `subprocess`
- Credenciais Git expostas em logs, estado compartilhado ou transmissões inseguras
- XSS ou injeção no render de Markdown/HTML interno
- Credenciais de banco de dados expostas via SQL Studio

### ❌ Fora do escopo

- Bugs funcionais sem impacto de segurança → [Issue normal](https://github.com/KillovSky/Codis/issues/new?template=bug_report.yml)
- Vulnerabilidades em dependências externas → reporte diretamente a elas
- Problemas em forks e distribuições de terceiros
- Vulnerabilidades que exigem acesso físico ao dispositivo do usuário
- Ataques de engenharia social fora do código

---

## Processo de Resposta

| Etapa | Prazo estimado |
|---|---|
| Confirmação de recebimento | ~1 semana |
| Avaliação inicial de severidade | ~5 dias após confirmação |
| Desenvolvimento de correção | Depende da complexidade |
| Lançamento do patch | Após validação |
| Publicação do advisory | Após o patch estar disponível |

> O projeto é mantido por uma única pessoa no tempo livre disponível. Os prazos são estimativas de boa-fé.

---

## Modelo de Segurança do Sistema de Plugins

O Codis executa código Python de plugins instalados pelo usuário. Isso é **intencional** - plugins são extensões Python nativas com acesso ao processo.

### Mitigações implementadas

- **Análise estática na instalação** - o `PluginLoader` executa análise AST de importações e chamadas suspeitas antes de ativar um plugin novo
- **Sistema de confiança** - `trusted_plugins.json` registra plugins verificados; plugins não listados passam por confirmação do usuário antes de carregar
- **Sandboxing de API** - a `PluginAPI` expõe apenas as interfaces necessárias; plugins não têm acesso direto a `ConfigManager`, credenciais Git ou providers de IA por padrão
- **Escrita atômica** - saves usam `tmp + rename`; plugins não podem corromper arquivos via race condition na API exposta

### Modelo de segurança do SQL Studio

- Senhas de conexão são armazenadas com codificação base64 em `~/.codis/sql_connections.json` - proteção básica contra exposição acidental em logs, não criptografia robusta
- Conexões SQLite usam `check_same_thread=False` com lock explícito para serialização de acesso concorrente
- Credenciais nunca aparecem em logs ou na interface

### Limitação conhecida (por design)

O sistema de confiança de plugins **não é uma sandbox completa**. Um plugin Python malicioso com permissão do usuário pode fazer qualquer coisa que Python pode fazer. **Instale apenas plugins de fontes confiáveis.**

Veja [wiki/plugins.md](wiki/plugins.md) para detalhes sobre o modelo de segurança e as APIs disponíveis.

---

## Créditos

Pesquisadores que reportarem vulnerabilidades válidas de forma responsável serão creditados nas notas de release (com permissão) como **Security Researchers**.

---

## Referências

- [OWASP - Secure Coding Practices](https://owasp.org/www-project-secure-coding-practices-quick-reference-guide/)
- [Python Security - subprocess](https://docs.python.org/3/library/subprocess.html#security-considerations)
- [GitHub - Private Security Advisories](https://docs.github.com/en/code-security/security-advisories/working-with-repository-security-advisories/creating-a-repository-security-advisory)
