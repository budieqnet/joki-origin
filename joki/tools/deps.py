import ast
import os
import re
from collections import defaultdict

from joki.state import _WORKSPACE_ROOT

_RESOLVE_EXTS = [".py", ".js", ".ts", ".jsx", ".tsx", ".go", ".php", ".rs", ".rb", ".java", ".cs"]


_IMPORT_PATTERNS = {
    ".py": [
        re.compile(r'^\s*import\s+(\S+)', re.MULTILINE),
        re.compile(r'^\s*from\s+(\S+)\s+import', re.MULTILINE),
    ],
    ".js": [
        re.compile(r'require\([\'"](\S+?)[\'"]\)'),
        re.compile(r'^import\s+(?:\{[^}]*\}|\S+)\s+from\s+[\'"](\S+?)[\'"]', re.MULTILINE),
        re.compile(r'^import\s+[\'"](\S+?)[\'"]', re.MULTILINE),
    ],
    ".jsx": [
        re.compile(r'require\([\'"](\S+?)[\'"]\)'),
        re.compile(r'^import\s+(?:\{[^}]*\}|\S+)\s+from\s+[\'"](\S+?)[\'"]', re.MULTILINE),
    ],
    ".ts": [
        re.compile(r'^import\s+(?:\{[^}]*\}|\S+)\s+from\s+[\'"](\S+?)[\'"]', re.MULTILINE),
        re.compile(r'^import\s+[\'"](\S+?)[\'"]', re.MULTILINE),
    ],
    ".tsx": [
        re.compile(r'^import\s+(?:\{[^}]*\}|\S+)\s+from\s+[\'"](\S+?)[\'"]', re.MULTILINE),
    ],
    ".go": [
        re.compile(r'^\s*import\s+[\'"](.+?)[\'"]', re.MULTILINE),
        re.compile(r'^\s*import\s+\(([^)]+)\)', re.MULTILINE),
    ],
    ".php": [
        re.compile(r'^\s*use\s+(\S+);', re.MULTILINE),
        re.compile(r'^\s*require(?:_[once]+)?\s*\(?[\'"](\S+?)[\'"]\)?', re.MULTILINE),
    ],
    ".rs": [
        re.compile(r'^\s*use\s+(\S+);', re.MULTILINE),
        re.compile(r'^\s*extern\s+crate\s+(\S+);', re.MULTILINE),
    ],
    ".rb": [
        re.compile(r'^\s*require\s+[\'"](\S+?)[\'"]', re.MULTILINE),
        re.compile(r'^\s*require_relative\s+[\'"](\S+?)[\'"]', re.MULTILINE),
    ],
    ".java": [
        re.compile(r'^\s*import\s+(\S+);', re.MULTILINE),
    ],
    ".cs": [
        re.compile(r'^\s*using\s+(\S+);', re.MULTILINE),
    ],
}

_EXCLUDE_DIRS = {".git", "node_modules", "__pycache__", "venv", ".venv",
                 "vendor", "target", "build", "dist", ".next",
                 "coverage", ".ruff_cache", ".mypy_cache", ".pytest_cache"}


def _scan_python_imports_ast(content):
    """Scan Python imports dengan AST sungguhan.

    Mengembalikan None saat SyntaxError sehingga caller bisa fallback ke regex.
    Relative import di-encode dengan titik di depan: '.utils', '..helpers'.
    """
    try:
        tree = ast.parse(content)
    except (SyntaxError, ValueError):
        return None
    imports = []
    seen = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                name = alias.name
                if name not in seen:
                    seen.add(name)
                    imports.append(name)
        elif isinstance(node, ast.ImportFrom):
            if node.level > 0:
                if node.module:
                    names = ["." * node.level + node.module]
                else:
                    names = ["." * node.level + a.name for a in node.names]
            else:
                names = [node.module or ""]
            for name in names:
                if name and name not in seen:
                    seen.add(name)
                    imports.append(name)
    return imports


def _scan_file_imports(file_path):
    ext = os.path.splitext(file_path)[1].lower()
    patterns = _IMPORT_PATTERNS.get(ext, [])
    if not patterns:
        return [], ""
    try:
        with open(file_path, "r", errors="ignore") as f:
            content = f.read()
    except OSError:
        return [], ""
    if ext == ".py":
        ast_imports = _scan_python_imports_ast(content)
        if ast_imports is not None:
            return ast_imports, ext
    imports = set()
    for pattern in patterns:
        matches = pattern.findall(content)
        is_from_pattern = "from" in pattern.pattern and "import" in pattern.pattern
        for m in matches:
            if isinstance(m, str):
                if is_from_pattern:
                    imports.add(m)
                else:
                    imports.add(m.split(".")[0])
            elif isinstance(m, tuple):
                if is_from_pattern:
                    imports.add(m[0])
                else:
                    imports.add(m[0].split(".")[0])
    return list(imports), ext


