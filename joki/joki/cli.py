import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
from datetime import datetime

from prompt_toolkit import PromptSession
from prompt_toolkit.completion import Completer, Completion, PathCompleter
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.history import FileHistory
from prompt_toolkit.key_binding import KeyBindings
from rich.markup import escape

from joki.config import (
    _CONFIG_PATH,
    _MODELS,
    _current_model_config,
    _get_data_dir,
    _load_models,
)
from joki.constants import *
from joki.display import *
from joki.executor import *
from joki.llm import *
from joki.plugins import _load_plugins
from joki.project_index import (
    _build_index_text,
    get_project_index,
    refresh_project_index,
)
from joki.state import *
from joki.tools.files import _file_signature
from joki.tools.lsp import (
    _cleanup_lsp,
    _is_error_query,
    get_project_diagnostics,
    handle_install_lsp_command,
)
from joki.tools.memory import _load_memory
from joki.tools.shell import _close_shell

_TOOL_FUNC_NAMES = {t["function"]["name"] for t in TOOLS}

_SYSTEM_PROMPT_BASE = (
    "Kamu adalah Joki — AI agent yang dibuat oleh Rahmad Budiman. "
    "Hanya jika ditanya siapa yang membuatmu, jawab: 'Saya dibuat oleh Rahmad Budiman.' "
    "Jangan sebut ini di setiap respons.\n\n"
    
    "ATURAN UTAMA — PATUHI ATAU KAMU DIHENTIKAN:\n"
    "1. KERJAKAN SATU TUGAS SAMPAI SELESAI. Jangan ngelantur ke hal lain. Jangan ngasih saran yang gak diminta.\n"
    "2. LANGSUNG KERJA — Jangan cuma ngomong 'saya akan...' KALAU BISA LANGSUNG, KERJAKAN. "
    "Kalo perlu info, baca dulu (read_file, run_command). Kalo tahu apa yang harus dilakukan, langsung lakukan.\n"
    "3. KALAU ERROR, ANALISA DULU — Baca error, cari akar masalah, baru perbaiki. "
    "Jangan tebak-tebak. Jangan ulang perbaikan yang sama kalo udah gagal.\n"
    "4. KALAU BUNTU (3x gagal berturut-turut), LAPORKAN KE USER — Jangan loop sendiri. "
    "Beri tahu apa yang udah dicoba dan minta petunjuk.\n"
    "5. JAWAB SINGKAT, PADAT, TIDAK BERLEBIH — Gak perlu basa-basi. Langsung ke inti.\n"
    "6. SELESAI? VERIFIKASI — Cek hasilnya dengan perintah read-only sebelum lapor.\n"
    "7. PRIORITAS TOOL — KALAU BENERIN KODE: baca file dulu (read_file), lalu edit pake edit_file atau write_file. "
    "JANGAN PERNAH pake run_command buat ngedit file (sed/echo/awk redirect). "
    "run_command cuma buat: ngetes kode, install dependencies, build, atau jalanin service. "
    "JANGAN pake sudo kalo lagi benerin kode biasa — sudo cuma buat urusan sistem (install package, restart service, dll).\n\n"
    
    "MODE TUGAS (otomatis):\n"
    "  - CODING: file tool (read_file/edit_file/write_file) + run_command (hanya untuk test/build/install) + web search. "
    "DILARANG: run_command untuk edit file, sudo untuk kode biasa.\n"
    "  - SYSADMIN: service_control + config_edit + security tools\n"
    "  - SECURITY: scanning tools (port_scan, web_vuln_scan, dll)\n"
    "  - GENERAL: semua tool\n\n"
    
    "WORKFLOW — BACA DULU, IKUTI SESUAI TUGAS:\n"
    "\n"
    "=== BUAT KODE BARU / APLIKASI BARU ===\n"
    "  1. [RENCANA] Pahami dulu yang diminta user. Struktur folder apa aja yang bakal dibuat. "
    "Bikin todo_create buat list file-nya.\n"
    "  2. [CEK KONVENSI] Kalo user udah punya project: pake search_code / list_dir buat lihat pola kode yang udah ada "
    "(nama file, struktur folder, framework, library yang dipake). IKUTIN POLA ITU.\n"
    "  3. [INSTALL] run_command buat init project (composer init, npm init, go mod init, django-admin, dll). "
    "Jangan tulis semua dari nol kalo ada generator/scaffold.\n"
    "  4. [BUAT FILE] write_file satu per satu — mulai dari entry point (index.php, main.py, main.go, dll), "
    "terus models, controllers, views/routes, config.\n"
    "  5. [BEST PRACTICE] Ikutin struktur standar framework yang dipake:\n"
    "     - Laravel: routes/web.php, app/Http/Controllers/, resources/views/, database/migrations/\n"
    "     - Django: urls.py, views.py, models.py, templates/, settings.py\n"
    "     - Flask: app.py, routes/, templates/, static/\n"
    "     - Express: routes/, views/, public/, app.js\n"
    "     - Go: cmd/, internal/, pkg/, go.mod\n"
    "     - React/Vue: src/components/, src/pages/, src/utils/\n"
    "     - Node/TS: src/, dist/, package.json, tsconfig.json\n"
    "  6. [DEPENDENSI] run_command buat install dependencies (npm install, pip install, composer install, go mod tidy).\n"
    "  7. [TEST] run_command buat jalanin aplikasi / test — pastikan gak error.\n"
    "  8. [SELESAI] Kasih tau user file apa aja yang dibuat dan cara jalaninnya.\n"
    "  LARANGAN: JANGAN write_file semua file sekaligus — tulis satu per satu biar auto-test jalan.\n"
    "  JANGAN lanjut ke step 6/7/8 kalo file inti (step 4) belum lengkap.\n\n"
    
    "=== BENERIN / NGEDIT KODE ===\n"
    "  1. read_file(path) — baca file yang bermasalah.\n"
    "  2. edit_file(path, old_text, new_text) — langsung perbaiki. "
    "old_text harus UNIK (min 3-5 baris konteks) biar gak salah match.\n"
    "  3. run_command — test hasilnya (python3 script.py, npm run dev, php artisan test, dll).\n"
    "  MAKS 3 KALI EDIT PERCUMAAN. Kalo 3x masih gagal: BERHENTI, lapor ke user.\n"
    "  LARANGAN: JANGAN baca file berulang-ulang tanpa ngedit. JANGAN panggil tools yang gak relevan "
    "(screenshot, port_scan, usb_list, dll) pas benerin kode.\n\n"
    
    "SEBELUM KERJA: buat rencana 2-3 langkah pake todo_create, tandai selesai pake todo_done.\n"
    "KALO SELESAI: ringkas hasil kerja dalam 2-3 kalimat di bahasa Indonesia.\n"
    "FILE SEMENTARA: simpan di /tmp, hapus kalo udah gak dipake.\n\n"
    
    "PANDUAN TOOL:\n"
    "  read_file / write_file / edit_file / undo_edit / search_code / list_dir / glob\n"
    "  run_command — HANYA untuk: test, build, install, run service. JANGAN untuk edit file!\n"
    "  db_query — mysql/postgres/mongodb/sqlite\n"
    "  service_control — start/stop/restart/status (restart otomatis divalidasi)\n"
    "  config_edit — edit file konfigurasi sistem (backup otomatis)\n"
    "  test_and_fix — jalanin script, error difeedback biar difix\n"
    "  package_check / web_fetch / web_search\n"
    "  memory_store / memory_recall / memory_forget\n"
    "  screenshot — bukti visual\n"
    "  port_scan / dns_enum / web_vuln_scan / whois_lookup / ssl_check\n"
    "  dir_bruteforce / cve_search / tech_detect / js_analyze\n"
    "  api_discover / source_map_check / form_analyze\n"
    "  apk_analyze / binary_analyze\n"
     "  todo_create / todo_done / todo_show\n"
    "  git_status / git_diff / git_log / git_commit / git_push / git_pull / git_branch\n"
    "  git_clone / git_init / git_add / git_merge / git_stash / git_remote\n"
    "  run_linter / lint_install / run_tests\n\n"
    
    "BEST PRACTICE PER BAHASA — PATUHI SETIAP KALI NULIS KODE:\n\n"
    
    "=== PYTHON ===\n"
    "  - PEP 8: snake_case untuk variable/fungsi, PascalCase untuk class, UPPER_CASE untuk konstanta.\n"
    "  - Type hints WAJIB untuk function signature (def foo(x: int) -> str:).\n"
    "  - Docstrings: gunakan triple quotes, Google style untuk fungsi publik.\n"
    "  - Project structure:\n"
    "      myproject/\n"
    "        src/myproject/__init__.py, main.py, config.py, models/, services/, utils/\n"
    "        tests/test_*.py (pytest)\n"
    "        pyproject.toml atau setup.py\n"
    "  - Gunakan pathlib.Path, bukan os.path.\n"
    "  - Context manager (with) untuk file, koneksi DB, lock.\n"
    "  - logging module, bukan print(), untuk production.\n"
    "  - Pytest fixtures, bukan setUp/tearDown.\n"
    "  - prefer @dataclass atau Pydantic untuk data containers.\n"
    "  - Jangan gunakan bare except: — tangkap exception spesifik.\n"
    "  - Async: gunakan asyncio + httpx untuk I/O paralel.\n\n"
    
    "=== JAVASCRIPT / TYPESCRIPT ===\n"
    "  - ES6+: const/let (gak pernah var), arrow functions, destructuring, spread, template literals.\n"
    "  - TypeScript: strict mode, interface over type untuk object shapes, gunakan enum untuk konstanta.\n"
    "  - Naming: camelCase untuk variable/fungsi, PascalCase untuk class/interface/types.\n"
    "  - Async: async/await (bukan .then()), Promise.all() untuk paralel.\n"
    "  - Error handling: jangan swallow errors — always .catch() atau try/catch.\n"
    "  - Project structure:\n"
    "      src/\n"
    "        components/ (React), pages/, hooks/, utils/, services/\n"
    "        types/ (TypeScript), constants/\n"
    "      __tests__/ atau *.test.ts (Jest)\n"
    "  - package.json: scripts untuk dev, build, test, lint.\n"
    "  - ESLint + Prettier untuk konsistensi.\n"
    "  - Gunakan nullish coalescing (??) dan optional chaining (?.)\n"
    "  - Jangan gunakan any di TypeScript — gunakan unknown kalo gak yakin tipenya.\n\n"
    
    "=== GO ===\n"
    "  - Idiomatic: gofmt, lowercase untuk unexported, PascalCase untuk exported.\n"
    "  - Error handling: selalu cek error, jangan pernah ignore (kecuali func khusus).\n"
    "  - Project structure:\n"
    "      myproject/\n"
    "        cmd/myproject/main.go\n"
    "        internal/ (private code), pkg/ (public library)\n"
    "        api/, handlers/, models/, repository/, service/\n"
    "        go.mod, go.sum\n"
    "  - Testing: *_test.go, table-driven tests, go test ./...\n"
    "  - Interfaces kecil (1-2 methods), jangan generic interfaces.\n"
    "  - Konteks: context.Context sebagai parameter pertama fungsi yang butuh cancellation/deadline.\n"
    "  - Struct tags untuk serialization (json:\"field_name\").\n"
    "  - sync.Mutex atau channel untuk concurrency — jangan shared memory.\n"
    "  - go mod tidy setelah setiap perubahan dependency.\n\n"
    
    "=== PHP (LARAVEL) ===\n"
    "  - PSR-4 autoloading, PSR-2/PSR-12 coding style.\n"
    "  - camelCase untuk method/variable, PascalCase untuk class.\n"
    "  - Laravel: Ikutin konvensi:\n"
    "      routes/web.php atau api.php\n"
    "      app/Http/Controllers/\n"
    "      app/Models/ (Eloquent)\n"
    "      app/Http/Requests/ (Form Request untuk validasi)\n"
    "      app/Services/ (business logic)\n"
    "      database/migrations/, database/seeders/\n"
    "      resources/views/ (Blade)\n"
    "  - Gunakan Eloquent ORM, jangan raw SQL (kecuali performa kritis).\n"
    "  - Validasi di Form Request, bukan di controller.\n"
    "  - Queue untuk task berat (email, export).\n"
    "  - PHPUnit untuk testing, Factory + Seeder untuk test data.\n"
    "  - Jangan gunakan dd()/dump() di kode production.\n\n"
    
    "=== RUST ===\n"
    "  - cargo fmt, cargo clippy untuk lint.\n"
    "  - Result<T, E> untuk error handling, ? operator untuk propagate.\n"
    "  - Struct + impl, bukan class.\n"
    "  - Trait untuk shared behavior.\n"
    "  - Project: Cargo.toml, src/main.rs atau src/lib.rs\n"
    "  - Testing: #[cfg(test)] module, #[test] attribute.\n"
    "  - Gunakan Option<T> untuk nullable values, bukan null.\n"
    "  - Clone cuma kalo perlu — prefer reference & borrowing.\n\n"
    
    "=== JAVA ===\n"
    "  - Package naming: com.project.module\n"
    "  - Dependency injection (Spring @Autowired, @Inject).\n"
    "  - Lombok (@Data, @Builder, @Slf4j) untuk boilerplate.\n"
    "  - Project: Maven (pom.xml) atau Gradle (build.gradle).\n"
    "  - Testing: JUnit 5 + Mockito.\n"
    "  - Stream API + lambda untuk collection operations.\n"
    "  - Optional<T> untuk nullable return values.\n\n"
    
    "=== RUBY (RAILS) ===\n"
    "  - Snake case untuk method/variable, CamelCase untuk class/module.\n"
    "  - Rails conventions: MVC, ActiveRecord, migrations.\n"
    "  - Fat model, thin controller — atau Service Objects untuk logika kompleks.\n"
    "  - RSpec untuk testing, FactoryBot untuk test data.\n"
    "  - RuboCop untuk lint.\n"
    "  - Gunakan gems yang mature daripada reinvent the wheel.\n\n"
    
    "=== C# (.NET) ===\n"
    "  - PascalCase untuk semua (class, method, property, public field).\n"
    "  - async Task<T> untuk async operations.\n"
    "  - LINQ untuk query collections.\n"
    "  - Dependency injection via constructor.\n"
    "  - xUnit atau NUnit untuk testing.\n"
    "  - Nullable reference types (enable di csproj).\n\n"
    
    "=== REACT ===\n"
    "  - Functional components + Hooks (bukan class components).\n"
    "  - useState, useEffect, useCallback, useMemo dengan benar.\n"
    "  - Custom hooks untuk reusable logic.\n"
    "  - Props interface/type untuk setiap component.\n"
    "  - Jangan gunakan any untuk props — selalu define type.\n"
    "  - Gunakan React Query (TanStack Query) untuk server state.\n"
    "  - Zustand atau Context untuk client state.\n"
    "  - Testing: React Testing Library + Jest.\n"
    "  - Tailwind CSS atau CSS Modules untuk styling.\n\n"
    
    "=== VUE.JS ===\n"
    "  - Composition API (setup script) + <script setup lang=\"ts\">.\n"
    "  - Pinia untuk state management.\n"
    "  - Vue Router untuk routing.\n"
    "  - Single File Component (.vue): template, script, style scoped.\n"
    "  - Gunakan composables untuk reusable logic.\n"
    "  - Vitest + @vue/test-utils untuk testing.\n\n"
    
    "=== DATABASE ===\n"
    "  - Index untuk kolom yang sering di-query (WHERE, JOIN, ORDER BY).\n"
    "  - Foreign key constraints untuk referential integrity.\n"
    "  - Migration untuk perubahan schema (jangan manual ALTER TABLE).\n"
    "  - Prepared statements / ORM untuk hindari SQL injection.\n"
    "  - N+1 query: waspada, gunakan eager loading (JOIN, select_related, prefetch_related).\n"
    "  - Transaction untuk operasi multi-step.\n"
    "  - Connection pooling di production.\n\n"
    
    "=== GIT ===\n"
    "  - Commit message: imperative, singkat (<72 chars), jelaskan WHY bukan WHAT.\n"
    "  - Satu commit untuk satu logical change.\n"
    "  - Branch: feature/xxx, fix/xxx, chore/xxx.\n"
    "  - Pull/Diff dulu sebelum commit untuk review perubahan.\n"
    "  - Jangan commit file besar, node_modules, .env, __pycache__ — pastikan .gitignore.\n\n"
    
    "EDIT FILE:\n"
    "  - WAJIB: read_file DULU sebelum edit_file\n"
    "  - read_file(path, offset, limit) untuk baca sebagian, force_full=True untuk baca file utuh\n"
    "  - edit_file: old_text (3-5 baris konteks) + new_text\n"
    "  - write_file untuk file baru / overwrite\n"
    "  - config_edit untuk file konfigurasi (/etc/, dll) — backup otomatis\n"
    "  - undo_edit untuk balikin perubahan terakhir\n\n"
    
    "AUTO-TEST:\n"
    "  Setelah write_file, sistem auto-jalankan script. Kalo gagal: analisa, fix, ulangi. "
    "Maks 5 percobaan. Kalo mentok, lapor ke user.\n\n"
    
    "LINT SEBELUM COMMIT:\n"
    "  Sebelum git_commit, jalankan run_linter untuk deteksi masalah lebih awal.\n\n"
    
    "TESTING:\n"
    "  - run_tests(framework='auto') untuk jalanin semua test.\n"
    "  - TDD untuk logika kritis: test dulu → implementasi → verifikasi.\n\n"
    
    "AUTO WEB SEARCH:\n"
    "  - Kalau user nanya info terkini (berita, harga, versi terbaru, tahun 2025/2026, \"tahun ini\", \"baru-baru ini\"), "
    "WAJIB panggil web_search dulu sebelum jawab. Jangan tebak data yang mungkin berubah.\n"
    "  - Kalau ada error code / stack trace yang asing, cari solusi di web dulu.\n"
    "  - web_search(query=...) untuk cari, web_fetch(url=...) untuk ambil detail halaman.\n\n"
    
    "SEBELUM REFACTORING BESAR:\n"
    "  Panggil analyze_deps(path) dulu untuk lihat dependency graph, "
    "lalu impact_analysis(path) untuk lihat file yang terpengaruh.\n"
    "  Ini penting biar gak ada file yang kelewat pas refactoring.\n\n"
    
    "LSP CODE ACTIONS:\n"
    "  lsp_query(operation='codeActions', file_path=..., line=..., character=...) untuk lihat quickfix/refactor yang tersedia.\n"
    "  lsp_query(operation='format', file_path=...) untuk format otomatis file.\n\n"
    
    "CODE REVIEW SETELAH MENULIS KODE:\n"
    "  Cek: logic errors, security (injection, XSS, CSRF), edge cases, error handling, "
    "code style, duplicate code, performance. Kalo nemu masalah, perbaiki langsung.\n\n"
    
    "Tool calls: KIRIM sebagai struktur data fungsi (tool_calls API), BUKAN ditulis manual sebagai teks.\n"
    "Kalo user minta sesuatu yang gak jelas, tanya balik — jangan ditebak.\n"
)

