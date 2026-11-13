"""PainelIA (GUI real, provedor falso em thread): turnos, modos, checkpoints, cancelamento."""

from __future__ import annotations

import threading
import time
from types import SimpleNamespace

import pytest

from src.ia.approval_gate import ActionRisk
from src.ia.pair import tool_name
from src.ia.session import SessionStore


class Cfg(dict):
    def get(self, k, d=None):
        return dict.get(self, k, d)

    def set(self, k, v):
        self[k] = v

    def save(self):
        pass


class FakeProvider:
    name = "Fake"
    requires_key = False

    def __init__(self, script):
        self.script = script
        self.calls: list[tuple[list, dict]] = []
        self._cfg: dict = {}
        self.cancel_called = False

    def configure(self, cfg):
        self._cfg = cfg

    def cancel(self):
        self.cancel_called = True

    def send(self, messages, on_chunk, on_done, on_error, options=None):
        self.calls.append(([dict(m) for m in messages], dict(options or {})))
        n = len(self.calls)
        threading.Thread(target=self.script, args=(n, self, options or {}, on_chunk, on_done, on_error),
                         daemon=True).start()


def say(text):
    def script(_n, _p, _o, on_chunk, on_done, _on_error):
        for part in text.split(" "):
            on_chunk(part + " ")
        on_done(text)
    return script


