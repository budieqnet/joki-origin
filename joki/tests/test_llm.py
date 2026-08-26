import json
from unittest.mock import MagicMock, patch

from joki.llm import (
    MODE_GENERAL,
    _call_summary,
    _current_tools,
    _is_small_talk,
    _safe_split,
    _summarize_context,
    _system_prefix_len,
    _trim_messages,
    call_llm,
)


def _msg(content):
    return [{"role": "user", "content": content}]


def test_short_task_prompts_not_small_talk():
    assert not _is_small_talk(_msg("jalankan test sekarang"))
    assert not _is_small_talk(_msg("cek file config"))
    assert not _is_small_talk(_msg("fix bug ini"))


def test_greetings_still_small_talk():
    assert _is_small_talk(_msg("halo"))
    assert _is_small_talk(_msg("makasih"))
    assert _is_small_talk(_msg("oke sip"))


def _make_sse_lines(chunks):
    lines = []
    for c in chunks:
        lines.append(f"data: {json.dumps(c)}")
    lines.append("data: [DONE]")
    return lines


@patch("joki.llm._current_model_config", {
    "name": "Test Model",
    "base_url": "http://localhost:11434",
    "model": "test-model",
    "api_keys": ["test-key"],
    "provider": "ollama"
})
@patch("joki.llm.httpx.post")
def test_call_llm_success(mock_post):
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "message": {"role": "assistant", "content": "Hello world"}
    }
    mock_post.return_value = mock_response

    messages = [{"role": "user", "content": "Hi"}]
    result = call_llm(messages)

    assert result["role"] == "assistant"
    assert result["content"] == "Hello world"
    mock_post.assert_called_once()


@patch("joki.llm._current_model_config", {
    "name": "DeepSeek",
    "base_url": "https://api.deepseek.com",
    "model": "deepseek-v4-flash",
    "api_keys": ["sk-test"],
    "provider": "openai",
    "max_tokens": 8192,
    "reasoning": {"max_tokens": 2048},
})
@patch("joki.llm.httpx.stream")
@patch("joki.llm.ThinkingDisplay", autospec=True)
def test_reasoning_content_not_in_final_response(mock_td, mock_stream):
    sse_chunks = [
        {"choices": [{"delta": {"role": "assistant"}, "index": 0}]},
        {"choices": [{"delta": {"reasoning_content": "Hmm, let me think about this..."}, "index": 0}]},
        {"choices": [{"delta": {"reasoning_content": " I need to analyze step by step."}, "index": 0}]},
        {"choices": [{"delta": {"content": "Here is the answer:"}, "index": 0}]},
        {"choices": [{"delta": {"content": " 42"}, "index": 0, "finish_reason": "stop"}]},
    ]

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.iter_lines.return_value = _make_sse_lines(sse_chunks)
    mock_cm = MagicMock()
    mock_cm.__enter__.return_value = mock_response
    mock_stream.return_value = mock_cm

    messages = [{"role": "user", "content": "What is the meaning of life?"}]
    result = call_llm(messages)

    assert result["role"] == "assistant"
    assert result["content"] == "Here is the answer: 42"
    assert "Hmm, let me think" not in result["content"]


@patch("joki.llm._current_model_config", {
    "name": "DeepSeek",
    "base_url": "https://api.deepseek.com",
    "model": "deepseek-v4-flash",
    "api_keys": ["sk-test"],
    "provider": "openai",
    "max_tokens": 8192,
    "reasoning": {"max_tokens": 2048},
})
@patch("joki.llm.httpx.stream")
@patch("joki.llm.ThinkingDisplay", autospec=True)
def test_reasoning_param_in_request_body(mock_td, mock_stream):
    sse_chunks = [
        {"choices": [{"delta": {"role": "assistant"}, "index": 0}]},
        {"choices": [{"delta": {"content": "OK"}, "index": 0, "finish_reason": "stop"}]},
    ]

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.iter_lines.return_value = _make_sse_lines(sse_chunks)
    mock_cm = MagicMock()
    mock_cm.__enter__.return_value = mock_response
    mock_stream.return_value = mock_cm

    messages = [{"role": "user", "content": "Hi"}]
    call_llm(messages)

    _, kwargs = mock_stream.call_args
    sent_body = kwargs["json"]
    assert "reasoning" in sent_body
    assert sent_body["reasoning"] == {"max_tokens": 2048}


