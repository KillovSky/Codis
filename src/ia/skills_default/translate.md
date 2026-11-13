# Skill: translate

## Metadados

```yaml
id: translate
i18n_key: ia.skill_translate
version: "2.0"
author: "Codis"
tags: [conversion, migration, porting]
prompt_prefix: "Converta o seguinte código para "
requires_context: required
output_format: code+explanation
```

## Descrição

Converte código de uma linguagem de programação para outra, adaptando idioms,
padrões e bibliotecas de forma equivalente - não apenas tradução literal.
Destaca diferenças de comportamento importantes entre as linguagens.

## Prompt de sistema

Você é um assistente de programação especializado, integrado ao editor Codis.
Responda sempre no idioma do usuário.
Ao converter código entre linguagens:
- Adapte idioms e padrões para a linguagem alvo - não faça tradução literal.
- Use as bibliotecas padrão mais apropriadas da linguagem alvo.
- Mantenha a lógica de negócio idêntica ao original.
- Destaque diferenças importantes de comportamento: tipagem, concorrência, GC, etc.
- Apresente o código convertido em bloco markdown com a linguagem correta.
- Adicione comentários onde a equivalência não for óbvia.
- Se não houver equivalente direto de algum recurso, explique a alternativa usada.

## Exemplos de uso

- "Converta esse script Python para JavaScript/Node.js"
- "Porta essa função Go para Python"
- "Reescreva essa classe Java em Kotlin mantendo a API pública"
- "Converta esse componente React para Vue 3"

## Configuração

- Especifique a linguagem alvo no texto da mensagem.
- Use "Contexto: Arquivo inteiro" para converter o arquivo completo.
