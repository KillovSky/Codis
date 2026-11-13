"""Painel de debug integrado do Codis.

Provê ``DebugPanel``, que gerencia sessões DAP (Debug Adapter Protocol),
exibindo breakpoints, variáveis, call stack e console de saída durante
a execução de programas em modo debug.
"""

from __future__ import annotations

import contextlib
import logging
import queue
import shutil
import subprocess
import sys
import threading
import tkinter as tk
from collections.abc import Callable
from pathlib import Path
from tkinter import ttk
from typing import Any

from src.config import ConfigManager
from src.constants import FONT_MONO, FONT_SIZE, FONT_UI
from src.i18n import _t
from src.lsp.dap_client import DAPClient
from src.theme.base import Theme
from src.theme.manager import ThemeManager
from src.ui.grab_safe import safe_grab_set

logger = logging.getLogger("Codis")


class DebugPanel(tk.Frame):
    """
    Painel de depuração funcional:
    - Run / Stop / Restart / Step In / Step Over / Step Out / Continue
    - Sessão DAP real (via debugpy) com breakpoints, stepping e variáveis
      de fato vinculados ao processo em execução, sem depender de um
      cliente DAP externo - o próprio Codis assume esse papel
    - Console com eval de expressões Python (REPL local ou no frame
      parado, quando uma sessão DAP está ativa)
    - Watch expressions com atualização automática
    - Variáveis do frame local/remoto e call stack
    - Breakpoints (linhas marcadas no editor), sincronizados ao vivo
    - Suporte a Python (DAP real), JS, TS, Go (execução simples)
    """

    def __init__(
        self,
        parent: tk.Widget,
        tmgr: ThemeManager,
        cfg: ConfigManager,
        get_editor: Callable | None = None,
    ) -> None:
        """Cria o painel de depuração integrado.

        Args:
            parent: Widget pai Tkinter.
            config: Instância de ConfigManager.
        """
        t = tmgr.theme
        super().__init__(parent, bg=t.panel_bg)
        self._tmgr = tmgr
        self._cfg = cfg
        self._get_editor = get_editor
        self._proc: subprocess.Popen | None = None
        self._alive = True
        self._eof_count = 0
        self._watch_exprs: list[str] = []
        self._eval_ns: dict[str, Any] = {}

        self._dap: DAPClient | None = None
        self._dap_active: bool = False
        self._dap_path: Path | None = None
        self._dap_frames: list[dict] = []
        self._dap_active_frame_id: int | None = None
        self._known_breakpoints: dict[str, set[int]] = {}

        self._build()

    def _build(self) -> None:

        t = self._tmgr.theme

        tb = tk.Frame(self, bg=t.tabbar_bg, height=32)
        tb.pack(fill="x")
        tb.pack_propagate(False)

        tk.Label(
            tb,
            text=_t("debug.title"),
            bg=t.tabbar_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 8, "bold"),
        ).pack(side="left", padx=4)

        self._toolbar_btns: dict[str, tk.Label] = {}
        for icon, _tip, key in (
            ("▶", "Run (F5)", "run"),
            ("⏹", "Stop", "stop"),
            ("↻", "Restart", "restart"),
            ("⤵", "Step Into (F11)", "step_in"),
            ("⤴", "Step Over (F10)", "step_over"),
            ("⤴⤴", "Step Out (Shift+F11)", "step_out"),
            ("⏩", "Continue (F5)", "continue"),
        ):
            btn = tk.Label(
                tb,
                text=f" {icon} ",
                bg=t.tabbar_bg,
                fg=t.fg,
                font=(FONT_UI, 11),
                cursor="hand2",
                relief="flat",
            )
            btn.pack(side="left", padx=1)
            btn.bind("<Enter>", lambda _, b=btn: b.config(
                bg=t.btn_hover, fg=t.accent))
            btn.bind("<Leave>", lambda _, b=btn: b.config(
                bg=t.tabbar_bg, fg=t.fg))
            btn.bind("<Button-1>", lambda _, k=key: self._toolbar_action(k))
            self._toolbar_btns[key] = btn

        self._status_lbl = tk.Label(
            tb,
            text=f"  ⬤ {_t('debug.idle')}",
            bg=t.tabbar_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 8),
        )
        self._status_lbl.pack(side="right", padx=8)

        tk.Frame(self, bg=t.border, height=1).pack(fill="x")

        paned = tk.PanedWindow(self, orient="horizontal",
                               bg=t.border, sashwidth=4)
        paned.pack(fill="both", expand=True)

        left = tk.Frame(paned, bg=t.panel_bg)
        right = tk.Frame(paned, bg=t.panel_bg)
        paned.add(left, stretch="always")
        paned.add(right, width=240, stretch="never")

        tk.Label(
            left,
            text=_t("debug.console"),
            bg=t.panel_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 8, "bold"),
        ).pack(anchor="w", padx=4, pady=(4, 0))

        console_frame = tk.Frame(left, bg=t.panel_bg)
        console_frame.pack(fill="both", expand=True, padx=4)

        self._console = tk.Text(
            console_frame,
            bg=t.terminal_bg,
            fg=t.terminal_fg,
            font=(FONT_MONO, FONT_SIZE - 2),
            relief="flat",
            bd=0,
            state="disabled",
            wrap="word",
            highlightthickness=0,
            padx=4,
            pady=4,
        )
        vsb = ttk.Scrollbar(
            console_frame,
            orient="vertical",
            command=self._console.yview,
            style="Thin.Vertical.TScrollbar",
        )
        vsb.pack(side="right", fill="y")
        self._console.config(yscrollcommand=vsb.set)
        self._console.pack(side="left", fill="both", expand=True)

        for tag, fg in (
            ("err", "#f44747"),
            ("ok", "#6a9955"),
            ("dim", "#808080"),
            ("bold", "#dcdcaa"),
            ("info", "#569cd6"),
        ):
            self._console.tag_configure(tag, foreground=fg)

        inp_sep = tk.Frame(left, bg=t.border, height=1)
        inp_sep.pack(fill="x", padx=4)

        inp_row = tk.Frame(left, bg=t.terminal_bg, height=26)
        inp_row.pack(fill="x", padx=4, pady=(0, 4))
        inp_row.pack_propagate(False)

        tk.Label(
            inp_row,
            text=_t("hc.debug_panel.prompt_arrow"),
            bg=t.terminal_bg,
            fg=t.t_func,
            font=(FONT_MONO, FONT_SIZE - 2),
        ).pack(side="left", padx=(4, 2))

        self._eval_var = tk.StringVar()
        eval_entry = tk.Entry(
            inp_row,
            textvariable=self._eval_var,
            bg=t.terminal_bg,
            fg=t.terminal_fg,
            insertbackground=t.cursor,
            relief="flat",
            bd=0,
            font=(FONT_MONO, FONT_SIZE - 2),
            highlightthickness=0,
        )
        eval_entry.pack(side="left", fill="x", expand=True, padx=4)
        eval_entry.bind("<Return>", self._eval_expr)

        watch_hdr = tk.Frame(right, bg=t.panel_bg)
        watch_hdr.pack(fill="x", padx=4, pady=(4, 0))
        tk.Label(
            watch_hdr,
            text=_t("debug.watch"),
            bg=t.panel_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 8, "bold"),
        ).pack(side="left")
        add_btn = tk.Label(
            watch_hdr,
            text=_t("hc.debug_panel.btn_add"),
            bg=t.panel_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 9),
            cursor="hand2",
        )
        add_btn.pack(side="right")
        add_btn.bind("<Button-1>", self._add_watch)

        watch_frame = tk.Frame(right, bg=t.panel_bg)
        watch_frame.pack(fill="x", padx=4)
        self._watch_lb = tk.Listbox(
            watch_frame,
            bg=t.sidebar_bg,
            fg=t.fg,
            selectbackground=t.sel_bg,
            relief="flat",
            bd=0,
            font=(FONT_MONO, FONT_SIZE - 2),
            activestyle="none",
            highlightthickness=1,
            highlightbackground=t.border,
            height=5,
        )
        self._watch_lb.pack(fill="x")
        self._watch_lb.bind("<Delete>", self._remove_watch)
        self._watch_lb.bind("<BackSpace>", self._remove_watch)

        tk.Label(
            right,
            text=_t("debug.variables"),
            bg=t.panel_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 8, "bold"),
        ).pack(anchor="w", padx=4, pady=(8, 0))

        vars_frame = tk.Frame(right, bg=t.panel_bg)
        vars_frame.pack(fill="both", expand=True, padx=4)
        self._vars_text = tk.Text(
            vars_frame,
            bg=t.sidebar_bg,
            fg=t.fg,
            font=(FONT_MONO, FONT_SIZE - 2),
            relief="flat",
            bd=0,
            state="disabled",
            wrap="none",
            highlightthickness=1,
            highlightbackground=t.border,
            height=8,
        )
        self._vars_text.pack(fill="both", expand=True)

        tk.Label(
            right,
            text=_t("debug.breakpoints"),
            bg=t.panel_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 8, "bold"),
        ).pack(anchor="w", padx=4, pady=(8, 0))

        bp_frame = tk.Frame(right, bg=t.panel_bg)
        bp_frame.pack(fill="x", padx=4, pady=(0, 4))
        self._bp_lb = tk.Listbox(
            bp_frame,
            bg=t.sidebar_bg,
            fg=t.fg,
            selectbackground=t.sel_bg,
            relief="flat",
            bd=0,
            font=(FONT_MONO, FONT_SIZE - 2),
            activestyle="none",
            highlightthickness=1,
            highlightbackground=t.border,
            height=4,
        )
        self._bp_lb.pack(fill="x")
        self._bp_lb.bind("<Delete>", self._remove_breakpoint)
        self._bp_lb.bind("<BackSpace>", self._remove_breakpoint)

    def _toolbar_action(self, key: str) -> None:
        """Executa a operação 'toolbar action'.

        Args:
            key: Chave de identificação.
        """
        if key == "run":
            self._run_file()
        elif key == "stop":
            self._stop()
        elif key == "restart":
            self._stop()
            self.after(300, self._run_file)
        elif key in ("step_in", "step_over", "step_out", "continue"):
            self._dap_step(key)

    def _dap_step(self, key: str) -> None:
        """Encaminha um comando de execução (step/continue) à sessão DAP ativa.

        Se não houver sessão DAP ativa (ex.: depurando sem debugpy, ou
        executando uma linguagem sem suporte real a DAP), informa
        claramente ao usuário em vez de fingir que a ação teve efeito.
        """
        if not self._dap_active or self._dap is None:
            self._console_print(
                _t(
                    "debug.no_dap_session",
                    "[Nenhuma sessão de depuração ativa - habilite "
                    "'Debug via debugpy' nas configurações e clique em "
                    "Run para depurar com breakpoints/stepping reais]\n",
                ),
                "dim",
            )
            return
        if not self._dap.is_stopped:
            self._console_print(
                _t(
                    "debug.not_stopped",
                    "[A execução não está parada em um breakpoint - nada para avançar]\n",
                ),
                "dim",
            )
            return

        thread_id = self._dap.last_stopped_thread_id
        if key == "continue":
            self._console_print("▶ Continue\n", "info")
            self._dap.continue_(thread_id)
        elif key == "step_over":
            self._console_print("⤴ Step Over\n", "info")
            self._dap.step_over(thread_id)
        elif key == "step_in":
            self._console_print("⤵ Step Into\n", "info")
            self._dap.step_in(thread_id)
        elif key == "step_out":
            self._console_print("⤴⤴ Step Out\n", "info")
            self._dap.step_out(thread_id)

    def _run_file(self) -> None:
        """Inicia o processo de depuração para o arquivo ativo.

        Por padrão (``debug_dap_enabled`` é ``True`` a menos que o usuário
        desative explicitamente), arquivos Python são depurados via uma
        sessão DAP real contra debugpy - o próprio Codis conecta como
        cliente, sem precisar de nenhum IDE/cliente externo. Se debugpy
        não estiver instalado, cai automaticamente para execução simples
        com captura de stdout/stderr (sem breakpoints reais).
        """
        if self._proc and self._proc.poll() is None:
            self._stop()

        editor = self._get_editor() if self._get_editor else None
        if editor is None:
            self._console_print(
                _t("debug.no_file", "[Nenhum arquivo aberto]\n"), "err")
            return

        path = getattr(editor, "path", None)
        if path is None:
            self._console_print(
                _t(
                    "debug.save_first", "[Arquivo não salvo - salve antes de depurar]\n"
                ),
                "err",
            )
            return

        lang = getattr(editor, "lang", "plain")
        dap_enabled = self._cfg.get(
            "debug_dap_enabled", True) if self._cfg else True
        dap_port = int(self._cfg.get("debug_dap_port", 5678)
                       ) if self._cfg else 5678

        if lang == "python" and dap_enabled:
            self._run_with_debugpy(path, dap_port)
            return

        cmd: list[str] = []
        if lang == "python":
            py = shutil.which("python3") or shutil.which("python") or "python"
            cmd = [py, "-u", str(path)]
        elif lang in ("javascript", "typescript"):
            if lang == "typescript":
                tsc = shutil.which("ts-node") or shutil.which("npx")
                cmd = (
                    [tsc, "ts-node", str(path)]
                    if tsc and tsc.endswith("npx")
                    else [tsc or "ts-node", str(path)]
                )
            else:
                nd = shutil.which("node")
                if nd:
                    cmd = [nd, str(path)]
        elif lang == "go":
            cmd = ["go", "run", str(path)]
        elif lang in ("c", "cpp"):
            self._console_print(
                _t(
                    "debug.compile_manual",
                    "[Compile manualmente - C/C++ não tem execução automática]\n",
                ),
                "dim",
            )
            return

        if not cmd:
            self._console_print(
                _t(
                    "debug.cannot_run",
                    f"[Não é possível executar arquivos {lang} diretamente]\n",
                ),
                "dim",
            )
            return

        self._console_print(f"▶ {' '.join(cmd)}\n", "info")
        self._set_status("▶ Running", "#6a9955")

        flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        try:
            self._proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=str(Path(path).parent),
                bufsize=0,
                creationflags=flags,
            )
        except OSError as exc:
            self._console_print(f"[Error: {exc}]\n", "err")
            return

        self._read_proc_output(self._proc)

    def _run_with_debugpy(self, path: Path, port: int) -> None:
        """Inicia depuração real via debugpy, com o próprio Codis atuando
        como cliente DAP - sem depender de nenhum cliente externo.

        Fluxo:
          1. Lança ``python -m debugpy --listen <port> --wait-for-client``.
             O ``--wait-for-client`` é necessário para que os breakpoints
             enviados no passo 3 sejam aplicados *antes* do script começar
             a rodar - sem ele, debugpy executa o programa de imediato e
             qualquer breakpoint chega tarde demais.
          2. Conecta via TCP nessa mesma porta, como cliente DAP (não é
             preciso nenhum IDE/cliente externo - é o próprio Codis).
          3. Envia o handshake completo (initialize/attach/setBreakpoints/
             configurationDone), o que libera a execução automaticamente.

        Caso o `debugpy` não esteja instalado, cai de volta para execução
        simples (sem breakpoints reais), avisando o usuário uma única vez.
        """
        try:
            import importlib.util

            if importlib.util.find_spec("debugpy") is None:
                self._console_print(
                    _t(
                        "debug.debugpy_missing",
                        "[debugpy não encontrado. Instale com: pip install debugpy]\n",
                    ),
                    "err",
                )
                self._console_print(
                    _t(
                        "debug.debugpy_fallback", "[Executando sem depurador real...]\n"
                    ),
                    "dim",
                )
                py = shutil.which("python3") or shutil.which(
                    "python") or "python"
                self._launch_process([py, "-u", str(path)], path)
                return
        except Exception:
            logger.debug(
                "Falha ao configurar fallback de execução sem debugpy.", exc_info=True
            )

        py = shutil.which("python3") or shutil.which("python") or "python"
        bps = sorted(self._collect_breakpoint_lines())

        cmd = [
            py,
            "-m",
            "debugpy",
            "--listen",
            f"127.0.0.1:{port}",
            "--wait-for-client",
            str(path),
        ]

        self._console_print(
            _t("debug.debugpy_starting",
               f"▶ Iniciando debugpy na porta {port}...\n"),
            "info",
        )
        if bps:
            self._console_print(
                _t("debug.debugpy_breakpoints",
                   f"   Breakpoints: linhas {bps}\n"),
                "dim",
            )
        else:
            self._console_print(
                _t(
                    "debug.no_breakpoints_warning",
                    "   Nenhum breakpoint definido - o programa vai rodar até "
                    "o fim sem parar. Clique na margem esquerda (gutter), ao "
                    "lado do número da linha, para adicionar um breakpoint "
                    "antes de rodar.\n",
                ),
                "dim",
            )
        self._set_status(f"⏳ debugpy :{port}", "#569cd6")

        self._dap_path = path
        if not self._launch_process(cmd, path):
            return

        threading.Thread(
            target=self._attach_dap_session,
            args=(port, str(path), bps),
            daemon=True,
        ).start()

    def _collect_breakpoint_lines(self) -> list[int]:
        """Lê os breakpoints atualmente exibidos no painel (sincronizados
        ao vivo a partir do gutter do editor ativo)."""
        bps = []
        for i in range(self._bp_lb.size()):
            item = self._bp_lb.get(i).strip()
            try:
                line = int(item.split(":")[-1])
                bps.append(line)
            except (ValueError, IndexError):
                pass
        return bps

    def _attach_dap_session(self, port: int, path: str, bps: list[int]) -> None:
        """Conecta ao debugpy e realiza o handshake DAP (thread em segundo
        plano - não bloqueia a UI). Chamado pouco depois de o processo
        debugpy ser lançado.
        """
        client = DAPClient(
            on_event=self._on_dap_event,
            on_close=self._on_dap_close,
            tk_widget=self,
        )
        if not client.connect("127.0.0.1", port):
            self.after(
                0,
                lambda: self._console_print(
                    _t(
                        "debug.debugpy_connect_failed",
                        "[Não foi possível conectar ao debugpy - a sessão "
                        "seguirá sem breakpoints reais]\n",
                    ),
                    "err",
                ),
            )
            return

        ok = client.attach(breakpoints={path: bps} if bps else None)
        if not ok:
            self.after(
                0,
                lambda: self._console_print(
                    _t(
                        "debug.dap_handshake_failed",
                        "[Handshake DAP falhou - veja o log para detalhes]\n",
                    ),
                    "err",
                ),
            )
            client.disconnect()
            return

        self._dap = client
        self._dap_active = True
        if path:
            self._known_breakpoints[path] = set(bps)
        self.after(
            0,
            lambda: (
                self._console_print(
                    _t(
                        "debug.dap_attached",
                        "✓ Sessão DAP conectada - breakpoints e stepping reais ativos.\n",
                    ),
                    "ok",
                ),
                self._set_status("▶ Debugging", "#6a9955"),
            ),
        )

    def _on_dap_event(self, name: str, body: dict) -> None:
        """Callback de eventos DAP assíncronos - já despachado na thread Tk
        pelo próprio ``DAPClient`` (via ``after(0, ...)``)."""
        if name == "output":
            text = body.get("output", "")
            category = body.get("category", "")
            tag = "err" if category == "stderr" else ""
            if text:
                self._console_print(text, tag)
        elif name == "stopped":
            reason = body.get("reason", "")
            thread_id = body.get("threadId")
            self._console_print(
                f"⏸ Parado ({reason})\n",
                "bold",
            )
            self._set_status(f"⏸ Stopped: {reason}", "#dcdcaa")
            self._refresh_dap_stack(thread_id)
        elif name == "continued":
            self._set_status("▶ Debugging", "#6a9955")
        elif name in ("exited", "terminated"):
            rc = body.get("exitCode")
            msg = (
                f"\n[Exited with code {rc}]\n"
                if rc is not None
                else "\n[Debug session terminated]\n"
            )
            self._console_print(msg, "ok" if rc in (0, None) else "err")
            self._set_status(
                "⬤ Idle" if rc in (0, None) else f"ERR Exited ({rc})",
                "#6a9955" if rc in (0, None) else "#f44747",
            )
            self._dap_active = False

    def _on_dap_close(self) -> None:
        """Chamado quando a conexão DAP é perdida ou encerrada."""
        self._dap_active = False
        self._dap = None
        self._dap_frames = []
        self._dap_active_frame_id = None

    def _refresh_dap_stack(self, thread_id: int | None) -> None:
        """Busca e exibe a call stack + variáveis do frame parado atual."""
        if self._dap is None or thread_id is None:
            return

        def _work() -> None:
            frames = self._dap.stack_trace(thread_id) if self._dap else []
            self.after(0, lambda: self._on_dap_stack(frames))

        threading.Thread(target=_work, daemon=True).start()

    def _on_dap_stack(self, frames: list[dict]) -> None:
        """Atualiza a UI com os frames recebidos e salta o editor para a
        linha atual de execução (topo da pilha)."""
        self._dap_frames = frames
        self._dap_active_frame_id = frames[0]["id"] if frames else None

        if frames:
            top = frames[0]
            line = top.get("line")
            src = (top.get("source") or {}).get("path")
            if line and src:
                editor = self._get_editor() if self._get_editor else None
                if (
                    editor is not None
                    and getattr(editor, "path", None)
                    and str(editor.path) == src
                ):
                    with contextlib.suppress(Exception):
                        editor.goto_line(int(line))
                        with contextlib.suppress(Exception):
                            editor.gutter.set_current_line(int(line))

        if self._dap_active_frame_id is not None:
            self._refresh_dap_variables(self._dap_active_frame_id)

    def _refresh_dap_variables(self, frame_id: int) -> None:
        """Busca os escopos (locals/globals) de um frame e exibe na lista
        de variáveis do painel."""
        if self._dap is None:
            return

        def _work() -> None:
            scopes = self._dap.scopes(frame_id) if self._dap else []
            all_lines: list[str] = []
            for scope in scopes:
                ref = scope.get("variablesReference", 0)
                if not ref:
                    continue
                variables = self._dap.variables(ref) if self._dap else []
                if not variables:
                    continue
                all_lines.append(f"# {scope.get('name', '')}")
                for v in variables:
                    name = v.get("name", "?")
                    val = v.get("value", "")
                    if len(val) > 60:
                        val = val[:57] + "..."
                    all_lines.append(f"{name} = {val}")
            self.after(0, lambda: self._show_dap_variables(all_lines))

        threading.Thread(target=_work, daemon=True).start()

    def _show_dap_variables(self, lines: list[str]) -> None:
        self._vars_text.config(state="normal")
        self._vars_text.delete("1.0", "end")
        self._vars_text.insert("1.0", "\n".join(lines))
        self._vars_text.config(state="disabled")

    def _launch_process(self, cmd: list[str], path: Path) -> bool:
        """Lança um processo e inicia leitura de stdout/stderr.

        Returns:
            True se o processo foi lançado com sucesso.
        """
        flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        try:
            self._proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=str(Path(path).parent),
                bufsize=0,
                creationflags=flags,
            )
        except OSError as exc:
            self._console_print(f"[Error: {exc}]\n", "err")
            return False
        self._read_proc_output(self._proc)
        return True

    def _read_proc_output(self, proc: subprocess.Popen) -> None:
        """Executa a operação 'read proc output'.

        Args:
            proc: Parâmetro da operação.
        """
        q: queue.Queue[tuple] = queue.Queue()

        def _reader(stream: Any, kind: str) -> None:
            try:
                while True:
                    line = stream.readline()
                    if not line:
                        break
                    q.put((kind, line))
            except OSError:
                pass
            finally:
                q.put(("eof_" + kind, None))

        threading.Thread(target=_reader, args=(
            proc.stderr, "err"), daemon=True).start()
        self._eof_count = 0
        self._drain_proc(q, proc)

    def _drain_proc(self, q: queue.Queue, proc: subprocess.Popen) -> None:
        """Executa a operação 'drain proc'.

        Args:
            q: Parâmetro da operação.
            proc: Parâmetro da operação.
        """
        if not self._alive:
            return
        try:
            if not self.winfo_exists():
                return
        except tk.TclError:
            return

        try:
            for _ in range(100):
                kind, data = q.get_nowait()
                if kind.startswith("eof_"):
                    self._eof_count += 1
                    if self._eof_count >= 2:
                        rc = proc.wait()
                        self._console_print(
                            f"\n[Exited with code {rc}]\n", "ok" if rc == 0 else "err"
                        )
                        self._set_status(
                            f"{'OK' if rc == 0 else 'ERR'} Exited ({rc})",
                            "#6a9955" if rc == 0 else "#f44747",
                        )
                        return
                    continue
                if data:
                    text = (
                        data.decode("utf-8", errors="replace")
                        if isinstance(data, bytes)
                        else data
                    )
                    tag = "err" if kind == "err" else ""
                    self._console_print(text, tag)
        except queue.Empty:
            pass

        self.after(30, lambda: self._drain_proc(q, proc))

    def _stop(self) -> None:
        """Para o processo ou animação em execução, encerrando também a
        sessão DAP ativa (se houver)."""
        if self._dap is not None:
            with contextlib.suppress(Exception):
                self._dap.disconnect()
            self._dap = None
            self._dap_active = False
            self._dap_frames = []
            self._dap_active_frame_id = None
        if self._proc:
            with contextlib.suppress(tk.TclError):
                self._proc.terminate()
            self._proc = None
        self._set_status("⬤ Idle", None)

    def _set_status(self, text: str, color: str | None) -> None:
        """Define o valor de 'status'.

        Args:
            text: Dado textual a processar.
            color: Parâmetro da operação.
        """
        t = self._tmgr.theme
        self._status_lbl.config(text=f"  {text}", fg=color or t.fg_muted)

    def _console_print(self, text: str, tag: str = "") -> None:
        """Executa a operação 'console print'.

        Args:
            text: Dado textual a processar.
            tag: Parâmetro da operação.
        """
        self._console.config(state="normal")
        self._console.insert("end", text, (tag,) if tag else ())
        self._console.see("end")
        self._console.config(state="disabled")

    def _eval_expr(self, _e: Any = None) -> None:
        """Avalia uma expressão digitada no console.

        Se houver uma sessão DAP ativa e parada em um breakpoint, a
        expressão é avaliada de fato no frame remoto (via ``evaluate`` do
        DAP) - REPL real, com acesso às variáveis do programa depurado.
        Caso contrário, cai para um REPL local sandboxed (sem relação com
        o processo em execução).

        Args:
            _e: Parâmetro da operação.
        """
        expr = self._eval_var.get().strip()
        if not expr:
            return
        self._eval_var.set("")
        self._console_print(f">>> {expr}\n", "bold")

        if self._dap_active and self._dap is not None and self._dap.is_stopped:
            self._eval_expr_dap(expr)
            return

        try:
            result = eval(expr, self._eval_ns)  # pylint: disable=eval-used
            self._eval_ns["_"] = result
            self._console_print(repr(result) + "\n", "ok")
            self._refresh_vars()
        except SyntaxError:
            try:
                exec(expr, self._eval_ns)  # pylint: disable=exec-used
                self._console_print("(executed)\n", "dim")
                self._refresh_vars()
            except Exception as exc:
                self._console_print(f"{type(exc).__name__}: {exc}\n", "err")
        except Exception as exc:
            self._console_print(f"{type(exc).__name__}: {exc}\n", "err")

    def _eval_expr_dap(self, expr: str) -> None:
        """Avalia ``expr`` no frame parado atual via DAP, em background."""
        dap = self._dap
        frame_id = self._dap_active_frame_id

        def _work() -> None:
            body = dap.evaluate(expr, frame_id) if dap else {}
            self.after(0, lambda: self._show_dap_eval_result(body))

        threading.Thread(target=_work, daemon=True).start()

    def _show_dap_eval_result(self, body: dict) -> None:
        if body.get("error"):
            self._console_print(f"{body['error']}\n", "err")
            return
        result = body.get("result", "")
        self._console_print(f"{result}\n", "ok")
        if self._dap_active_frame_id is not None:
            self._refresh_dap_variables(self._dap_active_frame_id)

    def _refresh_vars(self) -> None:
        """Atualiza a lista de variáveis locais e globais no frame atual."""
        lines = []
        for k, v in self._eval_ns.items():
            if k.startswith("__"):
                continue
            try:
                rep = repr(v)
                if len(rep) > 60:
                    rep = rep[:57] + "..."
                lines.append(f"{k} = {rep}")
            except Exception:
                lines.append(f"{k} = <?>")
        self._vars_text.config(state="normal")
        self._vars_text.delete("1.0", "end")
        self._vars_text.insert("1.0", "\n".join(lines))
        self._vars_text.config(state="disabled")

    def _add_watch(self, _e: Any = None) -> None:
        """Executa a operação 'add watch'.

        Args:
            _e: Parâmetro da operação.
        """
        t = self._tmgr.theme
        dlg = tk.Toplevel(self)
        dlg.title("Add Watch")
        dlg.configure(bg=t.floating_bg)
        dlg.transient(self)
        safe_grab_set(dlg)
        dlg.resizable(False, False)

        tk.Label(
            dlg,
            text=_t("debug.expression"),
            bg=t.floating_bg,
            fg=t.fg,
            font=(FONT_UI, 10),
        ).pack(padx=16, pady=(12, 4))
        var = tk.StringVar()
        ent = tk.Entry(
            dlg,
            textvariable=var,
            bg=t.input_bg,
            fg=t.fg,
            insertbackground=t.cursor,
            relief="flat",
            bd=0,
            font=(FONT_MONO, 10),
            highlightthickness=1,
            highlightbackground=t.border,
            width=32,
        )
        ent.pack(padx=16, pady=4)
        ent.focus_set()

        def _ok(*_):
            expr = var.get().strip()
            if expr and expr not in self._watch_exprs:
                self._watch_exprs.append(expr)
                self._refresh_watch()
            dlg.destroy()

        ent.bind("<Return>", _ok)
        for text, cmd in (("Add", _ok), ("Cancel", dlg.destroy)):
            b = tk.Label(
                dlg,
                text=text,
                bg=t.btn_bg,
                fg=t.fg,
                font=(FONT_UI, 9),
                cursor="hand2",
                padx=14,
                pady=4,
            )
            b.pack(side="left" if text ==
                   "Cancel" else "right", padx=8, pady=(0, 8))
            b.bind("<Button-1>", lambda _, c=cmd: c())

    def _remove_watch(self, _e: Any = None) -> None:
        """Executa a operação 'remove watch'.

        Args:
            _e: Parâmetro da operação.
        """
        sel = self._watch_lb.curselection()
        if sel:
            self._watch_exprs.pop(sel[0])
            self._refresh_watch()

    def _refresh_watch(self) -> None:
        """Reavalia e atualiza os valores das expressões monitoradas."""
        self._watch_lb.delete(0, "end")
        for expr in self._watch_exprs:
            try:
                val = repr(eval(expr, self._eval_ns)
                           )  # pylint: disable=eval-used
                if len(val) > 40:
                    val = val[:37] + "..."
                self._watch_lb.insert("end", f"  {expr} = {val}")
            except Exception as exc:
                self._watch_lb.insert(
                    "end", f"  {expr} → {type(exc).__name__}")

    def set_breakpoints(self, breakpoints: list[int], filepath: str = "") -> None:
        """Atualiza a lista de breakpoints exibida no painel.

        Se houver uma sessão DAP ativa para o mesmo arquivo, os novos
        breakpoints também são enviados ao debugpy imediatamente (DAP
        permite atualizar breakpoints em tempo real, mesmo com a execução
        em andamento ou parada).
        """
        self._bp_lb.delete(0, "end")
        for bp in sorted(breakpoints):
            stem = Path(filepath).name if filepath else "?"
            self._bp_lb.insert("end", f"  {stem}:{bp}")

        if not filepath:
            return
        self._known_breakpoints[filepath] = set(breakpoints)

        if (
            self._dap_active
            and self._dap is not None
            and self._dap_path is not None
            and str(self._dap_path) == filepath
        ):
            lines = sorted(breakpoints)
            dap = self._dap

            def _work() -> None:
                dap.set_breakpoints(filepath, lines)

            threading.Thread(target=_work, daemon=True).start()

    def _remove_breakpoint(self, _e: Any = None) -> None:
        """Executa a operação 'remove breakpoint'.

        Args:
            _e: Parâmetro da operação.
        """
        sel = self._bp_lb.curselection()
        if sel:
            self._bp_lb.delete(sel[0])

    def apply_theme(self, t: Theme) -> None:
        """Aplica o tema ao painel de debug."""
        self.config(bg=t.panel_bg)
        self._console.config(
            bg=t.terminal_bg,
            fg=t.terminal_fg,
            insertbackground=t.cursor,
            selectbackground=t.sel_bg,
            selectforeground=t.sel_fg,
        )
        self._vars_text.config(bg=t.sidebar_bg, fg=t.fg)
        self._watch_lb.config(bg=t.sidebar_bg, fg=t.fg,
                              selectbackground=t.sel_bg)
        self._bp_lb.config(bg=t.sidebar_bg, fg=t.fg, selectbackground=t.sel_bg)

    def destroy(self) -> None:
        """Para o processo e destrói o painel."""
        self._alive = False
        self._stop()
        super().destroy()
