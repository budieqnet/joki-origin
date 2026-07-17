import pytest
from unittest.mock import patch, MagicMock

import joki.llm
from joki.llm import call_llm
import httpx

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
