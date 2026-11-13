"""Codis IA - Toolbox Expandido (nível Cursor/Windsurf/Zed Agent).

Ferramentas disponíveis para o assistente de IA:

LEITURA (SAFE):
  read_file, read_active_file, get_cursor_context, list_files,
  search_in_files, find_definition, get_project_summary, read_diagnostics,
  read_symbols, read_git_diff, read_git_log, find_references,
  preview_rename, workspace_symbols, get_env_info, check_dependency,
  diff_files, read_file_tree, get_clipboard

ESCRITA (MEDIUM):
  write_file, apply_edit, insert_after_line, format_code, patch_files

EXECUÇÃO (HIGH):
  run_command, run_script, run_tests, create_directory, delete_file,
  move_file, copy_file, init_project, install_dependency,
  git_commit, git_stage, git_branch, open_terminal_tab

CRÍTICO (CRITICAL):
  delete_directory, git_reset, git_clean
"""

from __future__ import annotations

import contextlib
import logging
import os
import re
import shutil
import subprocess
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from src.i18n import _t
from src.ia.checkpoints import CheckpointStore
from src.workspace_state import is_restricted

logger = logging.getLogger("Codis.IA.Toolbox")

TOOL_SCHEMA: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": (
                "Lê o conteúdo completo de um arquivo, ou um intervalo de linhas específico. "
                "Use quando o usuário perguntar sobre o conteúdo, lógica ou implementação de um arquivo."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Caminho relativo à raiz do workspace ou absoluto.",
                    },
                    "start_line": {
                        "type": "integer",
                        "description": "Primeira linha (base 1, inclusiva). Padrão: 1.",
                    },
                    "end_line": {
                        "type": "integer",
                        "description": "Última linha (base 1, inclusiva). Padrão: fim.",
                    },
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_active_file",
            "description": "Retorna o conteúdo completo do arquivo aberto no editor com números de linha.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_cursor_context",
            "description": "Retorna as linhas ao redor da posição atual do cursor no editor ativo.",
            "parameters": {
                "type": "object",
                "properties": {
                    "window": {
                        "type": "integer",
                        "description": "Linhas acima/abaixo do cursor. Padrão 40.",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_files",
            "description": "Lista arquivos e diretórios de uma pasta do workspace.",
            "parameters": {
                "type": "object",
                "properties": {
                    "folder": {
                        "type": "string",
                        "description": "Caminho relativo à raiz. Padrão: '.'",
                    },
                    "recursive": {
                        "type": "boolean",
                        "description": "Se true, percorre subdiretórios. Padrão: false.",
                    },
                    "extensions": {
                        "type": "string",
                        "description": "Extensões para filtrar, ex: '.py,.js'. Padrão: todos.",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file_tree",
            "description": "Retorna a árvore completa do projeto em formato visual similar ao comando 'tree'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "max_depth": {
                        "type": "integer",
                        "description": "Profundidade máxima. Padrão: 4.",
                    },
                    "show_hidden": {
                        "type": "boolean",
                        "description": "Incluir arquivos ocultos. Padrão: false.",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_in_files",
            "description": "Busca por regex em todos os arquivos do workspace.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Expressão regular ou texto.",
                    },
                    "extensions": {
                        "type": "string",
                        "description": "Extensões para buscar. Padrão: todos.",
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Máximo de matches. Padrão 30.",
                    },
                    "case_sensitive": {
                        "type": "boolean",
                        "description": "Case sensitive? Padrão: false.",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "find_definition",
            "description": "Localiza a DEFINIÇÃO de um símbolo (classe, função, variável).",
            "parameters": {
                "type": "object",
                "properties": {
                    "symbol": {"type": "string"},
                    "extensions": {"type": "string"},
                },
                "required": ["symbol"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "find_references",
            "description": "Encontra todos os USOS de um símbolo no workspace (excluindo a definição).",
            "parameters": {
                "type": "object",
                "properties": {
                    "symbol": {"type": "string"},
                    "extensions": {"type": "string"},
                    "max_results": {
                        "type": "integer",
                        "description": "Padrão 30, máx 200.",
                    },
                },
                "required": ["symbol"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_project_summary",
            "description": "Retorna resumo completo: tipo, linguagens, dependências, git, árvore raiz.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_diagnostics",
            "description": "Retorna erros e avisos do linter/LSP para o arquivo ativo.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_symbols",
            "description": "Lista símbolos de nível superior no arquivo ativo com números de linha.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "workspace_symbols",
            "description": "Coleta todos os símbolos de todo o workspace, organizado por arquivo.",
            "parameters": {
                "type": "object",
                "properties": {
                    "extensions": {"type": "string"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_git_diff",
            "description": "Mostra o git diff do workspace. Use antes de commitar.",
            "parameters": {
                "type": "object",
                "properties": {
                    "staged_only": {
                        "type": "boolean",
                        "description": "Se true, mostra apenas mudanças staged.",
                    },
                    "path": {
                        "type": "string",
                        "description": "Arquivo específico. Padrão: todo o workspace.",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_git_log",
            "description": "Mostra histórico de commits recentes.",
            "parameters": {
                "type": "object",
                "properties": {
                    "count": {
                        "type": "integer",
                        "description": "Número de commits. Padrão 10.",
                    },
                    "oneline": {
                        "type": "boolean",
                        "description": "Formato condensado. Padrão: false.",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "preview_rename",
            "description": "Simula renomear um símbolo no workspace inteiro. Nenhum arquivo é modificado.",
            "parameters": {
                "type": "object",
                "properties": {
                    "symbol_old": {"type": "string"},
                    "symbol_new": {"type": "string"},
                    "extensions": {"type": "string"},
                },
                "required": ["symbol_old", "symbol_new"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_env_info",
            "description": "Retorna informações sobre o ambiente de desenvolvimento: versões de ferramentas, OS, workspace.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_dependency",
            "description": "Verifica se uma dependência está instalada, sua versão e localização.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Nome da dependência/ferramenta.",
                    },
                    "manager": {
                        "type": "string",
                        "description": "pip, npm, cargo, gem. Padrão: auto-detectar.",
                    },
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "diff_files",
            "description": "Mostra o diff entre dois arquivos ou entre um arquivo e um conteúdo fornecido.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path_a": {"type": "string", "description": "Primeiro arquivo."},
                    "path_b": {
                        "type": "string",
                        "description": "Segundo arquivo (ou omita para usar content_b).",
                    },
                    "content_b": {
                        "type": "string",
                        "description": "Conteúdo alternativo para comparar com path_a.",
                    },
                },
                "required": ["path_a"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_clipboard",
            "description": "Retorna o conteúdo atual da área de transferência do sistema.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Cria ou sobrescreve um arquivo no workspace atomicamente. Um backup (.bak) é criado. Requer aprovação.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                    "encoding": {"type": "string", "description": "Padrão: utf-8."},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "apply_edit",
            "description": "Realiza uma ou mais substituições precisas dentro de um arquivo. Forma preferida para mudanças cirúrgicas. Requer aprovação.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "edits": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "old_text": {"type": "string"},
                                "new_text": {"type": "string"},
                                "count": {
                                    "type": "integer",
                                    "description": "Qual ocorrência (1=primeira, 0=todas). Padrão 1.",
                                },
                                "regex": {
                                    "type": "boolean",
                                    "description": "Se true, old_text é regex Python. Padrão false.",
                                },
                            },
                            "required": ["old_text", "new_text"],
                        },
                    },
                    "dry_run": {
                        "type": "boolean",
                        "description": "Se true, retorna mudanças sem salvar.",
                    },
                },
                "required": ["path", "edits"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "insert_after_line",
            "description": "Insere um bloco de texto exatamente após um número de linha. Útil para adicionar imports ou novas funções.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "line": {
                        "type": "integer",
                        "description": "Linha após a qual inserir (base 1).",
                    },
                    "text": {"type": "string"},
                },
                "required": ["path", "line", "text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "format_code",
            "description": "Auto-formata um arquivo (Black para Python, Prettier para JS/TS, rustfmt, gofmt).",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "patch_files",
            "description": "Aplica edições em MÚLTIPLOS arquivos de uma vez. Ideal para refatorações grandes. Requer aprovação.",
            "parameters": {
                "type": "object",
                "properties": {
                    "patches": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "path": {"type": "string"},
                                "edits": {
                                    "type": "array",
                                    "items": {
                                        "type": "object",
                                        "properties": {
                                            "old_text": {"type": "string"},
                                            "new_text": {"type": "string"},
                                            "count": {"type": "integer"},
                                            "regex": {"type": "boolean"},
                                        },
                                        "required": ["old_text", "new_text"],
                                    },
                                },
                                "create_if_missing": {"type": "boolean"},
                            },
                            "required": ["path", "edits"],
                        },
                    },
                    "dry_run": {"type": "boolean"},
                },
                "required": ["patches"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_command",
            "description": "Executa um comando shell no diretório do workspace. Requer aprovação. Use para build, lint, test, install, git, etc.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string"},
                    "timeout": {
                        "type": "integer",
                        "description": "Timeout em segundos. Padrão 60.",
                    },
                    "working_dir": {
                        "type": "string",
                        "description": "Subpasta do workspace. Padrão: raiz.",
                    },
                    "env": {
                        "type": "object",
                        "description": "Variáveis de ambiente extras.",
                    },
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_script",
            "description": "Escreve código em arquivo temporário e executa com o runtime escolhido. Requer aprovação.",
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {"type": "string"},
                    "language": {
                        "type": "string",
                        "description": "python, bash, node, ruby, perl. Padrão: python.",
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "Máx segundos. Padrão 30.",
                    },
                    "env": {"type": "object"},
                },
                "required": ["code"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_tests",
            "description": "Detecta o framework de testes e executa. Requer aprovação.",
            "parameters": {
                "type": "object",
                "properties": {
                    "target": {
                        "type": "string",
                        "description": "Arquivo ou módulo. Padrão: projeto inteiro.",
                    },
                    "extra_args": {"type": "string"},
                    "framework": {
                        "type": "string",
                        "description": "pytest, jest, cargo, go. Padrão: auto.",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_directory",
            "description": "Cria uma pasta no workspace (incluindo intermediárias). Requer aprovação.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_file",
            "description": "Remove um arquivo do workspace. Cria backup antes. Requer aprovação.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_directory",
            "description": "⚠️ Remove uma pasta. Com recursive=true, deleta tudo dentro. AÇÃO IRREVERSÍVEL. Requer aprovação CRÍTICA.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "recursive": {
                        "type": "boolean",
                        "description": "Deleta conteúdo recursivamente.",
                    },
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "move_file",
            "description": "Move ou renomeia um arquivo/pasta no workspace. Requer aprovação.",
            "parameters": {
                "type": "object",
                "properties": {
                    "src": {"type": "string"},
                    "dst": {"type": "string"},
                },
                "required": ["src", "dst"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "copy_file",
            "description": "Copia um arquivo ou pasta para outro local no workspace. Requer aprovação.",
            "parameters": {
                "type": "object",
                "properties": {
                    "src": {"type": "string"},
                    "dst": {"type": "string"},
                },
                "required": ["src", "dst"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "init_project",
            "description": (
                "Inicializa um projeto do ZERO em uma pasta: git init, npm init, estrutura de pastas, "
                "arquivos base (README, .gitignore, etc). "
                "Templates: 'node-ts', 'python-lib', 'react', 'fastapi', 'rust', 'vazio'. "
                "Requer aprovação."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Pasta onde inicializar. Padrão: workspace atual.",
                    },
                    "template": {
                        "type": "string",
                        "description": "node-ts, python-lib, react, fastapi, rust, vazio.",
                    },
                    "name": {"type": "string", "description": "Nome do projeto."},
                    "git": {
                        "type": "boolean",
                        "description": "Fazer git init? Padrão: true.",
                    },
                    "extra_cmds": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Comandos adicionais após inicialização.",
                    },
                },
                "required": ["template"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "install_dependency",
            "description": "Instala uma dependência usando o gerenciador detectado (pip, npm, cargo, gem). Requer aprovação.",
            "parameters": {
                "type": "object",
                "properties": {
                    "package": {"type": "string"},
                    "manager": {
                        "type": "string",
                        "description": "pip, npm, cargo, gem. Padrão: auto.",
                    },
                    "dev": {
                        "type": "boolean",
                        "description": "Dev dependency? Padrão: false.",
                    },
                    "global_": {
                        "type": "boolean",
                        "description": "Global? Padrão: false.",
                    },
                    "extra_args": {"type": "string"},
                },
                "required": ["package"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "git_stage",
            "description": "Faz git add. Requer aprovação.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Arquivo/pasta. Use '.' para todos. Padrão: todos.",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "git_commit",
            "description": "Faz git commit com mensagem. Requer aprovação.",
            "parameters": {
                "type": "object",
                "properties": {
                    "message": {"type": "string"},
                    "add_all": {
                        "type": "boolean",
                        "description": "Fazer git add -A antes? Padrão: false.",
                    },
                },
                "required": ["message"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "git_branch",
            "description": "Cria, muda ou lista branches git. Requer aprovação.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "description": "create, checkout, list, delete.",
                    },
                    "name": {"type": "string"},
                },
                "required": ["action"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "git_reset",
            "description": "⚠️ Git reset. Pode perder alterações. Requer aprovação CRÍTICA.",
            "parameters": {
                "type": "object",
                "properties": {
                    "mode": {
                        "type": "string",
                        "description": "soft, mixed, hard. Padrão: mixed.",
                    },
                    "target": {
                        "type": "string",
                        "description": "Ref/commit. Padrão: HEAD.",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "git_clean",
            "description": "⚠️ Remove arquivos não rastreados. IRREVERSÍVEL. Requer aprovação CRÍTICA.",
            "parameters": {
                "type": "object",
                "properties": {
                    "flags": {
                        "type": "string",
                        "description": "Flags git clean. Padrão: '-fd'.",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "open_terminal_tab",
            "description": "Abre uma nova aba no painel de terminal integrado, opcionalmente executando um comando inicial.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Nome da aba. Padrão: 'IA Terminal'.",
                    },
                    "command": {
                        "type": "string",
                        "description": "Comando inicial. Opcional.",
                    },
                },
                "required": [],
            },
        },
    },
]

_BAD_REF = "Nome de branch ou referência inválido."

_TRUST_REQUIRED = frozenset(
    {"run_command", "run_script", "run_tests", "init_project", "install_dependency",
     "open_terminal_tab", "format_code"}
)
_RESTRICTED_MSG = (
    "Workspace em modo restrito: ferramentas que executam código estão bloqueadas. "
    "Marque a pasta como confiável para usá-las."
)


class ToolExecutor:
    """Executa ferramentas da IA no workspace local.

    O ApprovalGate intercepta as chamadas antes de chegarem aqui; o executor assume
    que a permissão já foi concedida.
    """

    def __init__(
        self,
        get_editor: Any,
        get_workspace: Any,
        on_open_terminal: Callable[[str, str], None] | None = None,
        get_root_widget: Callable[[], Any] | None = None,
        get_open_editors: Callable[[], Any] | None = None,
    ) -> None:
        """Configura as dependências necessárias para execução das ferramentas.

        Args:
            get_editor:       Callable que retorna o editor ativo ou None.
            get_workspace:    Callable que retorna o caminho raiz do workspace ou None.
            on_open_terminal: Callback para abrir nova aba no terminal integrado.
            get_root_widget:  Callable que retorna o widget raiz Tkinter (para clipboard).
        """
        self._get_editor = get_editor
        self._get_workspace = get_workspace
        self._on_open_terminal = on_open_terminal
        self._get_root_widget = get_root_widget
        self._get_open_editors = get_open_editors
        self.checkpoints: CheckpointStore | None = None
        self._cp_session: str | None = None

    def set_session(self, session_id: str | None) -> None:
        """Define a sessão de IA atual (cada sessão tem seus checkpoints)."""
        if session_id != self._cp_session:
            self._cp_session = session_id
            self.checkpoints = None

    def _cps(self) -> CheckpointStore | None:
        if self.checkpoints is None and self._cp_session:
            root = self._workspace()
            if root is not None:
                self.checkpoints = CheckpointStore(root, self._cp_session)
        return self.checkpoints

    def begin_turn(self, label: str) -> None:
        """Abre um checkpoint para o turno da IA que vai começar."""
        cps = self._cps()
        if cps is not None:
            cps.begin(label)

    def end_turn(self) -> Any:
        """Fecha o checkpoint do turno; retorna-o (ou ``None`` se nada mudou)."""
        cps = self.checkpoints
        return cps.commit() if cps is not None else None

    def undo_turn(self, cp_id: int | None = None, *, force: bool = False) -> Any:
        """Desfaz um checkpoint (padrão: o mais recente)."""
        cps = self._cps()
        return cps.undo(cp_id, force=force) if cps is not None else None

    def _open_editor_for(self, path: Path) -> Any | None:
        """Editor aberto cujo arquivo é *path* (ou ``None``)."""
        try:
            editors = list(self._get_open_editors()) if self._get_open_editors else []
            if not editors and self._get_editor:
                ed = self._get_editor()
                editors = [ed] if ed is not None else []
            target = path.resolve()
            for ed in editors:
                ep = getattr(ed, "path", None)
                if ep and Path(ep).resolve() == target:
                    return ed
        except Exception:  # noqa: BLE001
            logger.debug("busca de editor aberto falhou", exc_info=True)
        return None

    def _unsaved_block(self, dest: Path) -> str | None:
        """Mensagem de bloqueio se *dest* tem alterações não salvas no editor.

        Escrever por cima apagaria trabalho do usuário que só existe no buffer.
        """
        ed = self._open_editor_for(dest)
        if ed is not None and getattr(ed, "modified", False):
            return _t(
                "ia.tool_unsaved_block", name=dest.name,
                default="[Bloqueado] '{name}' tem alterações não salvas no editor. "
                        "Peça ao usuário para salvar (Ctrl+S) e tente de novo; "
                        "use read_active_file para ver o conteúdo atual do editor.",
            )
        return None

    def execute(self, name: str, args: dict[str, Any]) -> str:
        """
        Retorna sempre uma string - o resultado da ferramenta ou mensagem de erro.
        """
        try:
            method = getattr(self, f"_tool_{name}", None)
            if method is None:
                return _t("ia.tool_unknown", name=name)
            if name in _TRUST_REQUIRED and is_restricted():
                return _RESTRICTED_MSG
            return method(**args)
        except TypeError as exc:
            return _t("ia.tool_bad_args", name=name, err=exc)
        except Exception as exc:
            logger.warning("Ferramenta '%s' falhou: %s",
                           name, exc, exc_info=True)
            return f"[Erro] {exc}"

    def _workspace(self) -> Path | None:
        """Retorna o Path do workspace ativo, ou None se nenhum estiver aberto."""
        ws = self._get_workspace()
        return Path(ws) if ws else None

    _SENSITIVE_NAMES = frozenset(
        {".env", ".netrc", ".pgpass", ".npmrc", ".pypirc", ".git-credentials",
         "id_rsa", "id_ed25519", "id_ecdsa", "id_dsa", "authorized_keys", ".gitconfig"}
    )
    _SENSITIVE_SUFFIXES = frozenset({".pem", ".key", ".p12", ".pfx", ".kdbx"})

    def _allowed_roots(self) -> list[Path]:
        """Diretórios onde a IA pode ler: workspace e pasta do arquivo ativo."""
        roots: list[Path] = []
        root = self._workspace()
        if root:
            roots.append(root.resolve())
        editor = self._get_editor() if self._get_editor else None
        active = getattr(editor, "path", None) if editor else None
        if active:
            roots.append(Path(active).resolve().parent)
        return roots

    @staticmethod
    def _is_within(path: Path, root: Path) -> bool:
        """Indica se `path`, já resolvido, está dentro de `root` (ou é igual a ele)."""
        return path == root or root in path.parents

    def _is_sensitive(self, path: Path) -> bool:
        """Indica se o arquivo costuma guardar credenciais e não deve ser lido pela IA."""
        name = path.name.lower()
        return (
            name in self._SENSITIVE_NAMES
            or name.startswith(".env.")
            or path.suffix.lower() in self._SENSITIVE_SUFFIXES
        )

    def _resolve(self, path: str) -> Path | None:
        """Resolve `path` para um arquivo existente dentro das áreas permitidas.

        Tenta, em ordem: absoluto, relativo ao workspace, relativo à pasta do
        editor ativo. Caminhos fora do workspace e da pasta do arquivo ativo,
        e arquivos de credenciais, resultam em None.
        """
        raw = Path(path)
        candidates = [raw] if raw.is_absolute() else []
        if not raw.is_absolute():
            candidates.extend(base / raw for base in self._allowed_roots())
        allowed = self._allowed_roots()
        for candidate in candidates:
            try:
                resolved = candidate.resolve()
            except (OSError, RuntimeError):
                continue
            if not resolved.exists() or self._is_sensitive(resolved):
                continue
            if any(self._is_within(resolved, root) for root in allowed):
                return resolved
        return None

    def _confine(self, path: str) -> Path | None:
        """Resolve `path` para escrita, exigindo que fique dentro do workspace.

        O arquivo não precisa existir. Retorna None se não houver workspace ou
        se o caminho, após resolver ``..`` e links simbólicos, escapar dele.
        """
        root = self._workspace()
        if root is None:
            return None
        base = root.resolve()
        try:
            target = (base / path).resolve()
        except (OSError, RuntimeError):
            return None
        return target if self._is_within(target, base) else None

    def _read(self, fp: Path) -> str:
        """Lê um arquivo como texto UTF-8, substituindo bytes inválidos.

        Se o arquivo está aberto com alterações não salvas, devolve o buffer do
        editor (o que o usuário realmente está vendo), não a versão antiga do disco.
        """
        ed = self._open_editor_for(fp)
        if ed is not None and getattr(ed, "modified", False):
            with contextlib.suppress(Exception):
                return ed.text.get("1.0", "end-1c")
        return fp.read_text("utf-8", errors="replace")

    def _git(self, args: list[str], cwd: str | None = None) -> str:
        """Executa um subcomando git e retorna stdout + stderr como string.

        Usa o workspace como diretório de trabalho se `cwd` não for fornecido.
        """
        root = self._workspace()
        c = cwd or (str(root) if root else None)
        try:
            r = subprocess.run(
                ["git"] + args,
                cwd=c,
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
            out = r.stdout.strip()
            err = r.stderr.strip()
            if r.returncode != 0 and err:
                return _t("ia.tool_git_error", err=err)
            return out or "(sem output)"
        except FileNotFoundError:
            return _t("ia.tool_git_unavail")
        except subprocess.TimeoutExpired:
            return _t("ia.tool_git_timeout")

    def _safe_write(self, dest: Path, content: str, encoding: str = "utf-8") -> str:
        """Escreve `content` em `dest` atomicamente via arquivo temporário.

        Cria um backup `.bak` se o arquivo já existir. Cria diretórios intermediários
        automaticamente. Retorna mensagem i18n de sucesso ou erro.
        """
        root = self._workspace()
        blocked = self._unsaved_block(dest)
        if blocked:
            return blocked
        cps = self._cps()
        if cps is not None and cps._current is not None:  # noqa: SLF001
            cps.record_write(dest)
        elif dest.exists() and dest.stat().st_size > 0:
            bak = dest.with_suffix(dest.suffix + ".bak")
            with contextlib.suppress(OSError):
                bak.write_bytes(dest.read_bytes())
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding=encoding,
                dir=dest.parent,
                prefix=f".tmp_{dest.name}_",
                suffix=".tmp",
                delete=False,
            ) as tmp:
                tmp.write(content)
                tmp_path = Path(tmp.name)
            tmp_path.replace(dest)
            if cps is not None and cps._current is not None:  # noqa: SLF001
                cps.record_after(dest)
            sz = dest.stat().st_size
            sz_str = f"{sz // 1024}KB" if sz >= 1024 else f"{sz}B"
            try:
                rel = dest.relative_to(root) if root else dest
            except ValueError:
                rel = dest
            return _t("ia.tool_write_ok", path=str(rel), size=sz_str)
        except Exception as exc:
            return _t("ia.tool_write_err", path=str(dest), err=exc)

    _IGNORED = {
        "__pycache__",
        ".codis_trash",
        "node_modules",
        ".git",
        "venv",
        ".venv",
        "dist",
        "build",
        "target",
        ".mypy_cache",
    }

    def _tool_read_active_file(self) -> str:
        """Retorna o conteúdo do editor ativo com número de linhas à esquerda."""
        editor = self._get_editor()
        if editor is None:
            return _t("ia.tool_no_editor")
        fp = getattr(editor, "path", None)
        lang = getattr(editor, "lang", "")
        try:
            content = editor.text.get("1.0", "end-1c")
        except Exception:
            if fp:
                try:
                    content = Path(fp).read_text("utf-8", errors="replace")
                except OSError as e:
                    return _t("ia.tool_read_error", path=str(fp) if fp else "?", err=e)
            else:
                return _t("ia.tool_no_editor")
        lines = content.splitlines(keepends=True)
        total = len(lines)
        header = f"# Arquivo ativo: {fp or '(sem nome)'}  |  Linguagem: {lang}  |  Total: {total} linhas\n\n"
        return header + "".join(f"{i + 1:4d}  {line}" for i, line in enumerate(lines))

    def _tool_get_cursor_context(self, window: int = 40) -> str:
        """Retorna as `window` linhas antes e depois da posição atual do cursor.

        A linha do cursor é marcada com `>>>`. Inclui cabeçalho com arquivo, linguagem
        e posição do cursor.
        """
        editor = self._get_editor()
        if editor is None:
            return _t("ia.tool_no_editor")
        fp = getattr(editor, "path", None)
        lang = getattr(editor, "lang", "")
        try:
            index = editor.text.index("insert")
            row, col = index.split(".")
            cur_line = int(row)
            cur_col = int(col) + 1
        except Exception:
            return _t("ia.tool_cursor_error")
        try:
            content = editor.text.get("1.0", "end-1c")
        except Exception:
            return _t("ia.tool_no_editor")
        lines = content.splitlines()
        total = len(lines)
        start = max(0, cur_line - window - 1)
        end = min(total, cur_line + window)
        region = lines[start:end]
        numbered = "\n".join(
            f"{'>>>' if start + i + 1 == cur_line else '   '} {start + i + 1:4d}  {line}"
            for i, line in enumerate(region)
        )
        return (
            f"# Arquivo: {fp or '(sem nome)'}  |  Linguagem: {lang}\n"
            f"# Cursor: linha {cur_line}, col {cur_col}\n"
            f"# Mostrando linhas {start + 1}–{start + len(region)} de {total}\n"
            f"# (>>> = linha do cursor)\n\n{numbered}"
        )

    def _tool_read_file(
        self, path: str, start_line: int | None = None, end_line: int | None = None
    ) -> str:
        """Lê um arquivo completo ou intervalo de linhas.

        Args:
            path:       Caminho relativo ao workspace ou absoluto.
            start_line: Primeira linha a incluir (base 1). None = início do arquivo.
            end_line:   Última linha a incluir (base 1). None = fim do arquivo.
        """
        resolved = self._resolve(path)
        if resolved is None:
            return _t("ia.tool_file_not_found", path=path)
        try:
            content = self._read(resolved)
            lines = content.splitlines(keepends=True)
            lf = max(0, (start_line or 1) - 1)
            lt = end_line if end_line else len(lines)
            excerpt = "".join(lines[lf:lt])
            total = len(lines)
            return f"# Arquivo: {resolved}  |  Linhas {lf + 1}-{min(lt, total)} de {total}\n\n{excerpt}"
        except OSError as exc:
            return _t("ia.tool_read_error", path=path, err=exc)

    def _tool_read_file_tree(
        self, max_depth: int = 4, show_hidden: bool = False
    ) -> str:
        """Retorna a estrutura de diretórios do workspace em formato de árvore visual.

        Ignora pastas de artefatos gerados (node_modules, __pycache__, dist etc.).
        """
        root = self._workspace()
        if root is None:
            return _t("ia.tool_no_workspace")
        lines: list[str] = [f"# Árvore: {root.name}\n{root.name}/"]

        def walk(path: Path, prefix: str, depth: int) -> None:
            """Percorre recursivamente o diretório e emite entradas de arquivo."""
            if depth > max_depth:
                return
            try:
                entries = sorted(
                    path.iterdir(), key=lambda p: (p.is_file(), p.name.lower())
                )
            except PermissionError:
                return
            entries = [
                e
                for e in entries
                if (show_hidden or not e.name.startswith("."))
                and e.name not in self._IGNORED
            ]
            for i, entry in enumerate(entries):
                connector = "\\-- " if i == len(entries) - 1 else "|-- "
                ext = "    " if i == len(entries) - 1 else "│   "
                if entry.is_dir():
                    lines.append(f"{prefix}{connector}{entry.name}/")
                    walk(entry, prefix + ext, depth + 1)
                else:
                    sz = entry.stat().st_size
                    lines.append(
                        f"{prefix}{connector}{entry.name}"
                        + (f" ({sz // 1024}KB)" if sz >= 1024 else "")
                    )

        walk(root, "", 1)
        return "\n".join(lines[:500])

    def _tool_list_files(
        self, folder: str = ".", recursive: bool = False, extensions: str = ""
    ) -> str:
        """Lista arquivos e pastas dentro de `folder`.

        Args:
            folder:     Subpasta relativa ao workspace. Padrão: raiz.
            recursive:  Se True, percorre subdiretórios.
            extensions: Filtro de extensões separadas por vírgula, ex: `.py,.ts`.
        """
        root = self._workspace()
        if root is None:
            return _t("ia.tool_no_workspace")
        target = (root / folder).resolve()
        if not target.exists():
            return _t("ia.tool_folder_missing", path=folder)
        ext_filter = (
            {e.strip() for e in extensions.split(",") if e.strip()}
            if extensions
            else set()
        )
        lines = [f"# Conteúdo de: {target.relative_to(root)}\n"]
        pattern = "**/*" if recursive else "*"
        for p in sorted(target.glob(pattern)):
            rel = p.relative_to(root)
            if p.is_dir():
                lines.append(f"📁  {rel}/")
            elif not ext_filter or p.suffix.lower() in ext_filter:
                sz = p.stat().st_size
                lines.append(
                    f"📄  {rel}  ({sz // 1024}KB)"
                    if sz >= 1024
                    else f"📄  {rel}  ({sz}B)"
                )
        return "\n".join(lines[:300])

    def _tool_search_in_files(
        self,
        query: str,
        extensions: str = "",
        max_results: int = 30,
        case_sensitive: bool = False,
    ) -> str:
        """
        Args:
            query:          Expressão regular Python.
            max_results:    Número máximo de correspondências retornadas.
            case_sensitive: Se False, ignora maiúsculas/minúsculas.
        """
        root = self._workspace()
        if root is None:
            return _t("ia.tool_no_workspace")
        ext_filter = (
            {e.strip() for e in extensions.split(",") if e.strip()}
            if extensions
            else set()
        )
        flags = 0 if case_sensitive else re.IGNORECASE
        try:
            pattern = re.compile(query, flags)
        except re.error as exc:
            return _t("ia.tool_bad_regex", err=exc)
        results: list[str] = []
        for fp in sorted(root.rglob("*")):
            if not fp.is_file():
                continue
            if any(p in self._IGNORED for p in fp.parts):
                continue
            if ext_filter and fp.suffix.lower() not in ext_filter:
                continue
            try:
                for i, line in enumerate(self._read(fp).splitlines(), 1):
                    if pattern.search(line):
                        results.append(
                            f"{fp.relative_to(root)}:{i}:  {line.strip()[:120]}"
                        )
                        if len(results) >= max_results:
                            break
            except OSError:
                continue
            if len(results) >= max_results:
                break
        if not results:
            return f"Nenhum resultado para '{query}'."
        return f"# {len(results)} resultado(s) para '{query}'\n\n" + "\n".join(results)

    def _tool_find_definition(self, symbol: str, extensions: str = "") -> str:
        """Localiza a definição de um símbolo (classe, função, variável) no workspace.

        Usa padrões de regex específicos para Python, JS/TS e Rust/Go.
        Retorna até 5 ocorrências com contexto de linhas ao redor.
        """
        root = self._workspace()
        if root is None:
            return _t("ia.tool_no_workspace")
        ext_filter = (
            {e.strip() for e in extensions.split(",") if e.strip()}
            if extensions
            else set()
        )
        sym_esc = re.escape(symbol)
        def_re = re.compile(
            rf"^\s*(?:async\s+)?(?:def|class)\s+{sym_esc}\b|"
            rf"^(?:export\s+)?(?:async\s+)?(?:function|class)\s+{sym_esc}\b|"
            rf"^(?:export\s+)?(?:const|let|var)\s+{sym_esc}\s*=|"
            rf"^(?:pub\s+)?(?:fn|struct|enum|trait|impl|mod|type)\s+{sym_esc}\b",
            re.MULTILINE | re.IGNORECASE,
        )
        results: list[str] = []
        for fp in sorted(root.rglob("*")):
            if not fp.is_file():
                continue
            if any(p in self._IGNORED for p in fp.parts):
                continue
            if ext_filter and fp.suffix.lower() not in ext_filter:
                continue
            try:
                fl = self._read(fp).splitlines()
                for i, line in enumerate(fl, 1):
                    if def_re.search(line):
                        ctx = "\n".join(
                            f"{'>>>' if j + 1 == i else '   '} {j + 1:4d}  {fl[j]}"
                            for j in range(max(0, i - 4), min(len(fl), i + 5))
                        )
                        results.append(
                            f"## {fp.relative_to(root)}  (linha {i})\n{ctx}")
                        if len(results) >= 5:
                            break
            except OSError:
                continue
            if len(results) >= 5:
                break
        if not results:
            return f"Nenhuma definição encontrada para '{symbol}'."
        return f"# Definições de: {symbol}\n\n" + "\n\n".join(results)

    def _tool_find_references(
        self, symbol: str, extensions: str = "", max_results: int = 30
    ) -> str:
        """
        Retorna cada ocorrência com duas linhas de contexto acima e abaixo.
        """
        root = self._workspace()
        if root is None:
            return _t("ia.tool_no_workspace")
        ext_filter = (
            {e.strip() for e in extensions.split(",") if e.strip()}
            if extensions
            else set()
        )
        sym_esc = re.escape(symbol)
        usage_re = re.compile(rf"\b{sym_esc}\b")
        def_re = re.compile(
            rf"^\s*(?:async\s+)?(?:def|class)\s+{sym_esc}\b|"
            rf"^(?:export\s+)?(?:async\s+)?(?:function|class)\s+{sym_esc}\b|"
            rf"^(?:export\s+)?(?:const|let|var)\s+{sym_esc}\s*=",
            re.MULTILINE | re.IGNORECASE,
        )
        results: list[str] = []
        for fp in sorted(root.rglob("*")):
            if not fp.is_file():
                continue
            if any(p in self._IGNORED for p in fp.parts):
                continue
            if ext_filter and fp.suffix.lower() not in ext_filter:
                continue
            try:
                fl = self._read(fp).splitlines()
                for i, line in enumerate(fl, 1):
                    if usage_re.search(line) and not def_re.search(line):
                        ctx = fl[max(0, i - 2): i + 2]
                        ctx_str = "\n".join(
                            f"   {j + 1:4d}  {l_}"
                            for j, l_ in enumerate(ctx, max(0, i - 2))
                        )
                        results.append(
                            f"{fp.relative_to(root)}:{i}\n{ctx_str}")
                        if len(results) >= max_results:
                            break
            except OSError:
                continue
            if len(results) >= max_results:
                break
        if not results:
            return f"Nenhuma referência encontrada para '{symbol}'."
        return f"# Referências para '{symbol}'\n\n" + "\n\n".join(results)

    def _tool_get_project_summary(self) -> str:
        """Retorna um resumo do projeto: tipo, linguagens, dependências, git e estrutura raiz."""
        root = self._workspace()
        if root is None:
            return _t("ia.tool_no_workspace")
        parts = [f"# Resumo: {root.name}\n**Caminho:** {root}\n"]
        markers = {
            "pyproject.toml": "Python",
            "setup.py": "Python",
            "package.json": "Node.js",
            "Cargo.toml": "Rust",
            "go.mod": "Go",
            "pom.xml": "Java",
            "Gemfile": "Ruby",
        }
        for m, label in markers.items():
            if (root / m).exists():
                parts.append(f"**Tipo:** {label}")
                break
        ext_map = {
            ".py": "Python",
            ".js": "JavaScript",
            ".ts": "TypeScript",
            ".java": "Java",
            ".rs": "Rust",
            ".go": "Go",
            ".html": "HTML",
            ".css": "CSS",
            ".cs": "C#",
            ".cpp": "C++",
        }
        counts: dict[str, int] = {}
        for p in root.rglob("*"):
            if p.is_file() and p.suffix.lower() in ext_map:
                if not any(d in self._IGNORED for d in p.parts):
                    lang = ext_map[p.suffix.lower()]
                    counts[lang] = counts.get(lang, 0) + 1
        if counts:
            top = sorted(counts.items(), key=lambda x: -x[1])[:6]
            parts.append(
                f"**Linguagens:** {', '.join(f'{l_} ({n})' for l_, n in top)}")
        branch = self._git(["branch", "--show-current"])
        if branch and "[git" not in branch:
            commit = self._git(["log", "-1", "--pretty=format:%h - %s"])
            parts.append(f"**Git:** branch `{branch}` | {commit}")
            recent = self._git(
                ["log", "-5", "--pretty=format:%h  %ad  %s", "--date=short"]
            )
            if recent:
                parts.append(f"**Commits recentes:**\n```\n{recent}\n```")
        try:
            entries = sorted(
                root.iterdir(), key=lambda p: (p.is_file(), p.name.lower())
            )
            tree = [
                (f"  {e.name}/" if e.is_dir() else f"  {e.name}")
                for e in entries
                if not e.name.startswith(".") and e.name not in self._IGNORED
            ]
            if tree:
                parts.append("**Estrutura raiz:**\n```\n" +
                             "\n".join(tree) + "\n```")
        except OSError:
            pass
        return "\n".join(parts)

    def _tool_read_diagnostics(self) -> str:
        """Retorna os diagnósticos (erros e avisos) do editor ativo registrados pelo LSP."""
        editor = self._get_editor()
        if editor is None:
            return _t("ia.tool_no_editor")
        diags = getattr(editor, "_diags", [])
        if not diags:
            return _t("ia.tool_no_diags")
        lines = [f"# Diagnósticos: {getattr(editor, 'path', 'arquivo')}\n"]
        for d in diags:
            level = getattr(d, "level", "?").upper()
            line_no = getattr(d, "line", 0)
            msg = getattr(d, "message", str(d))
            source = getattr(d, "source", "")
            entry = f"[{level}] linha {line_no}: {msg}"
            if source:
                entry += f"  ({source})"
            lines.append(entry)
        return "\n".join(lines)

    def _tool_read_symbols(self) -> str:
        """Lista símbolos de nível superior (classes, funções) do arquivo ativo com números de linha."""
        editor = self._get_editor()
        if editor is None:
            return _t("ia.tool_no_editor")
        fp = getattr(editor, "path", None)
        lang = getattr(editor, "lang", "")
        if fp is None:
            return _t("ia.tool_no_editor")
        try:
            content = self._read(Path(fp))
        except OSError as exc:
            return f"Erro ao ler arquivo: {exc}"
        symbols: list[str] = []
        for i, line in enumerate(content.splitlines(), 1):
            if lang == "python":
                m = re.match(r"^(class|def|async def)\s+(\w+)", line)
                if m:
                    symbols.append(f"linha {i:4d}: {m.group(1)} {m.group(2)}")
            elif lang in ("javascript", "typescript"):
                m = re.match(
                    r"^\s*(?:export\s+)?(?:async\s+)?(?:function|class|const|let|var)\s+(\w+)",
                    line,
                )
                if m:
                    symbols.append(f"linha {i:4d}: {line.strip()[:80]}")
            else:
                if re.match(r"^\s*(?:func|function|class|def|fn)\s+\w+", line):
                    symbols.append(f"linha {i:4d}: {line.strip()[:80]}")
        if not symbols:
            return f"Nenhum símbolo em {Path(fp).name}."
        return f"# Símbolos em {Path(fp).name}\n\n" + "\n".join(symbols[:100])

    def _tool_workspace_symbols(self, extensions: str = "") -> str:
        """
        Retorna um mapa organizado por arquivo, limitado a 50 símbolos por arquivo.
        """
        root = self._workspace()
        if root is None:
            return _t("ia.tool_no_workspace")
        ext_filter = (
            {e.strip() for e in extensions.split(",") if e.strip()}
            if extensions
            else set()
        )
        symbol_map: dict[str, list[str]] = {}
        py_re = re.compile(r"^(class|def|async def)\s+(\w+)")
        js_re = re.compile(
            r"^\s*(?:export\s+)?(?:async\s+)?(?:function|class|const|let|var)\s+(\w+)"
        )
        for fp in sorted(root.rglob("*")):
            if not fp.is_file():
                continue
            if any(p in self._IGNORED for p in fp.parts):
                continue
            if ext_filter and fp.suffix.lower() not in ext_filter:
                continue
            try:
                rel = str(fp.relative_to(root))
                for i, line in enumerate(self._read(fp).splitlines(), 1):
                    if fp.suffix == ".py":
                        m = py_re.match(line)
                        if m:
                            symbol_map.setdefault(rel, []).append(
                                f"  linha {i:4d}: {m.group(1)} {m.group(2)}"
                            )
                    elif fp.suffix in (".js", ".ts"):
                        m = js_re.match(line)
                        if m:
                            symbol_map.setdefault(rel, []).append(
                                f"  linha {i:4d}: {line.strip()[:80]}"
                            )
                    else:
                        if re.match(r"^\s*(?:func|function|class|def|fn)\s+\w+", line):
                            symbol_map.setdefault(rel, []).append(
                                f"  linha {i:4d}: {line.strip()[:80]}"
                            )
            except OSError:
                continue
        if not symbol_map:
            return _t("ia.tool_no_workspace")
        out = ["# Símbolos do workspace\n"]
        for fname in sorted(symbol_map):
            out.append(f"## {fname}")
            out.extend(symbol_map[fname][:50])
            out.append("")
        return "\n".join(out[:600])

    def _tool_read_git_diff(self, staged_only: bool = False, path: str = "") -> str:
        """Retorna o diff git do workspace, opcionalmente filtrado por arquivo ou staged."""
        args = ["diff"]
        if staged_only:
            args.append("--staged")
        if path:
            args.extend(["--", path])
        diff = self._git(args)
        return diff[:8000] if diff else _t("ia.tool_no_changes")

    def _tool_read_git_log(self, count: int = 10, oneline: bool = False) -> str:
        """Retorna os últimos `count` commits em formato tabular ou condensado."""
        fmt = "%h  %ad  %an  %s" if not oneline else "%h %s"
        result = self._git(
            ["log", f"-{count}", f"--pretty=format:{fmt}", "--date=short"]
        )
        return f"# {result}" if result else _t("ia.tool_no_commits")

    def _tool_preview_rename(
        self, symbol_old: str, symbol_new: str, extensions: str = ""
    ) -> str:
        """Simula renomear `symbol_old` para `symbol_new` sem modificar nenhum arquivo.

        Retorna lista de arquivos e linhas que seriam alterados.
        """
        root = self._workspace()
        if root is None:
            return _t("ia.tool_no_workspace")
        ext_filter = (
            {e.strip() for e in extensions.split(",") if e.strip()}
            if extensions
            else set()
        )
        pattern = re.compile(rf"\b{re.escape(symbol_old)}\b")
        lines: list[str] = []
        for fp in sorted(root.rglob("*")):
            if not fp.is_file():
                continue
            if any(p in self._IGNORED for p in fp.parts):
                continue
            if ext_filter and fp.suffix.lower() not in ext_filter:
                continue
            try:
                fl = self._read(fp).splitlines()
                changed = False
                for i, line in enumerate(fl, 1):
                    if pattern.search(line):
                        lines.append(
                            f"{fp.relative_to(root)}:{i}:  {line.strip()}")
                        lines.append(
                            f"        ->  {pattern.sub(symbol_new, line).strip()}"
                        )
                        changed = True
                if changed:
                    lines.append("")
            except OSError:
                continue
            if len(lines) > 200:
                lines.append("... (truncado)")
                break
        if not lines:
            return f"Nenhuma ocorrência de '{symbol_old}'."
        return f"# Preview rename: {symbol_old} -> {symbol_new}\n\n" + "\n".join(lines)

    def _tool_get_env_info(self) -> str:
        """Retorna versões de ferramentas instaladas (git, node, python etc.), OS e workspace."""
        import platform

        parts = ["# Informações do Ambiente\n"]
        parts.append(
            f"**OS:** {platform.system()} {platform.release()} ({platform.machine()})"
        )
        parts.append(f"**Python:** {platform.python_version()}")
        tools = [
            "git",
            "node",
            "npm",
            "npx",
            "cargo",
            "go",
            "ruby",
            "java",
            "docker",
            "pip",
            "pip3",
            "black",
            "prettier",
            "ruff",
            "mypy",
        ]
        found = []
        for tool in tools:
            try:
                r = subprocess.run(
                    [tool, "--version"],
                    capture_output=True,
                    text=True,
                    timeout=3,
                    check=False,
                )
                ver = (
                    r.stdout.strip().splitlines()[
                        0][:60] if r.returncode == 0 else None
                )
                found.append(
                    f"  {'✅' if ver else '❌'} {tool}: {ver or 'não disponível'}"
                )
            except FileNotFoundError:
                found.append(f"  ❌ {tool}: não encontrado")
            except Exception:
                found.append(f"  ⚠️  {tool}: erro")
        parts.append("**Ferramentas:**\n" + "\n".join(found))
        root = self._workspace()
        if root:
            parts.append(f"**Workspace:** {root}")
        return "\n".join(parts)

    def _tool_check_dependency(self, name: str, manager: str = "") -> str:
        """Verifica se uma dependência está instalada e retorna sua versão."""
        candidates = {
            "pip": ["pip", "show", name],
            "npm": ["npm", "list", name, "--depth=0"],
            "cargo": ["cargo", "search", "--limit", "1", name],
        }
        to_try = {manager: candidates[manager]
                  } if manager in candidates else candidates
        results: list[str] = []
        for mgr, cmd in to_try.items():
            try:
                r = subprocess.run(
                    cmd, capture_output=True, text=True, timeout=8, check=False
                )
                if r.returncode == 0 and r.stdout.strip():
                    results.append(
                        f"✅ {mgr}: {r.stdout.strip().splitlines()[0][:100]}"
                    )
                else:
                    results.append(f"❌ {mgr}: não instalado")
            except FileNotFoundError:
                results.append(f"⚠️  {mgr}: gerenciador não encontrado")
            except subprocess.TimeoutExpired:
                results.append(f"⚠️  {mgr}: timeout")
        return f"# Verificação: {name}\n\n" + "\n".join(results)

    def _tool_diff_files(
        self, path_a: str, path_b: str = "", content_b: str = ""
    ) -> str:
        """Compara dois arquivos ou um arquivo com um conteúdo fornecido em formato unified diff."""
        import difflib

        ra = self._resolve(path_a)
        if ra is None:
            return f"Arquivo não encontrado: {path_a}"
        try:
            lines_a = self._read(ra).splitlines(keepends=True)
        except OSError as e:
            return f"Erro ao ler {path_a}: {e}"
        if path_b:
            rb = self._resolve(path_b)
            if rb is None:
                return f"Arquivo não encontrado: {path_b}"
            try:
                lines_b = self._read(rb).splitlines(keepends=True)
            except OSError as e:
                return f"Erro ao ler {path_b}: {e}"
            label_b = path_b
        elif content_b:
            lines_b = content_b.splitlines(keepends=True)
            label_b = "(conteúdo fornecido)"
        else:
            return _t("ia.tool_bad_path")
        diff = list(
            difflib.unified_diff(
                lines_a, lines_b, fromfile=path_a, tofile=label_b, lineterm=""
            )
        )
        return "\n".join(diff[:300]) if diff else "Arquivos idênticos."

    def _tool_get_clipboard(self) -> str:
        """Retorna o conteúdo atual da área de transferência do sistema via Tkinter."""
        rw = self._get_root_widget() if self._get_root_widget else None
        if rw is None:
            return _t("ia.tool_no_root_widget")
        try:
            return rw.clipboard_get()
        except Exception as e:
            return _t("ia.tool_clipboard_empty", err=e)

    def _tool_write_file(self, path: str, content: str, encoding: str = "utf-8") -> str:
        """Cria ou sobrescreve `path` com `content` de forma atômica.

        Recusa escrita em arquivos sensíveis (.env, .pem, .key etc.).
        Cria um backup `.bak` antes de sobrescrever arquivos existentes.
        """
        root = self._workspace()
        if root is None:
            return _t("ia.tool_no_workspace")
        dest = self._confine(path)
        if dest is None:
            return f"Caminho '{path}' fora do workspace."
        blocked_ext = {".pyc", ".so", ".dll", ".exe", ".bin", ".key", ".pem"}
        if self._is_sensitive(dest) or dest.suffix.lower() in blocked_ext:
            return f"Escrita em '{dest.name}' não permitida."
        return self._safe_write(dest, content, encoding)

    def _tool_apply_edit(
        self, path: str, edits: list[dict[str, Any]], dry_run: bool = False
    ) -> str:
        """Aplica uma lista de substituições cirúrgicas em `path`.

        Cada edição especifica `old_text` (literal ou regex) e `new_text`.
        Com `dry_run=True`, retorna o diff sem gravar em disco.
        """
        resolved = self._resolve(path) or self._confine(path)
        if resolved is None or not resolved.exists():
            return _t("ia.tool_file_not_found", path=path)
        if self._is_sensitive(resolved):
            return f"Escrita em '{resolved.name}' não permitida."
        try:
            content = self._read(resolved)
        except OSError as exc:
            return _t("ia.tool_read_error", path=path, err=exc)
        original = content
        changes: list[str] = []
        for idx, edit in enumerate(edits, 1):
            old = edit["old_text"]
            new = edit.get("new_text", "")
            count = edit.get("count", 1)
            use_re = edit.get("regex", False)
            pattern = re.compile(
                re.escape(old) if not use_re else old, re.MULTILINE)
            occ = 0

            def repl(m: re.Match, _c: int = count, _n: str = new) -> str:
                """Executa um REPL de código via toolbox de IA e retorna a saída."""
                nonlocal occ
                occ += 1
                return (
                    _n if (_c == 0 or occ == _c) else m.group(0)
                )  # pylint: disable=consider-using-in

            content, n = pattern.subn(repl, content)
            changes.append(
                f"Edit #{idx}: {n} ocorrência(s) substituída(s)."
                if n
                else f"Edit #{idx}: padrão não encontrado: '{old[:60]}'"
            )
        if dry_run:
            import difflib

            diff = list(
                difflib.unified_diff(
                    original.splitlines(keepends=True),
                    content.splitlines(keepends=True),
                    fromfile=f"a/{path}",
                    tofile=f"b/{path}",
                    lineterm="",
                )
            )
            return (
                "# Dry-run diff\n\n"
                + "\n".join(diff[:200])
                + "\n\n"
                + "\n".join(changes)
            )
        if content == original:
            return _t("ia.tool_no_changes") + "\n" + "\n".join(changes)
        return self._safe_write(resolved, content) + "\n" + "\n".join(changes)

    def _tool_insert_after_line(self, path: str, line: int, text: str) -> str:
        """Insere `text` imediatamente após o número de linha `line` em `path`."""
        resolved = self._resolve(path)
        if resolved is None:
            return _t("ia.tool_file_not_found", path=path)
        try:
            content = self._read(resolved)
        except OSError as exc:
            return _t("ia.tool_read_error", path=path, err=exc)
        lines = content.splitlines(keepends=True)
        if line < 0 or line > len(lines):
            return f"Linha {line} fora do intervalo (1-{len(lines)})."
        if not text.endswith("\n"):
            text += "\n"
        new_content = "".join(lines[:line]) + text + "".join(lines[line:])
        return self._safe_write(resolved, new_content)

    def _tool_format_code(self, path: str) -> str:
        """Formata `path` com o formatador da linguagem detectada e salva o resultado.

        Suporta: Black (.py), Prettier (.js/.ts/.json), rustfmt (.rs), gofmt (.go).
        """
        resolved = self._resolve(path)
        if resolved is None:
            return _t("ia.tool_file_not_found", path=path)
        ext = resolved.suffix.lower()
        cmds: dict[str, list[str]] = {
            ".py": ["black", "--quiet", "-"],
            ".js": ["npx", "prettier", "--stdin-filepath", str(resolved)],
            ".ts": ["npx", "prettier", "--stdin-filepath", str(resolved)],
            ".json": ["npx", "prettier", "--parser", "json"],
            ".rs": ["rustfmt", "--emit", "stdout", str(resolved)],
            ".go": ["gofmt", str(resolved)],
        }
        cmd = cmds.get(ext)
        if not cmd:
            return f"Nenhum formatador para '{ext}'."
        try:
            original = self._read(resolved)
            proc = subprocess.run(
                cmd,
                input=original if ext in (
                    ".py", ".js", ".ts", ".json") else None,
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
            if proc.returncode != 0:
                return _t("ia.tool_formatter_err", err=proc.stderr)
            formatted = proc.stdout
            if formatted.strip() == original.strip():
                return _t("ia.tool_already_formatted")
            return self._safe_write(resolved, formatted)
        except FileNotFoundError:
            return _t("ia.tool_formatter_missing", cmd=cmd[0])
        except Exception as exc:
            return f"[Erro] {exc}"

    def _tool_patch_files(
        self, patches: list[dict[str, Any]], dry_run: bool = False
    ) -> str:
        """Aplica edições em múltiplos arquivos em sequência.

        Cada item de `patches` segue o mesmo formato de `apply_edit`.
        Com `dry_run=True`, simula sem gravar em disco.
        """
        results: list[str] = []
        for patch in patches:
            path = patch.get("path", "")
            edits = patch.get("edits", [])
            create = patch.get("create_if_missing", False)
            if not path:
                results.append(_t("ia.tool_no_path"))
                continue
            resolved = self._resolve(path)
            if resolved is None and create:
                resolved = self._confine(path)
                if resolved is None or self._is_sensitive(resolved):
                    results.append(f"{path}: fora do workspace ou não permitido.")
                    continue
                if dry_run:
                    results.append(f"📄 {path}: (dry-run) seria criado.")
                    continue
                cps = self._cps()
                if cps is not None and cps._current is not None:  # noqa: SLF001
                    cps.record_write(resolved)
                resolved.parent.mkdir(parents=True, exist_ok=True)
                resolved.touch()
            if resolved is None:
                results.append(f"❌ {path}: não encontrado.")
                continue
            result = self._tool_apply_edit(path, edits, dry_run=dry_run)
            results.append(f"📄 {path}: {result.splitlines()[0]}")
        return "\n".join(results)

    def _tool_run_command(
        self,
        command: str,
        timeout: int = 60,
        working_dir: str = "",
        env: dict[str, str] | None = None,
    ) -> str:
        """Executa `command` via shell no diretório do workspace.

        Args:
            command:     Comando shell completo.
            timeout:     Segundos antes de encerrar o processo.
            working_dir: Subdiretório relativo ao workspace. Vazio = raiz.
            env:         Variáveis de ambiente adicionais.
        """
        root = self._workspace()
        cwd = str(root) if root else None
        if working_dir and root:
            confined = self._confine(working_dir)
            if confined is None or not confined.is_dir():
                return f"Diretório '{working_dir}' fora do workspace ou inexistente."
            cwd = str(confined)
        run_env = os.environ.copy()
        if env:
            run_env.update(env)
        try:
            result = subprocess.run(
                command,
                shell=True,
                cwd=cwd,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
                env=run_env,
            )
            stdout = result.stdout.strip()
            stderr = result.stderr.strip()
            code = result.returncode
            parts = [f"# Comando: {command}\n# Código de saída: {code}\n"]
            if stdout:
                parts.append(f"## stdout\n{stdout[:5000]}")
            if stderr:
                parts.append(f"## stderr\n{stderr[:2000]}")
            return "\n\n".join(parts)
        except subprocess.TimeoutExpired:
            return f"Comando expirou após {timeout}s."
        except Exception as exc:
            return f"[Erro] {exc}"

    def _tool_run_script(
        self,
        code: str,
        language: str = "python",
        timeout: int = 30,
        env: dict[str, str] | None = None,
    ) -> str:
        """Grava `code` em arquivo temporário e executa com o runtime da linguagem.

        Suporta: python, bash, node, ruby, perl.
        """
        runtimes = {
            "python": ("python3", ".py"),
            "py": ("python3", ".py"),
            "bash": ("bash", ".sh"),
            "sh": ("sh", ".sh"),
            "node": ("node", ".js"),
            "js": ("node", ".js"),
            "javascript": ("node", ".js"),
            "ruby": ("ruby", ".rb"),
            "perl": ("perl", ".pl"),
        }
        lang_key = language.lower().strip()
        if lang_key not in runtimes:
            return _t("ia.tool_script_lang", lang=language)
        runtime, ext = runtimes[lang_key]
        root = self._workspace()
        cwd = str(root) if root else None
        sp: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", suffix=ext, prefix="codis_ia_", delete=False
            ) as tmp:
                sp = Path(tmp.name)
                tmp.write(code)
            run_env = os.environ.copy()
            if cwd:
                run_env["PYTHONPATH"] = cwd
            if env:
                run_env.update(env)
            r = subprocess.run(
                [runtime, str(sp)],
                cwd=cwd,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
                env=run_env,
            )
            stdout = r.stdout.strip()
            stderr = r.stderr.strip()
            status = "✅ sucesso" if r.returncode == 0 else f"❌ saída {r.returncode}"
            parts = [f"# Script ({runtime}) – {status}"]
            parts.append(
                f"## stdout\n{stdout[:5000]}" if stdout else "## stdout\n(vazio)"
            )
            if stderr:
                parts.append(f"## stderr\n{stderr[:2000]}")
            return "\n\n".join(parts)
        except subprocess.TimeoutExpired:
            return _t("ia.tool_script_timeout", timeout=timeout)
        except FileNotFoundError:
            return f"Runtime '{runtime}' não encontrado."
        except Exception as exc:
            return f"[Erro] {exc}"
        finally:
            if sp and sp.exists():
                with contextlib.suppress(OSError):
                    sp.unlink()

    def _tool_run_tests(
        self, target: str = "", extra_args: str = "", framework: str = ""
    ) -> str:
        """Executa a suite de testes do projeto com auto-detecção de framework.

        Detecta automaticamente pytest, jest, cargo test ou go test pela presença de
        arquivos de configuração. Timeout fixo de 120 segundos.
        """
        root = self._workspace()
        if not root:
            return _t("ia.tool_no_workspace")
        fw_map = {
            "pytest": ["pytest"],
            "jest": ["npx", "jest"],
            "cargo": ["cargo", "test"],
            "go": ["go", "test", "./..."],
        }
        if framework and framework in fw_map:
            selected = fw_map[framework]
        else:
            selected = None
            for cmd in [["pytest", "--version"]]:
                try:
                    subprocess.run(cmd, capture_output=True,
                                   timeout=5, check=False)
                    selected = ["pytest"]
                    break
                except FileNotFoundError:
                    pass
            if not selected:
                if (root / "package.json").exists():
                    selected = ["npm", "test"]
                elif (root / "go.mod").exists():
                    selected = ["go", "test", "./..."]
                elif (root / "Cargo.toml").exists():
                    selected = ["cargo", "test"]
        if not selected:
            return _t("ia.tool_tests_no_fw")
        full_cmd = selected + ([target] if target else [])
        if extra_args:
            full_cmd.extend(extra_args.split())
        try:
            result = subprocess.run(
                full_cmd,
                cwd=str(root),
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
            )
            stdout = result.stdout.strip()
            stderr = result.stderr.strip()
            status = (
                "PASSOU"
                if result.returncode == 0
                else f"FALHOU (saída {result.returncode})"
            )
            parts = [f"# Testes: {status}"]
            if stdout:
                parts.append(stdout[:6000])
            if stderr:
                parts.append(f"stderr:\n{stderr[:2000]}")
            return "\n".join(parts)
        except subprocess.TimeoutExpired:
            return _t("ia.tool_tests_timeout")
        except Exception as exc:
            return f"[Erro] {exc}"

    def _tool_create_directory(self, path: str) -> str:
        """Cria o diretório no caminho especificado, incluindo pais necessários."""
        root = self._workspace()
        if root is None:
            return _t("ia.tool_no_workspace")
        dest = self._confine(path)
        if dest is None:
            return _t("ia.tool_outside_ws")
        try:
            cps = self._cps()
            if cps is not None and cps._current is not None:  # noqa: SLF001
                cps.record_created_dir(dest)
            dest.mkdir(parents=True, exist_ok=True)
            return _t("ia.tool_dir_created", path=str(dest.relative_to(root.resolve())))
        except Exception as exc:
            return _t("ia.tool_dir_err", err=exc)

    def _tool_delete_file(self, path: str) -> str:
        """Remove `path` do workspace, criando um backup `.deleted.bak` antes."""
        resolved = self._confine(path)
        if resolved is None:
            return _t("ia.tool_outside_ws")
        if not resolved.exists():
            return _t("ia.tool_file_not_found", path=path)
        if not resolved.is_file():
            return f"'{path}' não é um arquivo."
        blocked = self._unsaved_block(resolved)
        if blocked:
            return blocked
        try:
            rel = resolved.relative_to(self._workspace().resolve())
            self._remove_reversibly(resolved)
            return f"✅ Arquivo movido para a lixeira do projeto: {rel}  (reversível)"
        except Exception as exc:
            return f"[Erro ao deletar] {exc}"

    def _remove_reversibly(self, target: Path) -> None:
        """Remove *target* mandando-o para a lixeira do projeto (``rename`` atômico).

        Nunca ``rmtree``/``unlink`` direto: o usuário pode restaurar pelo painel da
        lixeira ou desfazendo o turno da IA.  Se a lixeira não estiver disponível
        (ex.: volume diferente), recusa em vez de apagar de forma irreversível.
        """
        from src.trash_core import TrashStore, trash_root_for

        cps = self._cps()
        if cps is not None and cps._current is not None:  # noqa: SLF001
            cps.trash(target)
        else:
            TrashStore.for_root(trash_root_for(target, self._workspace())).put(target)

    def _tool_delete_directory(self, path: str, recursive: bool = False) -> str:
        """Remove o diretório `path`. Com `recursive=True`, remove conteúdo recursivamente.

        Recusa deletar fora do workspace. Operação irreversível.
        """
        root = self._workspace()
        if root is None:
            return _t("ia.tool_no_workspace")
        dest = (root / path).resolve()
        if not dest.exists():
            return _t("ia.tool_folder_missing", path=path)
        if not dest.is_dir():
            return f"'{path}' não é uma pasta."
        try:
            dest.relative_to(root.resolve())
        except ValueError:
            return _t("ia.tool_outside_ws")
        if dest == root.resolve():
            return _t("ia.tool_outside_ws")
        try:
            if recursive:
                self._remove_reversibly(dest)
                return f"✅ Pasta movida para a lixeira do projeto: {dest.relative_to(root)}  (reversível)"
            dest.rmdir()
            return f"✅ Pasta deletada: {dest.relative_to(root)}"
        except OSError as exc:
            return f"[Erro ao deletar pasta] {exc}"

    def _tool_move_file(self, src: str, dst: str) -> str:
        """Move ou renomeia `src` para `dst`, criando diretórios de destino se necessário."""
        root = self._workspace()
        if root is None:
            return _t("ia.tool_no_workspace")
        src_path = self._confine(src)
        dst_path = self._confine(dst)
        if src_path is None or dst_path is None:
            return _t("ia.tool_outside_ws")
        if not src_path.exists():
            return _t("ia.tool_file_not_found", path=src)
        dst_path.parent.mkdir(parents=True, exist_ok=True)
        if dst_path.exists():
            return f"[Erro] destino já existe: {dst}"
        try:
            shutil.move(str(src_path), str(dst_path))
            cps = self._cps()
            if cps is not None and cps._current is not None:  # noqa: SLF001
                cps.record_move(src_path, dst_path)
            return _t("ia.tool_moved", src=src, dst=dst)
        except Exception as exc:
            return _t("ia.tool_dir_err", err=exc)

    def _tool_copy_file(self, src: str, dst: str) -> str:
        """Copia `src` para `dst`. Funciona com arquivos e diretórios."""
        root = self._workspace()
        if root is None:
            return _t("ia.tool_no_workspace")
        src_path = self._confine(src)
        dst_path = self._confine(dst)
        if src_path is None or dst_path is None:
            return _t("ia.tool_outside_ws")
        if not src_path.exists():
            return _t("ia.tool_file_not_found", path=src)
        dst_path.parent.mkdir(parents=True, exist_ok=True)
        if dst_path.exists():
            return f"[Erro] destino já existe: {dst}"
        try:
            cps = self._cps()
            if cps is not None and cps._current is not None:  # noqa: SLF001
                cps.record_write(dst_path)
            (shutil.copytree if src_path.is_dir() else shutil.copy2)(
                str(src_path), str(dst_path)
            )
            return _t("ia.tool_copied", src=src, dst=dst)
        except Exception as exc:
            return _t("ia.tool_dir_err", err=exc)

    def _tool_init_project(
        self,
        template: str,
        path: str = ".",
        name: str = "",
        git: bool = True,
        extra_cmds: list[str] | None = None,
    ) -> str:
        """Inicializa um novo projeto a partir de um template.

        Templates disponíveis: node-ts, react, python-lib, fastapi, rust, vazio.
        Cria README, .gitignore e executa comandos de inicialização do template.
        Com `git=True`, faz git init e commit inicial.
        """
        root = self._workspace()
        if root is None:
            return _t("ia.tool_no_workspace")
        dest = self._confine(path)
        if dest is None:
            return _t("ia.tool_outside_ws")
        dest.mkdir(parents=True, exist_ok=True)
        project_name = re.sub(r"[^A-Za-z0-9_.-]+", "-", name or dest.name).strip("-.") or "project"
        module_name = re.sub(r"\W+", "_", project_name).strip("_") or "project"
        if module_name[0].isdigit():
            module_name = f"_{module_name}"
        results = [
            f"# Inicializando projeto '{project_name}' (template: {template})\n"]

        def run(cmd: str) -> None:
            """Executa a ferramenta de IA e retorna o resultado."""
            r = subprocess.run(
                cmd,
                shell=True,
                cwd=str(dest),
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
            out = (r.stdout + r.stderr).strip()
            results.append(f"$ {cmd}\n{out[:500]}")

        def write(rel: str, content: str) -> None:
            """Escreve conteúdo no arquivo especificado via toolbox de IA."""
            fp = dest / rel
            fp.parent.mkdir(parents=True, exist_ok=True)
            fp.write_text(content, "utf-8")
            results.append(f"✅ Criado: {rel}")

        if git:
            run("git init")
            write(".gitignore", _GITIGNORE.get(template, _GITIGNORE["vazio"]))

        write("README.md", f"# {project_name}\n\n> Criado com Codis IA.\n")

        if template == "node-ts":
            run("npm init -y")
            run(
                "npm install --save-dev typescript ts-node @types/node --ignore-scripts"
            )
            write("tsconfig.json", _TSCONFIG)
            write("src/index.ts", "console.log('Hello, TypeScript!');\n")
        elif template == "react":
            run("npx create-react-app . --template typescript --yes")
        elif template == "python-lib":
            write("pyproject.toml", _PYPROJECT.replace("PROJNAME", project_name))
            write(
                f"src/{module_name}/__init__.py",
                f'"""Package {project_name}."""\n__version__ = "0.1.0"\n',
            )
            write("tests/__init__.py", "")
            write(
                "tests/test_basic.py",
                f"def test_version():\n    from {module_name} import __version__\n    assert __version__ == '0.1.0'\n",
            )
        elif template == "fastapi":
            run("pip install fastapi uvicorn --quiet")
            write("main.py", _FASTAPI)
            write("requirements.txt", "fastapi\nuvicorn[standard]\n")
        elif template == "rust":
            run(f"cargo init . --name {project_name}")

        for cmd in extra_cmds or []:
            run(cmd)

        if git:
            run("git add -A")
            run(f'git commit -m "chore: init {project_name} via Codis IA"')

        results.append(f"\n✅ Projeto '{project_name}' pronto em {dest}")
        return "\n".join(results)

    def _tool_install_dependency(
        self,
        package: str,
        manager: str = "",
        dev: bool = False,
        global_: bool = False,
        extra_args: str = "",
    ) -> str:
        """Instala `package` com o gerenciador especificado ou auto-detectado.

        Auto-detecção: package.json -> npm, Cargo.toml -> cargo, senão -> pip.
        """
        root = self._workspace()
        cwd = str(root) if root else None
        if not manager:
            if root and (root / "package.json").exists():
                manager = "npm"
            elif root and (root / "Cargo.toml").exists():
                manager = "cargo"
            else:
                manager = "pip"
        cmds: dict[str, list[str]] = {
            "pip": ["pip", "install", package],
            "pip3": ["pip3", "install", package],
            "npm": ["npm", "install"]
            + (["--save-dev"] if dev else [])
            + (["-g"] if global_ else [])
            + [package],
            "yarn": ["yarn", "add"] + (["--dev"] if dev else []) + [package],
            "cargo": ["cargo", "add", package],
            "gem": ["gem", "install", package],
        }
        cmd = cmds.get(manager)
        if not cmd:
            return _t("ia.tool_mgr_unknown", mgr=manager)
        if package.startswith("-") or not package.strip():
            return f"Nome de pacote inválido: {package!r}"
        if extra_args:
            cmd.extend(extra_args.split())
        try:
            r = subprocess.run(
                cmd, cwd=cwd, capture_output=True, text=True, timeout=120, check=False
            )
            out = (r.stdout + r.stderr).strip()
            return f"{'✅' if r.returncode == 0 else '❌'} {manager} install {package}\n\n{out[:3000]}"
        except FileNotFoundError:
            return f"'{manager}' não encontrado."
        except subprocess.TimeoutExpired:
            return _t("ia.tool_install_timeout")
        except Exception as exc:
            return f"[Erro] {exc}"

    def _tool_git_stage(self, path: str = ".") -> str:
        """Adiciona o caminho ao staging area do Git (git add)."""
        return self._git(["add", "--", path or "."])

    def _tool_git_commit(self, message: str, add_all: bool = False) -> str:
        """Executa `git commit -m <message>`. Com `add_all=True`, faz `git add -A` antes."""
        if add_all:
            self._git(["add", "-A"])
        return self._git(["commit", "-m", message])

    @staticmethod
    def _valid_ref(name: str) -> bool:
        """Aceita nomes de refs não vazios que não possam ser lidos como opção do git."""
        return bool(name) and not name.startswith("-") and not any(c.isspace() for c in name)

    def _tool_git_branch(self, action: str, name: str = "") -> str:
        """Gerencia branches git. `action` aceita: list, create, checkout, delete."""
        actions = {
            "list": lambda: self._git(["branch", "-a"]),
            "create": lambda: (
                self._git(["checkout", "-b", name]) if self._valid_ref(name) else _BAD_REF
            ),
            "checkout": lambda: (
                self._git(["checkout", name]) if self._valid_ref(name) else _BAD_REF
            ),
            "delete": lambda: (
                self._git(["branch", "-d", name]) if self._valid_ref(name) else _BAD_REF
            ),
        }
        fn = actions.get(action)
        return fn() if fn else f"Ação desconhecida: {action}"

    def _tool_git_reset(self, mode: str = "mixed", target: str = "HEAD") -> str:
        """Executa `git reset --<mode> <target>`. Operação potencialmente destrutiva."""
        if mode not in ("soft", "mixed", "hard"):
            return f"Modo inválido: {mode}. Use: soft, mixed, hard."
        if not self._valid_ref(target):
            return _BAD_REF
        return self._git(["reset", f"--{mode}", target])

    def _tool_git_clean(self, flags: str = "-fd") -> str:
        """Remove arquivos não rastreados com `git clean`. Sempre inclui -f."""
        if "-f" not in flags:
            flags = "-f" + flags.lstrip("-")
        return self._git(["clean"] + flags.split())

    def _tool_open_terminal_tab(
        self, name: str = "IA Terminal", command: str = ""
    ) -> str:
        """Abre nova aba no terminal integrado do Codis via callback `on_open_terminal`."""
        if self._on_open_terminal:
            self._on_open_terminal(name, command)
            suffix = f"\nComando: {command}" if command else ""
            return _t("ia.tool_term_opened", name=name) + suffix
        return _t("ia.tool_no_terminal")

_GITIGNORE: dict[str, str] = {
    "node-ts": "__pycache__/\nnode_modules/\ndist/\n.env\n*.log\n",
    "react": "node_modules/\nbuild/\n.env\n*.log\n",
    "python-lib": "__pycache__/\n*.pyc\n.venv/\nvenv/\ndist/\nbuild/\n*.egg-info/\n.env\n",
    "fastapi": "__pycache__/\n*.pyc\n.venv/\n.env\n",
    "rust": "target/\nCargo.lock\n.env\n",
    "vazio": ".env\n*.log\n",
}

_TSCONFIG = """{
  "compilerOptions": {
    "target": "ES2022",
    "module": "commonjs",
    "lib": ["ES2022"],
    "outDir": "dist",
    "rootDir": "src",
    "strict": true,
    "esModuleInterop": true,
    "skipLibCheck": true,
    "forceConsistentCasingInFileNames": true
  },
  "include": ["src/**/*"],
  "exclude": ["node_modules", "dist"]
}
"""

_PYPROJECT = """[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "PROJNAME"
version = "0.1.0"
description = ""
requires-python = ">=3.12"
dependencies = []

[project.optional-dependencies]
dev = ["pytest", "black", "ruff", "mypy"]
"""

_FASTAPI = '''"""FastAPI application."""
from fastapi import FastAPI

app = FastAPI(title=_t("ia.my_api"))


@app.get("/")
async def root() -> dict:
    return {"message": "Olá, mundo!"}


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}
'''


def extract_code_blocks(text: str) -> list[tuple[str, str]]:
    """Extrai blocos de código Markdown e retorna lista de (linguagem, conteúdo)."""
    return [
        (m.group(1).lower(), m.group(2))
        for m in re.compile(r"```(\w*)\n(.*?)```", re.DOTALL).finditer(text)
        if not m.group(2).startswith("# File:")
        and m.group(1).lower() not in ("json", "tool_call")
    ]
