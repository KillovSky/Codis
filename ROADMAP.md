# Roadmap - Codis

Este documento descreve a direção de desenvolvimento do Codis. As prioridades são orientativas e podem mudar conforme o feedback da comunidade.

> **Nota:** Este projeto é mantido por uma pessoa em tempo livre. Não há datas fixas de entrega.

---

## Filosofia

O Codis segue alguns princípios que guiam o que entra no roadmap:

- **Python puro** - nenhuma dependência de Electron, Node.js ou runtimes externos
- **"Leve" por padrão** - recursos pesados são opcionais e não consomem recursos até serem ativados
- **Extensível** - qualquer funcionalidade que puder ser feita como plugin, deve ser feita como plugin primeiro
- **Acessível** - suporte a leitores de tela, navegação por teclado e modos de alto contraste são requisitos, não extras

---

## Estado Atual - v1.0.0

A versão 1.0.0 representa o editor completo como base sólida:

- Editor com 48 linguagens reconhecidas (25 com highlight de sintaxe), LSP, Git, Vim Mode, minimap, multi-cursor
- Assistente de IA multi-provedor (Anthropic, OpenAI, OpenAI-compatível, Gemini, Ollama, GGUF)
- SQL Studio integrado (SQLite, PostgreSQL, MySQL, DuckDB)
- K-UI holográfico com nebulosas, auroras e sistema solar de arquivos
- Sistema de plugins com sandbox estática e API de eventos
- KDocs - documentação nativa para 27 linguagens
- 21 temas built-in, 14 idiomas de interface, 29 modos standalone
- 1.683 testes automatizados, CI/CD completo

---

## Próximas Direções

As áreas abaixo estão no radar para versões futuras. A ordem não implica prioridade absoluta.
Muitas das funções abaixo já existem e funcionam no Codis, \
sendo sua inserção aqui um planejamento para adicionar ainda mais suporte a recursos e não uma nova feature.

### Editor e LSP

- Suporte a múltiplos language servers simultâneos por arquivo
- Code Actions via LSP (quick fixes, refactorings automáticos)
- Semantic tokens para highlighting baseado em semântica real
- Bracket pair colorização por profundidade de escopo

### Interface e UX

- Layouts salvos de painéis (workspaces de layout personalizados)
- Modo de foco por arquivo com ocultação automática de painéis
- Drag-and-drop de arquivos da árvore para o editor
- Pré-visualização de imagem inline no explorador de arquivos

### IA

- Histórico de conversas persistido por projeto
- Modo agente com execução de comandos supervisionada
- Embeddings locais para contexto semântico do projeto
- Suporte a mais provedores (Mistral, Cohere, DeepSeek)

### Git e Colaboração

- Git log visual com grafo de branches
- Cherry-pick e rebase interativo via interface gráfica
- Suporte a submódulos Git

### Plugins e Extensibilidade

- API de language server customizado via plugin
- Sistema de temas com herança e variáveis CSS-style
- Marketplace de plugins com instalação via UI

### Performance e Estabilidade

- Modo de edição de arquivos binários (hex editor básico)
- Suporte a arquivos muito grandes (>100K linhas) com virtualização de linhas
- Perfil de inicialização com carregamento lazy de painéis

---

## Como Influenciar o Roadmap

A melhor forma de influenciar o que entra no Codis é:

1. **Abrir uma Feature Request** - [→ Nova issue de feature](https://github.com/KillovSky/Codis/issues/new?template=feature_request.yml)
2. **Participar das Discussões** - [→ Discussions](https://github.com/KillovSky/Codis/discussions)
3. **Enviar um Pull Request** - veja o [Guia de Contribuição](CONTRIBUTING.md)
4. **Criar um plugin** - se a funcionalidade couber num plugin, é a via mais rápida

Features implementadas como plugins pela comunidade podem eventualmente ser promovidas ao núcleo do editor.

---

*Última atualização: v1.0.0*
