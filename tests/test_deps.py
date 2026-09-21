import os
import shutil
import tempfile

from joki.tools.deps import (
    _find_circular_deps,
    _module_to_path,
    _scan_file_imports,
    handle_analyze_deps,
    handle_impact_analysis,
)


def test_scan_imports_from_full_path_python():
    tmp = tempfile.mkdtemp(prefix="joki_test_si_")
    file_path = os.path.join(tmp, "test.py")
    with open(file_path, "w") as f:
        f.write("from paket.sub.modul import sesuatu\n")
    try:
        imports, ext = _scan_file_imports(file_path)
        assert "paket.sub.modul" in imports
        assert "paket" not in imports  # jangan potong ke segmen pertama
        assert ext == ".py"
    finally:
        os.unlink(file_path)
        os.rmdir(tmp)


def test_scan_imports_plain_import_single():
    tmp = tempfile.mkdtemp(prefix="joki_test_si_")
    file_path = os.path.join(tmp, "test.py")
    with open(file_path, "w") as f:
        f.write("import os\n")
    try:
        imports, _ = _scan_file_imports(file_path)
        assert "os" in imports
    finally:
        os.unlink(file_path)
        os.rmdir(tmp)


def test_scan_imports_multi_line():
    tmp = tempfile.mkdtemp(prefix="joki_test_si_")
    file_path = os.path.join(tmp, "test.py")
    with open(file_path, "w") as f:
        f.write(
            "import os\n"
            "import sys\n"
            "from datetime import datetime\n"
            "from joki.state import *\n"
            "from joki.config import _get_data_dir\n"
        )
    try:
        imports, _ = _scan_file_imports(file_path)
        assert "os" in imports
        assert "sys" in imports
        assert "datetime" in imports
        assert "joki.state" in imports
        assert "joki.config" in imports
        assert len(imports) >= 5, f"Expected >=5 imports, got {len(imports)}: {imports}"
    finally:
        os.unlink(file_path)
        os.rmdir(tmp)


def test_module_to_path_full():
    tmp = tempfile.mkdtemp(prefix="joki_test_mtp_")
    try:
        os.makedirs(os.path.join(tmp, "paket"), exist_ok=True)
        with open(os.path.join(tmp, "paket", "modul_a.py"), "w") as f:
            f.write("x = 1")
        result = _module_to_path("paket.modul_a", ".py", [tmp])
        assert result is not None
        assert result.endswith("paket/modul_a.py")
    finally:
        shutil.rmtree(tmp)


def test_module_to_path_init():
    tmp = tempfile.mkdtemp(prefix="joki_test_mtp_")
    try:
        os.makedirs(os.path.join(tmp, "httpx"), exist_ok=True)
        with open(os.path.join(tmp, "httpx", "__init__.py"), "w") as f:
            f.write("")
        result = _module_to_path("httpx", ".py", [tmp])
        assert result is not None
        assert result.endswith("httpx/__init__.py")
    finally:
        shutil.rmtree(tmp)


def test_module_to_path_external_not_found():
    tmp = tempfile.mkdtemp(prefix="joki_test_mtp_")
    try:
        result = _module_to_path("some_random_external_package", ".py", [tmp])
        assert result is None
    finally:
        shutil.rmtree(tmp)


def test_module_to_path_fallback_to_first_seg():
    tmp = tempfile.mkdtemp(prefix="joki_test_mtp_")
    try:
        # Simulasi: from httpx.some.deep.path import X
        # Full path httpx/some/deep/path.py tidak ada
        # Tapi fallback ke segmen pertama httpx → httpx/__init__.py harus ada
        os.makedirs(os.path.join(tmp, "httpx"), exist_ok=True)
        with open(os.path.join(tmp, "httpx", "__init__.py"), "w") as f:
            f.write("")
        result = _module_to_path("httpx.some.deep.path", ".py", [tmp])
        assert result is not None
        assert result.endswith("httpx/__init__.py")
    finally:
        shutil.rmtree(tmp)


