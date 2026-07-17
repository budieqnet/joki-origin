import pytest
from joki.executor import execute

def test_predict_command_dangerous():
    result = execute("predict_command", {"cmd": "rm -rf /"})
    assert "⚠" in result

def test_predict_command_safe():
    result = execute("predict_command", {"cmd": "ls -la"})
    assert "⚠" not in result
