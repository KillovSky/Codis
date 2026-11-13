.DEFAULT_GOAL := help
.PHONY: help install install-dev doctor run test test-cov lint format \
        typecheck security clean build sync-locales docs docs-check pre-commit all-checks

PYTHON ?= python3

help:  ## Mostra esta mensagem de ajuda
	@echo "Codis — comandos disponíveis:"
	@echo ""
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

install:  ## Instala dependências recomendadas
	$(PYTHON) -m pip install -r requirements.txt

install-dev:  ## Instala dependências de desenvolvimento (lint, testes, build)
	$(PYTHON) -m pip install -e .[dev,full]

doctor:  ## Diagnostica o ambiente (Python, Tkinter, deps, display)
	$(PYTHON) doctor.py --verbose

run:  ## Executa o Codis
	$(PYTHON) run.py

test:  ## Roda a suíte de testes
	$(PYTHON) -m pytest tests/ -v

test-cov:  ## Roda testes com relatório de cobertura
	$(PYTHON) -m pytest tests/ --cov=src --cov-report=term-missing --cov-report=html
	@echo "Relatório HTML em: htmlcov/index.html"

lint:  ## Roda ruff + pylint
	$(PYTHON) -m ruff check src/ tests/
	$(PYTHON) -m pylint src/ --rcfile=.pylintrc

format:  ## Formata o código com black + isort
	$(PYTHON) -m black src/ tests/ run.py
	$(PYTHON) -m isort src/ tests/ run.py

format-check:  ## Verifica formatação sem modificar arquivos
	$(PYTHON) -m black --check src/ tests/ run.py
	$(PYTHON) -m isort --check-only src/ tests/ run.py

typecheck:  ## Roda mypy
	$(PYTHON) -m mypy src/

security:  ## Roda análise de segurança com bandit
	$(PYTHON) -m bandit -r src/ -c pyproject.toml

complexity:  ## Mostra complexidade ciclomática do código
	$(PYTHON) -m radon cc src/ -a -nb

dead-code:  ## Detecta código morto com vulture
	$(PYTHON) -m vulture src/ --min-confidence 80

sync-locales:  ## Sincroniza chaves entre arquivos de tradução
	$(PYTHON) sync_locales.py

docs:  ## Regenera o site (docs/) a partir de wiki/ e dos .md da raiz
	$(PYTHON) tools/build_site.py

docs-check:  ## Confere se docs/ está atualizado e se os números dos .md batem com o código
	$(PYTHON) tools/docs_facts.py --check
	$(PYTHON) tools/build_site.py --check

build:  ## Gera o binário nativo via PyInstaller
	$(PYTHON) package_codis.py pyinstaller

build-zip:  ## Gera um .zip do código-fonte para distribuição
	$(PYTHON) package_codis.py zip

clean:  ## Remove artefatos de build, cache e arquivos temporários
	rm -rf build/ dist/ release_out/ *.egg-info
	rm -rf .pytest_cache/ .mypy_cache/ .ruff_cache/ htmlcov/
	rm -f .coverage coverage.xml
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete

pre-commit: format lint typecheck test  ## Roda tudo antes de um commit (formato + lint + tipos + testes)
	@echo ""
	@echo "✓ Pronto para commit."

all-checks: format-check lint typecheck security test-cov  ## Roda a suíte completa de verificações (igual ao CI)
	@echo ""
	@echo "✓ Todas as verificações passaram."