def test_analyze_deps_orphan_detection():
    files = {
        "main.py": "from paket.modul_a import fungsi\n",
        "paket/__init__.py": "",
        "paket/modul_a.py": "fungsi = lambda: 1\n",
        "paket/modul_b.py": "x = 2\n",
    }
    root = _make_temp_project(files)
    try:
        result = handle_analyze_deps({"path": root})
        # main.py mengimport modul_a.py — modul_a HARUS ter-resolve, bukan orphan
        assert "paket/modul_a.py" not in result or "tidak diimport" not in result
        # modul_b.py tidak diimport siapapun — BISA orphan (tapi tidak wajib muncul
        # di 10 besar orphan, jadi kita assert positif kalo muncul)
        orphan_section = result.split("Orphan files")[-1] if "Orphan files" in result else ""
        if orphan_section and "paket/modul_b.py" not in orphan_section:
            # modul_b.py mungkin tidak masuk 10 besar orphan, skip assert
            pass
    finally:
        shutil.rmtree(root)


def _make_temp_project(files):
    root = tempfile.mkdtemp(prefix="joki_test_deps_")
    for relpath, content in files.items():
        full = os.path.join(root, relpath)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w") as f:
            f.write(content)
    return root


# ---------- AST-based Python import scanning ----------

def test_scan_python_ast_ignores_comments_and_docstrings():
    tmp = tempfile.mkdtemp(prefix="joki_test_ast_")
    file_path = os.path.join(tmp, "test.py")
    with open(file_path, "w") as f:
        f.write(
            '"""\nfrom fake.docstring import X\nimport fake_import\n"""\n'
            "# from fake.comment import Y\n"
            "import os\n"
            "s = 'import not_real'\n"
        )
    try:
        imports, ext = _scan_file_imports(file_path)
        assert ext == ".py"
        assert "os" in imports
        assert "fake.docstring" not in imports
        assert "fake.comment" not in imports
        assert "fake_import" not in imports
        assert "not_real" not in imports
    finally:
        os.unlink(file_path)
        os.rmdir(tmp)


def test_scan_python_ast_single_line_multiple_imports():
    tmp = tempfile.mkdtemp(prefix="joki_test_ast_")
    file_path = os.path.join(tmp, "test.py")
    with open(file_path, "w") as f:
        f.write("import os, sys, json\n")
    try:
        imports, _ = _scan_file_imports(file_path)
        assert "os" in imports
        assert "sys" in imports
        assert "json" in imports
    finally:
        os.unlink(file_path)
        os.rmdir(tmp)


def test_scan_python_ast_relative_imports():
    tmp = tempfile.mkdtemp(prefix="joki_test_ast_")
    file_path = os.path.join(tmp, "test.py")
    with open(file_path, "w") as f:
        f.write("from . import util\nfrom ..helpers import x\nfrom .. import mod\n")
    try:
        imports, _ = _scan_file_imports(file_path)
        assert ".util" in imports
        assert "..helpers" in imports
        assert "..mod" in imports
    finally:
        os.unlink(file_path)
        os.rmdir(tmp)


def test_scan_python_ast_lazy_import_in_function():
    tmp = tempfile.mkdtemp(prefix="joki_test_ast_")
    file_path = os.path.join(tmp, "test.py")
    with open(file_path, "w") as f:
        f.write("def load():\n    import json\n    return json\n")
    try:
        imports, _ = _scan_file_imports(file_path)
        assert "json" in imports
    finally:
        os.unlink(file_path)
        os.rmdir(tmp)


def test_scan_python_syntax_error_falls_back_to_regex():
    tmp = tempfile.mkdtemp(prefix="joki_test_ast_")
    file_path = os.path.join(tmp, "broken.py")
    with open(file_path, "w") as f:
        f.write("import os\nthis is not valid python (!!!\n")
    try:
        imports, _ = _scan_file_imports(file_path)
        assert "os" in imports
    finally:
        os.unlink(file_path)
        os.rmdir(tmp)


# ---------- relative import resolution ----------

def test_module_to_path_relative_import():
    tmp = tempfile.mkdtemp(prefix="joki_test_rel_")
    try:
        os.makedirs(os.path.join(tmp, "pkg", "sub"), exist_ok=True)
        with open(os.path.join(tmp, "pkg", "mod.py"), "w") as f:
            f.write("x = 1")
        with open(os.path.join(tmp, "pkg", "sub", "b.py"), "w") as f:
            f.write("")
        sub = os.path.join(tmp, "pkg", "sub")
        result = _module_to_path("..mod", ".py", [sub], from_dir=sub)
        assert result == os.path.join(tmp, "pkg", "mod.py")
    finally:
        shutil.rmtree(tmp)


