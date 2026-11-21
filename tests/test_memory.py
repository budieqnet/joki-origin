import json
import os
from datetime import datetime, timedelta, timezone

from joki.tools.memory import (
    _is_sensitive_key,
    _load_memory,
    _load_todo,
    _make_record,
    _memory_path,
    _memory_prompt_block,
    _read_memory_store,
    _todo_path,
    _write_memory_store,
    handle_memory_forget,
    handle_memory_recall,
    handle_memory_store,
    handle_todo_create,
    handle_todo_done,
)


def test_is_sensitive_key_detects_credentials():
    assert _is_sensitive_key("db_password") is True
    assert _is_sensitive_key("github_token") is True
    assert _is_sensitive_key("API_KEY_main") is True
    assert _is_sensitive_key("auth_secret") is True
    assert _is_sensitive_key("preferred_editor") is False
    assert _is_sensitive_key("project_path") is False


def test_memory_store_password_rejected(monkeypatch):
    monkeypatch.setattr("joki.state._CURRENT_SESSION", "test_session_reject")
    result = handle_memory_store({"key": "db_password", "value": "secret123"})
    assert "Error" in result
    assert "credential" in result or "memory_store" in result
    assert not os.path.exists(_memory_path("test_session_reject"))


def test_memory_store_token_rejected(monkeypatch):
    monkeypatch.setattr("joki.state._CURRENT_SESSION", "test_session_reject2")
    result = handle_memory_store({"key": "github_token", "value": "ghp_xxx"})
    assert "Error" in result
    assert not os.path.exists(_memory_path("test_session_reject2"))


def test_memory_store_safe_key_allowed(monkeypatch):
    monkeypatch.setattr("joki.state._CURRENT_SESSION", "test_session_allow")
    path = _memory_path("test_session_allow")
    try:
        result = handle_memory_store({"key": "preferred_editor", "value": "vim"})
        assert "Memory saved" in result
        mem = _load_memory("test_session_allow")
        assert mem.get("preferred_editor") == "vim"
    finally:
        if os.path.exists(path):
            os.remove(path)


def test_memory_legacy_raw_file_still_loads(tmp_path, monkeypatch):
    import joki.state as st
    from joki.tools import memory as mem
    monkeypatch.setattr(mem, "_get_data_dir", lambda: str(tmp_path))
    st._set_current_session("test_memory_legacy")
    try:
        path = mem._memory_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"preferred_editor": "vim"}, fh)
        assert mem._load_memory()["preferred_editor"] == "vim"
        record = mem._read_memory_store()["preferred_editor"]
        assert record["value"] == "vim"
        assert record["scope"] == "global"
        assert record["updated_at"]
    finally:
        st._set_current_session(None)


def test_memory_expired_is_not_loaded_or_injected(tmp_path, monkeypatch):
    import joki.state as st
    from joki.tools import memory as mem
    monkeypatch.setattr(mem, "_get_data_dir", lambda: str(tmp_path))
    st._set_current_session("test_memory_ttl")
    st._set_current_task_context("perbaiki login")
    old = datetime.now(timezone.utc) - timedelta(days=45)
    try:
        mem._write_memory_store({
            "stale_key": mem._make_record(
                "nilai-lama", scope="global", ttl_days=30,
                created_at=old.isoformat(), updated_at=old.isoformat(),
            ),
            "fresh_key": mem._make_record(
                "nilai-baru", scope="global", ttl_days=30,
            ),
        })
        loaded = mem._load_memory()
        assert "fresh_key" in loaded
        assert "stale_key" not in loaded
        block = mem._memory_prompt_block()
        assert "nilai-baru" in block
        assert "nilai-lama" not in block
        assert "kedaluwarsa" in mem.handle_memory_recall({"key": "stale_key"})
    finally:
        st._set_current_task_context("")
        st._set_current_session(None)


def test_memory_task_scope_hides_old_task_and_marks_it(tmp_path, monkeypatch):
    import joki.state as st
    from joki.tools import memory as mem
    monkeypatch.setattr(mem, "_get_data_dir", lambda: str(tmp_path))
    st._set_current_session("test_memory_scope")
    try:
        st._set_current_task_context("tugas A perbaiki login")
        handle_memory_store({"key": "task_a_key", "value": "nilai A"})
        task_a_record = mem._read_memory_store()["task_a_key"]
        assert task_a_record["scope"] == "task"
        assert task_a_record["task_scope"] == st._get_current_task_scope()

        st._set_current_task_context("tugas B perbaiki logout")
        handle_memory_store({"key": "task_b_key", "value": "nilai B"})
        handle_memory_store({
            "key": "global_key", "value": "nilai global", "scope": "global",
        })

        block = mem._memory_prompt_block()
        assert "nilai B" in block
        assert "nilai global" in block
        assert "nilai A" not in block
        assert "task_a_key" not in block
        assert "1 memori dari tugas lama" in block

        recall = handle_memory_recall({})
        assert "task_a_key: nilai A [tugas lama]" in recall
        assert "task_b_key: nilai B" in recall
    finally:
        st._set_current_task_context("")
        st._set_current_session(None)


