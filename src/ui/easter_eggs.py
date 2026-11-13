"""Codis - Easter Eggs 🥚

Difficulty tiers:
  ★☆☆☆☆  Trivial  - just type something obvious
  ★★☆☆☆  Easy     - slightly hidden
  ★★★☆☆  Medium   - requires knowing the codebase
  ★★★★☆  Hard     - obscure key sequences or timing
  ★★★★★  Legendary- combine multiple eggs or reverse-engineer
"""

from __future__ import annotations

import contextlib
import logging
import random
import threading
import time
import tkinter as tk
from collections.abc import Callable
from typing import Any

from src.i18n import _t

logger = logging.getLogger("Codis.Eggs")

_TERMINAL_PREFIX = "/codis "


class SequenceWatcher:
    """Despacha easter eggs via comando de terminal (/codis <código>).

    NÃO intercepta mais digitação no editor - zero risco de interferir
    com código sendo escrito pelo desenvolvedor.
    """

    def __init__(self) -> None:
        """Inicializa o despachante de comandos."""
        self._triggers: dict[str, Callable] = {}
        self._arg_triggers: dict[str, Callable[[str], None]] = {}

    def register(self, sequence: str, cb: Callable) -> None:
        """Registra um código e seu callback correspondente.

        Args:
            sequence: Código secreto (sem o prefixo /codis).
            cb: Função de callback invocada ao reconhecer o código.
        """
        self._triggers[sequence.lower()] = cb

    def register_args(self, sequence: str, cb: Callable[[str], None]) -> None:
        """Registra um comando que aceita argumentos (``/codis app notepad.exe``)."""
        self._arg_triggers[sequence.lower()] = cb

    def dispatch(self, raw_cmd: str) -> bool:
        """Processa um comando de terminal; retorna True se foi um easter egg.

        Deve ser chamado apenas pelo TerminalPanel quando o usuário pressiona
        Enter. Comandos que não começam com o prefixo são ignorados.

        Args:
            raw_cmd: Texto completo digitado no terminal antes do Enter.
        """
        cmd = raw_cmd.strip()
        if not cmd.lower().startswith(_TERMINAL_PREFIX):
            return False
        code = cmd[len(_TERMINAL_PREFIX):].strip().lower()
        cb = self._triggers.get(code)
        if cb is None:
            head, _, rest = code.partition(" ")
            arg_cb = self._arg_triggers.get(head)
            if arg_cb is None:
                return False
            raw_rest = cmd[len(_TERMINAL_PREFIX):].strip().partition(" ")[2].strip()
            try:
                arg_cb(raw_rest)
            except Exception as e:
                logger.debug("Easter egg error: %s", e)
            return True
        try:
            cb()
        except Exception as e:
            logger.debug("Easter egg error: %s", e)
        return True

    @staticmethod
    def feed(char: str) -> None:
        """Descontinuado: easter eggs não são mais ativados por digitação."""


def _matrix_rain(root: tk.Tk, _tmgr) -> None:
    """★★☆☆☆  Chuva Matrix. Digite 'matrix' para ativar."""
    overlay = tk.Toplevel(root)
    overlay.wm_overrideredirect(True)
    overlay.wm_attributes("-topmost", True)
    overlay.wm_attributes("-alpha", 0.88)
    overlay.geometry(
        f"{root.winfo_width()}x{root.winfo_height()}+{root.winfo_rootx()}+{root.winfo_rooty()}"
    )
    overlay.configure(bg="#000000")

    canvas = tk.Canvas(overlay, bg="#000000", highlightthickness=0)
    canvas.pack(fill="both", expand=True)

    cols = root.winfo_width() // 14
    drops = [random.randint(-40, 0) for _ in range(cols)]
    chars = list(
        "ｦｧｨｩｪｫｬｭｮｯｰｱｲｳｴｵｶｷｸｹｺｻｼｽｾｿﾀﾁﾂﾃﾄﾅﾆﾇﾈﾉﾊﾋﾌﾍﾎﾏﾐﾑﾒﾓﾔﾕﾖﾗﾘﾙﾚﾛﾜﾝ0123456789")
    stop = [False]

    def draw():
        if stop[0]:
            return
        canvas.delete("all")
        for i, drop in enumerate(drops):
            if drop > 0:
                ch = random.choice(chars)
                x = i * 14 + 7
                y = drop * 16
                canvas.create_text(
                    x, y, text=ch, fill="#00ff41", font=("Courier", 11, "bold")
                )
            drops[i] += 1
            if drops[i] * 16 > root.winfo_height() and random.random() > 0.975:
                drops[i] = 0
        overlay.after(45, draw)

    overlay.bind(
        "<Button-1>", lambda _: (stop.__setitem__(0, True), overlay.destroy()))
    overlay.bind("<Escape>", lambda _: (
        stop.__setitem__(0, True), overlay.destroy()))

    overlay.after(
        12000,
        lambda: (
            stop.__setitem__(0, True),
            overlay.destroy() if overlay.winfo_exists() else None,
        ),
    )
    draw()