def _find_local_files(root_dir):
    files_by_ext = defaultdict(list)
    for root, dirs, fnames in os.walk(root_dir):
        dn = os.path.basename(root)
        if dn.startswith(".") or dn in _EXCLUDE_DIRS:
            dirs[:] = []
            continue
        for fname in fnames:
            fpath = os.path.join(root, fname)
            ext = os.path.splitext(fname)[1].lower()
            if ext in _IMPORT_PATTERNS:
                try:
                    if os.path.getsize(fpath) > 500_000:
                        continue
                    files_by_ext[ext].append(fpath)
                except Exception:  # noqa: BLE001, S112
                    continue
    return files_by_ext


def _candidate_paths(module_path, ext):
    exts = [ext] + [e for e in _RESOLVE_EXTS if e != ext]
    candidates = []
    for base_ext in exts:
        candidates.extend([
            f"{module_path}{base_ext}",
            f"{module_path}/__init__{base_ext}",
            f"{module_path}/index{base_ext}",
            f"{module_path}/mod{base_ext}",
            f"{module_path}/main{base_ext}",
        ])
    return candidates


def _resolve_relative_import(module_name, ext, from_dir, search_dirs):
    base = from_dir or (search_dirs[0] if search_dirs else None)
    if not base or not os.path.isdir(base):
        return None
    stripped = module_name.lstrip(".")
    level = len(module_name) - len(stripped)
    pkg_dir = os.path.abspath(base)
    for _ in range(max(0, level - 1)):
        parent = os.path.dirname(pkg_dir)
        if parent == pkg_dir:
            return None
        pkg_dir = parent
    if stripped:
        module_path = stripped.replace(".", "/")
        candidates = _candidate_paths(f"{pkg_dir}/{module_path}", ext)
    else:
        candidates = _candidate_paths(f"{pkg_dir}/__init__", ext)
    for c in candidates:
        if os.path.exists(c):
            return os.path.abspath(c)
    return None


def _module_to_path(module_name, ext, search_dirs=None, from_dir=None):
    if not module_name:
        return None
    if search_dirs is None:
        search_dirs = ["."]

    if module_name.startswith("."):
        return _resolve_relative_import(module_name, ext, from_dir, search_dirs)

    def _try_resolve(mod_name):
        if "." in mod_name:
            module_path = mod_name.replace(".", "/")
        else:
            module_path = mod_name
        candidates = _candidate_paths(module_path, ext)
        for d in search_dirs:
            for c in candidates:
                fp = os.path.join(d, c)
                if os.path.exists(fp):
                    return os.path.abspath(fp)
        return None

    # Coba full path dulu (e.g. "joki.state" → "joki/state.py")
    result = _try_resolve(module_name)
    if result:
        return result

    # Fallback ke segmen pertama (e.g. "httpx" untuk "from httpx import X")
    first_seg = module_name.split(".")[0]
    if first_seg != module_name:
        return _try_resolve(first_seg)
    return None


def _build_file_graph(root_dir):
    files_by_ext = _find_local_files(root_dir)
    file_graph = {}
    for ext_files in files_by_ext.values():
        for fpath in ext_files:
            imports, ext = _scan_file_imports(fpath)
            rel = os.path.relpath(fpath, root_dir)
            search_dirs = [os.path.dirname(fpath), root_dir]
            resolved = []
            for imp in imports:
                resolved_path = _module_to_path(imp, ext, search_dirs, from_dir=os.path.dirname(fpath))
                if resolved_path:
                    rrel = os.path.relpath(resolved_path, root_dir)
                    if rrel != rel:
                        resolved.append(rrel)
            file_graph[rel] = {"imports": imports, "resolved": resolved}
    return file_graph


def _compute_dependants(file_graph, target_rel, transitive=False):
    """Return (direct, indirect) dependants dari target_rel.

    direct   = file yang langsung mengimport target.
    indirect = file yang mengimport (secara transitif) file yang mengimport target.
    """
    direct = sorted([f for f, info in file_graph.items() if target_rel in info["resolved"]])
    if not transitive:
        return direct, []
    affected = set()
    visited = set(direct)
    queue = list(direct)
    while queue:
        current = queue.pop(0)
        if current in affected:
            continue
        affected.add(current)
        for f, info in file_graph.items():
            if f not in visited and current in info["resolved"]:
                visited.add(f)
                queue.append(f)
    affected.discard(target_rel)
    indirect = sorted(affected - set(direct))
    return direct, indirect