def test_memory_prompt_limits_injected_items(tmp_path, monkeypatch):
    import joki.state as st
    from joki.tools import memory as mem
    monkeypatch.setattr(mem, "_get_data_dir", lambda: str(tmp_path))
    monkeypatch.setattr(mem, "MEMORY_MAX_INJECT", 3)
    st._set_current_session("test_memory_limit")
    st._set_current_task_context("tugas batas")
    try:
        mem._write_memory_store({
            f"key_{i}": mem._make_record(f"nilai_{i}", scope="global")
            for i in range(10)
        })
        block = mem._memory_prompt_block()
        assert block.startswith("[MEMORI RELEVAN] (3 relevan)")
        assert block.count("\n  - ") == 3
    finally:
        st._set_current_task_context("")
        st._set_current_session(None)


def test_inject_memory_context_replaces_stale_block(tmp_path, monkeypatch):
    import joki.cli as cli
    import joki.state as st
    from joki.tools import memory as mem
    monkeypatch.setattr(mem, "_get_data_dir", lambda: str(tmp_path))
    st._set_current_session("test_memory_inject")
    try:
        st._set_current_task_context("tugas A")
        handle_memory_store({"key": "old_task_key", "value": "nilai lama"})
        st._set_current_task_context("tugas B")
        handle_memory_store({"key": "new_task_key", "value": "nilai baru"})
        messages = [
            {"role": "system", "content": "sys"},
            {"role": "system", "content": "[MEMORI RELEVAN]\n  - old_task_key: nilai lama"},
            {"role": "user", "content": "lanjut"},
        ]
        cli._inject_memory_context(messages)
        contents = [str(m.get("content", "")) for m in messages]
        assert sum(c.startswith("[MEMORI RELEVAN]") for c in contents) == 1
        joined = "\n".join(contents)
        assert "nilai baru" in joined
        assert "nilai lama" not in joined
    finally:
        st._set_current_task_context("")
        st._set_current_session(None)


def test_memory_forget_removes_entry(tmp_path, monkeypatch):
    import joki.state as st
    from joki.tools import memory as mem
    monkeypatch.setattr(mem, "_get_data_dir", lambda: str(tmp_path))
    st._set_current_session("test_memory_forget")
    try:
        handle_memory_store({"key": "hapus_ini", "value": "x"})
        assert "Memory forgotten" in handle_memory_forget({"key": "hapus_ini"})
        assert "hapus_ini" not in mem._load_memory()
    finally:
        st._set_current_session(None)


# ============================================================
# Regresi: model sering kirim "array" sebagai string (payload nyata
# poolside-laguna: items = "1. ...\n2. ...\n3. ...").
# ============================================================


def test_todo_create_accepts_numbered_string(monkeypatch):
    monkeypatch.setattr("joki.state._CURRENT_SESSION", "test_todo_str")
    path = _todo_path()
    try:
        result = handle_todo_create({"items": "1. Buat file\n2. Test fitur\n3. Verifikasi"})
        assert "Error" not in result
        assert "Rencana Pengerjaan dibuat (3 item)" in result
        items = _load_todo()
        assert items == ["Buat file", "Test fitur", "Verifikasi"]
    finally:
        if os.path.exists(path):
            os.remove(path)


def test_todo_create_accepts_json_and_python_string(monkeypatch):
    monkeypatch.setattr("joki.state._CURRENT_SESSION", "test_todo_variants")
    path = _todo_path()
    try:
        result = handle_todo_create({"items": '["Buat file", "Test"]'})
        assert "Error" not in result
        assert _load_todo() == ["Buat file", "Test"]

        result = handle_todo_create({"items": "['Buat file', 'Test']"})
        assert "Error" not in result
        assert _load_todo() == ["Buat file", "Test"]
    finally:
        if os.path.exists(path):
            os.remove(path)


def test_todo_create_rejects_empty(monkeypatch):
    monkeypatch.setattr("joki.state._CURRENT_SESSION", "test_todo_empty")
    for kosong in ("", None, {}, []):
        result = handle_todo_create({"items": kosong})
        assert "Error" in result
    assert "Error" in handle_todo_create({})
    assert not os.path.exists(_todo_path())


def test_todo_done_accepts_string_indices(monkeypatch):
    monkeypatch.setattr("joki.state._CURRENT_SESSION", "test_todo_indices")
    path = _todo_path()
    try:
        handle_todo_create({"items": ["Satu", "Dua", "Tiga"]})
        result = handle_todo_done({"indices": "1, 2"})
        assert "Item Rencana Pengerjaan 1 dan 2 selesai" in result
        assert _load_todo() == ["✅ Satu", "✅ Dua", "Tiga"]
    finally:
        if os.path.exists(path):
            os.remove(path)
