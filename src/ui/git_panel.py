"""Painel de controle Git integrado ao Codis.

Provê ``GitPanel``, que exibe status, diff, log, branches e permite
operações de commit, push, pull, stash e merge diretamente na interface,
sem necessidade de terminal.
"""

from __future__ import annotations

import contextlib
import logging
import shutil
import subprocess
import threading
import tkinter as tk
from collections.abc import Callable
from pathlib import Path
from tkinter import messagebox, simpledialog, ttk
from typing import Any

from src.config import ConfigManager
from src.constants import (
    FONT_MONO,
    FONT_UI,
)
from src.i18n import _t
from src.theme.base import Theme
from src.theme.manager import ThemeManager
from src.ui.dropdown import DropDown
from src.ui.git_auth import GitAuthManager, _host_from_url, is_auth_error
from src.ui.grab_safe import safe_grab_set

logger = logging.getLogger("Codis")


class _LogTextStub:
    """Stub que emula a interface mínima de tk.Text usada para o log de commits,
    redirecionando inserções para o Listbox interativo."""

    def __init__(self, listbox: tk.Listbox, hashes: list) -> None:
        """Inicializa o adaptador de Listbox com interface de Text."""
        self._lb = listbox
        self._hashes = hashes

    def config(self, **_kw) -> None:
        """Absorve chamadas de configuração de estado (ex.: ``state="disabled"``).

        Este adaptador expõe a interface de um widget ``tk.Text`` sobre um
        ``tk.Listbox``; chamadas ``config()`` são ignoradas intencionalmente,
        pois o Listbox não requer gerenciamento de estado externo.
        """

    def delete(self, _start: str, _end: str) -> None:
        """Limpa o conteúdo do Listbox."""
        self._lb.delete(0, "end")
        self._hashes.clear()

    def insert(self, _index: str, text: str) -> None:
        """Insere linhas de texto no Listbox."""
        for line in text.strip().splitlines():
            line = line.strip()
            if not line:
                continue
            parts = line.split(" ", 1)
            hash_ = parts[0] if parts else ""
            self._hashes.append(hash_)
            self._lb.insert("end", f"  {line}")