@patch("joki.llm._current_model_config", {
    "name": "NoReasoning",
    "base_url": "https://api.example.com",
    "model": "no-reasoning",
    "api_keys": ["sk-test"],
    "provider": "openai",
    "max_tokens": 4096,
})
@patch("joki.llm.httpx.stream")
@patch("joki.llm.ThinkingDisplay", autospec=True)
def test_no_reasoning_param_when_not_configured(mock_td, mock_stream):
    sse_chunks = [
        {"choices": [{"delta": {"role": "assistant"}, "index": 0}]},
        {"choices": [{"delta": {"content": "OK"}, "index": 0, "finish_reason": "stop"}]},
    ]

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.iter_lines.return_value = _make_sse_lines(sse_chunks)
    mock_cm = MagicMock()
    mock_cm.__enter__.return_value = mock_response
    mock_stream.return_value = mock_cm

    messages = [{"role": "user", "content": "Hi"}]
    call_llm(messages)

    _, kwargs = mock_stream.call_args
    sent_body = kwargs["json"]
    assert "reasoning" not in sent_body


@patch("joki.llm._current_model_config", {
    "name": "Test",
    "base_url": "https://api.test.com",
    "model": "test",
    "api_keys": ["sk-test"],
    "provider": "openai",
    "max_tokens": 4096,
})
@patch("joki.llm.httpx.stream")
@patch("joki.llm.ThinkingDisplay", autospec=True)
def test_tool_call_only_delta_does_not_push_gear(mock_td, mock_stream):
    """Tool call tanpa content sama sekali tidak boleh nge-push placeholder
    gear (⚙️) ke ThinkingDisplay -- panel 'Joki Berpikir (selesai)' seharusnya
    kosong/tidak muncul kalau tidak ada konten reasoning yang beneran."""
    sse_chunks = [
        {"choices": [{"delta": {"role": "assistant"}, "index": 0}]},
        {"choices": [{"delta": {"tool_calls": [
            {"index": 0, "id": "c1", "function": {"name": "read_file", "arguments": "{}"}}
        ]}, "index": 0}]},
        {"choices": [{"delta": {}, "index": 0, "finish_reason": "tool_calls"}]},
    ]
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.iter_lines.return_value = _make_sse_lines(sse_chunks)
    mock_cm = MagicMock()
    mock_cm.__enter__.return_value = mock_response
    mock_stream.return_value = mock_cm

    result = call_llm([{"role": "user", "content": "baca file"}])

    assert "tool_calls" in result
    thinking_instance = mock_td.return_value.__enter__.return_value
    push_calls = [c.args[0] for c in thinking_instance.push.call_args_list]
    assert not any("\u2699" in str(a) for a in push_calls), (
        f"gear placeholder masih di-push: {push_calls}"
    )