def _konami_code(root: tk.Tk, _tmgr) -> None:
    """★★★☆☆  Código Konami: ↑↑↓↓ <- -> <- -> BA - mostra tela de +30 vidas."""
    dlg = tk.Toplevel(root)
    dlg.title("🎮  CHEAT ACTIVATED")
    dlg.configure(bg="#000000")
    dlg.geometry("440x260")
    dlg.transient(root)

    frames = [
        ("★ ★ ★  +30 LIVES  ★ ★ ★", "#ffff00", 40),
        ("KONAMI CODE ACCEPTED", "#00ff00", 18),
        ("↑ ↑ ↓ ↓ ← → ← → B A", "#ffffff", 14),
        ("You found the secret!", "#aaaaaa", 12),
    ]
    for text, color, size in frames:
        tk.Label(
            dlg, text=text, bg="#000000", fg=color, font=("Courier New", size, "bold")
        ).pack(pady=4)

    tk.Button(
        dlg,
        text=_t("hc.easter_eggs.insert_coin"),
        bg="#ffff00",
        fg="#000000",
        font=("Courier New", 12, "bold"),
        command=dlg.destroy,
    ).pack(pady=12)
    dlg.after(6000, lambda: dlg.destroy() if dlg.winfo_exists() else None)


def _uwu_mode(root: tk.Tk, tmgr, editor_getter) -> None:
    """★☆☆☆☆  Type 'uwu' - transforms code text to uwu-speak briefly."""
    editor = editor_getter()
    if not editor:
        return

    UWU = {
        "r": "w",
        "l": "w",
        "R": "W",
        "L": "W",
        "th": "d",
        "Th": "D",
        "TH": "D",
    }
    original = editor.text.get("1.0", "end-1c")
    uwu_text = original
    for k, v in UWU.items():
        uwu_text = uwu_text.replace(k, v)

    editor.text.config(state="normal")
    editor.text.delete("1.0", "end")
    editor.text.insert("1.0", uwu_text)

    def restore():
        time.sleep(5)
        with contextlib.suppress(tk.TclError):
            editor.text.after(
                0,
                lambda: (
                    editor.text.delete("1.0", "end"),
                    editor.text.insert("1.0", original),
                ),
            )

    threading.Thread(target=restore, daemon=True).start()

    t = tmgr.theme
    dlg = tk.Toplevel(root)
    dlg.wm_overrideredirect(True)
    dlg.wm_attributes("-topmost", True)
    dlg.configure(bg=t.floating_bg, pady=8, padx=12)
    dlg.geometry(f"+{root.winfo_rootx() + 40}+{root.winfo_rooty() + 40}")
    tk.Label(
        dlg,
        text=_t("hc.easter_eggs.owo_uwu_mode_activated_5s"),
        bg=t.floating_bg,
        fg="#ff79c6",
        font=("Segoe UI", 11, "bold"),
    ).pack()
    dlg.after(5000, lambda: dlg.destroy() if dlg.winfo_exists() else None)


