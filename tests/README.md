# Suíte de Testes - Codis

Resumo rápido. A documentação completa (inventário por arquivo, fixtures, convenções e cobertura) vive em **[wiki/testes.md](../wiki/testes.md)**.

- `tests/` - 70 arquivos, 1.683 casos, 100% headless (o `conftest.py` instala um stub de Tkinter e isola `CONFIG_DIR`).
- `tests_gui/` - 191 funções de teste que exigem display real.

```bash
pip install pytest pytest-cov

pytest tests/                                        # todos os testes
pytest tests/ --cov=src --cov-report=term-missing    # com cobertura
pytest tests/test_syntax_patterns.py -v              # arquivo específico
pytest tests/ -k "events" -v                         # por palavra-chave
pytest tests/ -x                                     # para no primeiro falho
```

Requisitos: os mesmos do Codis (`requirements.txt`) mais `pytest` e `pytest-cov`; `websockets` habilita os testes de chat WebSocket/MQTT.

Para conferir se a documentação ainda reflete o código: `python tools/docs_facts.py --check`.