@patch("joki.llm._current_model_config", {
    "name": "Ollama Test",
    "base_url": "http://localhost:11434",
    "model": "test",
    "api_keys": [],
    "provider": "ollama",
    "max_tokens": 4096,
})
@patch("joki.llm.httpx.post")
@patch("joki.llm.ThinkingDisplay", autospec=True)
def test_ollama_nonstreaming_tool_call_does_not_push_gear(mock_td, mock_post):
    """Sama seperti di atas, tapi untuk jalur non-streaming (Ollama)."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "message": {"role": "assistant", "content": "", "tool_calls": [
            {"function": {"name": "read_file", "arguments": {}}}
        ]}
    }
    mock_post.return_value = mock_response

    result = call_llm([{"role": "user", "content": "baca file"}])

    assert result["role"] == "assistant"
    thinking_instance = mock_td.return_value.__enter__.return_value
    push_calls = [c.args[0] for c in thinking_instance.push.call_args_list]
    assert not any("\u2699" in str(a) for a in push_calls), (
        f"gear placeholder masih di-push: {push_calls}"
    )


def _assert_pairing_ok(messages):
    tool_ids = {m.get("tool_call_id") for m in messages if m.get("role") == "tool"}
    for m in messages:
        if m.get("role") == "assistant" and m.get("tool_calls"):
            for tc in m["tool_calls"]:
                assert tc.get("id") in tool_ids, "assistant.tool_calls tanpa pasangan tool result"
    declared = set()
    for m in messages:
        if m.get("role") == "assistant":
            for tc in m.get("tool_calls") or []:
                declared.add(tc.get("id"))
    for m in messages:
        if m.get("role") == "tool":
            assert m.get("tool_call_id") in declared, "tool result yatim (tanpa assistant)"


def test_trim_messages_preserves_pairs():
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "kerjakan X"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "call_1", "type": "function", "function": {"name": "read_file", "arguments": "{}"}}
        ]},
        {"role": "tool", "tool_call_id": "call_1", "content": "X" * 4000},
        {"role": "assistant", "content": "Langkah berikutnya"},
        {"role": "user", "content": "lanjut"},
    ]
    _trim_messages(messages, max_tokens=500)
    assert messages[0]["role"] == "system"
    _assert_pairing_ok(messages)
    assert all(m.get("role") != "tool" for m in messages)


def test_trim_messages_keeps_last_message():
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "A" * 2000},
        {"role": "assistant", "content": "B" * 2000},
        {"role": "user", "content": "C" * 2000},
    ]
    _trim_messages(messages, max_tokens=100)
    assert len(messages) >= 2
    assert messages[-1]["content"] == "C" * 2000


def test_safe_split_extends_to_paired_assistant():
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "c1", "type": "function", "function": {"name": "f", "arguments": "{}"}}
        ]},
        {"role": "tool", "tool_call_id": "c1", "content": "result"},
        {"role": "user", "content": "ok"},
    ]
    assert _safe_split(messages, keep=2) == 1


def test_safe_split_no_extension_needed():
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "task"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "c1", "type": "function", "function": {"name": "f", "arguments": "{}"}}
        ]},
        {"role": "tool", "tool_call_id": "c1", "content": "result"},
        {"role": "user", "content": "ok"},
    ]
    assert _safe_split(messages, keep=3) == 2


@patch("joki.llm._call_summary", return_value="ringkasan tes")
@patch("joki.llm._current_model_config", {
    "provider": "openai",
    "base_url": "https://api.example.com",
    "model": "m",
    "api_keys": ["k"],
})
def test_summarize_context_condenses_over_threshold(mock_summary):
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "A" * 300},
        {"role": "assistant", "content": "B" * 300},
        {"role": "user", "content": "C" * 300},
        {"role": "user", "content": "D" * 300},
    ]
    with patch("joki.llm.MAX_TOKENS", 100), patch("joki.llm.SUMMARY_WORKING_SET", 2):
        _summarize_context(messages)
    mock_summary.assert_called_once()
    assert messages[1]["role"] == "system"
    assert "[RINGKASAN KONTEKS]" in messages[1]["content"]
    assert messages[-2:] == [
        {"role": "user", "content": "C" * 300},
        {"role": "user", "content": "D" * 300},
    ]


@patch("joki.llm._call_summary", return_value=None)
@patch("joki.llm._current_model_config", {
    "provider": "openai",
    "base_url": "https://api.example.com",
    "model": "m",
    "api_keys": ["k"],
})
def test_summarize_context_falls_back_when_summary_fails(mock_summary):
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "A" * 300},
        {"role": "assistant", "content": "B" * 300},
        {"role": "user", "content": "C" * 300},
        {"role": "user", "content": "D" * 300},
    ]
    with patch("joki.llm.MAX_TOKENS", 100), patch("joki.llm.SUMMARY_WORKING_SET", 2):
        _summarize_context(messages)
    assert mock_summary.called
    assert len(messages) == 5
    assert messages[1]["role"] == "user"


@patch("joki.llm.httpx.post")
def test_call_summary_openai_success(mock_post):
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"choices": [{"message": {"content": "ringkasan"}}]}
    mock_post.return_value = mock_resp
    cfg = {"provider": "openai", "base_url": "https://x/v1", "model": "m", "api_keys": ["k"]}
    out = _call_summary(cfg, [{"role": "user", "content": "isi panjang"}])
    assert out == "ringkasan"
    _, kwargs = mock_post.call_args
    assert kwargs["json"]["stream"] is False


@patch("joki.llm.httpx.post")
def test_call_summary_rotates_keys_on_quota(mock_post):
    r1 = MagicMock()
    r1.status_code = 429
    r2 = MagicMock()
    r2.status_code = 200
    r2.json.return_value = {"choices": [{"message": {"content": "ok"}}]}
    mock_post.side_effect = [r1, r2]
    cfg = {"provider": "openai", "base_url": "https://x/v1", "model": "m", "api_keys": ["a", "b"]}
    out = _call_summary(cfg, [{"role": "user", "content": "x"}])
    assert out == "ok"
    assert mock_post.call_count == 2


@patch("joki.llm.httpx.post")
def test_call_summary_all_keys_fail_returns_none(mock_post):
    mock_resp = MagicMock()
    mock_resp.status_code = 500
    mock_post.return_value = mock_resp
    cfg = {"provider": "openai", "base_url": "https://x/v1", "model": "m", "api_keys": ["a", "b"]}
    assert _call_summary(cfg, [{"role": "user", "content": "x"}]) is None
    assert mock_post.call_count == 2


def test_system_prefix_protected_from_trim():
    messages = [
        {"role": "system", "content": "prompt"},
        {"role": "system", "content": "[INDEX PROYEK] map"},
        {"role": "system", "content": "[CHECKPOINT TUGAS] cek"},
        {"role": "user", "content": "A" * 3000},
        {"role": "assistant", "content": "B" * 3000},
        {"role": "user", "content": "C" * 3000},
    ]
    _trim_messages(messages, max_tokens=100)
    assert len(messages) >= 4
    assert messages[0]["content"] == "prompt"
    assert messages[1]["content"].startswith("[INDEX PROYEK]")
    assert messages[2]["content"].startswith("[CHECKPOINT TUGAS]")


def test_system_prefix_len_counts_leading_system():
    messages = [
        {"role": "system", "content": "a"},
        {"role": "system", "content": "b"},
        {"role": "user", "content": "c"},
        {"role": "system", "content": "d"},
    ]
    assert _system_prefix_len(messages) == 2


@patch("joki.llm._call_summary", return_value="ringkasan")
@patch("joki.llm._current_model_config", {
    "provider": "openai",
    "base_url": "https://api.example.com",
    "model": "m",
    "api_keys": ["k"],
})
def test_summarize_preserves_system_prefix(mock_summary):
    messages = [
        {"role": "system", "content": "prompt"},
        {"role": "system", "content": "[INDEX PROYEK] map"},
        {"role": "system", "content": "[CHECKPOINT TUGAS] cek"},
        {"role": "user", "content": "A" * 300},
        {"role": "assistant", "content": "B" * 300},
        {"role": "user", "content": "C" * 300},
        {"role": "user", "content": "D" * 300},
    ]
    with patch("joki.llm.MAX_TOKENS", 100), patch("joki.llm.SUMMARY_WORKING_SET", 2):
        _summarize_context(messages)
    assert messages[0]["content"] == "prompt"
    assert messages[1]["content"].startswith("[INDEX PROYEK]")
    assert messages[2]["content"].startswith("[CHECKPOINT TUGAS]")
    assert "[RINGKASAN KONTEKS]" in messages[3]["content"]
    assert messages[-2:] == [
        {"role": "user", "content": "C" * 300},
        {"role": "user", "content": "D" * 300},
    ]


@patch("joki.llm._current_task_mode", MODE_GENERAL)
@patch("joki.llm._recent_tools", set())
def test_current_tools_general_only_core():
    tools = _current_tools()
    names = {t["function"]["name"] for t in tools}
    assert "read_file" in names
    assert "run_command" in names
    assert "port_scan" not in names  # bukan core, belum pernah dipakai


@patch("joki.llm._current_task_mode", MODE_GENERAL)
@patch("joki.llm._recent_tools", {"port_scan"})
def test_current_tools_general_adds_recent():
    tools = _current_tools()
    names = {t["function"]["name"] for t in tools}
    assert "port_scan" in names