class GitPanel(tk.Frame):
    """
    Controle de Código Fonte:
    - Seções colapsáveis: Staged / Changes / Untracked
    - Stage/unstage/discard individuais e em massa
    - Commit com validação de mensagem (resiste a paste)
    - Push/Pull/Fetch em thread com indicador de progresso
    - Ahead/behind do remote
    - Stash rápido (Stash / Pop Stash)
    - Diff inline ao clicar num arquivo
    - Branch switch/create/delete
    - Log compacto com navegação e exploração de commits
    """

    def __init__(
        self,
        parent: tk.Widget,
        tmgr: ThemeManager,
        cfg: ConfigManager,
        root_path: Path | None = None,
        on_refresh: Callable | None = None,
        on_show_diff: Callable[[str, str, str, str], None] | None = None,
        root_win: tk.Tk | None = None,
    ) -> None:
        """Cria o painel de integração Git do Codis.

        Args:
            parent: Widget pai Tkinter.
            tmgr: Instância de ThemeManager.
            cfg: Instância de ConfigManager.
            root_path: Caminho raiz do repositório Git.
            on_refresh: Callback chamado após refresh do status.
            on_show_diff: Callback(orig, mod, label_a, label_b) para exibir diff.
            root_win: Janela raiz Tkinter, necessária para diálogos de autenticação.
        """
        t = tmgr.theme
        super().__init__(parent, bg=t.panel_bg)
        self._tmgr = tmgr
        self._cfg = cfg
        self._git_root = root_path
        self._on_refresh = on_refresh
        self._on_show_diff = on_show_diff
        self._busy = False
        self._cached_remote_url: str | None = None

        _root = root_win or self.winfo_toplevel()
        self._auth = GitAuthManager(cfg, _root)
        self._git_available = shutil.which("git") is not None
        self._build()
        if not self._git_available:
            self.after(
                100,
                lambda: messagebox.showwarning(
                    _t("git.no_git_title", "Git não encontrado"),
                    _t(
                        "git.no_git_msg",
                        "O executável 'git' não foi encontrado no PATH do sistema.\n\n"
                        "Instale o Git (https://git-scm.com/downloads) para usar o\n"
                        "controle de código-fonte no Codis.",
                    ),
                    parent=self,
                ),
            )
        elif root_path:
            self.after(100, self.refresh)

    def _build(self) -> None:
        """Constrói os widgets e layout interno do componente."""
        t = self._tmgr.theme

        accent_strip = tk.Frame(self, bg=getattr(
            t, "accent", t.t_func), height=2)
        accent_strip.pack(fill="x")

        hdr = tk.Frame(self, bg=t.panel_bg, height=34)
        hdr.pack(fill="x")
        hdr.pack_propagate(False)
        tk.Label(
            hdr,
            text="🌿 " + _t("hc.git_panel.source_control"),
            bg=t.panel_bg,
            fg=t.sidebar_header_fg,
            font=(FONT_UI, 9, "bold"),
        ).pack(side="left", padx=6)

        def _hbtn(
            text: str, cmd: Any, bg: str | None = None, tooltip: str = ""
        ) -> None:
            bg_c = bg or t.btn_bg
            fg_c = "#000" if bg_c == t.accent else t.fg
            b = tk.Label(
                hdr,
                text=text,
                bg=bg_c,
                fg=fg_c,
                font=(FONT_UI, 8),
                cursor="hand2",
                padx=6,
                pady=3,
                relief="flat",
            )
            b.pack(side="right", padx=1, pady=4)
            b.bind("<Button-1>", lambda _: cmd())
            b.bind("<Enter>", lambda _: b.config(bg=t.btn_hover))
            b.bind("<Leave>", lambda _: b.config(bg=bg_c))

        _hbtn("↻", self.refresh, tooltip="Refresh")
        _hbtn("↑ Push", self._push, bg=t.accent, tooltip="Push para remote")
        _hbtn("↓ Pull", self._pull, tooltip="Pull do remote")
        _hbtn("⟳ Fetch", self._fetch, tooltip="Fetch sem merge")
        _hbtn("📦 Stash", self._stash, tooltip="Guardar alterações")
        _hbtn("↩ Pop", self._pop_stash, tooltip="Restaurar stash")
        _hbtn("🏷 Tag", self._create_tag, tooltip="Criar tag de versão")
        _hbtn("⚙ Rebase", self._interactive_rebase, tooltip="Rebase interativo")

        self._status_lbl = tk.Label(
            self, text="", bg=t.panel_bg, fg=t.fg_muted, font=(FONT_UI, 8), anchor="w"
        )
        self._status_lbl.pack(fill="x", padx=8)

        branch_f = tk.Frame(self, bg=t.panel_bg)
        branch_f.pack(fill="x", padx=4, pady=2)
        tk.Label(
            branch_f, text="🌿", bg=t.panel_bg, fg=t.t_func, font=(FONT_UI, 10)
        ).pack(side="left")
        self._branch_var = tk.StringVar(value="-")
        self._branch_combo = DropDown(
            branch_f,
            textvariable=self._branch_var,
            width=20,
            font=(FONT_UI, 9),
            state="readonly",
        )
        self._branch_combo.pack(side="left", padx=2)
        self._branch_combo.bind("<<ComboboxSelected>>", self._switch_branch)

        plus_lbl = tk.Label(
            branch_f,
            text="⊕",
            bg=t.panel_bg,
            fg=t.fg,
            font=(FONT_UI, 11),
            cursor="hand2",
        )
        plus_lbl.pack(side="left", padx=2)
        plus_lbl.bind("<Button-1>", lambda _: self._create_branch())

        msg_f = tk.Frame(self, bg=t.panel_bg)
        msg_f.pack(fill="x", padx=4, pady=2)
        self._msg_text = tk.Text(
            msg_f,
            bg=t.input_bg,
            fg=t.fg,
            insertbackground=t.cursor,
            relief="flat",
            bd=0,
            height=3,
            font=(FONT_UI, 9),
            highlightthickness=1,
            highlightbackground=t.border,
            wrap="word",
            padx=4,
            pady=2,
        )
        self._msg_text.pack(fill="x")
        self._placeholder = _t("ui.commit_placeholder")
        self._msg_text.insert("1.0", self._placeholder)
        self._msg_text.config(fg=t.fg_muted)
        self._msg_text.bind("<FocusIn>", self._msg_focus_in)
        self._msg_text.bind("<FocusOut>", self._msg_focus_out)

        self._msg_text.bind("<<Paste>>", self._msg_on_paste)

        btn_f = tk.Frame(self, bg=t.panel_bg)
        btn_f.pack(fill="x", padx=4, pady=(0, 4))
        self._btn(btn_f, "＋ Stage All", self._stage_all, side="left")
        self._btn(btn_f, "Commit", self._commit,
                  side="right", bg=t.accent, fg="#000")

        tk.Frame(self, bg=t.border, height=1).pack(fill="x")

        style = ttk.Style()
        style.configure(
            "Git.Treeview",
            background=t.panel_bg,
            foreground=t.fg,
            fieldbackground=t.panel_bg,
            borderwidth=0,
            rowheight=20,
            font=(FONT_UI, 9),
        )
        style.map(
            "Git.Treeview",
            background=[("selected", t.sel_bg)],
            foreground=[("selected", t.sel_fg)],
        )

        tree_f = tk.Frame(self, bg=t.panel_bg)
        tree_f.pack(fill="both", expand=True)

        self._tree = ttk.Treeview(
            tree_f, columns=(), show="tree", style="Git.Treeview", selectmode="browse"
        )
        vsb = ttk.Scrollbar(
            tree_f,
            orient="vertical",
            command=self._tree.yview,
            style="Thin.Vertical.TScrollbar",
        )
        vsb.pack(side="right", fill="y")
        self._tree.config(yscrollcommand=vsb.set)
        self._tree.pack(fill="both", expand=True)

        self._tree.tag_configure(
            "section", foreground=t.fg_muted, font=(FONT_UI, 9, "bold")
        )
        self._tree.tag_configure("staged", foreground=t.ok_fg)
        self._tree.tag_configure("modified", foreground=t.warn_fg)
        self._tree.tag_configure("untracked", foreground=t.fg_muted)
        self._tree.tag_configure("deleted", foreground=t.fg_muted)

        self._tree.bind("<Double-Button-1>", self._on_double)
        self._tree.bind("<Return>", self._on_double)
        self._tree.bind("<Button-3>", self._on_ctx)

        self._item_meta: dict[str, tuple[str, str]] = {}

        tk.Frame(self, bg=t.border, height=1).pack(fill="x")
        log_hdr = tk.Frame(self, bg=t.panel_bg, height=22)
        log_hdr.pack(fill="x")
        log_hdr.pack_propagate(False)
        tk.Label(
            log_hdr,
            text=_t("hc.git_panel.recent_commits"),
            bg=t.panel_bg,
            fg=t.sidebar_header_fg,
            font=(FONT_UI, 8, "bold"),
        ).pack(side="left", padx=4)
        tk.Label(
            log_hdr,
            text=_t("hc.git_panel.click_to_explore", "clique para explorar"),
            bg=t.panel_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 7),
        ).pack(side="right", padx=4)

        log_frame = tk.Frame(self, bg=t.panel_bg)
        log_frame.pack(fill="x")
        self._log_listbox = tk.Listbox(
            log_frame,
            bg=t.panel_bg,
            fg=t.fg_muted,
            selectbackground=getattr(t, "accent", t.t_func),
            selectforeground=t.editor_bg,
            relief="flat",
            bd=0,
            height=5,
            font=(FONT_MONO, 8),
            highlightthickness=0,
            activestyle="none",
            cursor="hand2",
        )
        self._log_listbox.pack(fill="x", side="left", expand=True)
        self._log_listbox.bind("<ButtonRelease-1>", self._on_commit_click)
        self._log_listbox.bind("<Return>", self._on_commit_click)
        self._commit_hashes: list[str] = []

        self._log_text = _LogTextStub(self._log_listbox, self._commit_hashes)

    def _btn(
        self,
        parent: tk.Widget,
        text: str,
        cmd: Callable,
        side: str = "left",
        bg: str | None = None,
        fg: str | None = None,
    ) -> tk.Label:
        """Cria e retorna um botão estilizado.

        Args:
            parent: Widget pai Tkinter.
            text: Dado textual a processar.
            cmd: Parâmetro da operação.
            side: Parâmetro da operação.
            bg: Parâmetro da operação.
            fg: Parâmetro da operação.
        """
        t = self._tmgr.theme
        lbl = tk.Label(
            parent,
            text=text,
            bg=bg or t.btn_bg,
            fg=fg or t.fg,
            font=(FONT_UI, 8),
            padx=8,
            pady=2,
            cursor="hand2",
            relief="flat",
        )
        lbl.pack(side=side, padx=2, pady=2)
        lbl.bind("<Button-1>", lambda _: cmd())
        hover = t.btn_hover if bg != t.accent else t.btn_primary
        lbl.bind("<Enter>", lambda _: lbl.config(bg=hover))
        lbl.bind("<Leave>", lambda _: lbl.config(bg=bg or t.btn_bg))
        return lbl

    def _git(self, *args, timeout: int = 15) -> tuple[int, str, str]:
        """Executa comando Git e retorna (returncode, stdout, stderr).

        Injeta variáveis de ambiente de autenticação automaticamente
        quando há credenciais salvas para o remote atual.

        Args:
            *args: Argumentos do comando Git.
            timeout: Timeout em segundos.

        Returns:
            tuple[int, str, str]: (returncode, stdout, stderr).
        """
        if not self._git_root:
            return -1, "", "no root"
        try:
            if self._cached_remote_url is None:
                self._cached_remote_url = self._detect_remote_url() or ""
            remote_url = self._cached_remote_url or None
            env = self._auth.build_env(remote_url) if remote_url else None

            r = subprocess.run(
                ["git"] + list(args),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                cwd=str(self._git_root),
                timeout=timeout,
                check=False,
                **({"env": env} if env else {}),
            )
            return r.returncode, r.stdout, r.stderr
        except FileNotFoundError:
            return (
                -1,
                "",
                _t(
                    "git.no_git_msg_short",
                    "Git não encontrado. Instale o Git para usar esta função.",
                ),
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return -1, "", str(exc)

    def _detect_remote_url(self) -> str | None:
        """Detecta a URL do remote 'origin' do repositório atual.

        Returns:
            str | None: URL do remote ou None se não encontrado.
        """
        try:
            r = subprocess.run(
                ["git", "remote", "get-url", "origin"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                cwd=str(self._git_root),
                timeout=5,
                check=False,
            )
            if r.returncode == 0:
                return r.stdout.strip()
        except (OSError, FileNotFoundError):
            pass
        return None

    def set_root(self, path: Path) -> None:
        """Define 'root'.

        Args:
            path: Caminho do arquivo ou diretório.
        """
        if self._git_root == path:
            return
        self._git_root = path
        self._cached_remote_url = None
        self.refresh()

    def refresh(self) -> None:
        """Atualiza o status do repositório e a lista de arquivos."""
        if not self._git_root:
            return

        def _work():
            rc_s, out_s, _ = self._git("status", "--porcelain", "-u")
            rc_b, out_b, _ = self._git("branch", "--format=%(refname:short)")
            rc_c, out_c, _ = self._git("branch", "--show-current")
            rc_l, out_l, _ = self._git(
                "log", "--oneline", "-20", "--format=%h %s (%an, %ar)"
            )

            rc_ab, out_ab, _ = self._git(
                "rev-list", "--left-right", "--count", "@{upstream}...HEAD"
            )
            self.after(
                0,
                lambda: self._apply_refresh(
                    out_s if rc_s == 0 else "",
                    out_b if rc_b == 0 else "",
                    out_c if rc_c == 0 else "",
                    out_l if rc_l == 0 else "",
                    out_ab if rc_ab == 0 else "",
                ),
            )

        threading.Thread(target=_work, daemon=True).start()

    def _apply_refresh(
        self,
        status_out: str,
        branches_out: str,
        current_out: str,
        log_out: str,
        ahead_behind: str,
    ) -> None:
        """Atualiza a UI - chamado sempre na main thread via after()."""

        try:
            if not self.winfo_exists():
                return
        except tk.TclError:
            return
        for item in self._tree.get_children():
            self._tree.delete(item)
        self._item_meta.clear()

        staged: list[tuple[str, str]] = []
        unstaged: list[tuple[str, str]] = []
        untracked: list[str] = []

        for line in status_out.splitlines():
            if len(line) < 4:
                continue
            x, y = line[0], line[1]

            fname = line[3:].strip()
            if " -> " in fname:
                fname = fname.split(" -> ", 1)[1]
            if x not in (" ", "?"):
                staged.append((x, fname))
            if y not in (" ",) or x == "?":
                if x == "?":
                    untracked.append(fname)
                else:
                    unstaged.append((y, fname))

        def _section(title: str, count: int) -> str:
            return self._tree.insert(
                "", "end", text=f"  {title}  ({count})", tags=("section",), open=True
            )

        if staged:
            node = _section("STAGED CHANGES", len(staged))
            for x, f in staged:
                icon = {"M": "✎", "A": "＋", "D": "✖", "R": "➜"}.get(x, x)
                iid = self._tree.insert(
                    node, "end", text=f"  {icon}  {f}", tags=("staged",)
                )
                self._item_meta[iid] = (f, "staged")

        if unstaged:
            node = _section("CHANGES", len(unstaged))
            for y, f in unstaged:
                icon = {"M": "✎", "D": "✖"}.get(y, y)
                iid = self._tree.insert(
                    node, "end", text=f"  {icon}  {f}", tags=("modified",)
                )
                self._item_meta[iid] = (f, "unstaged")

        if untracked:
            node = _section("UNTRACKED", len(untracked))
            for f in untracked:
                iid = self._tree.insert(
                    node, "end", text=f"  ?  {f}", tags=("untracked",)
                )
                self._item_meta[iid] = (f, "untracked")

        if not (staged or unstaged or untracked):
            self._tree.insert(
                "", "end", text=_t("hc.git_panel.no_changes"), tags=("staged",)
            )

        branches = [b.strip() for b in branches_out.splitlines() if b.strip()]
        self._branch_combo["values"] = branches
        cur = current_out.strip()
        if cur:
            self._branch_var.set(cur)
        elif branches:
            self._branch_var.set(branches[0])

        ab = ahead_behind.strip()
        if ab:
            parts = ab.split()
            if len(parts) == 2:
                try:
                    behind, ahead = int(parts[0]), int(parts[1])
                    parts_str = []
                    if ahead:
                        parts_str.append(f"↑{ahead}")
                    if behind:
                        parts_str.append(f"↓{behind}")
                    self._status_lbl.config(
                        text="  " + " ".join(parts_str) if parts_str else ""
                    )
                except ValueError:
                    self._status_lbl.config(text="")

        self._log_text.config(state="normal")
        self._log_text.delete("1.0", "end")
        if log_out:
            self._log_text.insert("1.0", log_out.strip())
        self._log_text.config(state="disabled")

        if self._on_refresh:
            with contextlib.suppress(tk.TclError):
                self._on_refresh()

    def _stage(self, fname: str) -> None:
        self._git("add", "--", fname)
        self.refresh()

    def _stage_all(self) -> None:
        """Adiciona todos os arquivos modificados ao staging area."""
        self._git("add", "-A")
        self.refresh()

    def _unstage(self, fname: str) -> None:
        self._git("restore", "--staged", "--", fname)
        self.refresh()

    def _discard(self, fname: str) -> None:
        if messagebox.askyesno(
            _t("hc.git_panel.discard"), f"Discard changes to '{fname}'?", parent=self
        ):
            self._git("restore", "--", fname)
            self.refresh()

    def _commit(self) -> None:
        """Executa o commit com a mensagem do campo de texto."""
        msg = self._msg_text.get("1.0", "end-1c").strip()
        if not msg or msg == self._placeholder:
            messagebox.showwarning(
                _t("menu.git"), _t("msg.git_commit_empty"), parent=self
            )
            return

        if self._git_root:
            local_trash = Path(self._git_root) / ".codis_trash"
            gi = local_trash / ".gitignore"
            if local_trash.is_dir() and not gi.exists():
                with contextlib.suppress(OSError):
                    gi.write_text("# Lixeira local do Codis - não versionar\n*\n", encoding="utf-8")

        rc, out, err = self._git("commit", "-m", msg)
        if rc == 0:
            self._msg_text.delete("1.0", "end")
            self._msg_text.insert("1.0", self._placeholder)
            self._msg_text.config(fg=self._tmgr.theme.fg_muted)
            self.refresh()
        else:
            messagebox.showerror(
                _t("hc.git_panel.commit_failed"), err or out, parent=self
            )

    def _push(self) -> None:
        """Executa git push para o remote e branch configurados."""
        self._run_async("push", timeout=30)

    def _pull(self) -> None:
        """Executa git pull do remote e branch configurados."""
        self._run_async("pull", timeout=30)

    def _fetch(self) -> None:
        """Executa git fetch para atualizar as referências remotas."""
        self._run_async("fetch", timeout=20)

    def _stash(self) -> None:
        """Abre painel de gestão completa de stashes."""
        t = self._tmgr.theme
        win = tk.Toplevel(self)
        win.title(_t("hc.git_panel.stash_manager", "Gerenciador de Stash"))
        win.configure(bg=t.floating_bg)
        win.resizable(True, True)
        win.geometry("500x420")
        win.transient(self.winfo_toplevel())

        hdr = tk.Frame(win, bg=t.floating_bg)
        hdr.pack(fill="x", padx=16, pady=(14, 6))
        tk.Label(
            hdr,
            text="📦  " + _t("hc.git_panel.stash_manager",
                            "Gerenciador de Stash"),
            bg=t.floating_bg,
            fg=t.fg,
            font=(FONT_UI, 12, "bold"),
            anchor="w",
        ).pack(side="left")

        act_bar = tk.Frame(win, bg=t.floating_bg)
        act_bar.pack(fill="x", padx=16, pady=(0, 8))

        stash_msg_var = tk.StringVar()
        msg_ent = tk.Entry(
            act_bar,
            textvariable=stash_msg_var,
            bg=t.input_bg,
            fg=t.fg,
            insertbackground=t.cursor,
            relief="flat",
            bd=0,
            highlightthickness=1,
            highlightbackground=t.border,
            font=(FONT_UI, 9),
            width=26,
        )
        msg_ent.insert(
            0, _t("git.stash_msg_placeholder", "Mensagem (opcional)"))
        msg_ent.config(fg=t.fg_muted)

        def _msg_focus_in(_e):
            if msg_ent.get() == _t("git.stash_msg_placeholder", "Mensagem (opcional)"):
                msg_ent.delete(0, "end")
                msg_ent.config(fg=t.fg)

        def _msg_focus_out(_e):
            if not msg_ent.get().strip():
                msg_ent.insert(
                    0, _t("git.stash_msg_placeholder", "Mensagem (opcional)")
                )
                msg_ent.config(fg=t.fg_muted)

        msg_ent.bind("<FocusIn>", _msg_focus_in)
        msg_ent.bind("<FocusOut>", _msg_focus_out)
        msg_ent.pack(side="left", fill="x", expand=True, padx=(0, 6))

        def _do_stash():
            raw = stash_msg_var.get().strip()
            placeholder = _t("git.stash_msg_placeholder",
                             "Mensagem (opcional)")
            msg = raw if raw and raw != placeholder else ""
            args = ["stash", "push"]
            if msg:
                args += ["-m", msg]
            rc, out, err = self._git(*args)
            if rc == 0:
                self._set_status(_t("git.stash_saved", "Stash salvo."))
                _refresh_list()
                self.refresh()
            else:
                messagebox.showerror(
                    _t("git.err_title", "Erro"), err or out, parent=win
                )

        tk.Label(
            act_bar,
            text=_t("hc.git_panel.stash_changes", "📦 Guardar"),
            bg=t.accent,
            fg="#000",
            font=(FONT_UI, 9, "bold"),
            cursor="hand2",
            padx=10,
            pady=4,
            relief="flat",
        ).pack(side="left")
        act_bar.winfo_children()[-1].bind("<Button-1>", lambda _: _do_stash())

        tk.Frame(win, bg=t.border, height=1).pack(
            fill="x", padx=16, pady=(0, 4))
        list_frame = tk.Frame(win, bg=t.floating_bg)
        list_frame.pack(fill="both", expand=True, padx=16, pady=(0, 4))

        stash_lb = tk.Listbox(
            list_frame,
            bg=t.sidebar_bg,
            fg=t.fg,
            selectbackground=t.sel_bg,
            selectforeground=t.sel_fg,
            relief="flat",
            bd=0,
            font=(FONT_MONO, 9),
            activestyle="none",
            highlightthickness=1,
            highlightbackground=t.border,
        )
        vsb = tk.Scrollbar(
            list_frame,
            orient="vertical",
            command=stash_lb.yview,
            bg=t.scroll_thumb,
            troughcolor=t.scroll_bg,
            relief="flat",
            bd=0,
            width=6,
        )
        vsb.pack(side="right", fill="y")
        stash_lb.config(yscrollcommand=vsb.set)
        stash_lb.pack(fill="both", expand=True)

        _stash_refs: list[str] = []

        def _refresh_list():
            stash_lb.delete(0, "end")
            _stash_refs.clear()
            rc, out, _ = self._git("stash", "list")
            if rc == 0 and out.strip():
                for line in out.strip().splitlines():
                    parts = line.split(":", 1)
                    ref = parts[0].strip()
                    label = line.strip()
                    _stash_refs.append(ref)
                    stash_lb.insert("end", f"  {label}")
            else:
                stash_lb.insert(
                    "end", _t("git.no_stashes", "  Nenhum stash encontrado.")
                )

        _refresh_list()

        tk.Frame(win, bg=t.border, height=1).pack(
            fill="x", padx=16, pady=(0, 6))
        btn_bar = tk.Frame(win, bg=t.floating_bg)
        btn_bar.pack(fill="x", padx=16, pady=(0, 12))

        def _selected_ref() -> str | None:
            sel = stash_lb.curselection()
            if not sel or sel[0] >= len(_stash_refs):
                messagebox.showwarning(
                    _t("git.stash_none_sel", "Seleção"),
                    _t("git.stash_select_hint", "Selecione um stash na lista."),
                    parent=win,
                )
                return None
            return _stash_refs[sel[0]]

        def _apply_stash():
            ref = _selected_ref()
            if not ref:
                return
            rc, out, err = self._git("stash", "apply", ref)
            if rc == 0:
                self._set_status(_t("git.stash_applied", "Stash aplicado."))
                self.refresh()
            else:
                messagebox.showerror(
                    _t("git.err_title", "Erro"), err or out, parent=win
                )

        def _pop_stash_sel():
            ref = _selected_ref()
            if not ref:
                return
            rc, out, err = self._git("stash", "pop", ref)
            if rc == 0:
                self._set_status(_t("git.stash_popped", "Stash restaurado."))
                _refresh_list()
                self.refresh()
            else:
                messagebox.showerror(
                    _t("git.err_title", "Erro"), err or out, parent=win
                )

        def _drop_stash():
            ref = _selected_ref()
            if not ref:
                return
            if not messagebox.askyesno(
                _t("git.stash_drop_title", "Remover Stash"),
                _t(
                    "git.stash_drop_confirm",
                    f"Remover {ref}? Esta ação não pode ser desfeita.",
                ),
                parent=win,
            ):
                return
            rc, out, err = self._git("stash", "drop", ref)
            if rc == 0:
                self._set_status(_t("git.stash_dropped", "Stash removido."))
                _refresh_list()
            else:
                messagebox.showerror(
                    _t("git.err_title", "Erro"), err or out, parent=win
                )

        def _show_stash():
            ref = _selected_ref()
            if not ref:
                return
            rc, out, err = self._git("stash", "show", "-p", ref)
            if rc != 0:
                messagebox.showerror(
                    _t("git.err_title", "Erro"), err or out, parent=win
                )
                return
            if self._on_show_diff:
                self._on_show_diff(
                    "", out, f"{ref} (anterior)", f"{ref} (diff)")
            else:
                dwin = tk.Toplevel(win)
                dwin.title(f"Stash diff: {ref}")
                dwin.configure(bg=t.editor_bg)
                dwin.geometry("720x500")
                txt = tk.Text(
                    dwin,
                    bg=t.editor_bg,
                    fg=t.fg,
                    font=(FONT_MONO, 9),
                    relief="flat",
                    bd=0,
                    wrap="none",
                )
                sby = tk.Scrollbar(dwin, orient="vertical", command=txt.yview)
                sbx = tk.Scrollbar(
                    dwin, orient="horizontal", command=txt.xview)
                txt.configure(yscrollcommand=sby.set, xscrollcommand=sbx.set)
                sby.pack(side="right", fill="y")
                sbx.pack(side="bottom", fill="x")
                txt.pack(fill="both", expand=True)
                txt.tag_config("add", foreground="#4caf50")
                txt.tag_config("rem", foreground="#f44336")
                txt.tag_config("hunk", foreground="#64b5f6")
                for line in out.splitlines(keepends=True):
                    tag = ""
                    if line.startswith("+") and not line.startswith("+++"):
                        tag = "add"
                    elif line.startswith("-") and not line.startswith("---"):
                        tag = "rem"
                    elif line.startswith("@@"):
                        tag = "hunk"
                    txt.insert("end", line, tag)
                txt.config(state="disabled")

        btn_specs = [
            (
                _t("hc.git_panel.pop_stash", "↩ Pop (aplicar + remover)"),
                _pop_stash_sel,
                t.accent,
                "#000",
            ),
            (_t("git.stash_apply", "✅ Aplicar"), _apply_stash, t.btn_bg, t.fg),
            (_t("git.stash_show_diff", "🔍 Ver Diff"), _show_stash, t.btn_bg, t.fg),
            (_t("git.stash_drop", "🗑 Remover"), _drop_stash, t.btn_bg, t.fg),
        ]
        for label, cmd, bg, fg in btn_specs:
            b = tk.Label(
                btn_bar,
                text=label,
                bg=bg,
                fg=fg,
                font=(FONT_UI, 9, "bold" if bg == t.accent else "normal"),
                cursor="hand2",
                padx=8,
                pady=4,
                relief="flat",
            )
            b.pack(side="left", padx=(0, 4))
            b.bind("<Button-1>", lambda _, c=cmd: c())
            b.bind(
                "<Enter>",
                lambda _, w=b: w.config(
                    bg=t.btn_hover if w.cget("bg") != t.accent else t.accent
                ),
            )
            b.bind("<Leave>", lambda _, w=b, ob=bg: w.config(bg=ob))

        tk.Label(
            btn_bar,
            text=_t("ui.close", "Fechar"),
            bg=t.btn_bg,
            fg=t.fg,
            font=(FONT_UI, 9),
            cursor="hand2",
            padx=10,
            pady=4,
            relief="flat",
        ).pack(side="right")
        btn_bar.winfo_children()[-1].bind("<Button-1>",
                                          lambda _: win.destroy())

    def _run_async(self, *args, timeout: int = 15) -> None:
        """Executa comando Git em thread separada com suporte a autenticação.

        Ao detectar erro de autenticação no stderr, exibe o diálogo de
        credenciais e re-executa o comando automaticamente.

        Args:
            *args: Argumentos do comando Git.
            timeout: Timeout em segundos.
        """
        if self._busy:
            return
        self._busy = True
        self._status_lbl.config(text=_t("hc.git_panel.working"))

        def _work():
            rc, out, err = self._git(*args, timeout=timeout)

            def _done(rc=rc, out=out, err=err):
                self._busy = False
                self._status_lbl.config(text="")
                if rc != 0:
                    combined = (out + err).strip()

                    if is_auth_error(err):
                        self._handle_auth_retry(list(args), timeout, err)
                        return

                    err_low = err.lower()
                    if "host key verification failed" in err_low:
                        host = _host_from_url(self._detect_remote_url() or "")
                        self._auth.show_host_key_help(self._tmgr.theme, host)
                        return
                    if "permission denied (publickey" in err_low:
                        self._auth.show_ssh_help(self._tmgr.theme)
                        return
                    if err:
                        messagebox.showerror(
                            args[0].capitalize() + " failed", combined, parent=self
                        )
                self.refresh()

            self.after(0, _done)

        threading.Thread(target=_work, daemon=True).start()

    _MAX_AUTH_RETRIES = 3

    def _handle_auth_retry(
        self, git_args: list[str], timeout: int, _err: str, attempt: int = 1
    ) -> None:
        """Exibe diálogo de credenciais e re-executa o comando Git.

        Chamado quando _run_async detecta erro de autenticação. Se a nova
        tentativa também falhar por autenticação, reabre o diálogo de
        credenciais (até um limite de tentativas) em vez de mostrar apenas
        um erro genérico cru para o usuário.

        Args:
            git_args: Argumentos originais do comando Git.
            timeout: Timeout em segundos.
            _err: Mensagem de erro original (não usada diretamente).
            attempt: Número da tentativa atual (para limitar retries).
        """
        if not self._git_root:
            return
        remote_url = self._detect_remote_url() or ""

        def _on_done(rc: int, _out: str, err: str) -> None:
            self._busy = False
            self._status_lbl.config(text="")
            if rc != 0 and err:
                if is_auth_error(err) and attempt < self._MAX_AUTH_RETRIES:
                    self._handle_auth_retry(
                        git_args, timeout, err, attempt + 1)
                    return
                messagebox.showerror(
                    git_args[0].capitalize() + " failed", err.strip(), parent=self
                )
            self.refresh()

        self._auth.prompt_and_retry(
            remote_url,
            ["git"] + git_args,
            str(self._git_root),
            self._tmgr.theme,
            _on_done,
        )

    def _switch_branch(self, _e: Any = None) -> None:
        """Troca para a branch selecionada no combo de branches."""
        branch = self._branch_var.get()
        if not branch or branch == "-":
            return
        rc, _, err = self._git("checkout", branch)
        if rc != 0:
            messagebox.showerror(
                _t("hc.git_panel.checkout_failed"), err, parent=self)
        self.refresh()

    def _on_commit_click(self, _e: Any = None) -> None:
        """Abre o diff completo do commit selecionado no painel de diff do Codis."""
        sel = self._log_listbox.curselection()
        if not sel:
            return
        idx = sel[0]
        if idx >= len(self._commit_hashes):
            return
        commit_hash = self._commit_hashes[idx].strip()
        if not commit_hash:
            return

        def _fetch():
            rc_stat, stat_out, _ = self._git(
                "show",
                "--stat",
                "--format=%H %s",
                commit_hash,
            )
            rc_diff, diff_out, diff_err = self._git(
                "show",
                commit_hash,
                "--format=commit %H%nAuthor: %an <%ae>%nDate:   %ad%n%n    %s%n",
                "--unified=3",
            )
            if rc_diff != 0:
                err = diff_err or "Falha ao obter diff do commit."
                self.after(
                    0, lambda: messagebox.showerror(
                        "Git Show", err, parent=self)
                )
                return

            rc_p, parent_out, _ = self._git("rev-parse", f"{commit_hash}^")
            _ = parent_out.strip() if rc_p == 0 else ""

            label_a = f"{commit_hash[:7]}^ (anterior)"
            label_b = f"{commit_hash[:7]} (commit)"

            header_lines = [
                f"# Commit: {commit_hash}",
                stat_out.strip() if rc_stat == 0 else "",
                "",
                "# Diff completo:",
                "-" * 60,
            ]
            content_b = "\n".join(header_lines) + "\n" + diff_out
            content_a = ""

            self.after(
                0,
                lambda: self._open_commit_view(
                    content_a,
                    content_b,
                    label_a,
                    label_b,
                    commit_hash,
                ),
            )

        threading.Thread(target=_fetch, daemon=True).start()

    def _open_commit_view(
        self,
        content_a: str,
        content_b: str,
        label_a: str,
        label_b: str,
        commit_hash: str,
    ) -> None:
        """Exibe o conteúdo do commit no diff pane ou num diálogo dedicado."""
        if self._on_show_diff:
            self._on_show_diff(content_a, content_b, label_a, label_b)
            return

        t = self._tmgr.theme
        win = tk.Toplevel(self)
        win.title(f"Commit {commit_hash[:12]}")
        win.configure(bg=t.editor_bg)
        win.geometry("860x620")

        header = tk.Label(
            win,
            text=f"🔍  Explorando commit {commit_hash[:12]}",
            bg=t.editor_bg,
            fg=t.fg,
            font=(FONT_UI, 11, "bold"),
            anchor="w",
            padx=12,
            pady=8,
        )
        header.pack(fill="x")
        tk.Frame(win, bg=t.border, height=1).pack(fill="x")

        txt = tk.Text(
            win,
            bg=t.editor_bg,
            fg=t.fg,
            font=(FONT_MONO, 9),
            relief="flat",
            bd=0,
            wrap="none",
            state="normal",
        )
        sb_y = tk.Scrollbar(win, orient="vertical", command=txt.yview)
        sb_x = tk.Scrollbar(win, orient="horizontal", command=txt.xview)
        txt.configure(yscrollcommand=sb_y.set, xscrollcommand=sb_x.set)
        sb_y.pack(side="right", fill="y")
        sb_x.pack(side="bottom", fill="x")
        txt.pack(fill="both", expand=True, padx=4, pady=4)

        txt.tag_config("add", foreground="#4caf50")
        txt.tag_config("rem", foreground="#f44336")
        txt.tag_config("hunk", foreground="#64b5f6")
        txt.tag_config("header", foreground=t.t_func)

        for line in content_b.splitlines(keepends=True):
            tag = None
            if line.startswith("+") and not line.startswith("+++"):
                tag = "add"
            elif line.startswith("-") and not line.startswith("---"):
                tag = "rem"
            elif line.startswith("@@"):
                tag = "hunk"
            elif line.startswith("commit ") or line.startswith("diff "):
                tag = "header"
            txt.insert("end", line, tag or "")

        txt.config(state="disabled")
        tk.Button(
            win,
            text=_t("git.btn_close", "Fechar"),
            command=win.destroy,
            bg=t.btn_bg,
            fg=t.btn_fg,
            relief="flat",
            padx=16,
        ).pack(pady=6)

    def _pop_stash(self) -> None:
        """Restaura o último stash salvo."""
        rc, out, err = self._git("stash", "pop")
        if rc == 0:
            self._set_status(_t("git.stash_popped", "Stash restaurado."))
            self.refresh()
        else:
            messagebox.showerror(
                _t("git.err_title", "Erro"), err or out, parent=self)

    def _create_tag(self) -> None:
        """Cria uma tag anotada no commit atual."""
        t = self._tmgr.theme
        win = tk.Toplevel(self)
        win.title(_t("git.create_tag_title", "Criar Tag"))
        win.config(bg=t.floating_bg)
        safe_grab_set(win)
        win.resizable(False, False)

        tk.Label(
            win,
            text=_t("git.tag_name", "Nome da tag (ex: v1.0.0):"),
            bg=t.floating_bg,
            fg=t.fg,
            font=(FONT_UI, 9),
        ).pack(padx=12, pady=(10, 2))
        tag_var = tk.StringVar()
        tk.Entry(
            win,
            textvariable=tag_var,
            bg=t.input_bg,
            fg=t.fg,
            insertbackground=t.cursor,
            relief="flat",
            font=(FONT_UI, 10),
            width=24,
            bd=0,
            highlightthickness=1,
            highlightbackground=t.border,
        ).pack(padx=12, pady=2)

        tk.Label(
            win,
            text=_t("git.tag_msg", "Mensagem (opcional):"),
            bg=t.floating_bg,
            fg=t.fg,
            font=(FONT_UI, 9),
        ).pack(padx=12, pady=(6, 2))
        msg_var = tk.StringVar()
        tk.Entry(
            win,
            textvariable=msg_var,
            bg=t.input_bg,
            fg=t.fg,
            insertbackground=t.cursor,
            relief="flat",
            font=(FONT_UI, 10),
            width=24,
            bd=0,
            highlightthickness=1,
            highlightbackground=t.border,
        ).pack(padx=12, pady=2)

        def _do_tag() -> None:
            name = tag_var.get().strip()
            if not name:
                return
            msg = msg_var.get().strip() or f"Release {name}"
            rc, out, err = self._git("tag", "-a", name, "-m", msg)
            if rc == 0:
                self._set_status(
                    _t("git.tag_created", f"Tag '{name}' criada."))
                self.refresh()
            else:
                messagebox.showerror(
                    _t("git.err_title", "Erro"), err or out, parent=self
                )
            win.destroy()

        btn_row = tk.Frame(win, bg=t.floating_bg)
        btn_row.pack(fill="x", padx=8, pady=8)
        tk.Button(
            btn_row,
            text=_t("git.create", "Criar"),
            command=_do_tag,
            bg=t.accent,
            fg="#000",
            relief="flat",
            font=(FONT_UI, 9),
            padx=10,
        ).pack(side="right", padx=2)
        tk.Button(
            btn_row,
            text=_t("git.cancel", "Cancelar"),
            command=win.destroy,
            bg=t.sidebar_bg,
            fg=t.fg,
            relief="flat",
            font=(FONT_UI, 9),
        ).pack(side="right")
        win.update_idletasks()
        sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
        ww, wh = win.winfo_reqwidth(), win.winfo_reqheight()
        win.geometry(f"{ww}x{wh}+{(sw - ww) // 2}+{(sh - wh) // 2}")

    def _interactive_rebase(self) -> None:
        """Rebase interativo nos últimos N commits."""
        n = simpledialog.askinteger(
            _t("git.rebase_title", "Rebase Interativo"),
            _t("git.rebase_prompt", "Quantos commits incluir no rebase?"),
            minvalue=1,
            maxvalue=50,
            initialvalue=3,
            parent=self,
        )
        if not n:
            return
        import os

        env = dict(os.environ)
        env["GIT_SEQUENCE_EDITOR"] = env.get(
            "VISUAL") or env.get("EDITOR") or "nano"
        cmd = ["git", "-C", str(self._repo), "rebase", "-i", f"HEAD~{n}"]
        try:
            subprocess.Popen(cmd, env=env)
            self._set_status(
                _t("git.rebase_launched",
                   f"Rebase interativo lançado (HEAD~{n}).")
            )
        except Exception as exc:
            messagebox.showerror(
                _t("git.err_title", "Erro"), str(exc), parent=self)

    def _create_branch(self) -> None:
        """Cria nova branch Git com o nome digitado e faz checkout nela."""
        name = simpledialog.askstring(
            "New Branch", "Branch name:", parent=self)
        if not name or not name.strip():
            return
        name = name.strip()
        rc, _, err = self._git("checkout", "-b", name)
        if rc != 0:
            messagebox.showerror(_t("hc.git_panel.error"), err, parent=self)
        self.refresh()

    def _on_double(self, _e: Any = None) -> None:
        """Trata o evento 'double'.

        Args:
            _e: Parâmetro da operação.
        """
        sel = self._tree.selection()
        if not sel:
            return
        meta = self._item_meta.get(sel[0])
        if not meta:
            return
        fname, section = meta
        self._show_diff(fname, section == "staged")

    def _on_ctx(self, e: tk.Event) -> None:
        """Trata o evento 'ctx'.

        Args:
            e: Evento Tkinter.
        """
        item = self._tree.identify_row(e.y)
        if not item:
            return
        self._tree.selection_set(item)
        meta = self._item_meta.get(item)
        if not meta:
            return
        fname, section = meta
        staged = section == "staged"

        t = self._tmgr.theme
        menu = tk.Menu(
            self,
            tearoff=False,
            bg=t.floating_bg,
            fg=t.fg,
            activebackground=t.sel_bg,
            activeforeground=t.sel_fg,
        )
        if staged:
            menu.add_command(
                label=_t("hc.git_panel.unstage"), command=lambda: self._unstage(fname)
            )
        else:
            menu.add_command(
                label=_t("hc.git_panel.stage"), command=lambda: self._stage(fname)
            )
            if section != "untracked":
                menu.add_command(
                    label=_t("hc.git_panel.discard_changes"),
                    command=lambda: self._discard(fname),
                )
        menu.add_separator()
        menu.add_command(
            label=_t("hc.git_panel.view_diff"),
            command=lambda: self._show_diff(fname, staged),
        )
        try:
            menu.tk_popup(e.x_root, e.y_root)
        finally:
            menu.grab_release()

    def _show_diff(self, fname: str, staged: bool) -> None:
        """
        Mostra diff correto.
        - staged=True -> HEAD vs índice (staged)
        - staged=False -> índice/HEAD vs working tree

        Garante que orig/mod nunca são None (evita AttributeError em DiffEngine).
        """
        if not self._git_root or not self._on_show_diff:
            return
        path = self._git_root / fname
        try:
            mod: str = (
                path.read_text(encoding="utf-8", errors="replace")
                if path.exists()
                else ""
            )
        except OSError:
            mod = ""

        if staged:
            rc, orig, _ = self._git("show", f"HEAD:{fname}")
            orig = orig if rc == 0 else ""
            rc2, idx_content, _ = self._git("show", f":{fname}")
            mod_content = idx_content if rc2 == 0 else mod
            self._on_show_diff(
                orig or "",
                mod_content or "",
                f"{fname} (HEAD)",
                f"{fname} (staged index)",
            )
        else:
            rc, orig, _ = self._git("show", f":{fname}")
            if rc != 0:
                rc, orig, _ = self._git("show", f"HEAD:{fname}")
            orig = orig if rc == 0 else ""
            self._on_show_diff(
                orig or "",
                mod or "",
                f"{fname} (index/HEAD)",
                f"{fname} (working)",
            )

    def _msg_focus_in(self, _e: Any) -> None:
        """Limpa o placeholder do campo de mensagem de commit ao receber foco."""
        if self._msg_text.get("1.0", "end-1c") == self._placeholder:
            self._msg_text.delete("1.0", "end")
            self._msg_text.config(fg=self._tmgr.theme.fg)

    def _msg_focus_out(self, _e: Any) -> None:
        """Restaura o placeholder se o campo de commit estiver vazio ao perder foco."""
        if not self._msg_text.get("1.0", "end-1c").strip():
            self._msg_text.delete("1.0", "end")
            self._msg_text.insert("1.0", self._placeholder)
            self._msg_text.config(fg=self._tmgr.theme.fg_muted)

    def _msg_on_paste(self, _e: Any) -> None:
        """Limpa o placeholder do campo de commit antes de colar o conteúdo."""
        if self._msg_text.get("1.0", "end-1c") == self._placeholder:
            self._msg_text.delete("1.0", "end")
            self._msg_text.config(fg=self._tmgr.theme.fg)

    def apply_theme(self, t: Theme) -> None:
        """Aplica o tema de cores ao painel Git (fundo, Treeview, tags de status).

        Args:
            t: Objeto Theme com as cores do tema ativo.
        """
        self.config(bg=t.panel_bg)
        style = ttk.Style()
        style.configure(
            "Git.Treeview",
            background=t.panel_bg,
            foreground=t.fg,
            fieldbackground=t.panel_bg,
            font=(FONT_UI, 9),
        )
        style.map(
            "Git.Treeview",
            background=[("selected", t.sel_bg)],
            foreground=[("selected", t.sel_fg)],
        )
        self._tree.tag_configure("section", foreground=t.fg_muted)
        self._tree.tag_configure("staged", foreground=t.ok_fg)
        self._tree.tag_configure("modified", foreground=t.warn_fg)
        self._tree.tag_configure("untracked", foreground=t.fg_muted)
        self._tree.tag_configure("deleted", foreground=t.fg_muted)