def _classify_task(user_message):
    msg_lower = user_message.lower()

    code_keywords = [
        "buat", "bikin", "coding", "tulis", "program", "implementasi",
        "feature", "bug", "refactor", "fungsi", "class", "module",
        "script", "kode", "program", "aplikasi", "website",
        "php", "python", "javascript", "js", "typescript", "ts",
        "go", "rust", "java", "html", "css", "vue", "react",
        "database", "api", "endpoint", "rest", "graphql",
        "index.php", "index.html", "app.py", "main.py",
    ]
    sysadmin_keywords = [
        "setting", "konfigurasi", "deploy", "install", "uninstall",
        "service", "server", "apache2", "apache", "nginx",
        "config", "firewall", "ssl", "tls", "certificate",
        "dns", "domain", "vhost", "virtual host",
        "systemctl", "restart", "start", "stop", "enable",
        "firewall", "ufw", "iptables", "port",
        "docker", "container", "kubernetes", "k8s",
        "monitoring", "backup", "restore", "migrate",
        "performance", "optimasi", "tuning",
    ]
    security_keywords = [
        "scan", "hack", "exploit", "vulnerability", "port scan",
        "cve", "recon", "reconnaissance", "penetration",
        "pentest", "security", "audit", "malware",
        "reverse engineering", "reverse", "forensic",
        "bruteforce", "brute force", "dns enum",
        "sql injection", "sqli", "xss", "csrf",
        "whois", "source map", "js analyze",
        "nmap", "keamanan", "celah", "retas",
    ]

    msg_lower = user_message.lower()

    code_score = sum(1 for kw in code_keywords if kw in msg_lower)
    sysadmin_score = sum(1 for kw in sysadmin_keywords if kw in msg_lower)
    security_score = sum(1 for kw in security_keywords if kw in msg_lower)

    if security_score >= 1 and security_score >= sysadmin_score and security_score >= code_score:
        return MODE_SECURITY
    if sysadmin_score > code_score and sysadmin_score >= 2:
        return MODE_SYSADMIN
    if code_score >= 1:
        return MODE_CODE
    if sysadmin_score >= 1:
        return MODE_SYSADMIN
    return MODE_GENERAL


