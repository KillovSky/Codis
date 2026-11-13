"""Codis - LintManager: execução assíncrona de linters externos.

Integração com o barramento de eventos (RESA)
---------------------------------------------
O LintManager publica eventos no ``EventBus`` global ao longo do ciclo de vida
de cada análise:

``EventKind.LINT_STARTED``
    Emitido quando uma análise é efetivamente iniciada em thread separada.
    Payload: ``{path, lang}``

``EventKind.LINT_RESULT``
    Emitido quando a análise conclui com uma lista de diagnósticos (pode ser
    vazia).  Payload: ``{path, lang, diags}``

``EventKind.LINT_CANCELLED``
    Emitido quando a análise é interrompida antes de completar.
    Payload: ``{path, lang}``

``EventKind.LINT_ERROR``
    Emitido quando ocorre uma exceção irrecuperável durante a análise.
    Payload: ``{path, lang, error}``

O callback ``deliver`` passado ao construtor coexiste com os eventos.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import tkinter as tk
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from src.config import ConfigManager
from src.constants import LINT_CSS, LINT_DELAY, LINT_HTML, LINT_JS, LINT_PHP, LINT_PY

if TYPE_CHECKING:
    pass


def _get_bus():
    """Retorna a instância global do EventBus, ou ``None`` se indisponível (importação circular)."""
    try:
        from src.events import bus

        return bus
    except Exception:
        return None


logger = logging.getLogger("Codis")


@dataclass
class Diag:
    """Um diagnóstico de lint (erro, aviso, info ou hint)."""

    line: int
    col: int
    end_col: int
    severity: int
    message: str
    rule: str = ""
    source: str = ""
    fix: dict | None = None
    related: list[dict] = field(default_factory=list)

    SEVERITY_LABELS = {1: "error", 2: "warning", 3: "info", 4: "hint"}
    SEVERITY_ICONS = {1: "E", 2: "W", 3: "I", 4: "H"}

    @property
    def icon(self) -> str:
        """Ícone Unicode correspondente à severidade do diagnóstico."""
        return self.SEVERITY_ICONS.get(self.severity, "?")

    @property
    def is_error(self) -> bool:
        """Retorna ``True`` se o diagnóstico tem severidade de erro (nível 1)."""
        return self.severity == 1


class LintManager:
    """
    Roda múltiplos linters em thread daemon.

    Para JavaScript/TypeScript:
        - ESLint (com suporte a configs .eslintrc, eslint.config.js, etc.)
        - Prettier (apenas verificação)

    Para Python:
        - py_compile (sintaxe)
        - pyflakes (problemas semânticos simples)
        - pylint (análise aprofundada, se disponível)
        - mypy (verificação de tipos, se disponível)
        - flake8 (estilo + erros, se disponível)

    Para CSS/SCSS:
        - stylelint (se disponível)

    """

    def __init__(
        self, deliver: Callable[[list[Diag]], None], cfg: ConfigManager | None = None
    ) -> None:
        """
        Inicializa o LintManager.

        Args:
            deliver: Callback invocado com a lista de diagnósticos ao fim de cada análise.
            cfg: Instância do ConfigManager para ler configurações de linters.
        """
        self._deliver = deliver
        self._cfg = cfg
        self._root: tk.Misc | None = None
        self._job: str | None = None
        self._tool_cache: dict[str, str | None] = {}
        self._cancel_event = threading.Event()
        self._current_thread: threading.Thread | None = None
        self._log_callback: Callable[[str, str, str], None] | None = None

    def attach(self, root: tk.Misc) -> None:
        """Vincula o LintManager à raiz Tk para poder agendar callbacks."""
        self._root = root

    def set_log_callback(self, callback: Callable[[str, str, str], None]) -> None:
        """Registra um callback de log chamado durante análise: (mensagem, nível, fonte)."""
        self._log_callback = callback

    def schedule(self, path: Path | None, content: str, lang: str) -> None:
        """Agenda análise de lint com debounce; análises anteriores são canceladas."""
        if not self._root:
            return
        if not (self._cfg.get("lint_enabled") if self._cfg else True):
            return
        if self._job:
            self._root.after_cancel(self._job)
        self._job = self._root.after(
            LINT_DELAY,
            lambda: self._start_thread(path, content, lang),
        )

    def _start_thread(self, path: Path | None, content: str, lang: str) -> None:
        """Cancela a thread de lint em curso (se houver) e inicia uma nova."""
        self._cancel_event.set()
        self._cancel_event = threading.Event()
        t = threading.Thread(
            target=self._run,
            args=(path, content, lang, self._cancel_event),
            daemon=True,
        )
        self._current_thread = t
        t.start()

    def cancel(self) -> None:
        """Cancela imediatamente qualquer análise agendada ou em andamento."""
        if self._root and self._job:
            self._root.after_cancel(self._job)
            self._job = None
        self._cancel_event.set()

    def _run(
        self, path: Path | None, content: str, lang: str, cancel: threading.Event
    ) -> None:
        """Executa os linters em thread daemon e entrega os diagnósticos via callback e EventBus."""
        if cancel.is_set():
            if _get_bus():
                _get_bus().emit(
                    "lint.cancelled",
                    {"path": str(path) if path else "", "lang": lang},
                    source="LintManager",
                )
            return
        if _get_bus():
            _get_bus().emit(
                "lint.started",
                {"path": str(path) if path else "", "lang": lang},
                source="LintManager",
            )
        try:
            diags: list[Diag] = []
            if lang in LINT_JS:
                diags = self._lint_js(path, content, cancel)
            elif lang in LINT_PY:
                diags = self._lint_python(path, content, cancel)
            elif lang in LINT_CSS:
                diags = self._stylelint(path, cancel)
            elif lang in LINT_HTML:
                diags = self._lint_html(path, content, cancel)
            elif lang in LINT_PHP:
                diags = self._lint_php(path, content, cancel)
        except (OSError, ValueError, RuntimeError) as exc:
            logger.debug("Lint run error: %s", exc)
            if _get_bus():
                _get_bus().emit(
                    "lint.error",
                    {
                        "path": str(path) if path else "",
                        "lang": lang,
                        "error": str(exc),
                    },
                    source="LintManager",
                )
            diags = []
        if cancel.is_set():
            if _get_bus():
                _get_bus().emit(
                    "lint.cancelled",
                    {"path": str(path) if path else "", "lang": lang},
                    source="LintManager",
                )
            return

        if _get_bus():
            _get_bus().emit(
                "lint.result",
                {
                    "path": str(path) if path else "",
                    "lang": lang,
                    "diags": diags,
                },
                source="LintManager",
            )

        if self._root:
            self._root.after(0, lambda: self._deliver(diags))

    def _find_tool(self, name: str, config_key: str | None = None) -> str | None:
        """Resolve caminho de uma ferramenta (com cache)."""
        if name in self._tool_cache:
            return self._tool_cache[name]

        if config_key and self._cfg is not None:
            configured = self._cfg.get(config_key, "")
            if configured and shutil.which(configured):
                self._tool_cache[name] = configured
                return configured
        result = shutil.which(name)

        if not result:
            local = Path.cwd() / "node_modules" / ".bin" / name
            if sys.platform == "win32":
                local = local.with_suffix(".cmd")
            if local.exists():
                result = str(local)
        self._tool_cache[name] = result
        return result

    @staticmethod
    def _run_proc(
        cmd: list[str],
        cwd: Path | None = None,
        input_text: str | None = None,
        timeout: int = 20,
    ) -> tuple[int, str, str]:
        """Executa um subprocesso e retorna (returncode, stdout, stderr)."""
        try:
            kwargs: dict[str, Any] = {
                "capture_output": True,
                "text": True,
                "timeout": timeout,
                "encoding": "utf-8",
                "errors": "replace",
            }
            if cwd:
                kwargs["cwd"] = str(cwd)
            if input_text is not None:
                kwargs["input"] = input_text
            if sys.platform == "win32":
                kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
                kwargs["shell"] = True
            r = subprocess.run(cmd, check=False, **kwargs)
            return r.returncode, r.stdout, r.stderr
        except subprocess.TimeoutExpired:
            return -1, "", "timeout"
        except (OSError, ValueError) as exc:
            return -1, "", str(exc)

    def _lint_js(
        self, path: Path | None, content: str, cancel: threading.Event
    ) -> list[Diag]:
        """Executa o ESLint e retorna os diagnósticos.

        Args:
            path: Caminho do arquivo.
            content: Conteúdo do arquivo.

        Returns:
            Lista de objetos Diag.
        """
        if cancel.is_set():
            return []
        if not (self._cfg.get("lint_js") if self._cfg else True):
            return []
        diags = self._eslint(path, content, cancel)
        return diags

    def _eslint(
        self, path: Path | None, content: str, cancel: threading.Event
    ) -> list[Diag]:
        """
        Executa ESLint via stdin com fallback por arquivo.
        BUGS CORRIGIDOS:
        1. `cwd` era NameError (undefined) - silenciado pelo except -> lista vazia.
        3. rc=1 é normal quando há erros de lint no ESLint v8+ FlatConfig.
        """
        eslint = self._find_tool("eslint", "eslint_path")
        if not eslint:
            if self._root and hasattr(self, "_log_callback"):
                self._root.after(
                    0,
                    lambda: self._log_callback(
                        "⚠ ESLint não encontrado. Instale: npm i -g eslint",
                        "ESLint",
                        "warn",
                    ),
                )
            return []

        cwd: Path | None = path.parent if path else None

        def _log(msg: str, lvl: str = "info") -> None:
            if self._root and hasattr(self, "_log_callback"):
                self._root.after(
                    0, lambda m=msg, l_=lvl: self._log_callback(
                        m, "ESLint", l_)
                )

        def _extract_json(raw: str) -> Any:
            raw = raw.strip()
            if not raw:
                return []
            idx = raw.find("[")
            if idx > 0:
                raw = raw[idx:]
            return json.loads(raw)

        filename = str(path) if path else "untitled.js"
        cmd = [eslint, "--format", "json",
               "--stdin-filename", filename, "--stdin"]

        try:
            _log(f"▶ ESLint (stdin): {filename}", "info")
            rc, stdout, stderr = self._run_proc(
                cmd, cwd=cwd, input_text=content, timeout=25
            )
            if cancel.is_set():
                return []
            if rc == 2:
                _log(f"✖ ESLint erro fatal (rc=2): {stderr[:200]}", "error")
                raise RuntimeError(f"ESLint fatal (rc=2): {stderr[:200]}")
            if stderr.strip():
                _log(f"  stderr: {stderr.strip()[:300]}", "warn")
            result = self._parse_eslint(_extract_json(stdout))
            _log(f"✔ ESLint concluído - {len(result)} diagnóstico(s)", "info")
            return result
        except (json.JSONDecodeError, ValueError):
            pass
        except (OSError, RuntimeError) as exc:
            _log(f"✖ ESLint stdin error: {exc}", "error")
            logger.debug("ESLint stdin error: %s", exc)

        if not path:
            return []
        cmd2 = [eslint, "--format", "json", str(path)]
        try:
            _log(f"▶ ESLint (file): {path.name}", "info")
            rc, stdout, stderr = self._run_proc(cmd2, cwd=cwd, timeout=25)
            if cancel.is_set():
                return []
            if rc == 2:
                return []
            result = self._parse_eslint(_extract_json(stdout))
            _log(f"✔ ESLint concluído - {len(result)} diagnóstico(s)", "info")
            return result
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            _log(f"✖ ESLint file error: {exc}", "error")
            logger.debug("ESLint file error: %s", exc)
            return []

    @staticmethod
    def _parse_eslint(data: Any) -> list[Diag]:
        """Parseia a saída JSON do ESLint para objetos Diag.

        Args:
            output: Saída JSON do ESLint.

        Returns:
            Lista de Diag.
        """
        out: list[Diag] = []
        for file_result in data if isinstance(data, list) else []:
            for m in file_result.get("messages", []):
                sev_raw = m.get("severity", 1)

                sev = 1 if sev_raw == 2 else 2
                fix = m.get("fix")
                out.append(
                    Diag(
                        line=m.get("line", 1),
                        col=max(0, m.get("column", 1) - 1),
                        end_col=max(
                            0, m.get("endColumn", m.get("column", 1)) - 1),
                        severity=sev,
                        message=m.get("message", ""),
                        rule=m.get("ruleId") or "",
                        source="eslint",
                        fix=fix,
                    )
                )
        return out

    def _lint_html(
        self, path: Path | None, content: str, cancel: threading.Event
    ) -> list[Diag]:
        """Executa validação HTML via tidy-html5 (se disponível) e retorna diagnósticos."""
        if cancel.is_set():
            return []
        tidy = self._find_tool("tidy")
        if not tidy:
            return self._lint_html_basic(content)
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".html", delete=False, encoding="utf-8"
        ) as tmp:
            tmp.write(content)
            tmp_path = tmp.name
        try:
            _, _, stderr = self._run_proc(
                [tidy, "-quiet", "-errors", "--gnu-emacs", "yes", tmp_path],
                timeout=15,
            )
            if cancel.is_set():
                return []
            diags: list[Diag] = []

            pattern = re.compile(
                r"(?:line\s+(\d+)\s+column\s+(\d+)\s+-\s+)?(Warning|Error|Info):?\s+(.+)",
                re.IGNORECASE,
            )
            for ln in stderr.splitlines():
                m = pattern.search(ln)
                if not m:
                    continue
                line_n = int(m.group(1)) if m.group(1) else 1
                col_n = int(m.group(2)) if m.group(2) else 0
                level = m.group(3).lower()
                msg = m.group(4).strip()
                sev = 1 if level == "error" else (
                    2 if level == "warning" else 3)
                diags.append(
                    Diag(
                        line=line_n,
                        col=col_n,
                        end_col=col_n + 1,
                        severity=sev,
                        message=msg,
                        rule="tidy",
                        source="tidy",
                    )
                )
            return diags[:200]
        except Exception as exc:
            logger.debug("tidy html lint error: %s", exc)
            return []
        finally:
            with contextlib.suppress(OSError):
                os.unlink(tmp_path)

    def _lint_html_basic(self, content: str) -> list[Diag]:
        """Validação HTML heurística sem ferramentas externas."""
        diags: list[Diag] = []

        stack: list[tuple[str, int]] = []
        void_tags = {
            "area",
            "base",
            "br",
            "col",
            "embed",
            "hr",
            "img",
            "input",
            "link",
            "meta",
            "param",
            "source",
            "track",
            "wbr",
        }
        tag_re = re.compile(r"<(/?)([A-Za-z][A-Za-z0-9]*)[^>]*(/?)>")
        for line_n, line in enumerate(content.splitlines(), 1):
            for m in tag_re.finditer(line):
                closing = m.group(1) == "/"
                tag_name = m.group(2).lower()
                self_cl = m.group(3) == "/"
                if tag_name in void_tags or self_cl:
                    continue
                if closing:
                    if stack and stack[-1][0] == tag_name:
                        stack.pop()
                    else:
                        diags.append(
                            Diag(
                                line=line_n,
                                col=m.start(),
                                end_col=m.end(),
                                severity=2,
                                message=f"Tag de fechamento inesperada: </{tag_name}>",
                                rule="html-mismatch",
                                source="codis-html",
                            )
                        )
                else:
                    stack.append((tag_name, line_n))
        for tag_name, line_n in stack[-10:]:
            diags.append(
                Diag(
                    line=line_n,
                    col=0,
                    end_col=0,
                    severity=2,
                    message=f"Tag não fechada: <{tag_name}>",
                    rule="html-unclosed",
                    source="codis-html",
                )
            )
        return diags[:200]

    def _lint_php(
        self, path: Path | None, content: str, cancel: threading.Event
    ) -> list[Diag]:
        """Executa php -l (lint syntax) se disponível."""
        if cancel.is_set():
            return []
        php = self._find_tool("php")
        if not php:
            return []
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".php", delete=False, encoding="utf-8"
        ) as tmp:
            tmp.write(content)
            tmp_path = tmp.name
        try:
            _, stdout, stderr = self._run_proc(
                [php, "-l", tmp_path], timeout=10)
            if cancel.is_set():
                return []
            diags: list[Diag] = []
            err_re = re.compile(
                r"(Parse|Fatal) error:\s+(.+?)\s+in\s+.+?\s+on\s+line\s+(\d+)"
            )
            for ln in (stdout + stderr).splitlines():
                m = err_re.search(ln)
                if m:
                    diags.append(
                        Diag(
                            line=int(m.group(3)),
                            col=0,
                            end_col=0,
                            severity=1,
                            message=m.group(2).strip(),
                            rule="php-syntax",
                            source="php",
                        )
                    )
            return diags
        except Exception as exc:
            logger.debug("php lint error: %s", exc)
            return []
        finally:
            with contextlib.suppress(OSError):
                os.unlink(tmp_path)

    def format_prettier(self, path: Path, content: str) -> str | None:
        """Formata usando Prettier. Retorna texto formatado ou None."""
        prettier = self._find_tool("prettier", "prettier_path")
        if not prettier:
            return None
        cwd = path.parent
        ext = path.suffix.lstrip(".")
        parser_map = {
            "js": "babel",
            "mjs": "babel",
            "cjs": "babel",
            "jsx": "babel",
            "ts": "typescript",
            "tsx": "typescript",
            "json": "json",
            "jsonc": "json",
            "json5": "json5",
            "css": "css",
            "scss": "scss",
            "less": "less",
            "html": "html",
            "htm": "html",
            "vue": "html",
            "svelte": "html",
            "md": "markdown",
            "mdx": "mdx",
            "yaml": "yaml",
            "yml": "yaml",
            "graphql": "graphql",
            "gql": "graphql",
        }
        parser = parser_map.get(ext)
        if not parser:
            return None
        cmd = [prettier, "--stdin-filepath", str(path), "--parser", parser]
        rc, stdout, stderr = self._run_proc(
            cmd, cwd=cwd, input_text=content, timeout=15
        )
        if rc == 0 and stdout:
            return stdout
        logger.debug("Prettier error: %s", stderr[:200])
        return None

    def _lint_python(
        self, path: Path | None, content: str, cancel: threading.Event
    ) -> list[Diag]:
        """Executa os linters Python (pylint, flake8, mypy) e retorna os diagnósticos.

        Args:
            path: Caminho do arquivo.
            content: Conteúdo do arquivo.

        Returns:
            Lista de objetos Diag.
        """
        if not (self._cfg.get("lint_python") if self._cfg else True):
            return []
        diags: list[Diag] = []

        def _log(msg: str, lvl: str = "info") -> None:
            if self._root and hasattr(self, "_log_callback"):
                self._root.after(
                    0, lambda m=msg, l_=lvl: self._log_callback(
                        m, "Python", l_)
                )

        fname = path.name if path else "untitled.py"
        _log(f"▶ Python lint: {fname}", "info")

        syntax_diags = self._py_syntax(content)
        diags.extend(syntax_diags)
        if syntax_diags:
            _log(
                f"✖ Erro de sintaxe em {fname}: {syntax_diags[0].message}", "error")
            return diags
        if cancel.is_set():
            return diags

        if not path:
            return diags

        if cancel.is_set():
            return diags
        pf = self._pyflakes(path, cancel)
        diags.extend(pf)
        if pf:
            _log(f"  pyflakes: {len(pf)} aviso(s)", "warn")

        if cancel.is_set():
            return diags
        flake8_diags = self._flake8(path, cancel)

        existing_msgs = {(d.line, d.message[:30]) for d in diags}
        for d in flake8_diags:
            if (d.line, d.message[:30]) not in existing_msgs:
                diags.append(d)
                existing_msgs.add((d.line, d.message[:30]))
        if flake8_diags:
            _log(f"  flake8: {len(flake8_diags)} problema(s)", "warn")

        if self._cfg and self._cfg.get("mypy_path") and not cancel.is_set():
            mypy_diags = self._mypy(path, cancel)
            for d in mypy_diags:
                if (d.line, d.message[:30]) not in existing_msgs:
                    diags.append(d)
            if mypy_diags:
                _log(f"  mypy: {len(mypy_diags)} problema(s)", "warn")

        _log(
            f"✔ Python lint concluído - {len(diags)} diagnóstico(s) em {fname}", "info"
        )
        return diags

    @staticmethod
    def _py_syntax(content: str) -> list[Diag]:
        """Verifica sintaxe Python via py_compile."""
        import py_compile

        tmp = None
        try:
            with tempfile.NamedTemporaryFile(
                suffix=".py",
                mode="w",
                encoding="utf-8",
                delete=False,
                prefix="codis_lint_",
            ) as f:
                f.write(content)
                tmp = f.name
            try:
                py_compile.compile(tmp, doraise=True)
            except py_compile.PyCompileError as exc:
                msg = str(exc)
                m = re.search(r"line (\d+)", msg)
                line = int(m.group(1)) if m else 1
                clean = re.sub(
                    r"\s*\(.*?\)\s*$", "", msg.split("\n", maxsplit=1)[0]
                ).strip()
                clean = re.sub(r"Sorry.*?::", "", clean).strip()
                return [
                    Diag(
                        line=line,
                        col=0,
                        end_col=0,
                        severity=1,
                        message=clean or "Syntax error",
                        source="py_compile",
                    )
                ]
        except OSError as exc:
            logger.debug("py_syntax error: %s", exc)
        finally:
            if tmp:
                with contextlib.suppress(OSError):
                    os.unlink(tmp)
        return []

    def _pyflakes(self, path: Path, cancel: threading.Event) -> list[Diag]:
        pyflakes = self._find_tool("pyflakes")
        if not pyflakes:
            return []
        _, stdout, stderr = self._run_proc([pyflakes, str(path)], timeout=15)
        if cancel.is_set():
            return []
        out: list[Diag] = []
        pattern = re.compile(r"^.+:(\d+):(\d+):\s*(.+)$")
        pattern2 = re.compile(r"^.+:(\d+):\s*(.+)$")
        for line in (stdout + stderr).splitlines():
            m = pattern.match(line)
            if m:
                out.append(
                    Diag(
                        line=int(m.group(1)),
                        col=int(m.group(2)) - 1,
                        end_col=0,
                        severity=2,
                        message=m.group(3).strip(),
                        source="pyflakes",
                    )
                )
                continue
            m2 = pattern2.match(line)
            if m2:
                out.append(
                    Diag(
                        line=int(m2.group(1)),
                        col=0,
                        end_col=0,
                        severity=2,
                        message=m2.group(2).strip(),
                        source="pyflakes",
                    )
                )
        return out

    def _flake8(self, path: Path, cancel: threading.Event) -> list[Diag]:
        flake8 = self._find_tool("flake8", "flake8_path")
        if not flake8:
            return []
        _, stdout, _ = self._run_proc(
            [
                flake8,
                "--format=default",
                "--max-line-length=120",
                "--extend-ignore=E501",
                str(path),
            ],
            timeout=15,
        )
        if cancel.is_set():
            return []
        out: list[Diag] = []
        pattern = re.compile(r"^.+:(\d+):(\d+):\s*([EWF]\d+)\s+(.+)$")
        sev_map = {"E": 1, "F": 1, "W": 2, "C": 2, "N": 3, "B": 2}
        for line in stdout.splitlines():
            m = pattern.match(line)
            if not m:
                continue
            code, msg = m.group(3), m.group(4)
            sev = sev_map.get(code[0], 2)
            out.append(
                Diag(
                    line=int(m.group(1)),
                    col=max(0, int(m.group(2)) - 1),
                    end_col=0,
                    severity=sev,
                    message=f"{code} {msg}",
                    rule=code,
                    source="flake8",
                )
            )
        return out

    def _mypy(self, path: Path, cancel: threading.Event) -> list[Diag]:
        mypy = self._find_tool("mypy", "mypy_path")
        if not mypy:
            return []
        _, stdout, _ = self._run_proc(
            [
                mypy,
                "--no-error-summary",
                "--show-column-numbers",
                "--ignore-missing-imports",
                str(path),
            ],
            timeout=20,
        )
        if cancel.is_set():
            return []
        out: list[Diag] = []
        pattern = re.compile(
            r"^.+:(\d+):(\d+):\s*(error|warning|note):\s*(.+)$")
        sev_map = {"error": 1, "warning": 2, "note": 3}
        for line in stdout.splitlines():
            m = pattern.match(line)
            if m:
                out.append(
                    Diag(
                        line=int(m.group(1)),
                        col=max(0, int(m.group(2)) - 1),
                        end_col=0,
                        severity=sev_map.get(m.group(3), 2),
                        message=m.group(4).strip(),
                        source="mypy",
                    )
                )
        return out

    def _stylelint(
        self, path: Path | None, cancel: threading.Event
    ) -> list[Diag]:
        """Executa stylelint no arquivo especificado e retorna diagnósticos CSS."""
        stylelint = self._find_tool("stylelint")
        if not stylelint:
            return []
        if not path:
            return []
        cwd = path.parent
        _, stdout, _ = self._run_proc(
            [stylelint, "--formatter", "json", str(path)],
            cwd=cwd,
            timeout=15,
        )
        if cancel.is_set():
            return []
        out: list[Diag] = []
        try:
            data = json.loads(stdout.strip() or "[]")
            for file_res in data if isinstance(data, list) else []:
                for w in file_res.get("warnings", []):
                    sev = 1 if w.get("severity") == "error" else 2
                    out.append(
                        Diag(
                            line=w.get("line", 1),
                            col=max(0, w.get("column", 1) - 1),
                            end_col=0,
                            severity=sev,
                            message=w.get("text", ""),
                            rule=w.get("rule", ""),
                            source="stylelint",
                        )
                    )
        except (json.JSONDecodeError, ValueError) as exc:
            logger.debug("stylelint parse error: %s", exc)
        return out

    def format_python(self, content: str) -> str | None:
        """Formata Python com black ou autopep8. Retorna o conteúdo formatado ou None."""
        black = self._find_tool("black")
        if black:
            tmp = None
            try:
                with tempfile.NamedTemporaryFile(
                    suffix=".py",
                    mode="w",
                    encoding="utf-8",
                    delete=False,
                    prefix="codis_fmt_",
                ) as f:
                    f.write(content)
                    tmp = f.name
                rc, _, _ = self._run_proc(
                    [black, "--quiet", "--line-length", "120", tmp],
                    timeout=15,
                )
                if rc == 0:
                    return Path(tmp).read_text(encoding="utf-8")
            except OSError as exc:
                logger.debug("black format error: %s", exc)
            finally:
                if tmp:
                    with contextlib.suppress(OSError):
                        os.unlink(tmp)

        autopep8 = self._find_tool("autopep8")
        if autopep8:
            rc, stdout, _ = self._run_proc(
                [autopep8, "--max-line-length", "120", "-"],
                input_text=content,
                timeout=15,
            )
            if rc == 0 and stdout:
                return stdout
        return None

    @staticmethod
    def apply_eslint_fix(content: str, fix: dict) -> str:
        """Aplica uma única correção ESLint ao conteúdo usando offsets originais.

        Usado apenas para fixes individuais (clique direito -> Corrigir).
        Para múltiplos fixes simultâneos, use ``apply_all_eslint_fixes``.

        Args:
            content: Conteúdo original do arquivo.
            fix:     Dict ESLint com ``range`` ([start, end]) e ``text``.

        Returns:
            Conteúdo com a correção aplicada, ou o original em caso de erro.
        """
        if not fix:
            return content
        try:
            rng = fix.get("range", [0, 0])
            text = fix.get("text", "")
            start, end = int(rng[0]), int(rng[1])
            if start < 0 or end < start or end > len(content):
                return content
            return content[:start] + text + content[end:]
        except (IndexError, TypeError, KeyError, ValueError):
            return content

    def apply_all_eslint_fixes(
        self, path: Path, content: str, diags: list[Diag]
    ) -> str:
        """Aplica todas as correções ESLint delegando ao próprio ESLint (--fix-dry-run).

        Estratégia (a mesma usada pelo VSCode com a extensão ESLint):

        1. **``--fix-dry-run`` via stdin** (ESLint >= 5): passa o conteúdo pelo
           stdin e recebe de volta o arquivo corrigido no campo ``output`` do JSON.
           Não toca o disco. É a abordagem mais segura e a que o ESLint usa
           internamente para garantir que todos os fixes sejam aplicados de forma
           atômica e correta.

        2. **Fallback via arquivo temporário** (ESLint antigo ou --fix-dry-run
           indisponível): grava o conteúdo num arquivo .js temporário, executa
           ``eslint --fix`` sobre ele e lê o resultado de volta. O arquivo
           original nunca é tocado.

        3. **Último recurso**: se o ESLint não estiver disponível, aplica cada
           fix individualmente em ordem reversa - menos confiável, mas melhor
           que nada para um único fix isolado.

        Args:
            path:    Caminho original do arquivo (usado como ``--stdin-filename``
                     para que o ESLint encontre a config correta).
            content: Conteúdo atual do arquivo no editor.
            diags:   Lista de diagnósticos; apenas os que têm ``fix`` são usados
                     para decidir se há algo a corrigir.

        Returns:
            Conteúdo com todas as correções aplicadas, ou o original se nada
            puder ser corrigido.
        """
        if not any(d.fix for d in diags):
            return content

        eslint = self._find_tool("eslint", "eslint_path")
        if not eslint:
            fixable = sorted(
                [d for d in diags if d.fix],
                key=lambda d: d.fix.get("range", [0])[0],
                reverse=True,
            )
            result = content
            for d in fixable:
                result = self.apply_eslint_fix(result, d.fix)
            return result

        cwd = path.parent if path else None
        filename = str(path) if path else "untitled.js"

        def _extract_json(raw: str):
            raw = raw.strip()
            if not raw:
                return []
            idx = raw.find("[")
            if idx > 0:
                raw = raw[idx:]
            return json.loads(raw)

        cmd_dry = [
            eslint,
            "--fix-dry-run",
            "--format",
            "json",
            "--stdin-filename",
            filename,
            "--stdin",
        ]
        try:
            rc, stdout, _stderr = self._run_proc(
                cmd_dry, cwd=cwd, input_text=content, timeout=30
            )
            if rc in (0, 1):
                data = _extract_json(stdout)
                if isinstance(data, list) and data:
                    output = data[0].get("output")
                    if output and isinstance(output, str):
                        return output
                if rc in (0, 1):
                    return content
        except (OSError, json.JSONDecodeError, ValueError, IndexError):
            pass

        suffix = path.suffix if path else ".js"
        tmp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                suffix=suffix,
                delete=False,
                encoding="utf-8",
                errors="replace",
            ) as tmp:
                tmp.write(content)
                tmp_path = Path(tmp.name)

            cmd_fix = [eslint, "--fix", str(tmp_path)]
            rc2, _out2, _err2 = self._run_proc(cmd_fix, cwd=cwd, timeout=30)
            if rc2 in (0, 1) and tmp_path.exists():
                fixed = tmp_path.read_text(encoding="utf-8", errors="replace")
                return fixed
        except (OSError, ValueError):
            pass
        finally:
            if tmp_path and tmp_path.exists():
                with contextlib.suppress(OSError):
                    tmp_path.unlink()

        return content
