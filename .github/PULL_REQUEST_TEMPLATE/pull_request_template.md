## 🔍 Tipo de Mudança

<!-- Marque o que se aplica. PRs que misturam múltiplos tipos sem justificativa podem ser recusados. -->

- [ ] 🐛 Correção de bug (não quebra funcionalidade existente)
- [ ] ✨ Nova feature (não quebra funcionalidade existente)
- [ ] 💥 Breaking change (muda comportamento existente de forma incompatível)
- [ ] ♻️ Refatoração (sem mudança de comportamento)
- [ ] ⚡ Performance (melhoria de desempenho sem mudança de comportamento)
- [ ] 🎨 Novo tema
- [ ] 🌍 Tradução / i18n
- [ ] 📝 Documentação (wiki, comentários, docstrings)
- [ ] 🔧 CI/CD / Configuração
- [ ] 🧪 Testes

---

## 📋 Descrição

<!-- O que esta PR faz e **por quê**? Contexto importa. -->

### O que mudou
-
-

### Por que mudou
<!-- Qual problema resolve? Qual issue fecha? -->

Closes # <!-- número da issue, se aplicável -->

---

## 🧪 Como Testar

<!-- Passos claros para verificar que a mudança funciona e não quebrou nada. -->

1.
2.
3.

**Resultado esperado:**

**Testado em:**
- [ ] Windows
- [ ] Linux
- [ ] macOS
- Outro (Especifique): 

---

## 📸 Screenshots / Demos

<!-- Para mudanças visuais, inclua antes/depois. Para features novas, um GIF curto é muito bem-vindo. -->

| Antes | Depois |
|:---:|:---:|
| | |

---

## ✅ Checklist

<!-- Marque o que for aplicável. Itens com * são obrigatórios. -->

### Código
- [ ] * Segue o estilo do projeto (sem reformatações em massa não solicitadas)
- [ ] * Thread safety preservado - toda manipulação de widget usa `root.after()` ou a main thread
- [ ] * Não introduzi dependências novas sem justificativa técnica sólida
- [ ] Adicionei type hints nas funções/classes novas ou modificadas

### Qualidade
- [ ] * `python -m py_compile` passa em todos os arquivos alterados
- [ ] Sem warnings novos do Pylint ou Flake8 que possam ser evitados
- [ ] Testes existentes continuam passando (`pytest tests/`)
- [ ] Adicionei testes para a mudança (se aplicável)

### Documentação
- [ ] Atualizei a wiki (`wiki/*.md`) se a mudança afeta o comportamento visível ao usuário
- [ ] Atualizei o `CHANGELOG.md` com uma entrada descritiva (Se necessário)
- [ ] Docstrings nas funções/classes novas

### Segurança (para mudanças que tocam plugins, IA, Git ou arquivos do usuário)
- [ ] Não introduzi leituras/escritas de arquivos arbitrários sem validação
- [ ] Não exponho chaves de API, tokens ou credenciais em logs ou estado compartilhado
- [ ] Entradas externas (plugins, URLs, config do usuário) são validadas antes de usar

---

## 📎 Contexto Adicional

<!-- Qualquer informação que o revisor deva saber: decisões de design, alternativas
consideradas, trade-offs, dependências com outras PRs, etc. -->
