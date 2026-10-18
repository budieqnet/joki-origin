"""Verifier terpisah (read-only) untuk klaim selesai.

Dipanggil dari `joki.cli.agent_loop` tepat sebelum return final.
Tujuannya: agent worker tidak boleh menilai dirinya sendiri.

Desain:
- Tidak memutasi global (`_current_model_config`, mode, dsb). Snapshot via
  deepcopy dan panggil LLM langsung via httpx (pola sama seperti
  `_call_summary` di `joki/llm.py`), bukan via `call_llm` agar tidak
  rekursif dan tidak memicu summarize/trim/fallback global.
- Read-only: tidak memanggil tool apa pun, hanya menilai bukti yang
  sudah ada (changed files vs verified texts vs klaim + repro terkunci).
- Bukti palsu ditolak: echo/ls/cat DAN viewer file (grep/awk/sed/diff)
  tidak dihitung; cek sintaks/lint murni (php -l, py_compile, node
  --check, tsc --noEmit, eslint/dkk) tidak dihitung KECUALI error yang
  diminta memang syntax/parse error (lihat `_is_lint_only`).
- Judge independen: JOKI_JUDGE_MODEL (env) > config.json "judge_model" >
  model aktif, agar hakim bukan model worker yang sama (self-judge).
- Fail-soft: error/timeout/cancel/API habis -> SKIP hanya bila sudah ada
  bukti minimal; bila ada file diubah tapi NOL bukti sama sekali -> FAIL
  lunak (tahan klaim, minta verifikasi 1x) agar klaim palsu tidak lolos
  tepat saat jaringan/quota mati. Pengecualian: dimatikan eksplisit via
  JOKI_DISABLE_VERIFIER=1 dan pembatalan user -> tetap SKIP.
- Maks 2x per agent_loop (dijaga oleh pemanggil via `_verifier_calls`).
"""

import copy
import json
import os
import re
import shlex

import httpx

_VERIFY_FAIL_RE = re.compile(
    r"(\[ERROR\]|\bFAILED\b|\bGAGAL\b|Traceback \(most recent|\[PRE-FLIGHT|\[BLOKIR\]|\[DILARANG\]|\[KONFIRMASI\]|\[DITOLAK|TIMEOUT|exit code [1-9]|exit [1-9]\b)",
    re.IGNORECASE,
)

# Runner yang dianggap menguji kode bila dijalankan sebagai perintah utama,
# bukan sebagai argumen echo. "python3 " butuh spasi agar "python3x" lolos.
_RUNNER_BINS = (
    "pytest", "jest", "phpunit", "go", "cargo",
    "npm", "npx", "yarn", "pnpm",
    "python3", "python", "node", "ts-node", "bash", "ruby", "php",
)

# Perintah yang jelas BUKAN bukti eksekusi.
_NON_EVIDENCE_BINS = (
    "echo", "ls", "pwd", "cat", "head", "tail", "clear", "whoami", "which",
    # Viewer file: menyebut/membaca nama file bukan berarti mengeksekusinya.
    "grep", "egrep", "fgrep", "rg", "awk", "sed", "find", "wc", "strings",
    "xxd", "od", "stat", "file", "diff", "sort", "uniq", "tr", "jq",
    "md5sum", "sha256sum", "tree", "less", "more", "touch",
    "basename", "dirname", "realpath", "readlink",
)

# Repro HTTP lokal (curl/wget ke localhost) = app benar-benar dijalankan;
# dianggap mencakup file yang diubah (pola sama dengan runner suite).
_HTTP_PROOF_BINS = ("curl", "wget", "httpie")
_LOCAL_HTTP_RE = re.compile(
    r"https?://(?:localhost|127\.0\.0\.1|0\.0\.0\.0|\[::1\])(?::\d+)?",
    re.IGNORECASE,
)

# Shell wrapper: "bash -c '<inner>'" — bukti atau bukan tergantung <inner>,
# bukan "bash"-nya. Tanpa ini, `bash -c "echo pytest ok"` lolos sebagai
# runner "bash".
_SHELL_WRAPPERS = ("bash", "sh", "dash", "zsh", "fish")

