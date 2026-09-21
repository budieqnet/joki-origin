import os

import joki.skills as skills


def _seed_skill(tmp_path, fname, always_on=True, body="Isi skill"):
    d = os.path.join(tmp_path, "skills")
    os.makedirs(d, exist_ok=True)
    on = "true" if always_on else "false"
    content = (
        f"---\nname: Skill {fname}\nalways_on: {on}\n---\n{body}"
    )
    p = os.path.join(d, fname)
    with open(p, "w", encoding="utf-8") as fh:
        fh.write(content)
    return p


def test_load_skills_only_always_on(tmp_path, monkeypatch):
    # Supaya tidak menulis ke ~/.local/share/joki/skills sungguhan saat test
    _seed_skill(tmp_path, "always.md", True, "KONTEN_ALWAYS")
    _seed_skill(tmp_path, "off.md", False, "KONTEN_OFF")

    monkeypatch.setattr(skills, "_get_data_dir", lambda: str(tmp_path))
    # Jangan biarkan _seed_default_skills menulis ke tmp skills dir default
    monkeypatch.setattr(skills, "_seed_default_skills", lambda: None)

    result = skills._load_skills()
    assert "KONTEN_ALWAYS" in result
    assert "Skill always.md" in result
    assert "KONTEN_OFF" not in result
    assert "Skill off.md" not in result


def test_load_skills_empty_when_none_always_on(tmp_path, monkeypatch):
    _seed_skill(tmp_path, "off.md", False, "KONTEN_OFF")
    monkeypatch.setattr(skills, "_get_data_dir", lambda: str(tmp_path))
    monkeypatch.setattr(skills, "_seed_default_skills", lambda: None)
    assert skills._load_skills() == ""


def test_seed_default_skills_writes_default(tmp_path, monkeypatch):
    monkeypatch.setattr(skills, "_get_data_dir", lambda: str(tmp_path))
    skills._seed_default_skills()
    dest = os.path.join(tmp_path, "skills", "verifikasi-eksekusi.md")
    assert os.path.exists(dest)
    with open(dest, "r", encoding="utf-8") as fh:
        content = fh.read()
    assert content.startswith("---")
    assert "always_on: true" in content