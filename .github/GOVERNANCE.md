# 🏛️ Governança do Projeto Codis

Este documento descreve como o Codis é mantido, como decisões são tomadas e como a comunidade pode participar.

---

## Estrutura de Mantenedores

### Mantenedor Principal

| GitHub | Papel |
|---|---|
| [@KillovSky](https://github.com/KillovSky) | Criador e mantenedor principal |

O mantenedor principal tem autoridade final sobre roadmap, arquitetura e inclusão de contribuições.

---

## Participação da Comunidade

A comunidade do Codis tem papel ativo nas decisões do projeto. Toda sugestão, crítica e contribuição é levada a sério - o projeto existe para servir quem o usa.

### Como a comunidade participa

- **Votação informal em Issues e Discussions**: reações 👍/👎 e comentários são considerados ao priorizar features e mudanças
- **Roadmap público**: o planejamento de versões futuras é discutido abertamente em Discussions com tag `[roadmap]`
- **Propostas de feature**: qualquer pessoa pode propor features - as mais votadas pela comunidade recebem prioridade
- **Revisão de PRs**: membros ativos da comunidade são encorajados a revisar Pull Requests e fornecer feedback técnico
- **Decisão de design**: para mudanças que afetam a experiência de uso, a preferência da comunidade é fortemente ponderada

### Aprovação final

O mantenedor principal (@KillovSky) mantém autoridade final sobre:
- Arquitetura e direção técnica do projeto
- Inclusão de dependências externas
- Mudanças na API de plugins
- Releases e versionamento

Essa estrutura garante consistência técnica enquanto mantém o projeto responsivo às necessidades reais de quem o usa.

---

## Processo de Tomada de Decisão

### Mudanças pequenas (bugs, docs, ajustes)

Aceitas diretamente via Pull Request após revisão. Critérios:
- Não quebra comportamento existente
- Segue o estilo do projeto
- Tem uma descrição clara do que muda e por quê

### Mudanças significativas (novas features, mudanças de API)

1. Abra uma **Issue** ou **Discussion** descrevendo a proposta
2. Aguarde feedback da comunidade e do mantenedor (mínimo 7 dias)
3. Features com forte suporte da comunidade (10+ reações positivas) recebem prioridade
4. Se aprovada, submeta o Pull Request referenciando a discussão

### Mudanças arquiteturais (RFC)

Para novos subsistemas, remoção de funcionalidades existentes ou mudanças que afetam a API de plugins:

1. Crie uma **Discussion** com prefixo `[RFC]` no título
2. Documente: motivação, design proposto, alternativas consideradas, impacto em plugins existentes
3. Período mínimo de comentários: **14 dias**
4. A comunidade pode propor alterações ao design durante o período
5. Decisão final é do mantenedor principal, considerando o feedback recebido

---

## Critérios de Aceitação de Pull Requests

### Obrigatório

- [ ] Python 3.12+ - use f-strings, type hints, dataclasses quando apropriado
- [ ] Thread safety: toda manipulação de widget Tkinter na main thread (via `root.after()`)
- [ ] `python -m py_compile` passa em todos os arquivos modificados
- [ ] Sem regressões nos testes existentes (`pytest tests/`)
- [ ] Descrição clara do que muda, por que muda e como testar

### Fortemente recomendado

- [ ] Type hints nas assinaturas públicas
- [ ] Sem dependências novas sem justificativa técnica sólida
- [ ] Wiki atualizada para mudanças que afetam o usuário
- [ ] CHANGELOG.md atualizado

### O que não aceitamos

- Código gerado automaticamente sem revisão humana cuidadosa
- Reformatações massivas de código não relacionadas à mudança proposta
- Dependências pesadas sem justificativa técnica forte
- Mudanças que quebram a API de plugins sem discussão prévia
- Commits com mensagens vagas (`fix`, `update`, `changes`, `misc`)

---

## Versionamento

O Codis segue [Versionamento Semântico](https://semver.org/lang/pt-BR/) (SemVer):

| Tipo | Formato | Quando usar |
|---|---|---|
| **Major** | `X.0.0` | Mudanças incompatíveis na API ou comportamento |
| **Minor** | `X.Y.0` | Novas funcionalidades retrocompatíveis |
| **Patch** | `X.Y.Z` | Correções de bugs retrocompatíveis |
| **Pré-release** | `X.Y.Z-beta.N` | Versões de teste antes do release oficial |

---

## Processo de Release

1. Atualizar `CHANGELOG.md` com as mudanças da versão (seção `## [X.Y.Z]`)
2. Atualizar `APP_VERSION` em `src/constants.py`
3. Commit: `chore: release vX.Y.Z` e merge em `main`
4. O workflow `.github/workflows/pipeline.yml` dispara automaticamente no commit e, em uma única execução:
   - Valida o código (testes, lint, i18n) e compila em paralelo
   - Gera executáveis para Windows x64/x86/ARM64, Linux x64/ARM64 e macOS Apple Silicon/Intel (as plataformas extras são experimentais)
   - Gera o ZIP de código-fonte e o `SHA256SUMS.txt`
   - Cria a tag `vX.Y.Z` e publica a release oficial com todos os arquivos, as notas do CHANGELOG e as notas automáticas por *label*

Não é necessário criar a tag manualmente nem rodar outro workflow. Se preferir, a tag `vX.Y.Z` também pode ser enviada à mão (deve ser igual a `APP_VERSION`); caso a release já exista, nada é refeito.

Commits seguintes em `main` (sem mudar a versão) atualizam a pré-release móvel `nightly`; commits em `develop` atualizam `dev`. Commits só de documentação não geram build nem release. Detalhes em [wiki/builds.md](../wiki/builds.md#github-actions).

---

## Política de Branches

| Branch | Propósito |
|---|---|
| `main` / `master` | Código estável - cada commit com mudança de código atualiza a pré-release `nightly` |
| `develop` | Integração de features em desenvolvimento |
| `feature/*` | Branches de feature individuais |
| `fix/*` | Branches de correção de bugs |
| `docs/*` | Apenas mudanças de documentação |

---

## Código de Conduta

Todas as interações neste projeto são regidas pelo [Código de Conduta](CODE_OF_CONDUCT.md). Violações podem ser reportadas ao mantenedor principal.

---

## Alterações nesta Governança

Mudanças neste documento seguem o processo de RFC (discussão prévia de 14 dias + aprovação do mantenedor).