def agent_loop(messages, extra=True):
    _joki_cancel.clear()
    _tool_text_retries = 0
    _content_history = []
    _call_history = []
    _should_stop = False
    _iteration = 0
    _dry_tool_count = 0
    _dry_inject_count = 0
    _output_history = []
    _tool_methods_history = []
    _checkin_injected = False
    _auto_test_failed = False
    _exit_reason = "unknown"
    _exit_traceback = ""

    def _log_exit_reason():
        """Catat alasan agent_loop berhenti ke ~/.local/share/joki/agent_exit.log."""
        try:
            from datetime import datetime
            last_tools = "; ".join(f"{t}({a})" for t, a in _tool_methods_history[-5:])
            tb_line = _exit_traceback.strip().replace("\n", " | ").replace("  ", " ")
            line = (
                f"{datetime.now().astimezone().isoformat()} | {_CURRENT_SESSION} | "
                f"iter={_iteration} | model={_current_model_config.get('model', '?')} | "
                f"mode={_current_task_mode} | reason={_exit_reason} | "
                f"last_tools=[{last_tools}] | tb={tb_line}"
            )
            log_path = os.path.join(_get_data_dir(), "agent_exit.log")
            os.makedirs(os.path.dirname(log_path), exist_ok=True)
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception:  # noqa: BLE001, S110
            pass

    # === CONTEXT MANAGER — cegah LLM lupa konteks ===
    _task_context = {
        "original_request": "",
        "files_created": [],
        "files_modified": [],
        "current_step": "",
        "progress_notes": [],
        "last_context_inject": 0,
        "decisions": [],
        "facts": [],
    }
    # Ambil original request dari user message pertama
    for m in reversed(messages):
        if m.get("role") == "user":
            _task_context["original_request"] = (m.get("content") or "")[:500]
            break

    def _inject_project_index(msgs, force=False):
        """Inject peta struktur proyek sebagai pesan system (idempotent)."""
        try:
            root = os.getcwd()
            idx = get_project_index(root, force=force)
            idx_text = _build_index_text(idx)
            if not idx_text:
                return
            for i, m in enumerate(msgs):
                if m.get("role") == "system" and (m.get("content") or "").startswith("[INDEX PROYEK]"):
                    if force or m.get("content") != idx_text:
                        m["content"] = idx_text
                    return
            msgs.insert(1, {"role": "system", "content": idx_text})
        except (OSError, ValueError, KeyError, IndexError, TypeError, AttributeError) as _e:
            _console.print(f"[dim]Project index skip: {escape(str(_e))}[/dim]")

    def _build_checkpoint():
        ctx = _task_context
        parts = ["[CHECKPOINT TUGAS]"]
        parts.append(f"Tujuan: {ctx['original_request'][:200]}")
        if ctx["current_step"]:
            parts.append(f"Langkah saat ini: {ctx['current_step']}")
        if ctx["decisions"]:
            parts.append(f"Keputusan: {'; '.join(ctx['decisions'][-8:])}")
        if ctx["facts"]:
            parts.append(f"Fakta: {'; '.join(ctx['facts'][-10:])}")
        if ctx["files_created"]:
            parts.append(f"File dibuat: {', '.join(ctx['files_created'][-10:])}")
        if ctx["files_modified"]:
            parts.append(f"File diubah: {', '.join(ctx['files_modified'][-10:])}")
        if ctx["progress_notes"]:
            parts.append(f"Progress: {'; '.join(ctx['progress_notes'][-5:])}")
        parts.append("Gunakan info ini — lanjutkan dari state ini, jangan mulai dari awal.")
        return "\n".join(parts)

    def _update_checkpoint(msgs):
        """Perbarui pesan system checkpoint in-place (tahan summarization karena
        bagian dari prefix system yang dilindungi)."""
        text = _build_checkpoint()
        for i, m in enumerate(msgs):
            if m.get("role") == "system" and (m.get("content") or "").startswith("[CHECKPOINT TUGAS]"):
                if m.get("content") != text:
                    m["content"] = text
                return
        insert_at = 1
        while insert_at < len(msgs) and msgs[insert_at].get("role") == "system":
            insert_at += 1
        msgs.insert(insert_at, {"role": "system", "content": text})

    def _maybe_dedup_read(msgs, args):
        """Bila file yang sama belum berubah & hasil baca penuhnya masih ada di
        konteks, hindari mengirim isi lagi — cukup beri tahu."""
        path = args.get("path") or args.get("file_path", "")
        if not path or args.get("offset") is not None or args.get("limit") is not None or args.get("force_full"):
            return None
        abs_path = os.path.abspath(path)
        if not os.path.isfile(abs_path):
            return None
        sig = _file_signature(abs_path)
        rec = _read_in_context.get(abs_path)
        if not rec or not sig or rec.get("hash") != sig:
            return None
        idx = rec.get("idx")
        if idx is None or idx >= len(msgs):
            return None
        m = msgs[idx]
        if m.get("role") != "tool":
            return None
        head = rec.get("head", "")
        if head and not (m.get("content") or "").startswith(head):
            return None
        return (f"[FILE SUDAH DIKONTEKS] {path} tidak berubah sejak dibaca terakhir — isi masih tersedia di konteks.\n"
                f"Gunakan read_file(path, offset=..., limit=...) untuk bagian tertentu, atau force_full=true untuk memaksa baca ulang.")

    def _notify_changed_files(msgs):
        """Beritahu LLM bila ada file yang berubah; refresh project index."""
        if not _file_change_log:
            return
        paths = sorted(_file_change_log)
        _file_change_log.clear()
        root = os.getcwd()
        rels = []
        for p in paths:
            try:
                rel = os.path.relpath(p, root)
            except ValueError:
                rel = p
            rels.append(rel)
        msgs.append({
            "role": "user",
            "content": (
                "[PERUBAHAN FILE] File berikut berubah (oleh tool/sejak dibaca terakhir):\n"
                + "\n".join(f"- {r}" for r in rels[:15])
                + "\nBaca ulang file yang relevan sebelum melanjutkan — jangan pakai isi lama yang sudah basi."
            )
        })
        try:
            refresh_project_index(root, paths)
            _inject_project_index(msgs, force=True)
        except (OSError, ValueError, KeyError, IndexError, TypeError, AttributeError) as _e:
            _console.print(f"[dim]Project index refresh skip: {escape(str(_e))}[/dim]")

    def _detect_external_changes():
        """Cek hash file yang pernah dibaca untuk mendeteksi perubahan eksternal."""
        for abs_path in list(_file_cache.keys())[:20]:
            sig = _file_signature(abs_path)
            cached = _file_cache.get(abs_path)
            if sig and cached and cached.get("hash") != sig:
                cached["hash"] = sig
                _file_change_log.add(abs_path)

    def _inject_context():
        ctx = _task_context
        parts = ["[KONTEKS TUGAS]"]
        parts.append(f"Tujuan awal: {ctx['original_request'][:200]}")
        if ctx["current_step"]:
            parts.append(f"Langkah saat ini: {ctx['current_step']}")
        if ctx["files_created"]:
            parts.append(f"File dibuat: {', '.join(ctx['files_created'][-10:])}")
        if ctx["files_modified"]:
            parts.append(f"File diubah: {', '.join(ctx['files_modified'][-10:])}")
        if ctx["progress_notes"]:
            notes = ctx["progress_notes"][-5:]
            parts.append(f"Progress: {'; '.join(notes)}")
        parts.append("Gunakan info ini untuk melanjutkan tugas — jangan mulai dari awal.")
        return {"role": "user", "content": "\n".join(parts)}

    # === GLOBAL CANCEL MONITOR (Esc+Esc / Ctrl+C) — monitor bersama dari state.py ===
    _start_cancel_monitor()

    try:
        # === TASK MODE CLASSIFICATION ===
        if messages and len(messages) >= 1:
            last_user_msg = ""
            for m in reversed(messages):
                if m.get("role") == "user":
                    last_user_msg = m.get("content", "")
                    break
            if last_user_msg:
                global _current_task_mode
                mode = _classify_task(last_user_msg)
                _current_task_mode = mode
                mode_labels = {MODE_CODE: "Coding", MODE_SYSADMIN: "Sysadmin", MODE_SECURITY: "Security", MODE_GENERAL: "General"}
                _console.print(f"[dim]Mode tugas: [bold]{mode_labels.get(mode, 'General')}[/bold][/dim]")
        # === END TASK MODE CLASSIFICATION ===

        # === LSP PRE-INJECT ===
        _lsp_injected = False
        if messages:
            last_content = messages[-1].get("content", "") if messages else ""
            if _is_error_query(last_content):
                _console.print("[dim]Mendeteksi permintaan perbaikan error — mengambil diagnostik LSP...[/dim]")
                try:
                    project_dir = os.getcwd()
                    diags = get_project_diagnostics(project_dir)
                    if diags:
                        messages[-1]["content"] += f"\n\n{diags}"
                        _lsp_injected = True
                        _console.print("[dim]Diagnostik LSP berhasil dimuat.[/dim]")
                except Exception as e:  # noqa: BLE001
                    _console.print(f"[dim]LSP pre-inject skip: {escape(str(e))}[/dim]")
        # === END LSP PRE-INJECT ===

        # === WEB SEARCH PRE-INJECT ===
        _web_search_keywords = [
            "terbaru", "tahun ini", "2025", "2026", "tadi", "kemarin",
            "berita", "harga", "update", "versi", "release", "rilis",
            "crypto", "saham", "kurs", "bencana", "pemilu", "presiden",
            "covid", "vaksin", "gempa", "cuaca", "bola", "skor",
            "perang", "konflik", "kebijakan", "undang-undang", "uu",
            "error code", "stack trace", "vulnerability", "cve",
            "npm ", "pip ", "latest", "current", "news",
        ]
        _web_injected = False
        if messages:
            last_user = ""
            for m in reversed(messages):
                if m.get("role") == "user":
                    last_user = (m.get("content") or "").lower()
                    break
            if last_user and any(kw in last_user for kw in _web_search_keywords):
                _console.print("[dim]Mendeteksi permintaan info terkini — inject pengingat web search.[/dim]")
                messages.append({
                    "role": "user",
                    "content": (
                        "[PENGINGAT] Pertanyaan di atas kemungkinan butuh informasi terkini "
                        "yang mungkin tidak ada di pengetahuan offline-mu. "
                        "WAJIB gunakan web_search(query=...) untuk mencari informasi terkini "
                        "sebelum memberikan jawaban. Jangan mengarang data."
                    )
                })
                _web_injected = True
        # === END WEB SEARCH PRE-INJECT ===

        # === PROJECT INDEX PRE-INJECT (peta struktur proyek) ===
        _inject_project_index(messages)
        _update_checkpoint(messages)
        # === END PROJECT INDEX PRE-INJECT ===

        while not _should_stop:
            _iteration += 1
            if _joki_cancel.is_set():
                _stop_cancel_monitor()
                _console.print(f"[bold {_color_warn()}]Dibatalkan oleh pengguna (Esc Esc).[/bold {_color_warn()}]")
                _exit_reason = "cancel (Esc Esc)"
                return

            # === context refresh: checkpoint + notifikasi perubahan file ===
            _update_checkpoint(messages)
            if _iteration % 5 == 0:
                _detect_external_changes()
            _notify_changed_files(messages)
            # === end context refresh ===

            # === exhaustion safety nets ===
            _max_iter = _current_model_config.get("max_iterations", 0)
            if _max_iter > 0 and _iteration > _max_iter:
                stream_print(f"[{_color_warn()}]\u26a0 Batas iterasi ({_max_iter}) tercapai — memaksa respons teks.[/{_color_warn()}]")
                messages.append({
                    "role": "user",
                    "content": (
                        f"[ITERATION LIMIT] Kamu sudah mencapai batas iterasi ({_max_iter}). "
                        f"Berikan jawaban TEXT saja — jangan panggil tool apapun. "
                        f"Jelaskan progress yang sudah dicapai dan langkah selanjutnya ke user."
                    )
                })
                msg = call_llm(messages)
                if msg.get("content", ""):
                    print()
                    stream_print(msg["content"])
                _exit_reason = f"iteration limit ({_max_iter})"
                _should_stop = True
                break

            if _iteration >= 120:
                stream_print(f"[{_color_error()}]\u26a0 120 iterasi — hard stop.[/{_color_error()}]")
                messages.append({
                    "role": "user",
                    "content": (
                        "[FINAL STOP] Kamu sudah 120x berpikir tanpa menyelesaikan task. "
                        "Berhenti. Berikan ringkasan progress ke user dan tanya petunjuk lanjutan."
                    )
                })
                msg = call_llm(messages)
                if msg.get("content", ""):
                    print()
                    stream_print(msg["content"])
                _exit_reason = "hard stop (120 iterasi)"
                _should_stop = True
                break

            if _iteration >= 80 and not _checkin_injected:
                _checkin_injected = True
                stream_print(f"[{_color_warn()}]\u26a0 80 iterasi — inject check-in.[/{_color_warn()}]")
                _dry_inject_count = max(_dry_inject_count, 1)
                messages.append({
                    "role": "user",
                    "content": (
                        "[CHECK-IN] Kamu sudah 80x berpikir. Sepertinya task ini kompleks. "
                        "Berikan progress report singkat ke user tentang apa yang sudah dikerjakan "
                        "dan tanya apakah mau dilanjutkan atau diubah pendekatannya."
                    )
                })

            msg = call_llm(messages)
            messages.append(msg)

            # Track LLM's current step dari respons teksnya
            reply = (msg.get("content") or "").strip()
            if reply and len(reply) > 10 and len(reply) < 200:
                _task_context["current_step"] = reply[:150]
            elif msg.get("tool_calls"):
                tc_name = msg["tool_calls"][0]["function"]["name"]
                _task_context["current_step"] = f"memanggil {tc_name}"

            if _joki_cancel.is_set():
                _stop_cancel_monitor()
                _console.print(f"[bold {_color_warn()}]Dibatalkan oleh pengguna (Esc Esc).[/bold {_color_warn()}]")
                _exit_reason = "cancel (Esc Esc)"
                return

            content = (msg.get("content") or "")
            if content.startswith("[CANCELLED]"):
                _stop_cancel_monitor()
                _console.print(f"[bold {_color_warn()}]Dibatalkan oleh pengguna.[/bold {_color_warn()}]")
                _exit_reason = "cancel (LLM: [CANCELLED])"
                return

            # === DRY TOOL DETECTOR ===
            if msg.get("tool_calls") and not content.strip():
                _dry_tool_count += 1
            else:
                _dry_tool_count = 0
                _dry_inject_count = 0
                _tool_methods_history = []

            _dry_threshold = _current_model_config.get("dry_threshold", 12)

            if _dry_tool_count >= 36:
                stream_print(f"[{_color_error()}]\u26a0 Dry run 36x — final stop.[/{_color_error()}]")
                _exit_reason = "dry run final stop (36x)"
                _should_stop = True
                break

            elif _dry_tool_count >= 24 and _dry_inject_count < 2:
                _dry_inject_count += 1
                stream_print(f"[{_color_warn()}]\u26a0 Dry run {_dry_tool_count}x — inject tanya user.[/{_color_warn()}]")
                methods_summary = "; ".join(f"{t}({a})" for t, a in _tool_methods_history[-5:])
                messages.append({
                    "role": "user",
                    "content": (
                        f"[DRY RUN] Kamu sudah {_dry_tool_count}x manggil tool tanpa ngasih solusi. "
                        f"Metode terakhir: {methods_summary}. "
                        f"Coba pendekatan yang BERBEDA atau tanya user buat petunjuk."
                    )
                })

            elif _dry_tool_count >= _dry_threshold and _dry_inject_count < 1:
                _dry_inject_count += 1
                stream_print(f"[{_color_warn()}]\u26a0 Dry run {_dry_tool_count}x — inject ganti strategi.[/{_color_warn()}]")
                methods_summary = "; ".join(f"{t}({a})" for t, a in _tool_methods_history[-5:])
                messages.append({
                    "role": "user",
                    "content": (
                        f"[STRATEGI] Kamu sudah {_dry_tool_count}x manggil tool tanpa solusi. "
                        f"Metode yang udah dicoba: {methods_summary}. "
                        f"Coba pendekatan yang BERBEDA — evaluasi apa yang udah dicoba "
                        f"dan lakukan sesuatu yang BELUM dicoba."
                    )
                })

            # Show pre-tool-call thinking in blue panel
            if content and msg.get("tool_calls"):
                _console.print(_joki_card(content, "\U0001f9e0 Joki Berpikir", "blue"))

            if msg.get("tool_calls"):
                _auto_test_failed = False
                for tc in msg["tool_calls"]:
                    name = tc["function"]["name"]
                    raw = tc["function"]["arguments"]
                    try:
                        args = json.loads(raw) if isinstance(raw, str) else raw
                    except json.JSONDecodeError:
                        args = {"_raw": str(raw)}
                    if not isinstance(args, dict):
                        args = {"_raw": str(args)}

                    if name == "run_command":
                        detail = args.get("cmd", "")
                    elif name in ("read_file", "write_file", "edit_file", "undo_edit", "list_dir", "config_edit"):
                        detail = args.get("path", "")
                    elif name == "db_query":
                        detail = args.get("query", "")[:60]
                    elif name == "web_search":
                        detail = args.get("query", "")
                    elif name == "search_code":
                        detail = args.get("pattern", "")
                    elif name == "service_control":
                        detail = f"{args.get('action')} {args.get('service')}"
                    elif name == "package_check":
                        detail = args.get("app", "")
                    elif name == "web_fetch":
                        detail = args.get("url", "")
                    elif name == "test_and_fix":
                        detail = args.get("cmd", "")
                    elif name in ("memory_store", "memory_recall", "memory_forget"):
                        detail = args.get("key", "")
                    elif name == "screenshot":
                        detail = args.get("path", "(auto)")
                    elif name == "port_scan":
                        detail = f"{args.get('target')} ports:{args.get('ports','common')}"
                    elif name == "dns_enum":
                        detail = f"{args.get('domain')} {args.get('action','records')}"
                    elif name == "web_vuln_scan":
                        detail = f"{args.get('url')} {args.get('checks','headers,info')}"
                    elif name == "whois_lookup":
                        detail = args.get("target", "")
                    elif name == "ssl_check":
                        detail = f"{args.get('host')}:{args.get('port',443)}"
                    elif name == "dir_bruteforce":
                        detail = f"{args.get('url')} wordlist:{args.get('wordlist','small')}"
                    elif name == "cve_search":
                        detail = args.get("query", "")
                    elif name == "tech_detect":
                        detail = f"{args.get('url')} {args.get('deep','simple')}"
                    elif name == "js_analyze":
                        detail = f"{args.get('url')} {args.get('extract','all')}"
                    elif name == "api_discover":
                        detail = f"{args.get('url')} depth:{args.get('depth',2)}"
                    elif name == "source_map_check" or name == "form_analyze":
                        detail = args.get("url", "")
                    elif name == "apk_analyze" or name == "binary_analyze":
                        detail = args.get("path", "")
                    elif name == "todo_create":
                        detail = f"{len(args.get('items', []))} items"
                    elif name == "todo_done":
                        detail = f"item {args.get('indices', [])}"
                    elif name == "todo_show":
                        detail = ""
                    elif name in ("ui_screenshot", "ui_click", "ui_type", "ui_keypress", "ui_focus"):
                        detail = json.dumps(args)
                    elif name == "usb_list":
                        detail = "USB devices"
                    elif name == "serial_send":
                        detail = f"{args.get('port')}: {args.get('data','')[:60]}"
                    elif name == "camera_capture":
                        detail = args.get("device", "/dev/video0")
                    elif name == "sandbox_run":
                        detail = f"{args.get('interpreter','auto')} — {args.get('code','')[:80]}"
                    elif name == "predict_command":
                        detail = args.get("cmd", "")[:80]
                    elif name in ("audio_info", "audio_transcribe", "video_info", "video_extract"):
                        detail = args.get("path", "")
                    elif name.startswith("git_"):
                        detail = json.dumps(args)
                    elif name == "run_linter":
                        detail = args.get("path", ".") + (" --fix" if args.get("fix") else "")
                    elif name == "lint_install":
                        detail = args.get("lang", "(all)") if args.get("lang") else "(status)"
                    elif name == "run_tests":
                        detail = f"{args.get('framework','auto')} {args.get('path','.')}"
                    elif name == "analyze_deps":
                        detail = args.get("path", ".")
                    elif name == "impact_analysis":
                        detail = args.get("path", "")
                    else:
                        detail = json.dumps(args)

                    if name == "write_file" and "content" in args:
                        lines = args["content"].splitlines(keepends=True)
                        digits = len(str(len(lines)))
                        for i, l in enumerate(lines):
                            print(f"      {i+1:>{digits}}: {l}", end="", flush=True)
                        if lines:
                            print()
                    elif name == "edit_file":
                        ot = args.get("old_text", "")
                        nt = args.get("new_text", "")
                        if ot and nt:
                            show_edit_diff(ot, nt, args.get("path", ""))

                    if _joki_cancel.is_set():
                        _stop_cancel_monitor()
                        _exit_reason = "cancel (Esc Esc)"
                        return

                    _dedup_read = ""
                    if name == "read_file":
                        _dedup_read = _maybe_dedup_read(messages, args)
                    if _dedup_read:
                        result = _dedup_read
                    else:
                        try:
                            from joki.display import _Spinner
                            _spin_msg = f"Jalankan {name}"
                            if name == "run_command":
                                _spin_cmd = str(args.get("cmd", ""))
                                if len(_spin_cmd) > 100:
                                    _spin_cmd = _spin_cmd[:100] + "..."
                                _spin_msg = f"Menjalankan: {_spin_cmd}"
                            with _Spinner(_spin_msg):
                                result = execute(name, args)
                        except Exception as ex:  # noqa: BLE001
                            result = f"[ERROR] Exception saat mengeksekusi {name}: {ex}"
                    if _joki_cancel.is_set():
                        _stop_cancel_monitor()
                        _exit_reason = "cancel (Esc Esc)"
                        return
                    if result:
                        from joki.rich_display import print_tool_result_rich
                        print_tool_result_rich(name, args, result)
                    _recent_tools.add(name)
                    # Catat hasil baca penuh untuk dedup read berikutnya
                    if name == "read_file" and not _dedup_read and result and not str(result).startswith("[ERROR]"):
                        _rp = args.get("path") or args.get("file_path", "")
                        if _rp and args.get("offset") is None and args.get("limit") is None and not args.get("force_full"):
                            _abs_p = os.path.abspath(_rp)
                            _sig = _file_signature(_abs_p)
                            if _sig:
                                _read_in_context[_abs_p] = {
                                    "hash": _sig,
                                    "idx": len(messages),
                                    "head": str(result)[:80],
                                }
                    messages.append({
                        "role": "tool",
                        "content": (result or "")[:50000],
                        "tool_call_id": tc["id"]
                    })

                    # === PRODUCTIVITY TRACKER ===
                    _tool_methods_history.append((name, detail[:60] if detail else ""))
                    _output_hash = hashlib.md5((result or "").encode()).hexdigest()
                    _is_error = (result or "").startswith("[ERROR]")
                    _output_history.append((name, _output_hash, _is_error))

                    # === CONTEXT TRACKER — cegah LLM lupa ===
                    if name == "write_file" and not _is_error:
                        fpath = args.get("path", "")
                        if fpath and fpath not in _task_context["files_created"]:
                            _task_context["files_created"].append(fpath)
                    elif name == "edit_file" and not _is_error:
                        fpath = args.get("path", "")
                        if fpath and fpath not in _task_context["files_modified"]:
                            _task_context["files_modified"].append(fpath)
                    if name == "run_tests" and not _is_error:
                        _task_context["progress_notes"].append("test berjalan")
                    elif name == "git_commit" and not _is_error:
                        _task_context["progress_notes"].append("commit berhasil")
                    elif name == "run_linter" and not _is_error:
                        _task_context["progress_notes"].append("lint selesai")

                    # === CHECKPOINT: fakta & keputusan singkat untuk working memory ===
                    _ctx_fact = None
                    _ctx_decision = None
                    if name == "read_file" and not _is_error:
                        _ctx_fact = f"read {args.get('path', '')}"
                    elif name in ("write_file", "edit_file") and not _is_error:
                        _ctx_fact = f"{name}: {fpath}"
                    elif name == "run_tests":
                        _ctx_fact = f"run_tests: {'ok' if not _is_error else 'gagal'}"
                    elif name == "run_linter":
                        _ctx_fact = f"run_linter: {'ok' if not _is_error else 'gagal'}"
                    elif name == "git_commit" and not _is_error:
                        _ctx_decision = "commit dibuat"
                    elif name == "analyze_deps":
                        _ctx_fact = "dependency graph dianalisa"
                    elif name == "impact_analysis":
                        _ctx_fact = f"impact analysis: {args.get('path', '')}"
                    if _ctx_fact and _ctx_fact not in _task_context["facts"]:
                        _task_context["facts"].append(_ctx_fact)
                    if _ctx_decision and _ctx_decision not in _task_context["decisions"]:
                        _task_context["decisions"].append(_ctx_decision)
                    for _k in ("facts", "decisions"):
                        if len(_task_context[_k]) > 40:
                            _task_context[_k] = _task_context[_k][-40:]

                    # Inject context summary setiap 10 iterasi
                    if _iteration - _task_context["last_context_inject"] >= 10:
                        _task_context["last_context_inject"] = _iteration
                        ctx_msg = _inject_context()
                        messages.append(ctx_msg)
                        stream_print(f"       \u2003 Konteks di-inject (iterasi {_iteration})")

                    # === UNPRODUCTIVE OUTPUT DETECTOR ===
                    if len(_output_history) >= 8:
                        last8 = _output_history[-8:]
                        names = {t[0] for t in last8}
                        hashes = {t[1] for t in last8}
                        errors = sum(1 for t in last8 if t[2])
                        if len(names) <= 2 and len(hashes) <= 2 and errors >= 4:
                            stream_print(f"[{_color_warn()}]\u26a0 Output tool stagnan — inject strategi baru.[/{_color_warn()}]")
                            _output_history = []
                            meth_summary = "; ".join(f"{t}({a})" for t, a in _tool_methods_history[-4:])
                            messages.append({
                                "role": "user",
                                "content": (
                                    f"[STRATEGI] Kamu sudah memanggil tool yang sama 8x berturut-turut "
                                    f"dan sebagian besar error. Coba pendekatan BERBEDA — "
                                    f"baca ulang task, cek prekondisi, atau gunakan metode yang belum kamu coba. "
                                    f"Terakhir: {meth_summary}"
                                )
                            })

                    # === AUTO-TEST MODULE ===
                    if name == "write_file" and not _joki_cancel.is_set():
                        path = args.get("path", "")
                        content = args.get("content", "")
                        ext = os.path.splitext(path)[1].lower()
                        base = os.path.basename(path)
                        _auto_test_needed = False

                        test_cfg = _detect_test_framework(path, content, ext)

                        if test_cfg:
                            test_cmd, _ = test_cfg
                            if test_cmd.startswith(("python3 ", "node ", "bash ", "ruby ", "php ")):
                                full_cmd = f"{test_cmd} {shlex.quote(path)}"
                            else:
                                full_cmd = test_cmd

                            _is_interactive = any(kw in content.lower() for kw in
                                ["pygame", "tkinter", "turtle", "curses",
                                 "PyQt5", "PyQt6", "PySide", "gi.repository",
                                 "flask", "fastapi", "bottle", "django", "aiohttp",
                                 "sanic", "tornado", "uvicorn", "http.server",
                                 "socketserver", "twisted", "matplotlib"])

                            if _is_interactive:
                                stream_print(f"       \u2728 Auto-test {base} dilewati \u2014 program interaktif")
                            else:
                                for attempt in range(3):
                                    if _joki_cancel.is_set():
                                        break
                                    with _Spinner(f"Auto-test {base} ({attempt+1}/3)"):
                                        rc, output, timed_out = _run_auto_test(full_cmd)
                                    if rc == 0:
                                        stream_print(f"       \u2713 Auto-test {base} BERHASIL ({attempt+1}/3)")
                                        break
                                    elif timed_out:
                                        stream_print(f"       \u23F1 Auto-test {base} timeout \u2014 dilewati")
                                        break
                                    else:
                                        stream_print(f"       \u2717 Auto-test {base} GAGAL ({attempt+1}/3)")
                                        stream_print(f"       ```\n{output[:3000]}\n       ```", delay=0.001)
                                        if attempt < 2:
                                            messages.append({
                                                "role": "user",
                                                "content": f"[AUTO-TEST] {path} gagal test ({attempt+1}/3).\nPerintah: {full_cmd}\nError:\n{output[:4000]}\n\nPERBAIKI file ini."
                                            })
                                            _auto_test_needed = True
                                            break
                        if _auto_test_needed:
                            _auto_test_failed = True
                            break

                # === STUCK DETECTION (setelah for tc loop) ===
                if _auto_test_failed:
                    _call_history.clear()
                    _auto_test_failed = False
                else:
                    for tc in msg.get("tool_calls", []):
                        name = tc["function"]["name"]
                        raw = tc["function"]["arguments"]
                        try:
                            args = json.loads(raw) if isinstance(raw, str) else raw
                        except (json.JSONDecodeError, TypeError):
                            args = {"_raw": str(raw)}
                        if not isinstance(args, dict):
                            args = {"_raw": str(args)}
                        sorted_args = sorted(args.items(), key=lambda x: x[0])
                        args_key = str([(k, str(v)[:80]) for k, v in sorted_args])
                        _call_history.append((name, args_key))

                    if len(_call_history) >= 5:
                        last5 = _call_history[-5:]
                        if all(c[0] == last5[0][0] and c[1] == last5[0][1] for c in last5):
                            stream_print(f"[{_color_warn()}]\u26a0 Terdeteksi loop: tool yang sama dipanggil 5x berturut-turut dengan argumen yang sama.[/{_color_warn()}]")
                            messages.append({
                                "role": "user",
                                "content": (
                                    f"[LOOP DETECTED] Kamu memanggil `{last5[0][0]}` dengan argumen yang sama 5x berturut-turut. "
                                    f"Berhenti. Berikan jawaban TEXT — jelaskan kenapa kamu stuck dan apa yang butuh diubah user."
                                )
                            })
                            _final_msg = call_llm(messages)
                            if _final_msg.get("content", ""):
                                print()
                                stream_print(_final_msg["content"])
                            _exit_reason = "loop detected (tool sama 5x)"
                            _should_stop = True

            else:
                content = (msg.get("content") or "")
                # === CONTENT REPETITION DETECTION ===
                _content_history.append(content.strip())
                if len(_content_history) >= 3:
                    last3 = _content_history[-3:]
                    if all(len(c) < 20 for c in last3) and len(set(last3)) == 1:
                        stream_print(f"[{_color_warn()}]⚠ Terdeteksi repetisi konten pendek yang sama. Jangan cuma bersemangat — kerjakan pakai tool![{_color_warn()}]")
                        messages.append({
                            "role": "user",
                            "content": "Kamu cuma ngulang-ngulang kata yang sama tanpa mengerjakan tugas. HENTIKAN pola ini. Baca task-nya, buat rencana, dan KERJAKAN pakai tool yang tersedia. Jangan cuma teriak-teriak semangat doang."
                        })
                        _content_history = []
                        continue
                # === END CONTENT REPETITION DETECTION ===
                if content and any(re.search(rf'\b{re.escape(name)}\s*\(', content) for name in _TOOL_FUNC_NAMES):
                    _tool_text_retries += 1
                    if _tool_text_retries <= 2:
                        messages.append({
                            "role": "user",
                            "content": "Jangan tulis tool sebagai teks. KIRIMKAN tool_calls yang SEBENARNYA — jangan ditulis manual."
                        })
                        continue
                if not content.strip():
                    messages.append({
                        "role": "user",
                        "content": "Respons kamu kosong. Berikan respons atau panggil tool yang sesuai. Jangan diam saja — kerjakan task-nya."
                    })
                    continue
                if content:
                    stream_print(content, card=f"[bold {_color_info()}]JOKI[/bold {_color_info()}]")
                # Reset stase history — LLM kasih content, berarti babak baru
                _output_history = []
                _tool_methods_history = []
                _call_history = []
                _tool_text_retries = 0
                _exit_reason = "normal (LLM balas teks tanpa tool call)"
                _stop_cancel_monitor()
                return
    except Exception as _agent_exc:
        import traceback as _tb
        _exit_traceback = _tb.format_exc()
        _exit_reason = f"EXCEPTION: {type(_agent_exc).__name__}: {_agent_exc}"
        _console.print(f"[bold {_color_error()}]\u26a0 Agent loop error: {escape(str(_agent_exc))}[/bold {_color_error()}]")
        _console.print(f"[dim]{escape(_exit_traceback.rstrip())}[/dim]")
        raise
    finally:
        _stop_cancel_monitor()
        _log_exit_reason()
    _console.print(f"[dim]Agent loop berakhir: {escape(_exit_reason)}[/dim]")
    stream_print("\n[INFO] Task selesai atau dihentikan.")