# Interpreter dengan -c/-e: "python3 -c \"print('ok')\"" bukan eksekusi file.
_INTERP_DASH_C = ("python3", "python", "node", "ruby", "php", "perl")

# Keyword runner untuk menilai payload -c (apakah benar menjalankan test).
_RUNNER_KW = (
    "pytest", "jest", "go test", "cargo test", "phpunit", "npm test",
    "npm run test", "ts-node",
)


def _split_cmd(cmd):
    """Tokenisasi perintah, fallback ke split() bila shlex gagal."""
    try:
        return shlex.split(cmd or "")
    except Exception:  # noqa: BLE001
        try:
            return (cmd or "").strip().split()
        except Exception:  # noqa: BLE001
            return []


def _first_bin(cmd, _depth=0):
    """Ambil binary pertama dari perintah shell (abaikan sudo/env).

    Menembus wrapper satu lapis:
    - `bash -c "pytest -q"` -> "pytest" (tetap bukti bila runner sah).
    - `bash -c "echo pytest ok"` -> "echo" (bukan bukti).
    - `python3 -c "print('ok')"` -> "echo" (dipaksa non-evidence, karena
      hanya mencetak tanpa menyentuh file yang diubah).
    """
    try:
        toks = _split_cmd(cmd)
        i = 0
        while i < len(toks) and toks[i] in ("sudo", "env", "-n"):
            i += 1
        if i >= len(toks):
            return ""
        base = os.path.basename(toks[i]).lower()
        if _depth < 2 and base in _SHELL_WRAPPERS:
            try:
                ci = toks.index("-c", i + 1)
                inner = " ".join(toks[ci + 1:]).strip().strip("'\"")
                if inner:
                    return _first_bin(inner, _depth + 1) or base
            except ValueError:
                pass
            return base
        if _depth < 2 and base in _INTERP_DASH_C:
            rest = [t for t in toks[i + 1:] if t not in ("-u", "-W", "ignore")]
            if rest and rest[0] in ("-c", "-e"):
                payload = " ".join(rest[1:]).lower()
                # Payload -c yang benar menjalankan test menyebut runner;
                # print/echo/exit polos tanpa runner = bukan bukti.
                if payload and not any(k in payload for k in _RUNNER_KW):
                    return "echo"
                return base
        return base
    except Exception:  # noqa: BLE001
        return ""


# Cek sintaks/lint murni — bukan bukti eksekusi skenario. Dihitung bukti
# HANYA bila error yang diminta user memang syntax/parse error.
_LINT_ONLY_RE = re.compile(
    r"(?:^|[;&|]\s*)"
    r"(?:(?:sudo|env|npx|pnpm\s+exec|yarn|bunx)\s+)*"
    r"(?:"
    r"php\S*\s+(?:-l|--syntax-check)\b"
    r"|(?:python3?|py)\S*\s+-m\s+(?:py_compile|compileall)\b"
    r"|node\S*\s+--check\b"
    r"|tsc\S*\b[^\n;|&]*--?noEmit\b"
    r"|(?:eslint|flake8|pylint|ruff|mypy|phpcs|phpstan|psalm|shellcheck)\b"
    r")",
    re.IGNORECASE,
)

_SYNTAX_ERR_KW = (
    "syntax error", "parse error", "parseerror", "syntaxerror",
    "invalid syntax", "unexpected token", "unexpected end of",
    "unterminated string",
)


def _effective_cmd(cmd):
    """Perintah efektif: tembus wrapper `bash -c "..."` satu lapis."""
    try:
        toks = _split_cmd(cmd)
        i = 0
        while i < len(toks) and toks[i] in ("sudo", "env", "-n"):
            i += 1
        if i < len(toks) and os.path.basename(toks[i]).lower() in _SHELL_WRAPPERS:
            try:
                ci = toks.index("-c", i + 1)
                inner = " ".join(toks[ci + 1:]).strip().strip("'\"")
                if inner:
                    return inner
            except ValueError:
                pass
        return cmd or ""
    except Exception:  # noqa: BLE001
        return cmd or ""


