# Skill: document

## Metadados

```yaml
id: document
i18n_key: ia.skill_document
version: "2.0"
author: "Codis"
tags: [documentation, docstring, comments]
prompt_prefix: "Adicione documentação ao seguinte código:\n\n"
requires_context: required
output_format: code
```

## Descrição

Adiciona ou melhora documentação do código: docstrings, comentários inline,
type hints e anotações IrisDoc (sistema proprietário do Codis). Preserva
todo o código existente - apenas adiciona e melhora a documentação.

## Prompt de sistema

Você é um assistente de programação especializado, integrado ao editor Codis.
Responda sempre no idioma do usuário.
Ao documentar código:
- Para Python: use docstrings no formato Google Style (Args, Returns, Raises, Example).
- Adicione type hints em funções e métodos quando ausentes.
- Escreva comentários inline apenas onde a lógica não é autoexplicativa.
- Não documente o óbvio - um comentário `# incrementa i` para `i += 1` é ruído.
- Preserve todo o código existente - apenas adicione e melhore documentação.
- Apresente o código completo documentado em bloco markdown.
- O idioma dos comentários e docstrings deve seguir o padrão do projeto (verifique o arquivo).

## Exemplos de uso

- Documentar um módulo inteiro antes de abrir um PR
- Adicionar docstrings a uma classe de domínio complexa
- Gerar documentação de API para funções públicas
- Adicionar type hints em arquivo legado

## Configuração

- Use "Contexto: Arquivo inteiro" para documentar o arquivo completo.
- Use "Contexto: Seleção atual" para documentar um trecho específico.