def pump(root, cond, timeout=8.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        root.update()
        if cond():
            return True
        time.sleep(0.01)
    root.update()
    return cond()


class FakeText:
    def __init__(self, s):
        self.s = s

    def get(self, *_a):
        return self.s


@pytest.fixture(scope="module", autouse=True)
def _thread_safe(tk_root):
    from src.ui import thread_safe_tk

    thread_safe_tk.install(tk_root)


@pytest.fixture
def ws(tmp_path):
    w = tmp_path / "proj"
    w.mkdir()
    (w / "a.py").write_text("def alpha():\n    return 1\n")
    return w


@pytest.fixture
def make(tk_root, ws, tmp_path):
    from src.ia.iaspace import PainelIA
    from src.theme.base import _define_themes
    from src.theme.manager import ThemeManager

    _define_themes()
    made = []

    def factory(script=None, editors=()):
        cfg = Cfg(ia_privacy_warning_shown=True, ia_mode="edit")
        p = PainelIA(tk_root, ThemeManager("fluent_dark"), cfg, get_editor=lambda: None,
                     get_workspace=lambda: str(ws), get_open_editors=lambda: list(editors))
        p._store = SessionStore(tmp_path / "sessions")
        p._provider = FakeProvider(script or say("ok pronto"))
        p._auto_approve_levels = set(ActionRisk)
        p.pack(fill="both", expand=True)
        tk_root.update()
        made.append(p)
        return p

    yield factory
    for p in made:
        p.destroy()
    tk_root.update()


def ask(p, root, text):
    p._entry.delete("1.0", "end")
    p._entry.insert("1.0", text)
    p._send()
    assert pump(root, lambda: not p._generating), "turno não terminou"


def test_full_turn_streams_persists_and_keeps_provider_cfg_untouched(tk_root, make):
    p = make(say("Olá mundo cruel"))
    ask(p, tk_root, "oi")
    assert [t.role for t in p._session.turns] == ["user", "assistant"]
    assert p._session.turns[1].content == "Olá mundo cruel"
    msgs, opts = p._provider.calls[0]
    assert msgs[-1]["role"] == "user" and msgs[-1]["content"].startswith("oi")
    assert "Mode: EDIT" in opts["sistema"]
    assert p._provider._cfg == {}, "send() não pode alterar a config compartilhada"
    assert [i.id for i in p._store.list()] == [p._session.id]


def test_modes_change_tools_and_prompt(tk_root, make):
    p = make()
    p._mode.set("ask")
    ask(p, tk_root, "explica")
    names = {tool_name(t) for t in p._provider.calls[0][1]["_ferramentas"]}
    assert "read_file" in names and not names & {"write_file", "apply_edit", "run_command"}
    assert "Mode: ASK" in p._provider.calls[0][1]["sistema"]
    p._mode.set("agent")
    ask(p, tk_root, "faz")
    assert "run_command" in {tool_name(t) for t in p._provider.calls[1][1]["_ferramentas"]}
    assert p._cfg["ia_mode"] == "agent"


def test_tools_toggle_off_sends_no_tools(tk_root, make):
    p = make()
    p._tools_enabled.set(False)
    ask(p, tk_root, "oi")
    assert "_ferramentas" not in p._provider.calls[0][1]


def test_mentions_attach_file_once_then_only_a_note(tk_root, make):
    p = make()
    ask(p, tk_root, "explica @a.py")
    first = p._provider.calls[0][0][-1]["content"]
    assert "def alpha" in first and "   1  def alpha" in first
    ask(p, tk_root, "e agora?")
    second = p._provider.calls[1][0]
    assert "def alpha" not in "\n".join(m["content"] for m in second)
    assert "[anexado antes: a.py]" in second[0]["content"]


def test_error_drops_turn_and_returns_text_to_entry(tk_root, make):
    def boom(_n, _p, _o, _c, _d, on_error):
        on_error("sem rede")

    p = make(boom)
    ask(p, tk_root, "preciso disso")
    assert p._session.turns == []
    assert p._entry.get("1.0", "end-1c") == "preciso disso"


def test_cancel_keeps_partial_and_ignores_late_callbacks(tk_root, make):
    release = threading.Event()

    def slow(_n, _p, _o, on_chunk, on_done, _e):
        on_chunk("parcial ")
        release.wait(5)
        on_chunk("TARDE")
        on_done("parcial TARDE")

    p = make(slow)
    p._entry.insert("1.0", "vai")
    p._send()
    assert pump(tk_root, lambda: "parcial" in p._stream_buffer)
    p._cancel()
    release.set()
    for _ in range(30):
        tk_root.update()
        time.sleep(0.01)
    assert not p._generating
    last = p._session.turns[-1]
    assert last.role == "assistant" and "parcial" in last.content and "TARDE" not in last.content
    assert "cancelado" in last.content
    p._provider.script = say("ok pronto")
    ask(p, tk_root, "de novo")
    assert p._session.turns[-1].content == "ok pronto"


def test_edit_turn_creates_checkpoint_and_undo_restores(tk_root, make, ws, monkeypatch):
    from src.ia import iaspace

    def edit(_n, _p, opts, on_chunk, on_done, _e):
        out = opts["_on_tool_call"]("apply_edit", {"path": "a.py", "edits": [
            {"old_text": "return 1", "new_text": "return 2"}]})
        opts["_on_tool_call"]("write_file", {"path": "novo.py", "content": "x = 1\n"})
        on_chunk("feito")
        on_done("feito")

    p = make(edit)
    ask(p, tk_root, "muda")
    assert "return 2" in (ws / "a.py").read_text() and (ws / "novo.py").exists()
    turn = p._session.turns[-1]
    assert turn.checkpoint is not None
    assert any("apply_edit" in a for a in turn.actions)
    assert p._has_undo()
    monkeypatch.setattr(iaspace.messagebox, "askyesno", lambda *a, **k: False)
    p._undo_last_ai()
    assert "return 1" in (ws / "a.py").read_text() and not (ws / "novo.py").exists()
    assert not p._has_undo()


def test_ask_mode_has_no_checkpoint(tk_root, make):
    p = make()
    p._mode.set("ask")
    ask(p, tk_root, "só pergunta")
    assert p._session.turns[-1].checkpoint is None and not p._has_undo()


def test_regenerate_undoes_changes_and_asks_again(tk_root, make, ws):
    def edit(n, _p, opts, on_chunk, on_done, _e):
        opts["_on_tool_call"]("write_file", {"path": f"v{n}.py", "content": "x\n"})
        on_done(f"resp{n}")

    p = make(edit)
    ask(p, tk_root, "cria")
    assert (ws / "v1.py").exists()
    p._regenerate()
    assert pump(tk_root, lambda: not p._generating)
    assert not (ws / "v1.py").exists(), "a tentativa anterior foi desfeita"
    assert (ws / "v2.py").exists()
    assert [t.content for t in p._session.turns if t.role == "assistant"] == ["resp2"]
    assert [m["role"] for m in p._provider.calls[1][0]] == ["user"]


def test_user_edits_between_turns_are_reported(tk_root, make, ws):
    f = ws / "a.py"
    ed = SimpleNamespace(path=f, modified=True, lang="python", text=FakeText("linha1\nlinha2\n"))
    p = make(editors=[ed])
    ask(p, tk_root, "olha")
    ed.text.s = "linha1\nLINHA-MUDADA\n"
    ask(p, tk_root, "continua")
    last = p._provider.calls[1][0][-1]["content"]
    assert "Alterações que o usuário fez" in last and "+LINHA-MUDADA" in last


def test_new_session_and_load_previous(tk_root, make):
    p = make()
    ask(p, tk_root, "primeira conversa")
    first_id = p._session.id
    p._new_session()
    assert p._session.id != first_id and p._session.turns == []
    ask(p, tk_root, "segunda")
    p._load_session(first_id)
    assert p._session.turns[0].content == "primeira conversa"
    assert len(p._store.list()) == 2


def test_clear_deletes_conversation(tk_root, make):
    p = make()
    ask(p, tk_root, "apagar")
    sid = p._session.id
    p._clear_chat()
    assert p._store.load(sid) is None and p._session.turns == []


def test_long_history_is_condensed_not_unbounded(tk_root, make):
    p = make()
    p._cfg["ia_context_tokens"] = 4000
    for i in range(14):
        ask(p, tk_root, f"pergunta {i} " + "palavra " * 300)
    msgs = p._provider.calls[-1][0]
    total = sum(len(m["content"]) for m in msgs) / 3.5
    assert total < 4500
    assert "Conversa anterior condensada" in msgs[0]["content"]


def test_legacy_provider_without_options_still_gets_system_and_restores_after(tk_root, make):
    seen = {}

    class Legacy:
        name = "Legacy"

        def __init__(self):
            self._cfg = {"modelo": "m"}

        def configure(self, cfg):
            self._cfg = cfg

        def cancel(self):
            pass

        def send(self, messages, on_chunk, on_done, on_error):
            def run():
                time.sleep(0.05)
                seen["system"] = self._cfg.get("sistema")
                on_done("legado ok")
            threading.Thread(target=run, daemon=True).start()

    p = make()
    p._provider = Legacy()
    ask(p, tk_root, "oi")
    assert "Mode: EDIT" in seen["system"]
    assert p._provider._cfg == {"modelo": "m"}