def _is_lint_only(cmd):
    """True bila perintah cuma cek sintaks/lint (bukan eksekusi nyata)."""
    try:
        return bool(_LINT_ONLY_RE.search(_effective_cmd(cmd or "").strip()))
    except Exception:  # noqa: BLE001
        return False


def _is_syntax_error_request(text):
    """True bila error yang diminta user memang syntax/parse error."""
    try:
        t = (text or "").lower()
        return any(k in t for k in _SYNTAX_ERR_KW)
    except Exception:  # noqa: BLE001
        return False


def _has_any_evidence(verified_texts, allow_lint=False):
    """True bila ada minimal satu bukti yang bukan echo/ls/cat/lint.

    Dipakai untuk memutuskan SKIP vs FAIL lunak saat judge tak tersedia:
    tanpa bukti apa pun, klaim harus ditahan (FAIL), bukan diloloskan.
    `allow_lint=True` hanya saat error awal memang syntax/parse error.
    """
    try:
        for t in verified_texts or []:
            t = (t or "").strip()
            if not t:
                continue
            fb = _first_bin(t)
            if not fb or fb in _NON_EVIDENCE_BINS:
                continue
            if not allow_lint and _is_lint_only(t):
                continue
            return True
        return False
    except Exception:  # noqa: BLE001
        return False


def deterministic_check(changed_files, verified_texts, repro_cmds=None, original_request=""):
    """Cek deterministik tanpa LLM. Return (ok: bool, reason: str).

    Aturan ketat:
    - Tidak ada file diubah -> ok (tidak ada yang perlu dibuktikan).
    - Tidak ada verified_texts -> gagal.
    - Tiap file diubah (max 5 terakhir) harus disebut basename-nya di salah
      satu verified_text, ATAU verified_text diawali runner bin yang sah.
    - Perintah echo/ls/pwd/cat dan viewer file (grep/awk/sed/diff) tidak
      dihitung meski mengandung kata pytest / nama file.
    - Cek sintaks/lint murni (php -l, py_compile, node --check, tsc
      --noEmit, eslint/dkk) BUKAN bukti — kecuali error awal memang
      syntax/parse error (original_request).
    - Repro terkunci: bila repro_cmds ada, minimal satu bukti harus satu
      keluarga dengan skenario repro (first_bin sama, mis. repro
      "python3 a.py" wajib diverifikasi ulang via "python3 ...", bukan
      cuma "pytest" generik). Ini cegah ganti skenario diam-diam.
    """
    changed = [str(p) for p in (changed_files or [])][-5:]
    if not changed:
        return True, "tidak ada file diubah"
    texts = [(t or "").strip() for t in (verified_texts or []) if (t or "").strip()]
    if not texts:
        return False, "ada file diubah tapi tidak ada bukti eksekusi sama sekali"
    lowered = [t.lower() for t in texts]
    try:
        _syn_ctx = _is_syntax_error_request(original_request)
    except Exception:  # noqa: BLE001
        _syn_ctx = False

    def _evidence_bin(t):
        """first_bin yang dihitung bukti eksekusi; '' bila bukan bukti."""
        fb = _first_bin(t)
        if not fb or fb in _NON_EVIDENCE_BINS:
            return ""
        if not _syn_ctx and _is_lint_only(t):
            return ""
        return fb

    for p in changed:
        base = os.path.basename(p).lower()
        if not base:
            continue
        covered = False
        for t in lowered:
            eb = _evidence_bin(t)
            if not eb:
                # "echo testing app.py" / "grep x app.py" / "php -l app.php":
                # menyebut nama file / cek sintaks != mengeksekusinya.
                continue
            if base in t:
                covered = True
                break
            if eb in _HTTP_PROOF_BINS and _LOCAL_HTTP_RE.search(t):
                # Repro HTTP lokal: app dijalankan, dianggap mencakup file
                # yang diubah (mis. curl localhost untuk cek controller).
                covered = True
                break
            if eb in _RUNNER_BINS:
                # Runner suite (pytest/jest/go test) dianggap mencakup
                # semua file bila basename tidak disebut. Ini longgar tapi
                # diperlukan untuk proyek dengan test suite global.
                covered = True
                break
        if not covered:
            return False, f"file {os.path.basename(p)} belum disebut di bukti eksekusi"
    # Tolak bila semua bukti adalah non-evidence/lint murni.
    if all(not _evidence_bin(t) for t in lowered):
        return False, "bukti hanya echo/ls/pwd/cat/lint, bukan eksekusi nyata"
    # Repro terkunci: satu keluarga first_bin dengan skenario awal.
    try:
        repros = [(r or "").strip() for r in (repro_cmds or []) if (r or "").strip()]
        if repros:
            vbins = {_evidence_bin(t) for t in lowered} - {""}
            locked = False
            for r in repros:
                rb = _first_bin(r.lower())
                if not rb or rb in _NON_EVIDENCE_BINS or (not _syn_ctx and _is_lint_only(r)):
                    continue
                if rb in vbins:
                    locked = True
                    break
                # Suite runner (pytest/dkk) dianggap mencakup repro langsung
                # (python3/node) dan sebaliknya — asal ada eksekusi test nyata,
                # bukan ganti ke echo/ls. Longgar tapi cegah ganti skenario diam-diam.
                if rb in _RUNNER_BINS and (vbins & set(_RUNNER_BINS)):
                    locked = True
                    break
                # run_tests/test_and_fix tanpa cmd teks dianggap satu keluarga runner.
                if rb in ("run_tests", "test_and_fix", "lsp_query") and (vbins & set(_RUNNER_BINS)):
                    locked = True
                    break
            if not locked:
                return False, "skenario repro belum dijalankan ulang sesudah edit"
    except Exception:  # noqa: BLE001, S110
        pass
    return True, "deterministik lolos"