def _birthday_mode(root: tk.Tk, tmgr) -> None:
    """★★☆☆☆  Abra no seu aniversário - chuva de confetes."""

    t = tmgr.theme
    overlay = tk.Toplevel(root)
    overlay.wm_overrideredirect(True)
    overlay.wm_attributes("-topmost", True)
    overlay.wm_attributes("-alpha", 0.85)
    overlay.geometry(
        f"{root.winfo_width()}x{root.winfo_height()}+{root.winfo_rootx()}+{root.winfo_rooty()}"
    )
    overlay.configure(bg=t.editor_bg)

    canvas = tk.Canvas(overlay, bg=t.editor_bg, highlightthickness=0)
    canvas.pack(fill="both", expand=True)

    tk.Label(
        overlay,
        text=_t("hc.easter_eggs.happy_birthday_coder"),
        bg=t.editor_bg,
        fg=t.t_func,
        font=("Segoe UI", 22, "bold"),
    ).place(relx=0.5, rely=0.45, anchor="center")
    tk.Label(
        overlay,
        text=_t("hc.easter_eggs.may_your_code_be_bug_free_and_"),
        bg=t.editor_bg,
        fg=t.fg_muted,
        font=("Segoe UI", 11),
    ).place(relx=0.5, rely=0.55, anchor="center")

    COLORS = ["#ff6b6b", "#ffd93d", "#6bcb77", "#4d96ff", "#ff6bff", "#ff9f1c"]
    particles = [
        {
            "x": random.uniform(0, 1),
            "y": random.uniform(-0.2, 0),
            "vx": random.uniform(-0.003, 0.003),
            "vy": random.uniform(0.004, 0.012),
            "color": random.choice(COLORS),
            "size": random.randint(4, 10),
            "shape": random.choice(["rect", "oval"]),
        }
        for _ in range(80)
    ]
    stop = [False]

    def animate():
        if stop[0]:
            return
        canvas.delete("confetti")
        h = overlay.winfo_height()
        w = overlay.winfo_width()
        for p in particles:
            p["x"] += p["vx"]
            p["y"] += p["vy"]
            if p["y"] > 1.1:
                p["y"] = -0.05
                p["x"] = random.uniform(0, 1)
            x1, y1 = int(p["x"] * w), int(p["y"] * h)
            x2, y2 = x1 + p["size"], y1 + p["size"]
            if p["shape"] == "rect":
                canvas.create_rectangle(
                    x1, y1, x2, y2, fill=p["color"], outline="", tags="confetti"
                )
            else:
                canvas.create_oval(
                    x1, y1, x2, y2, fill=p["color"], outline="", tags="confetti"
                )
        overlay.after(30, animate)

    overlay.bind(
        "<Button-1>", lambda _: (stop.__setitem__(0, True), overlay.destroy()))
    overlay.after(
        8000,
        lambda: (
            stop.__setitem__(0, True),
            overlay.destroy() if overlay.winfo_exists() else None,
        ),
    )
    animate()


def _deep_thought(root: tk.Tk, _tmgr) -> None:
    """★★★★☆  Digite a resposta para a vida, o universo e tudo mais: '42'
    - revela o Deep Thought."""
    dlg = tk.Toplevel(root)
    dlg.title("Deep Thought")
    dlg.configure(bg="#000033")
    dlg.geometry("500x300")
    dlg.transient(root)

    lines = [
        ("", 0),
        ("The Answer to the Ultimate Question of", 800),
        ("Life, the Universe, and Everything is...", 1600),
        ("", 2200),
        ("       42", 2800),
        ("", 3400),
        ("- Deep Thought, HGTTG", 4000),
    ]

    frame = tk.Frame(dlg, bg="#000033")
    frame.pack(expand=True)

    for text, delay in lines:
        label = tk.Label(
            frame,
            text=text,
            bg="#000033",
            fg="#00ffaa" if "42" in text else "#aaffcc",
            font=(
                "Courier New",
                18 if "42" in text else 12,
                "bold" if "42" in text else "normal",
            ),
        )
        label.pack(pady=2)
        if delay:
            label.config(fg="#000033")
            dlg.after(
                delay,
                lambda lbl=label, txt=text: lbl.config(
                    fg="#00ffaa" if "42" in txt else "#aaffcc"
                ),
            )

    dlg.after(8000, lambda: dlg.destroy() if dlg.winfo_exists() else None)


def _sudo_mode(root: tk.Tk, tmgr) -> None:
    """★★★☆☆  Digite 'sudo' - mostra escalada de privilégio falsa."""

    dlg = tk.Toplevel(root)
    dlg.title("sudo")
    dlg.configure(bg="#000000")
    dlg.geometry("500x240")
    dlg.transient(root)
    dlg.wm_attributes("-topmost", True)

    output = tk.Text(
        dlg,
        bg="#000000",
        fg="#00ff00",
        font=("Courier New", 10),
        relief="flat",
        state="normal",
        insertbackground="#00ff00",
    )
    output.pack(fill="both", expand=True, padx=8, pady=8)

    lines = [
        ("[sudo] password for user: ", 0.4),
        ("*****\n", 1.2),
        ("Verifying credentials...\n", 1.6),
        ("✓ Root access granted\n", 2.4),
        ("Loading god-mode settings...\n", 2.9),
        ("Activating unlimited coffee mode ☕\n", 3.5),
        ("Making all bugs features™\n", 4.2),
        ("DONE. You may now code omnipotently.\n", 5.0),
    ]

    def type_line(idx=0):
        if idx >= len(lines) or not dlg.winfo_exists():
            return
        text, delay = lines[idx]
        output.insert("end", text)
        output.see("end")
        dlg.after(int(delay * 1000), lambda: type_line(idx + 1))

    type_line()
    dlg.after(8000, lambda: dlg.destroy() if dlg.winfo_exists() else None)