# ============================================================
# SLASH COMMANDS
# ============================================================
_SLASH_META = {
    "/model": "ganti model aktif",
    "/baru": "mulai session baru",
    "/keluar": "keluar",
    "/reset_quota": "reset state quota API key",
    "/reload": "reload config.json",
    "/install-lsp": "install LSP server",
}

_PATH_COMPLETER = PathCompleter()


class _JokiCompleter(Completer):
    """Autocomplete prompt_toolkit ala opencode: saat mengetik '/' muncul daftar
    perintah slash, lalu sub-opsi (daftar model) untuk /model. Selain itu lengkapi path."""

    def get_completions(self, document, complete_event):
        text = document.text_before_cursor
        line = text.split("\n")[-1]
        if not line.startswith("/"):
            yield from _PATH_COMPLETER.get_completions(document, complete_event)
            return
        m = re.match(r"^/([^\s]*)(\s+(.*))?$", line)
        if not m:
            return
        token, has_arg, rest = m.group(1), m.group(2), (m.group(3) or "")
        cmd = "/" + token
        if cmd in _SLASH_META and has_arg is not None:
            if cmd == "/model":
                word = rest.split()[-1] if rest else ""
                for key in sorted(_MODELS.keys()):
                    if word and not key.startswith(word):
                        continue
                    name = _MODELS.get(key, {}).get("name", key)
                    yield Completion(key, -len(word), display=f"{key}  —  {name}")
            return
        for c in sorted(_SLASH_META):
            if c.startswith(cmd):
                yield Completion(c + " ", -len(cmd), display=f"{c}  —  {_SLASH_META[c]}")