def _build_judge_messages(original_request, claim, changed, verified):
    changed_block = "\n".join(f"- {c}" for c in changed[-5:]) or "(tidak ada)"
    verified_block = "\n".join(f"- {v[:300]}" for v in verified[-5:]) or "(tidak ada)"
    system = (
        "Kamu hakim verifikasi independen untuk AI agent coding. Tugasmu hanya "
        "menilai apakah klaim SELESAI didukung bukti eksekusi.\n"
        "Konteks: daftar bukti di bawah SUDAH lolos filter sukses worker "
        "(tanpa FAILED/GAGAL/Traceback/ErrorException/HTTP 5xx). Output kosong "
        "dengan exit 0 tetap dianggap SUKSES bila perintahnya mengeksekusi file "
        "yang diubah (mis. python3 a.py tanpa output = sukses, bukan gagal).\n"
        "Aturan:\n"
        "1. PASS hanya bila ada bukti run_command/test_and_fix/run_tests yang "
        "benar-benar MENGEKSEKUSI file yang diubah (mis. python3 a.py), "
        "menjalankan ulang skenario (mis. curl ke localhost), atau runner "
        "suite (pytest/jest/go test/phpunit).\n"
        "2. TOLAK `echo ok`, `ls`, `pwd`, `cat` sebagai bukti, meski mengandung kata pytest.\n"
        "3. TOLAK bukti yang hanya MEMBACA file (grep/awk/sed/head/diff) — "
        "membaca file bukan menjalankannya.\n"
        "4. TOLAK bukti yang hanya cek sintaks/lint (php -l, python -m "
        "py_compile, node --check, tsc --noEmit, eslint/dkk) KECUALI error "
        "yang diminta memang syntax/parse error. Untuk error RUNTIME/HTTP/"
        "perilaku aplikasi, bukti wajib menjalankan ulang skenario nyata.\n"
        "5. TOLAK klaim yang sama sekali tanpa bukti yang relevan.\n"
        "6. Bila ragu, pilih FAIL.\n"
        "Balas HANYA JSON: {\"verdict\": \"PASS\" atau \"FAIL\", \"reason\": \"singkat Bahasa Indonesia\"}. "
        "Tanpa markdown, tanpa penjelasan lain."
    )
    user = (
        f"Permintaan awal: {original_request[:500]}\n\n"
        f"Klaim agent:\n{claim[:2000]}\n\n"
        f"File diubah:\n{changed_block}\n\n"
        f"Bukti eksekusi (perintah terverifikasi):\n{verified_block}\n\n"
        "Nilai sekarang."
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def _parse_verdict(text):
    """Parse JSON verdict dari output judge. Ambigresultat -> None.

    None berarti judge tak bisa dinilai; verify_completion yang memutuskan
    SKIP (ada bukti minimal) atau FAIL lunak (nol bukti). Pemanggil TIDAK
    boleh menganggap semua None = PASS."""
    try:
        t = (text or "").strip()
        if not t:
            return None
        # Buang fence markdown bila ada.
        t = re.sub(r"^```(?:json)?\s*", "", t, flags=re.IGNORECASE)
        t = re.sub(r"\s*```$", "", t)
        m = re.search(r"\{.*\}", t, re.DOTALL)
        if not m:
            return None
        data = json.loads(m.group(0))
        v = str(data.get("verdict", "")).strip().upper()
        if v not in ("PASS", "FAIL"):
            return None
        return {"verdict": v, "reason": str(data.get("reason", ""))[:500]}
    except Exception:
        return None


def _call_judge(model_cfg, judge_messages, timeout=60):
    """Panggil LLM sekali via httpx langsung. Tidak menyentuh global."""
    provider = (model_cfg or {}).get("provider", "openai")
    if provider == "google":
        return None
    keys = (model_cfg or {}).get("api_keys") or [(model_cfg or {}).get("api_key", "")]
    base_url = (model_cfg or {}).get("base_url", "")
    model = (model_cfg or {}).get("model", "")
    if not base_url or not model:
        return None
    body_base = {"messages": judge_messages, "stream": False}
    # Suhu 0 agar deterministik. Reasoning (gpt-5/codex/o) tidak dukung
    # temperature/max_tokens custom -> pakai max_completion_tokens tanpa suhu.
    try:
        _jreason = any(k in str(model).lower() for k in ("gpt-5", "gpt-6", "codex", "o1", "o3", "o4"))
    except Exception:
        _jreason = False
    if provider == "ollama":
        url = f"{base_url}/api/chat"
    else:
        url = f"{base_url}/chat/completions"
        body_base["model"] = model
        if _jreason:
            body_base["max_completion_tokens"] = 512
        else:
            body_base["max_tokens"] = 512
            body_base["temperature"] = 0
    for key in keys:
        try:
            from joki.state import _joki_cancel
            if _joki_cancel.is_set():
                return None
        except Exception:
            pass
        try:
            headers = {"Content-Type": "application/json"}
            if key:
                headers["Authorization"] = f"Bearer {key}"
            if provider == "ollama":
                body = dict(body_base)
                body.update({"model": model, "stream": False, "options": {"temperature": 0, "num_predict": 512}})
            else:
                body = dict(body_base)
            r = httpx.post(url, json=body, headers=headers, timeout=timeout, follow_redirects=True)
            if r.status_code in (429, 402):
                continue
            if r.status_code != 200:
                continue
            data = r.json()
            if provider == "ollama":
                content = (data.get("message") or {}).get("content", "")
            else:
                content = ((data.get("choices") or [{}])[0].get("message") or {}).get("content", "")
            if content and content.strip():
                return content.strip()
        except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError, AttributeError):
            continue
    return None