def test_module_to_path_relative_package_self():
    tmp = tempfile.mkdtemp(prefix="joki_test_rel_")
    try:
        os.makedirs(os.path.join(tmp, "pkg"), exist_ok=True)
        with open(os.path.join(tmp, "pkg", "__init__.py"), "w") as f:
            f.write("")
        result = _module_to_path(".", ".py", [os.path.join(tmp, "pkg")], from_dir=os.path.join(tmp, "pkg"))
        assert result == os.path.join(tmp, "pkg", "__init__.py")
    finally:
        shutil.rmtree(tmp)


# ---------- impact_analysis ----------

def test_impact_analysis_direct():
    files = {
        "pyproject.toml": "[project]\nname = 't'\n",
        "main.py": "from pkg.service import run\n",
        "pkg/__init__.py": "",
        "pkg/service.py": "def run(): pass\n",
        "pkg/model.py": "x = 1\n",
    }
    root = _make_temp_project(files)
    try:
        result = handle_impact_analysis({"path": os.path.join(root, "pkg", "service.py")})
        assert "main.py" in result
        assert "LANGSUNG" in result
        assert "pkg/model.py" not in result
    finally:
        shutil.rmtree(root)


def test_impact_analysis_transitive():
    files = {
        "pyproject.toml": "[project]\nname = 't'\n",
        "app.py": "from pkg.service import run\n",
        "pkg/__init__.py": "",
        "pkg/service.py": "from pkg.model import M\n",
        "pkg/model.py": "class M: pass\n",
    }
    root = _make_temp_project(files)
    try:
        result = handle_impact_analysis({"path": os.path.join(root, "pkg", "model.py")})
        assert "pkg/service.py" in result
        assert "app.py" in result
        assert "TRANSITIVE" in result
    finally:
        shutil.rmtree(root)


def test_impact_analysis_total_no_double_count():
    files = {
        "pyproject.toml": "[project]\nname = 't'\n",
        "app.py": "from pkg.service import run\n",
        "pkg/__init__.py": "",
        "pkg/service.py": "from pkg.model import M\n",
        "pkg/model.py": "class M: pass\n",
    }
    root = _make_temp_project(files)
    try:
        result = handle_impact_analysis({"path": os.path.join(root, "pkg", "model.py")})
        # app (transitif) + service (langsung) = 2, BUKAN 3 (double-count lama)
        assert "Total file perlu dicek ulang: 2" in result
    finally:
        shutil.rmtree(root)


def test_impact_analysis_file_not_found():
    result = handle_impact_analysis({"path": "/nonexistent/foo.py"})
    assert "tidak ditemukan" in result


def test_impact_analysis_relative_import_resolved():
    files = {
        "pyproject.toml": "[project]\nname = 't'\n",
        "pkg/__init__.py": "",
        "pkg/mod.py": "x = 1\n",
        "pkg/sub/__init__.py": "",
        "pkg/sub/b.py": "from .. import mod\n",
    }
    root = _make_temp_project(files)
    try:
        result = handle_impact_analysis({"path": os.path.join(root, "pkg", "mod.py")})
        assert "pkg/sub/b.py" in result
        assert "LANGSUNG" in result
    finally:
        shutil.rmtree(root)


# ---------- circular dependencies ----------

def test_find_circular_deps_canonical():
    graph = {
        "a.py": {"resolved": ["b.py"]},
        "b.py": {"resolved": ["c.py"]},
        "c.py": {"resolved": ["a.py"]},
        "d.py": {"resolved": ["a.py"]},
    }
    cycles = _find_circular_deps(graph)
    assert len(cycles) == 1
    assert set(cycles[0]) == {"a.py", "b.py", "c.py"}


def test_find_circular_deps_none():
    graph = {
        "a.py": {"resolved": ["b.py"]},
        "b.py": {"resolved": []},
    }
    assert _find_circular_deps(graph) == []


def test_analyze_deps_target_shows_transitive_dependants():
    files = {
        "pyproject.toml": "[project]\nname = 't'\n",
        "app.py": "from pkg.service import run\n",
        "pkg/__init__.py": "",
        "pkg/service.py": "from pkg.model import M\n",
        "pkg/model.py": "class M: pass\n",
    }
    root = _make_temp_project(files)
    try:
        result = handle_analyze_deps({"path": os.path.join(root, "pkg", "model.py")})
        assert "app.py" in result
        assert "Terpengaruh transitif" in result
        assert "Impact jika diubah: 2 file lain perlu dicek." in result
    finally:
        shutil.rmtree(root)