def handle_analyze_deps(args):
    path = args.get("path", _WORKSPACE_ROOT)
    if not os.path.exists(path):
        return f"Error: Path tidak ditemukan: {path}"

    target_file = ""
    if os.path.isfile(path):
        target_file = os.path.abspath(path)
        root_dir = _find_project_root_deps(path)
    else:
        root_dir = os.path.abspath(path)

    file_graph = _build_file_graph(root_dir)
    if not file_graph:
        return "Tidak ada file yang bisa dianalisis."

    if target_file:
        target_rel = os.path.relpath(target_file, root_dir)
        if target_rel not in file_graph:
            return f"File {target_rel} tidak memiliki dependency atau tidak ditemukan."
        target_info = file_graph[target_rel]
        direct, indirect = _compute_dependants(file_graph, target_rel, transitive=True)
        lines = [f"Dependency Analysis: {target_rel}"]
        if target_info["imports"]:
            lines.append(f"\n  Imports ({len(target_info['imports'])}):")
            for imp in target_info["imports"]:
                found = " [local]" if any(target_rel in r for r in target_info["resolved"]) else " [external]"
                lines.append(f"    ← {imp}{found}")
        else:
            lines.append("\n  Imports: (none)")
        if direct:
            lines.append(f"\n  Digunakan oleh ({len(direct)} file langsung):")
            for dep in direct:
                lines.append(f"    → {dep}")
        else:
            lines.append("\n  Digunakan oleh: (none — mungkin unused)")
        if indirect:
            lines.append(f"\n  Terpengaruh transitif ({len(indirect)} file):")
            for dep in indirect[:10]:
                lines.append(f"    → {dep}")
            if len(indirect) > 10:
                lines.append(f"    ... dan {len(indirect)-10} lainnya")

        lines.append(f"\n  Impact jika diubah: {len(direct) + len(indirect)} file lain perlu dicek.")
        return "\n".join(lines)

    total_files = len(file_graph)
    circular = _find_circular_deps(file_graph)
    orphan_files = [f for f, info in file_graph.items() if not info["resolved"] and not any(
        f in i["resolved"] for i in file_graph.values())]

    stats = [
        f"Dependency Analysis: {root_dir}",
        f"  Total file: {total_files}",
        f"  File dengan imports: {sum(1 for v in file_graph.values() if v['imports'])}",
        f"  File tanpa imports: {sum(1 for v in file_graph.values() if not v['imports'])}",
        f"  Circular dependencies: {len(circular)}",
        f"  Orphan files (tidak diimport siapapun): {len(orphan_files)}",
    ]
    if circular:
        stats.append("\n  Circular dependencies:")
        for cycle in circular[:5]:
            stats.append(f"    {' → '.join(cycle)} → {cycle[0]}")
    if orphan_files:
        stats.append("\n  Orphan files:")
        for f in sorted(orphan_files)[:10]:
            stats.append(f"    {f}")
    return "\n".join(stats)


def handle_impact_analysis(args):
    path = args.get("path", "")
    if not path:
        return "Error: Parameter 'path' wajib diisi."
    if not os.path.isfile(path):
        return f"Error: File tidak ditemukan: {path}"

    path = os.path.abspath(path)
    root_dir = _find_project_root_deps(path)
    rel = os.path.relpath(path, root_dir)

    file_graph = _build_file_graph(root_dir)
    if rel not in file_graph:
        return f"File {rel} tidak ditemukan di dependency graph (mungkin dalam direktori yang di-exclude atau >500KB)."

    direct, indirect = _compute_dependants(file_graph, rel, transitive=True)

    lines = [
        f"Impact Analysis: {rel}",
        f"  Root: {root_dir}",
        "",
    ]

    if direct:
        lines.append(f"  ⚡ LANGSUNG: {len(direct)} file mengimport file ini:")
        for d in direct:
            lines.append(f"     → {d}")
    else:
        lines.append("  Tidak ada file lain yang mengimport file ini secara langsung.")

    if indirect:
        lines.append(f"  ⚡ TRANSITIVE: {len(indirect)} file terpengaruh secara tidak langsung")
        for t in indirect[:10]:
            lines.append(f"     → {t}")
        if len(indirect) > 10:
            lines.append(f"     ... dan {len(indirect)-10} lainnya")

    lines.append("")
    lines.append(f"  Total file perlu dicek ulang: {len(direct) + len(indirect)}")
    return "\n".join(lines)


def _find_circular_deps(graph):
    cycles = []
    visited = set()
    path = []

    def dfs(node):
        if node in path:
            cycle = path[path.index(node):]
            if len(cycle) >= 2 and len(set(cycle)) == len(cycle):
                cycles.append(cycle)
            return
        if node in visited:
            return
        visited.add(node)
        path.append(node)
        for dep in graph.get(node, {}).get("resolved", []):
            if dep in graph:
                dfs(dep)
        path.pop()

    for node in graph:
        dfs(node)

    # Kanonikkan: rotasi setiap cycle agar mulai dari node terkecil, lalu dedup.
    canonical = set()
    for cycle in cycles:
        if not cycle:
            continue
        min_idx = min(range(len(cycle)), key=lambda i: cycle[i])
        canonical.add(tuple(cycle[min_idx:] + cycle[:min_idx]))
    return sorted(canonical)


def _find_project_root_deps(file_path):
    path = os.path.abspath(file_path)
    markers = [".git", "package.json", "pyproject.toml", "go.mod", "Cargo.toml",
               "pom.xml", "build.gradle", "composer.json", "Gemfile",
               ".project", "Makefile", "CMakeLists.txt", "requirements.txt",
               "setup.py", "setup.cfg"]
    current = os.path.dirname(path)
    while current and current != "/":
        if any(os.path.exists(os.path.join(current, m)) for m in markers):
            return current
        parent = os.path.dirname(current)
        if parent == current:
            break
        current = parent
    return os.path.dirname(path)