def _build_system_prompt():
    base = _SYSTEM_PROMPT_BASE
    memories = _load_memory()
    if memories:
        items = "\n".join(f"  - {k}: {v[:120]}" for k, v in memories.items())
        base += f"\n\nMemori tersimpan ({len(memories)}):\n{items}\n\nGunakan memory_recall untuk detail, memory_store untuk menyimpan info baru."
    return base

def _check_update():
    if getattr(sys, "frozen", False):
        return
    try:
        joki_dir = os.path.dirname(os.path.abspath(__file__))
        local = subprocess.run(["git", "rev-parse", "HEAD"], cwd=joki_dir, capture_output=True, text=True, check=False).stdout.strip()
        remote = subprocess.run(["git", "ls-remote", "origin", "HEAD"], cwd=joki_dir, capture_output=True, text=True, check=False).stdout.split()[0]
        if local and remote and local != remote:
            _console.print("[dim]Update tersedia! Jalankan: python joki.py --update[/dim]")
    except Exception:  # noqa: BLE001
        _console.print("[dim]Warning: Gagal check update (bukan di git repo)[/dim]")

def _detect_test_framework(path, content, ext):
    proj_dir = os.path.dirname(os.path.abspath(path))
    base = os.path.basename(path)
    markers = {
        "pytest": os.path.exists(os.path.join(proj_dir, "pytest.ini")) \
                  or os.path.exists(os.path.join(proj_dir, "pyproject.toml")) \
                  or os.path.exists(os.path.join(proj_dir, "setup.cfg")),
        "jest": os.path.exists(os.path.join(proj_dir, "jest.config.js")) \
                or os.path.exists(os.path.join(proj_dir, "jest.config.ts")) \
                or os.path.exists(os.path.join(proj_dir, "jest.config.json")),
        "phpunit": os.path.exists(os.path.join(proj_dir, "phpunit.xml")) \
                   or os.path.exists(os.path.join(proj_dir, "phpunit.xml.dist")),
        "go_test": os.path.exists(os.path.join(proj_dir, "go.mod")),
        "cargo_test": os.path.exists(os.path.join(proj_dir, "Cargo.toml")),
    }

    # Python: pytest kalau ada config atau file test_*, fallback ke python3
    if ext == ".py":
        is_test_file = base.startswith("test_") or base.endswith("_test.py")
        if markers["pytest"] and is_test_file:
            return ("pytest -v " + shlex.quote(path), "pytest")
        if "if __name__" in content or content.strip().startswith("#!"):
            return ("python3", "python3")

    # JavaScript: jest kalau ada config
    elif ext == ".js":
        is_test_file = ".test." in path or ".spec." in path or "_test." in path
        if markers["jest"] and is_test_file:
            return ("npx jest --no-coverage " + shlex.quote(path), "jest")
        return ("node", "node")

    # TypeScript: jest atau ts-node
    elif ext == ".ts":
        is_test_file = ".test." in path or ".spec." in path or "_test." in path
        if markers["jest"] and is_test_file:
            return ("npx jest --no-coverage " + shlex.quote(path), "jest")
        return ("npx ts-node", "ts-node")

    # Shell script
    elif ext == ".sh" and content.strip().startswith("#!"):
        return ("bash", "bash")

    # Ruby
    elif ext == ".rb":
        return ("ruby", "ruby")

    # Go: gunakan go test kalau ada _test.go, fallback go run
    elif ext == ".go":
        if base.endswith("_test.go"):
            dir_path = shlex.quote(proj_dir)
            return (f"go test -v {dir_path}", "go test")
        return ("go run", "go")

    # PHP: phpunit kalau ada config, fallback php
    elif ext == ".php":
        if markers["phpunit"] and ("test" in base.lower() or "Test" in base):
            return ("phpunit --no-coverage " + shlex.quote(path), "phpunit")
        return ("php", "php")

    # Rust
    elif ext == ".rs":
        if markers["cargo_test"] and "cfg(test)" in content or "#[test]" in content:
            return ("cargo test", "cargo test")
        return ("cargo run", "cargo run")

    return None