def verify_completion(original_request="", claim="", changed_files=None,
                      verified_texts=None, repro_cmds=None, timeout=60):
    """Entry point verifier terpisah.

    Return dict {verdict: PASS|FAIL|SKIP, reason: str, stage: str}.
    - PASS: bukti cukup.
    - FAIL: bukti kurang (termasuk judge tak tersedia padahal NOL bukti) —
      pemanggil harus tahan klaim dan minta verifikasi 1x.
    - SKIP: tidak bisa menilai TAPI sudah ada bukti minimal (atau
      dimatikan eksplisit / dibatalkan user) -> pemanggil boleh lanjut
      dengan label [UNVERIFIED] bila edit masih lebih baru dari bukti.
    Kantong: JOKI_SKIP_JUDGE=1 lewati LLM judge (deterministik saja, hemat
    ~1k token/tugas). JOKI_JUDGE_MODEL=<nama> pakai model murah untuk judge.
    JOKI_JUDGE_TIMEOUT detik (default 60, boleh 30 agar tak nunggu lama).
    """
    changed = list(changed_files or [])
    verified = list(verified_texts or [])
    try:
        repros = list(repro_cmds or [])
    except Exception:
        repros = []
    try:
        _syn_ctx = _is_syntax_error_request(original_request)
    except Exception:  # noqa: BLE001
        _syn_ctx = False

    def _fail_or_skip(stage, reason):
        """Judge tak tersedia: FAIL lunak bila nol bukti, SKIP bila ada.

        Mencegah klaim palsu lolos tepat saat timeout/quota/provider mati.
        """
        try:
            if changed and not _has_any_evidence(verified, allow_lint=_syn_ctx):
                return {"verdict": "FAIL", "reason": reason + " — tidak ada bukti eksekusi sama sekali, jangan klaim selesai", "stage": stage}
        except Exception:  # noqa: BLE001, S110
            pass
        return {"verdict": "SKIP", "reason": reason, "stage": stage}

    # Env kill-switch.
    try:
        if os.getenv("JOKI_DISABLE_VERIFIER", "") == "1":
            return {"verdict": "SKIP", "reason": "verifier dimatikan via env", "stage": "disabled"}
    except Exception:
        pass

    # Layer 1: deterministik, murah, tanpa LLM (termasuk repro terkunci).
    try:
        ok, reason = deterministic_check(changed, verified, repros, original_request)
    except Exception as e:
        return _fail_or_skip("deterministic", f"deterministik error: {e}")
    if not ok:
        return {"verdict": "FAIL", "reason": reason, "stage": "deterministic"}

    # Kantong: lewati judge bila diminta (hemat token/quota).
    try:
        if os.getenv("JOKI_SKIP_JUDGE", "") == "1":
            return {"verdict": "PASS", "reason": "deterministik lolos (judge dilewati via env)", "stage": "deterministic+skip"}
    except Exception:
        pass

    # Layer 2: LLM judge independen (sesi terpisah, snapshot config).
    # Judge sebaiknya BUKAN model worker yang sama (self-judge = klaim palsu
    # mudah lolos). Prioritas: JOKI_JUDGE_MODEL (env) > config.json
    # "judge_model" > model aktif.
    # JOKI_JUDGE_TIMEOUT override detik (default ikut argumen, boleh 30).
    try:
        from joki.state import _joki_cancel
        if _joki_cancel.is_set():
            return {"verdict": "SKIP", "reason": "dibatalkan user", "stage": "judge"}
    except Exception:
        pass
    try:
        from joki.config import _MODELS, _current_model_config
        try:
            _jm = (os.getenv("JOKI_JUDGE_MODEL", "") or "").strip()
            if not _jm:
                # config.json top-level "judge_model" (opsional).
                try:
                    from joki.config import _CONFIG_PATH as _cfg_path
                    if _cfg_path.exists():
                        _jdata = json.loads(_cfg_path.read_text())
                        _jm = str(_jdata.get("judge_model", "") or "").strip()
                except Exception:  # noqa: BLE001, S110
                    _jm = ""
            _jt = (os.getenv("JOKI_JUDGE_TIMEOUT", "") or "").strip()
            if _jt:
                timeout = max(10, min(int(_jt), 120))
            if _jm and _jm in _MODELS:
                mc = copy.deepcopy(dict(_MODELS[_jm]))
            else:
                mc = copy.deepcopy(dict(_current_model_config))
        except Exception:
            from joki.config import _current_model_config as _cmc
            mc = copy.deepcopy(dict(_cmc))
    except Exception as e:
        return _fail_or_skip("judge", f"config tak terbaca: {e}")
    try:
        judge_messages = _build_judge_messages(original_request, claim, changed, verified)
        raw = _call_judge(mc, judge_messages, timeout=timeout)
        if not raw:
            return _fail_or_skip("judge", "judge tak merespons (timeout/quota/provider)")
        parsed = _parse_verdict(raw)
        if not parsed:
            return _fail_or_skip("judge", "format judge tak valid")
        parsed["stage"] = "judge"
        return parsed
    except Exception as e:
        return _fail_or_skip("judge", f"judge error: {e}")