def _dev_console(root: tk.Tk, tmgr) -> None:
    """★★★★★  Type 'xyzzy' (classic Colossal Cave) - opens secret dev console."""
    t = tmgr.theme
    dlg = tk.Toplevel(root)
    dlg.title("✦  Secret Dev Console  ✦")
    dlg.configure(bg=t.floating_bg)
    dlg.geometry("560x380")
    dlg.transient(root)

    tk.Label(
        dlg,
        text=_t("hc.easter_eggs.xyzzy_secret_dev_console"),
        bg=t.floating_bg,
        fg=t.t_func,
        font=("Courier New", 12, "bold"),
    ).pack(pady=(12, 4))
    tk.Label(
        dlg,
        text="A hollow voice says: 'PLUGH'\nYou are in a maze of twisty passages, all alike.",
        bg=t.floating_bg,
        fg=t.fg_muted,
        font=("Courier New", 9),
        justify="center",
    ).pack()

    frame = tk.Frame(dlg, bg=t.editor_bg)
    frame.pack(fill="both", expand=True, padx=12, pady=8)
    out = tk.Text(
        frame,
        bg=t.editor_bg,
        fg=t.t_string,
        font=("Courier New", 9),
        relief="flat",
        state="disabled",
    )
    out.pack(fill="both", expand=True)

    entry_var = tk.StringVar()
    entry = tk.Entry(
        dlg,
        textvariable=entry_var,
        bg=t.input_bg,
        fg=t.fg,
        insertbackground=t.cursor,
        font=("Courier New", 10),
        relief="flat",
    )
    entry.pack(fill="x", padx=12, pady=(0, 8))
    entry.focus_set()

    RESPONSES = {
        "help": "Commands: look, inventory, go north/south/east/west, take <item>, codis, quit",
        "look": "You see a dimly lit terminal. A pile of uncommented code lies in the corner.\n"
        "Exits: north (Stack Overflow), south (GitHub), east (Documentation), west (Void).",
        "inventory": "You are carrying: 1x Cold Coffee, 3x Rubber Ducks, 1x Stack Trace",
        "go north": "You head north towards Stack Overflow.\n'Duplicate question! -5 rep' a voice cries.",
        "go south": "You venture to GitHub. A wild PR request appears!",
        "go east": "Documentation? It reads: 'TODO: write docs'. Typical.",
        "go west": "You stare into the Void. The Void stares back with a SegFault.",
        "codis": "✨ You are already inside Codis! Inception-level awareness +100.",
        "xyzzy": "A hollow voice says nothing. Try PLUGH.",
        "plugh": "A hollow voice says: 'PLUGH'. You have been transported... nowhere.",
        "take rubber duck": "You pick up a rubber duck and explain your bug to it.\nBug solved! (somehow)",
        "take coffee": "It's cold. You drink it anyway. Programmer power +5.",
        "quit": "You cannot leave. The code must be written.",
    }

    def run_cmd(_=None):
        cmd = entry_var.get().strip().lower()
        entry_var.set("")
        response = RESPONSES.get(
            cmd, f"I don't understand '{cmd}'. Type 'help'.")
        out.config(state="normal")
        out.insert("end", f"\n> {cmd}\n{response}\n")
        out.config(state="disabled")
        out.see("end")

    entry.bind("<Return>", run_cmd)
    out.config(state="normal")
    out.insert(
        "1.0",
        "═══════════════════════════════════════\n"
        " COLOSSAL CODIS  (c) 1977–2025 Codis  \n"
        "═══════════════════════════════════════\n"
        "You are standing at the end of a road before\n"
        "a small brick building. Around you is a forest.\n"
        "A small stream flows out of the building and down a gully.\n\n"
        "Type 'help' for commands.\n",
    )
    out.config(state="disabled")