def _run_auto_test(cmd):
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=30, stdin=subprocess.DEVNULL, check=False)
        return r.returncode, r.stdout + r.stderr, False
    except subprocess.TimeoutExpired:
        return -1, "", True
    except Exception as e:  # noqa: BLE001
        return -1, str(e), False

def _handle_command(user_input, messages):
    """Proses perintah /command. Mengembalikan (should_continue, new_messages).

    should_continue=False berarti aplikasi harus berhenti (mis. /keluar).
    """
    global _CURRENT_SESSION, _MODELS

    parts = user_input.strip().split(maxsplit=1)
    cmd = parts[0].lower()
    arg = parts[1] if len(parts) > 1 else ""

    if cmd in ("/exit", "/quit", "/keluar"):
        _close_shell()
        _cleanup_lsp()
        return (False, messages)

    elif cmd in ("/new", "/baru"):
        _exhausted_keys.clear()
        ts = subprocess.run(["date", "+%Y%m%d_%H%M%S"], capture_output=True, text=True, check=False).stdout.strip()
        _CURRENT_SESSION = f"session_{ts}"
        messages = [{"role": "system", "content": _build_system_prompt()}]
        _console.print(f"[{_color_info()}]New session started: {_CURRENT_SESSION}[/{_color_info()}]")

    elif cmd == "/model":
        sub = arg.strip().lower()
        if not sub:
            mc = _current_model_config
            keys = mc.get("api_keys") or [mc.get("api_key", "")]
            total = len(keys)
            exhausted = sum(1 for k in keys if k in _exhausted_keys)
            active = total - exhausted
            _console.print(f"[bold]Model aktif:[/bold] {mc['name']} ({mc['model']})")
            _console.print(f"  Provider: {mc['provider']} | {mc['base_url']}")
            _console.print(f"  API Keys: {active}/{total} available [{_color_error()}]({exhausted} exhausted)[/{_color_error()}]" if exhausted else f"  API Keys: {total}")
            if mc.get("fallback"):
                fb_name = _MODELS.get(mc["fallback"], {}).get("name", mc["fallback"]) if mc["fallback"] in _MODELS else f"[{_color_error()}]{mc['fallback']} (tidak ditemukan di config!)[/{_color_error()}]"
                _console.print(f"  Fallback: {mc['fallback']} — {fb_name}")
            _console.print("[dim]Model tersedia (edit config.json untuk menambah):[/dim]")
            for key, m in _MODELS.items():
                marker = f" [{_color_ok()}]<-- aktif[/{_color_ok()}]" if m["model"] == mc["model"] else ""
                kcount = len(m.get("api_keys") or [m.get("api_key", "")])
                key_info = f" ({kcount} keys)" if kcount > 1 else ""
                _console.print(f"    /model {key}  — {m['name']} ({m['model']}){key_info}{marker}")
            _console.print(f"  Config file: [underline]{_CONFIG_PATH}[/underline]")
        elif sub in _MODELS:
            cfg = dict(_MODELS[sub])
            keys = cfg.get("api_keys") or [cfg.get("api_key", "")]
            if cfg.get("provider") in ("openai", "google") and not any(keys):
                _console.print(f"[{_color_warn()}]Peringatan: API key untuk {sub} kosong. Isi 'api_keys' di config.json[/{_color_warn()}]")
            _current_model_config.clear()
            _current_model_config.update(cfg)
            _console.print(f"[{_color_ok()}]Model diganti: {cfg['name']} ({cfg['model']})[/{_color_ok()}]")
        else:
            matches = [k for k, v in _MODELS.items() if sub in k or sub in v["model"]]
            if matches:
                print(f"  Maksud Anda: {', '.join(f'/model {m}' for m in matches)}")
            else:
                print(f"  Model '{sub}' tidak dikenal. Lihat daftar: /model")

    elif cmd == "/reset_quota":
        _exhausted_keys.clear()
        _console.print(f"[{_color_ok()}]Quota exhausted state direset. Semua API key dianggap available kembali.[/{_color_ok()}]")

    elif cmd == "/themes":
        sub = arg.strip().lower()
        if sub in ("dark", "gelap"):
            _set_theme(True)
            _console.print(f"[{_color_ok()}]Tema diganti: Gelap[/{_color_ok()}]")
        elif sub in ("light", "terang"):
            _set_theme(False)
            _console.print(f"[{_color_ok()}]Tema diganti: Terang[/{_color_ok()}]")
        else:
            _console.print("[bold]Tema aktif:[/bold] " + ("Gelap" if _resolve_theme() else "Terang"))
            _console.print("    /themes dark  — tema gelap")
            _console.print("    /themes light — tema terang")

    elif cmd == "/reload":
        _MODELS = _load_models()
        default_model = next((v for v in _MODELS.values() if v.get("default")), next(iter(_MODELS.values())))
        _current_model_config.clear()
        _current_model_config.update(default_model)
        _console.print(f"[{_color_ok()}]Config reloaded dari {_CONFIG_PATH} ({len(_MODELS)} model)[/{_color_ok()}]")

    elif cmd == "/install-lsp":
        handle_install_lsp_command(arg)

    else:
        print(f"  Unknown command: {cmd}")

    return (True, messages)


