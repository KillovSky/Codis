"""Testes de src/ia/session.py: histórico, orçamento de tokens e persistência."""

from __future__ import annotations

from src.ia import session as S


def code(n: int, tag: str = "x") -> str:
    return "```python\n" + "\n".join(f"{tag}_{i} = {i}" for i in range(n)) + "\n```"


def roles_ok(msgs):
    assert msgs and msgs[0]["role"] == "user"
    assert all(a["role"] != b["role"] for a, b in zip(msgs, msgs[1:]))
    assert msgs[-1]["role"] == "user"


def test_attachment_only_lives_on_last_user_turn():
    s = S.ChatSession()
    s.add_user("explica", attachment="```py\nGRANDE ARQUIVO\n```", attached=["a.py"])
    s.add_assistant("ok")
    s.add_user("e agora?", attachment="```py\nOUTRO\n```", attached=["b.py"])
    msgs = s.messages_for_model().messages
    blob = "\n".join(m["content"] for m in msgs)
    assert "GRANDE ARQUIVO" not in blob
    assert "[anexado antes: a.py]" in blob
    assert "OUTRO" in msgs[-1]["content"]
    roles_ok(msgs)


def test_actions_are_remembered_in_assistant_turn():
    s = S.ChatSession()
    s.add_user("cria x")
    s.add_assistant("pronto", actions=["write_file a.py", "run_tests"])
    s.add_user("agora y")
    out = "\n".join(m["content"] for m in s.messages_for_model().messages)
    assert "write_file a.py" in out and "run_tests" in out


def test_old_code_blocks_shrink_recent_ones_do_not():
    s = S.ChatSession()
    for i in range(12):
        s.add_user(f"pergunta {i}")
        s.add_assistant(f"resp {i}\n{code(40, f'v{i}')}")
    s.add_user("final")
    msgs = s.messages_for_model(budget_tokens=10**6).messages
    text = [m["content"] for m in msgs]
    assert "linhas omitidas" in text[1]
    assert text[-2].count("\n") > 30
    roles_ok(msgs)


def test_budget_condenses_oldest_turns_and_keeps_current_message():
    s = S.ChatSession()
    for i in range(30):
        s.add_user(f"tarefa numero {i} " + "palavra " * 200)
        s.add_assistant("feito " + "texto " * 200, actions=[f"edit f{i}.py"])
    s.add_user("MENSAGEM ATUAL IMPORTANTE")
    view = s.messages_for_model(budget_tokens=3000)
    assert view.tokens <= 3000 * 1.05
    assert view.condensed_turns > 0
    assert "MENSAGEM ATUAL IMPORTANTE" in view.messages[-1]["content"]
    assert "Conversa anterior condensada" in view.messages[0]["content"]
    roles_ok(view.messages)


def test_huge_single_attachment_is_truncated_in_the_middle():
    s = S.ChatSession()
    s.add_user("analisa", attachment="INICIO\n" + ("linha de codigo\n" * 30000) + "FIM")
    view = s.messages_for_model(budget_tokens=4000)
    body = view.messages[-1]["content"]
    assert view.truncated and view.tokens <= 4000 * 1.1
    assert "analisa" in body and "INICIO" in body and "FIM" in body and "omitidos" in body


def test_reserve_tokens_shrinks_available_budget():
    s = S.ChatSession()
    for i in range(10):
        s.add_user("q" * 3000)
        s.add_assistant("a" * 3000)
    s.add_user("fim")
    full = s.messages_for_model(budget_tokens=20000).tokens
    tight = s.messages_for_model(budget_tokens=20000, reserve_tokens=17000).tokens
    assert tight < full


def test_roles_normalized_when_condensing_leaves_assistant_first():
    s = S.ChatSession()
    s.add_assistant("orfã")
    s.add_user("oi")
    msgs = s.messages_for_model().messages
    roles_ok(msgs)


def test_drop_last_exchange_for_regenerate():
    s = S.ChatSession()
    s.add_user("a")
    s.add_assistant("b")
    s.add_user("c")
    s.add_assistant("d")
    assert s.drop_last_exchange() == "c"
    assert [t.content for t in s.turns] == ["a", "b"]
    assert S.ChatSession().drop_last_exchange() is None


def test_title_from_first_message():
    s = S.ChatSession()
    s.add_user("  refatora\n   a função   de login  ")
    assert s.title == "refatora a função de login"


def test_store_roundtrip_excludes_attachment_and_lists_by_workspace(tmp_path):
    st = S.SessionStore(tmp_path)
    a = S.ChatSession(workspace="/w1")
    a.add_user("um", attachment="SEGREDO ANEXO", attached=["f.py"])
    a.add_assistant("r", actions=["x"], checkpoint=3)
    b = S.ChatSession(workspace="/w2")
    b.add_user("dois")
    st.save(a)
    st.save(b)
    assert "SEGREDO ANEXO" not in (tmp_path / f"{a.id}.json").read_text()
    back = st.load(a.id)
    assert back.turns[1].checkpoint == 3 and back.turns[0].attached == ["f.py"]
    assert [i.id for i in st.list("/w1")][0] == a.id
    assert [i.id for i in st.list("/w2")][0] == b.id


def test_empty_session_not_persisted(tmp_path):
    st = S.SessionStore(tmp_path)
    st.save(S.ChatSession())
    assert st.list() == []


def test_rename_delete_and_corrupt_files_ignored(tmp_path):
    st = S.SessionStore(tmp_path)
    a = S.ChatSession()
    a.add_user("x")
    st.save(a)
    (tmp_path / "deadbeef01.json").write_text("{ruim")
    assert st.rename(a.id, "Novo título")
    assert st.load(a.id).title == "Novo título"
    assert [i.id for i in st.list()] == [a.id]
    assert st.delete(a.id) and st.load(a.id) is None


def test_path_traversal_ids_rejected(tmp_path):
    st = S.SessionStore(tmp_path)
    assert st.load("../../etc/passwd") is None
    assert st.delete("../x") is False


def test_prune_keeps_max_sessions(tmp_path, monkeypatch):
    monkeypatch.setattr(S, "MAX_SESSIONS", 5)
    st = S.SessionStore(tmp_path)
    for i in range(9):
        s = S.ChatSession()
        s.add_user(str(i))
        s.updated += i
        st.save(s)
    assert len(st.list()) == 5


def test_skill_lead_only_applies_to_current_turn_and_is_not_persisted(tmp_path):
    s = S.ChatSession()
    s.add_user("login", lead="Corrija o bug:\n\n")
    s.add_assistant("ok")
    s.add_user("agora y", lead="Escreva testes:\n\n")
    msgs = s.messages_for_model().messages
    assert msgs[0]["content"] == "login"
    assert msgs[-1]["content"].startswith("Escreva testes:")
    st = S.SessionStore(tmp_path)
    st.save(s)
    assert "Corrija o bug" not in (tmp_path / f"{s.id}.json").read_text()