def _nyan_cat(root: tk.Tk, _tmgr) -> None:
    """★★★★☆  Digite 'nyan' - animação ASCII Nyan Cat."""
    overlay = tk.Toplevel(root)
    overlay.wm_overrideredirect(True)
    overlay.wm_attributes("-topmost", True)
    overlay.wm_attributes("-alpha", 0.92)
    h = 120
    overlay.geometry(
        f"{root.winfo_width()}x{h}"
        f"+{root.winfo_rootx()}+{root.winfo_rooty() + root.winfo_height() - h}"
    )
    overlay.configure(bg="#000000")

    NYAN_FRAMES = [
        "+      ,------,      \n"
        "|\\_,-~/        |\n"
        "/ _  _ |,-,  |  |\n"
        "(6--\\ Y     )-/  |\n"
        "|    }  |/|  /  |\n"
        "|   / _/|  | /  |\n"
        "   |/-'  |/ \\  |\n"
        "   `--'  |  |  `--.",
    ]
    RAINBOW = ["#ff0000", "#ff7700", "#ffff00",
               "#00ff00", "#0000ff", "#8800ff"]
    x_pos = [0]
    stop = [False]
    canvas = tk.Canvas(overlay, bg="#000000", highlightthickness=0)
    canvas.pack(fill="both", expand=True)

    def animate(frame_idx=0):
        if stop[0] or not overlay.winfo_exists():
            return
        canvas.delete("all")

        for i in range(x_pos[0], 0, -8):
            color = RAINBOW[(i // 8) % 6]
            canvas.create_rectangle(i - 8, 20, i, 80, fill=color, outline="")

        canvas.create_text(
            x_pos[0] + 10,
            h // 2,
            text=NYAN_FRAMES[0],
            fill="#ffffff",
            font=("Courier", 7),
            anchor="w",
        )
        x_pos[0] = (x_pos[0] + 6) % (root.winfo_width() + 200)
        overlay.after(40, lambda: animate((frame_idx + 1) % len(NYAN_FRAMES)))

    overlay.bind(
        "<Button-1>", lambda _: (stop.__setitem__(0, True), overlay.destroy()))
    overlay.after(
        7000,
        lambda: (
            stop.__setitem__(0, True),
            overlay.destroy() if overlay.winfo_exists() else None,
        ),
    )
    animate()


class EasterEggManager:
    """
    Central manager. Attach to app, registers all eggs.

    Eggs:
      'matrix' -> ★★☆☆☆  Matrix rain
      'uwu' -> ★☆☆☆☆  UWU mode
      'happybirthday' -> ★★☆☆☆  Confetti
      '42' -> ★★★★☆  Deep Thought
      'sudo' -> ★★★☆☆  Fake sudo
      'xyzzy' -> ★★★★★  Text adventure
      'nyan' -> ★★★★☆  Nyan Cat

    Konami code watcher on root window:
      ↑↑↓↓ <- -> <- -> BA -> ★★★☆☆  +30 lives
    """

    KONAMI = ["Up", "Up", "Down", "Down", "Left",
              "Right", "Left", "Right", "b", "a"]

    def __init__(
        self,
        root: tk.Tk,
        tmgr,
        editor_getter,
        status_notify: Callable[[str], None],
        editor_container_getter: Callable | None = None,
        cfg: Any | None = None,
    ) -> None:
        """Inicializa o gerenciador de easter eggs.

        Easter eggs são ativados EXCLUSIVAMENTE via terminal integrado usando
        o prefixo ``/codis <código>``. Nenhuma digitação no editor é monitorada.

        Args:
            root: Janela raiz Tkinter.
            tmgr: Gerenciador de temas.
            editor_getter: Callable que retorna o editor ativo.
            status_notify: Callable para exibir mensagens na status bar.
            editor_container_getter: Callable que retorna o tk.Frame do editor (para embed DOOM).
            cfg: Configuração do app (``get(chave, padrão)``), usada pelo DOOM
                (``doom_engine_path``, ``doom_iwad_path``, ``doom_extra_args``).
        """
        self._root = root
        self._tmgr = tmgr
        self._get_ed = editor_getter
        self._notify = status_notify
        self._get_container = editor_container_getter
        self._cfg = cfg
        self._doom_panel: Any | None = None
        self._embed_panel: Any | None = None
        self._watcher = SequenceWatcher()
        self._register_all()

    def handle_terminal_command(self, cmd: str) -> bool:
        """Processa um comando digitado no terminal integrado.

        Deve ser chamado pelo TerminalPanel antes de enviar o comando ao shell.
        Retorna True se o comando era um easter egg (e não deve ser enviado ao
        shell), False caso contrário.

        Args:
            cmd: Texto completo da linha de comando (ex: '/codis matrix').
        """
        return self._watcher.dispatch(cmd)

    def attach_terminal_konami(self, terminal_widget: tk.Widget) -> None:
        """Vincula o Konami code ao widget de input do terminal.

        Deve ser chamado pelo TerminalPanel após sua construção para que as
        setas do teclado sejam rastreadas apenas quando o terminal tem foco.

        Args:
            terminal_widget: Widget Entry do terminal (self._input no TerminalPanel).
        """
        konami_buf: list = []
        expected = [k.lower() for k in self.KONAMI]

        def _on_key(event: tk.Event) -> None:
            sym = event.keysym
            konami_buf.append(sym.lower() if len(sym) > 1 else sym)
            del konami_buf[: -len(self.KONAMI)]
            if konami_buf == expected:
                konami_buf.clear()
                self._fire(_konami_code)

        terminal_widget.bind("<Key>", _on_key, add=True)

    def _register_all(self) -> None:
        """Registra todos os easter eggs disponíveis no gerenciador de eventos."""
        self._watcher.register("matrix", lambda: self._fire(_matrix_rain))
        self._watcher.register(
            "uwu", lambda: self._fire(_uwu_mode, self._get_ed))
        self._watcher.register(
            "happybirthday", lambda: self._fire(_birthday_mode))
        self._watcher.register("42", lambda: self._fire(_deep_thought))
        self._watcher.register("sudo", lambda: self._fire(_sudo_mode))
        self._watcher.register("xyzzy", lambda: self._fire(_dev_console))
        self._watcher.register("nyan", lambda: self._fire(_nyan_cat))

        self._watcher.register("1123581321", self._fibonacci_egg)

        self._watcher.register("20250101", self._codis_birthday)

        self._watcher.register("opensesame", self._open_sesame)

        self._watcher.register("doom", self._doom_egg)
        self._watcher.register("app", lambda: self._app_embed_egg(""))
        self._watcher.register_args("app", self._app_embed_egg)

    def _fire(self, fn, *extra_args) -> None:
        args = [self._root, self._tmgr] + list(extra_args)
        try:
            fn(*args)
        except Exception as e:
            logger.debug("Egg fire error: %s", e)

    def _fibonacci_egg(self) -> None:
        """★★★★☆  Type '1123581321' -> Fibonacci spiral animation."""
        t = self._tmgr.theme
        dlg = tk.Toplevel(self._root)
        dlg.title("🌀 Fibonacci")
        dlg.configure(bg=t.editor_bg)
        dlg.geometry("420x420")
        canvas = tk.Canvas(
            dlg, bg=t.editor_bg, highlightthickness=0, width=400, height=400
        )
        canvas.pack(padx=10, pady=10)

        fib = [1, 1]
        for _ in range(12):
            fib.append(fib[-1] + fib[-2])

        colors = [
            "#cba6f7",
            "#f38ba8",
            "#fab387",
            "#f9e2af",
            "#a6e3a1",
            "#89dceb",
            "#89b4fa",
            "#b4befe",
        ]
        angle = [0]
        step = [0]
        stop = [False]
        cx, cy = 200, 200

        def draw():
            if stop[0] or not dlg.winfo_exists() or step[0] >= len(fib):
                return
            r = fib[step[0]] * 6
            color = colors[step[0] % len(colors)]
            canvas.create_arc(
                cx - r,
                cy - r,
                cx + r,
                cy + r,
                start=angle[0],
                extent=90,
                style="arc",
                outline=color,
                width=2,
            )
            angle[0] = (angle[0] + 90) % 360
            step[0] += 1
            dlg.after(120, draw)

        draw()
        dlg.bind("<Escape>", lambda _: (
            stop.__setitem__(0, True), dlg.destroy()))
        self._notify("🌀 Fibonacci sequence discovered!")

    def _codis_birthday(self) -> None:
        """★★★★★  Type '20250101' (Codis launch date) -> special message."""
        t = self._tmgr.theme
        dlg = tk.Toplevel(self._root)
        dlg.title("🎂 Codis Birthday")
        dlg.configure(bg=t.floating_bg)
        dlg.geometry("420x220")

        for text, color, size in (
            ("Happy Birthday, Codis! 🎂", t.t_func, 16),
            ("Born: January 1st, 2025", t.fg, 11),
            ("You found the origin date easter egg!", t.t_string, 10),
            ("★★★★★  Legendary  ★★★★★", t.warn_fg, 10),
        ):
            tk.Label(
                dlg, text=text, bg=t.floating_bg, fg=color, font=(
                    "Segoe UI", size)
            ).pack(pady=4)

        tk.Button(
            dlg,
            text=_t("hc.easter_eggs.celebrate"),
            bg=t.btn_primary,
            fg="#000",
            font=("Segoe UI", 10, "bold"),
            command=dlg.destroy,
        ).pack(pady=8)
        self._notify("★★★★★ Legendary egg found: Codis Birthday!")

    def _doom_manifesto_dialog(self) -> None:
        """Diálogo exibido quando nenhum motor de DOOM foi encontrado."""
        dlg = tk.Toplevel(self._root)
        dlg.title("DOOM NOT FOUND - The Manifesto")
        dlg.configure(bg="#1a0000")
        dlg.wm_attributes("-topmost", True)
        dlg.geometry("560x480")
        tk.Label(
            dlg,
            text="THINGS THAT RUN DOOM",
            bg="#1a0000",
            fg="#ff2200",
            font=("Courier New", 16, "bold"),
        ).pack(pady=(14, 2))
        tk.Label(
            dlg,
            text="(mas o Codis ainda nao tinha o executavel instalado)",
            bg="#1a0000",
            fg="#883300",
            font=("Courier New", 8),
        ).pack(pady=(0, 8))
        LISTA = [
            "  ATM bancario (Hyosung)",
            "  Impressora HP LaserJet",
            "  Camera de seguranca Hikvision",
            "  Relogio inteligente Apple Watch",
            "  Teclado mecanico (Wooting)",
            "  Osciloscopio Rigol DS1054Z",
            "  Calculadora TI-84",
            "  Piano digital MIDI",
            "  Mamografia (equipamento medico)",
            "  Escova de dentes Oral-B",
            "  Tela de aviao Boeing",
            "  Caixao inteligente (nao pergunte)",
            "  Geladeira Samsung SmartThings",
            "  Codis IDE  <- PROXIMO DA LISTA",
        ]
        lf = tk.Frame(dlg, bg="#000000")
        lf.pack(fill="both", expand=True, padx=14, pady=4)
        for item in LISTA:
            c = "#ff4400" if "Codis" in item else "#cc3300"
            w = "bold" if "Codis" in item else "normal"
            tk.Label(
                lf,
                text=item,
                bg="#000000",
                fg=c,
                font=("Courier New", 9, w),
                anchor="w",
            ).pack(fill="x", padx=8, pady=1)
        tk.Label(
            dlg,
            text=_t("doom.err_no_engine"),
            wraplength=520,
            bg="#1a0000",
            fg="#664400",
            font=("Courier New", 8),
        ).pack(pady=4)
        tk.Button(
            dlg,
            text="I'll install DOOM right now",
            bg="#550000",
            fg="#ff4400",
            font=("Courier New", 10, "bold"),
            relief="flat",
            cursor="hand2",
            command=dlg.destroy,
        ).pack(pady=8)
        dlg.after(25_000, lambda: dlg.destroy() if dlg.winfo_exists() else None)

    def _doom_egg(self) -> None:
        """Legendary /codis doom - embute uma janela nativa do DOOM no editor.

        A janela do motor (gzdoom, chocolate-doom...) é reparentada de verdade ao
        frame do editor (``SetParent`` no Windows, ``XReparentWindow`` no X11);
        veja :mod:`src.ui.doom_embed` e :mod:`src.ui.window_embed`.
        """
        from src.ui import doom_embed

        panel = self._doom_panel
        if panel is not None:
            try:
                if panel.winfo_exists():
                    panel.focus_game()
                    return
            except tk.TclError:
                pass
            self._doom_panel = None

        try:
            plan = doom_embed.resolve_plan(self._cfg)
        except doom_embed.DoomLaunchError as exc:
            if exc.kind == "engine":
                self._doom_manifesto_dialog()
            self._notify(str(exc))
            return

        container = self._get_container() if self._get_container else None
        if container is not None:
            try:
                self._doom_panel = doom_embed.start_embedded(
                    container,
                    plan,
                    on_status=self._notify,
                    on_close=self._on_doom_closed,
                )
                return
            except doom_embed.DoomLaunchError as exc:
                logger.info("[DOOM] sem embutir (%s) - usando janela externa", exc)
                self._notify(str(exc))

        try:
            doom_embed.launch_external(plan)
        except doom_embed.DoomLaunchError as exc:
            self._notify(str(exc))

    def _app_embed_egg(self, args: str) -> None:
        """``/codis app`` - incorpora qualquer programa (ver :mod:`src.ui.app_embed`).

        Sem argumentos abre o seletor (janelas em execução / iniciar .exe).
        ``/codis app notepad.exe`` adota a janela do processo; ``/codis app
        C:\\caminho\\prog.exe [args]`` inicia e incorpora.
        """
        from src.ui import app_embed
        from src.ui import window_embed as we

        panel = self._embed_panel
        if panel is not None:
            try:
                if panel.winfo_exists():
                    self._notify(_t("embed.already", "Já há um programa incorporado; devolva-o antes."))
                    panel.focus_game()
                    return
            except tk.TclError:
                pass
            self._embed_panel = None

        container = self._get_container() if self._get_container else None
        backend = we.get_backend()

        def _closed() -> None:
            self._embed_panel = None

        def _adopt(name: str, win: Any | None) -> None:
            if container is None or backend is None:
                self._notify(_t("embed.status_external", "Incorporar janelas não é possível neste ambiente."))
                return
            try:
                self._embed_panel = app_embed.start_adopt(
                    container, exe_name=name, window=win, backend=backend,
                    on_status=self._notify, on_close=_closed)
            except we.EmbedError as exc:
                self._notify(str(exc))

        def _launch(spec: Any) -> None:
            if container is not None and backend is not None:
                try:
                    self._embed_panel = app_embed.start_launch(
                        container, spec, backend=backend, on_status=self._notify, on_close=_closed)
                    return
                except we.EmbedError as exc:
                    self._notify(str(exc))
            try:
                app_embed.launch_external(spec)
            except we.EmbedError as exc:
                self._notify(str(exc))

        args = args.strip()
        if args:
            from pathlib import Path

            candidate = Path(args.strip('"')).expanduser()
            if candidate.is_file():
                _launch(app_embed.AppSpec(candidate))
            else:
                _adopt(args.strip('"'), None)
            return
        app_embed.AppEmbedDialog(
            self._root, self._tmgr, backend, _adopt, _launch, self._cfg)

    def _on_doom_closed(self) -> None:
        """Callback do painel: libera a referência ao encerrar o jogo."""
        self._doom_panel = None

    def _open_sesame(self) -> None:
        """★★★★★  Type 'opensesame' -> unlocks a joke hidden settings panel."""
        t = self._tmgr.theme
        dlg = tk.Toplevel(self._root)
        dlg.title("🗝  Open Sesame - Secret Settings")
        dlg.configure(bg=t.floating_bg)
        dlg.geometry("400x320")

        tk.Label(
            dlg,
            text=_t("hc.easter_eggs.secret_settings_unlocked"),
            bg=t.floating_bg,
            fg=t.t_func,
            font=("Courier New", 13, "bold"),
        ).pack(pady=(14, 6))

        settings = [
            ("☕ Infinite coffee mode", True),
            ("🦆 Rubber duck debug assistant", True),
            ("🚀 10x developer boost", True),
            ("😴 Auto-naptime after 2am", False),
            ("🎲 Randomly rename variables", False),
            ("🌈 Syntax highlighting in Comic Sans", False),
        ]

        for label, default in settings:
            var = tk.BooleanVar(value=default)
            tk.Checkbutton(
                dlg,
                text=label,
                variable=var,
                bg=t.floating_bg,
                fg=t.fg,
                selectcolor=t.input_bg,
                activebackground=t.floating_bg,
                font=("Segoe UI", 9),
            ).pack(anchor="w", padx=20)

        tk.Button(
            dlg,
            text=_t("hc.easter_eggs.apply_does_nothing_lol"),
            bg=t.btn_bg,
            fg=t.fg,
            font=("Segoe UI", 9),
            command=dlg.destroy,
        ).pack(pady=12)
        self._notify("🗝 Open Sesame! Secret settings revealed.")