def main():
    # === STDIN SAFETY NET ===
    try:
        import atexit
        import termios
        _saved_stdin = termios.tcgetattr(sys.stdin.fileno())
        def _restore_stdin():
            try:
                termios.tcsetattr(sys.stdin.fileno(), termios.TCSANOW, _saved_stdin)
            except Exception:  # noqa: BLE001, S110
                pass
        atexit.register(_restore_stdin)
    except Exception:  # noqa: BLE001, S110
        pass
    # === END STDIN SAFETY NET ===

    if "--version" in sys.argv:
        print(f"Joki v{__version__}")
        sys.exit(0)

    if "--update" in sys.argv:
        if getattr(sys, "frozen", False):
            print("Update otomatis tidak tersedia di build standalone.")
            sys.exit(0)
        os.chdir(os.path.dirname(os.path.abspath(__file__)))
        subprocess.run(["git", "pull", "origin", "main"], check=False)
        print("Updated! Restart Joki untuk menggunakan versi terbaru.")
        sys.exit(0)

    _load_plugins()
    _check_update()

    global _CURRENT_SESSION
    _exhausted_keys.clear()
    args = sys.argv[1:]
    target_dir = None
    user_input = ""

    if args:
        first = os.path.expanduser(args[0])
        if os.path.isdir(first):
            target_dir = first
            os.chdir(first)
            rest = args[1:]
            user_input = " ".join(rest)
        else:
            user_input = " ".join(args)

    if user_input:
        ts_name = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S")
        _CURRENT_SESSION = f"session_{ts_name}"
        messages = [{"role": "system", "content": _build_system_prompt()}]
        messages.append({"role": "user", "content": user_input})
        initial_input = user_input
    else:
        ts = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S")
        _CURRENT_SESSION = f"session_{ts}"
        messages = [{"role": "system", "content": _build_system_prompt()}]
        initial_input = None

    if _IS_TTY:
        try:
            from joki.tui import run_tui
            run_tui(messages, initial_input=initial_input)
        except Exception:  # noqa: BLE001
            import traceback
            _console.print(f"[dim]TUI gagal dimulai — fallback ke mode scrollback: {escape(traceback.format_exc())}[/dim]")
        else:
            _close_shell()
            _cleanup_lsp()
            return

    # ==== Mode non-TUI (scrollback / pipa / tes) ====
    os.system("clear")

    if target_dir:
        cwd = os.path.abspath(target_dir)
        _console.print(f"\n[{_color_info()}]\u2192 Working directory: {cwd}[/{_color_info()}]\n")

    if initial_input:
        _console.print(_message_card(Markdown(initial_input), f"[bold {_color_warn()}]USER[/bold {_color_warn()}]"))
        agent_loop(messages)
        _close_shell()
        _cleanup_lsp()
        return

    _console.print()
    _console.print("  [bold]/model[/bold] — ganti model  |  [bold]/baru[/bold]  |  [bold]/keluar[/bold]  |  [bold]/install-lsp[/bold] — install LSP", style="dim")

    bindings = KeyBindings()

    @bindings.add("escape", "enter")
    def _(event):
        event.current_buffer.insert_text("\n")

    history_path = os.path.join(_get_data_dir(), "history")

    cmd_completer = _JokiCompleter()

    session = PromptSession(
        key_bindings=bindings,
        history=FileHistory(history_path),
        completer=cmd_completer,
        complete_while_typing=True,
        bottom_toolbar=HTML('<gray>[Alt+Enter] atau [Esc+Enter] untuk baris baru</gray>')
    )

    while True:
        try:
            _MONITOR_PAUSED.set()
            user_input = session.prompt(HTML(f'<style fg="{_color_info()}">joki</style><gray>></gray> '))
        except (EOFError, KeyboardInterrupt):
            _MONITOR_PAUSED.clear()
            print()
            _close_shell()
            _cleanup_lsp()
            break

        _MONITOR_PAUSED.clear()

        if not user_input:
            continue

        if user_input.startswith("/"):
            should_continue, messages = _handle_command(user_input, messages)
            if not should_continue:
                break
            continue

        _console.print(_message_card(Markdown(user_input), f"[bold {_color_warn()}]USER[/bold {_color_warn()}]"))
        messages.append({"role": "user", "content": user_input})
        agent_loop(messages)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nDibatalkan.")
