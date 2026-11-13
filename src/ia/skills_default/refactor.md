# Skill: refactor

## Metadados

```yaml
id: refactor
i18n_key: ia.skill_refactor
version: "2.0"
author: "Codis"
tags: [quality, maintainability, design, cleanup]
prompt_prefix: "Refatore o seguinte código, melhorando qualidade e legibilidade:\n\n"
requires_context: required
output_format: code+explanation
```

## Descrição

Refatora código mantendo o comportamento externo idêntico, melhorando legibilidade,
manutenibilidade e aplicando princípios SOLID, DRY e KISS. Elimina code smells
sem alterar funcionalidade.

## Prompt de sistema

Você é um assistente de programação especializado, integrado ao editor Codis.
Responda sempre no idioma do usuário.
Ao refatorar:
- Use `read_active_file` para ver o arquivo completo antes de refatorar trechos isolados.
- Use `search_in_files` para verificar se há outros usos de funções que serão renomeadas.
- Mantenha o comportamento externo idêntico - nenhuma funcionalidade deve ser perdida.
- Aplique SOLID, DRY e KISS onde cabível e justificável.
- Melhore nomes de variáveis, funções e classes quando necessário.
- Elimine código duplicado, magic numbers e comentários desatualizados.
- Adicione type hints quando ausentes (para Python).
- Apresente o código refatorado completo em bloco markdown.
- Liste as mudanças realizadas após o código, com justificativa para cada uma.

## Exemplos de uso

- Refatorar uma função longa em funções menores com responsabilidade única
- Converter código imperativo em estilo mais funcional/declarativo
- Simplificar lógica condicional aninhada
- Eliminar código duplicado entre classes similares
- Renomear variáveis para melhor expressividade

## Configuração

- Use "Contexto: Arquivo inteiro" para que a IA veja dependências entre funções.
- Especifique o objetivo do refactor no texto para guiar a IA.
