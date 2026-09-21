import os
from unittest.mock import MagicMock, patch

from joki.tools.git import (
    _SHELL_META,
    handle_git_add,
    handle_git_commit,
    handle_git_log,
)


def test_shell_meta_pattern():
    assert _SHELL_META.search("; touch /tmp/pwned")
    assert _SHELL_META.search("`id`")
    assert _SHELL_META.search("$(whoami)")
    assert _SHELL_META.search("foo | bar")
    assert _SHELL_META.search("x & y")
    assert not _SHELL_META.search("feature/login")
    assert not _SHELL_META.search("main")
    assert not _SHELL_META.search("v1.2.3")


@patch("joki.tools.git.subprocess.run")
def test_git_log_oneline_default(mock_run):
    mock_run.return_value = MagicMock(
        returncode=0, stdout="abc123 commit msg\n", stderr=""
    )
    handle_git_log({"cwd": ""})
    mock_run.assert_called_once()
    args, kwargs = mock_run.call_args
    assert args[0] == ["git", "log", "--oneline", "--max-count=10"]
    assert kwargs.get("shell") is False


@patch("joki.tools.git.subprocess.run")
def test_git_log_full_format(mock_run):
    mock_run.return_value = MagicMock(
        returncode=0, stdout="full log output\n", stderr=""
    )
    handle_git_log({"cwd": "", "format": "full", "max_count": 5})
    args, kwargs = mock_run.call_args
    assert args[0] == ["git", "log", "--max-count=5"]
    assert kwargs.get("shell") is False


@patch("joki.tools.git.subprocess.run")
def test_git_log_custom_format(mock_run):
    mock_run.return_value = MagicMock(
        returncode=0, stdout="custom output\n", stderr=""
    )
    handle_git_log({"cwd": "", "format": "%H %s", "max_count": 3})
    args, _ = mock_run.call_args
    assert args[0] == ["git", "log", "--format=%H %s", "--max-count=3"]


@patch("joki.tools.git.subprocess.run")
def test_git_log_with_branch(mock_run):
    mock_run.return_value = MagicMock(
        returncode=0, stdout="abc123 feat\n", stderr=""
    )
    handle_git_log({"cwd": "", "branch": "feature/login"})
    args, _ = mock_run.call_args
    assert "feature/login" in args[0]


def test_git_log_injection_branch():
    result = handle_git_log({"branch": '; touch /tmp/should_not_exist #'})
    assert "ERROR" in result
    assert not os.path.exists("/tmp/should_not_exist")


def test_git_log_injection_branch_backtick():
    result = handle_git_log({"branch": "`id`"})
    assert "ERROR" in result


def test_git_log_injection_branch_subshell():
    result = handle_git_log({"branch": "$(whoami)"})
    assert "ERROR" in result


def test_git_log_injection_format():
    result = handle_git_log({"format": "$(id)", "max_count": 1, "cwd": ""})
    assert "ERROR" in result


@patch("joki.tools.git.subprocess.run")
def test_git_commit_no_verify_default_false(mock_run):
    mock_run.return_value = MagicMock(
        returncode=0, stdout="[main abc123] test\n", stderr=""
    )
    handle_git_commit({"message": "test commit"})
    args, _ = mock_run.call_args
    cmd = args[0]
    assert "--no-verify" not in cmd
    assert "-m" in cmd


@patch("joki.tools.git.subprocess.run")
def test_git_commit_no_verify_explicit_true(mock_run):
    mock_run.return_value = MagicMock(
        returncode=0, stdout="[main abc123] test\n", stderr=""
    )
    handle_git_commit({"message": "test commit", "no_verify": True})
    args, _ = mock_run.call_args
    assert "--no-verify" in args[0]


@patch("joki.tools.git.subprocess.run")
def test_git_commit_no_verify_explicit_false(mock_run):
    mock_run.return_value = MagicMock(
        returncode=0, stdout="[main abc123] test\n", stderr=""
    )
    handle_git_commit({"message": "test commit", "no_verify": False})
    args, _ = mock_run.call_args
    assert "--no-verify" not in args[0]


@patch("joki.tools.git.subprocess.run")
def test_git_commit_empty_message(mock_run):
    result = handle_git_commit({"message": ""})
    assert "ERROR" in result
    mock_run.assert_not_called()


@patch("joki.tools.git.subprocess.run")
def test_git_add(mock_run):
    mock_run.return_value = MagicMock(
        returncode=0, stdout="", stderr=""
    )
    handle_git_add({"cwd": "", "files": "."})
    args, kwargs = mock_run.call_args
    assert args[0] == ["git", "add", "--", "."]
    assert kwargs.get("shell") is False
