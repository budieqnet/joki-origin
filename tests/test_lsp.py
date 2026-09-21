from types import SimpleNamespace

from joki.tools import lsp


def test_handle_install_already_installed(monkeypatch, capsys):
    monkeypatch.setattr(
        lsp, "_get_available_servers",
        lambda: {"python": {"command": ["pyright-langserver", "--stdio"]}},
    )
    called = []
    monkeypatch.setattr(lsp, "_try_install_lsp", lambda *a, **k: called.append((a, k)))
    lsp.handle_install_lsp_command("python")
    assert called == []
    assert "sudah terinstall" in capsys.readouterr().out


def test_handle_install_auto_installs(monkeypatch):
    monkeypatch.setattr(lsp, "_get_available_servers", dict)
    called = []
    monkeypatch.setattr(lsp, "_try_install_lsp", lambda *a, **k: called.append((a, k)))
    lsp.handle_install_lsp_command("python")
    assert called == [(("python",), {"auto": True})]


def test_try_install_lsp_auto_skips_prompt(monkeypatch):
    monkeypatch.setattr(
        lsp.shutil, "which",
        lambda exe: "/usr/bin/npm" if exe == "npm" else None,
    )
    ran = {}

    def fake_run(cmd, **kw):
        ran["cmd"] = cmd
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr(lsp.subprocess, "run", fake_run)

    def boom(*a, **k):
        raise AssertionError("input() tidak boleh dipanggil saat auto=True")

    monkeypatch.setattr("builtins.input", boom)
    assert lsp._try_install_lsp("python", auto=True) is True
    assert ran["cmd"][:2] == ["npm", "install"]
