import ast
import json
import os
import re
from datetime import datetime, timedelta, timezone

from joki.config import _get_data_dir
from joki.state import *
from joki.utils import *

MEMORY_STORE_VERSION = 2
MEMORY_DEFAULT_TTL_DAYS = 30
MEMORY_MAX_INJECT = 8
MEMORY_PROMPT_PREFIX = "[MEMORI RELEVAN]"


def _memory_path(name=None):
    # Session aktif dibaca dinamis dari state (bukan salinan modul ini),
    # agar /baru & resume session memakai file memori masing-masing.
    name = name or _get_current_session() or "default"
    return os.path.join(_get_data_dir(), "memories", f"{name}.json")


def _now_utc():
    return datetime.now(timezone.utc)


def _iso(dt=None):
    value = dt or _now_utc()
    if isinstance(value, str):
        return value
    return value.isoformat()


def _parse_ts(value):
    try:
        text = str(value or "").strip()
        if not text:
            return None
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (TypeError, ValueError):
        return None


def _default_ttl_days():
    try:
        return max(0, int(MEMORY_DEFAULT_TTL_DAYS))
    except (TypeError, ValueError):
        return 30


def _entry_ttl_days(record):
    raw = record.get("ttl_days")
    if raw is None:
        return _default_ttl_days()
    try:
        return max(0, int(raw))
    except (TypeError, ValueError):
        return _default_ttl_days()


def _entry_base_time(record):
    for field in ("updated_at", "created_at", "last_used_at"):
        dt = _parse_ts(record.get(field))
        if dt is not None:
            return dt
    return None


def _entry_expired(record, now=None):
    ttl = _entry_ttl_days(record)
    if ttl <= 0:
        return False
    base = _entry_base_time(record)
    if base is None:
        return False
    return (now or _now_utc()) - base > timedelta(days=ttl)


def _make_record(
    value,
    scope="global",
    task_scope="",
    task_label="",
    ttl_days=None,
    created_at=None,
    updated_at=None,
    hits=0,
):
    now = _now_utc()
    return {
        "value": str(value),
        "scope": scope if scope in ("task", "global") else "global",
        "task_scope": str(task_scope or ""),
        "task_label": str(task_label or "")[:300],
        "ttl_days": _default_ttl_days() if ttl_days is None else max(0, int(ttl_days)),
        "created_at": _iso(created_at) if created_at else _iso(now),
        "updated_at": _iso(updated_at) if updated_at else _iso(now),
        "hits": max(0, int(hits or 0)),
    }


def _normalize_record(key, raw):
    if isinstance(raw, dict) and "value" in raw:
        return {
            "value": str(raw.get("value", "")),
            "scope": raw.get("scope") if raw.get("scope") in ("task", "global") else "global",
            "task_scope": str(raw.get("task_scope") or ""),
            "task_label": str(raw.get("task_label") or "")[:300],
            "ttl_days": raw.get("ttl_days"),
            "created_at": raw.get("created_at") or raw.get("updated_at") or raw.get("ts") or "",
            "updated_at": raw.get("updated_at") or raw.get("created_at") or raw.get("ts") or "",
            "hits": raw.get("hits", 0),
        }
    # Format lama {key: "nilai"} atau entri rusak: jadikan global permanen.
    return _make_record(str(raw), scope="global", ttl_days=0)


def _read_memory_store(name=None, now=None):
    path = _memory_path(name)
    if not os.path.exists(path):
        return {}
    try:
        with open(path) as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}
    if not isinstance(data, dict):
        return {}
    changed = False
    cleaned = {}
    for key, raw in data.items():
        record = _normalize_record(key, raw)
        if not isinstance(raw, dict) or "value" not in raw:
            changed = True
        cleaned[key] = record
    if changed:
        try:
            _write_memory_store(cleaned, name)
        except OSError:
            pass
    return cleaned


def _write_memory_store(records, name=None):
    path = _memory_path(name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    safe = {}
    for key, raw in (records or {}).items():
        record = dict(raw) if isinstance(raw, dict) else {"value": str(raw)}
        record.setdefault("scope", "global")
        record.setdefault("task_scope", "")
        record.setdefault("task_label", "")
        record.setdefault("ttl_days", _default_ttl_days())
        record.setdefault("created_at", _iso())
        record.setdefault("updated_at", record.get("created_at") or _iso())
        record.setdefault("hits", 0)
        safe[str(key)] = record
    with open(path, "w") as f:
        json.dump(safe, f, indent=2)


def _load_memory(name=None):
    """Kembalikan memori aktif yang relevan untuk tugas aktif (flat string)."""
    current_scope = _get_current_task_scope()
    out = {}
    for key, record in _read_memory_store(name).items():
        if _entry_expired(record):
            continue
        scope = record.get("scope") or "global"
        if scope == "task" and (not current_scope or record.get("task_scope") != current_scope):
            continue
        out[key] = str(record.get("value", ""))
    return out


def _load_memory_raw(name=None):
    return _load_memory(name)


def _memory_list(name=None):
    return _read_memory_store(name)


def _relevance_score(key, record, task_text):
    score = 0.0
    lowered = f"{key} {record.get('value', '')}".lower()
    words = set(re.findall(r"[a-z0-9_]{3,}", task_text.lower()))
    score += sum(1 for word in words if word in lowered) * 3.0
    if record.get("scope") == "global":
        score += 1.0
    if record.get("hits"):
        score += min(2.0, float(record.get("hits") or 0) * 0.25)
    updated = _parse_ts(record.get("updated_at"))
    if updated is not None:
        age_days = max(0.0, (_now_utc() - updated).total_seconds() / 86400)
        score += max(0.0, 2.0 - min(2.0, age_days / 30.0))
    return score


def _load_relevant_memories(name=None, limit=None):
    current_scope = _get_current_task_scope()
    task_text = _get_current_task_text()
    visible = []
    old_task_keys = []
    expired = []
    for key, record in _read_memory_store(name).items():
        if _entry_expired(record):
            expired.append(key)
            continue
        scope = record.get("scope") or "global"
        if scope == "task" and (not current_scope or record.get("task_scope") != current_scope):
            old_task_keys.append(key)
            continue
        visible.append((key, record))
    max_items = MEMORY_MAX_INJECT if limit is None else max(0, int(limit))
    visible.sort(
        key=lambda item: (
            _relevance_score(item[0], item[1], task_text),
            _parse_ts(item[1].get("updated_at")) or datetime.min.replace(tzinfo=timezone.utc),
        ),
        reverse=True,
    )
    return [(key, record.get("value", "")) for key, record in visible[:max_items]], old_task_keys, expired


def _memory_prompt_block(name=None, limit=None):
    items, old_task_keys, _expired = _load_relevant_memories(name=name, limit=limit)
    if not items and not old_task_keys:
        return ""
    parts = [f"{MEMORY_PROMPT_PREFIX} ({len(items)} relevan)"]
    if items:
        parts.extend(f"  - {key}: {str(value)[:120]}" for key, value in items)
        parts.append(
            "Gunakan memory_recall(key=...) untuk detail. Jangan samakan memori "
            "lama dengan kondisi saat ini tanpa verifikasi."
        )
    if old_task_keys:
        parts.append(
            f"{len(old_task_keys)} memori dari tugas lama tidak dimuat otomatis. "
            "Gunakan memory_recall(key=...) hanya bila relevan dengan tugas aktif."
        )
    return "\n".join(parts)


# ============================================================
# RENCANA PENGERJAAN
# ============================================================

_TODO_PREFIX_RE = re.compile(r"^\s*(?:[-*•>]\s*|\d+[.)]\s*|\(\d+\)\s*)+")


def _coerce_list(value):
    """Normalisasi nilai dari LLM menjadi list of str."""
    if isinstance(value, dict):
        value = list(value.values())
    if isinstance(value, str):
        raw = value.strip()
        parsed = None
        if raw[:1] in ("[", "("):
            try:
                parsed = json.loads(raw)
            except (json.JSONDecodeError, ValueError):
                try:
                    parsed = ast.literal_eval(raw)
                except (ValueError, SyntaxError, MemoryError):
                    parsed = None
        if isinstance(parsed, list):
            value = parsed
        else:
            parts = re.split(r"[\n;]+", raw)
            value = [p for p in (part.strip() for part in parts) if p]
    if not isinstance(value, (list, tuple)):
        return []
    out = []
    for item in value:
        if isinstance(item, (list, tuple)):
            item = " ".join(str(x) for x in item)
        text = str(item).strip()
        if not text:
            continue
        cleaned = _TODO_PREFIX_RE.sub("", text).strip()
        out.append(cleaned or text)
    return out


def _coerce_indices(value):
    """Normalisasi indices jadi list of int."""
    if isinstance(value, dict):
        value = list(value.values())
    if isinstance(value, str):
        raw = value.strip()
        parsed = None
        if raw[:1] == "[":
            try:
                parsed = json.loads(raw)
            except (json.JSONDecodeError, ValueError):
                try:
                    parsed = ast.literal_eval(raw)
                except (ValueError, SyntaxError, MemoryError):
                    parsed = None
        if isinstance(parsed, list):
            value = parsed
        else:
            value = [p for p in re.split(r"[,\s;]+", raw) if p]
    if isinstance(value, (int, float)):
        value = [value]
    if not isinstance(value, (list, tuple)):
        return []
    out = []
    for item in value:
        try:
            out.append(int(float(item)))
        except (TypeError, ValueError):
            continue
    return out


def _todo_path():
    # Todos per session: saat session di-resume, rencana pengerjaannya ikut kembali.
    return os.path.join(
        _get_data_dir(),
        "todos",
        f"{_get_current_session() or 'default'}.json")


def _load_todo():
    path = _todo_path()
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return []


def _save_todo(items):
    path = _todo_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(items, f, indent=2)

# ============================================================
# TOOL EXECUTOR
# ============================================================

_SENSITIVE_KEY_WORDS = (
    "password", "passwd", "secret", "token",
    "api_key", "apikey", "credential", "auth",
)


def _is_sensitive_key(key):
    k = (key or "").lower()
    return any(w in k for w in _SENSITIVE_KEY_WORDS)


def _coerce_ttl_days(value):
    if value is None or value == "":
        return _default_ttl_days()
    try:
        return max(0, min(3650, int(value)))
    except (TypeError, ValueError):
        return _default_ttl_days()


def _entry_status(record, now=None):
    if _entry_expired(record, now=now):
        return "kedaluwarsa"
    if (record.get("scope") or "global") == "task":
        current_scope = _get_current_task_scope()
        if not current_scope or record.get("task_scope") != current_scope:
            return "tugas lama"
    return ""


def handle_memory_store(args):
    key = str(args.get("key", "") or "").strip()
    value = str(args.get("value", "") or "").strip()
    if not key:
        return "Error: Parameter 'key' wajib diisi. Contoh: memory_store(key=\"db_password\", value=\"secret123\")"
    if not value:
        return "Error: Parameter 'value' wajib diisi. Contoh: memory_store(key=\"db_password\", value=\"secret123\")"
    if _is_sensitive_key(key):
        return (f"Error: key '{key}' terdeteksi berisi credential "
                f"(password/secret/token/api_key). Jangan simpan credential di "
                f"memory_store — file memory disimpan sebagai plaintext. "
                f"Gunakan environment variable atau config untuk credential.")
    scope = str(args.get("scope", "") or "").strip().lower()
    if scope not in ("task", "global"):
        scope = "task"
    current_scope = _get_current_task_scope()
    if scope == "task" and not current_scope:
        scope = "global"
    ttl_days = _coerce_ttl_days(args.get("ttl_days"))
    records = _read_memory_store()
    old = records.get(key) or {}
    record = _make_record(
        value,
        scope=scope,
        task_scope=current_scope if scope == "task" else "",
        task_label=_get_current_task_text() if scope == "task" else "",
        ttl_days=ttl_days,
        created_at=old.get("created_at") or None,
        updated_at=None,
        hits=old.get("hits", 0),
    )
    records[key] = record
    _write_memory_store(records)
    return f"Memory saved: {key}"


def handle_memory_recall(args):
    key = str(args.get("key", "") or "").strip()
    records = _read_memory_store()
    if key:
        record = records.get(key)
        if not record:
            return f"Memory '{key}' not found"
        status = _entry_status(record)
        suffix = f" [{status}]" if status else ""
        return f"{key}: {record.get('value', '')}{suffix}"
    if not records:
        return "(no memories stored)"
    lines = []
    for name, record in records.items():
        status = _entry_status(record)
        suffix = f" [{status}]" if status else ""
        value = str(record.get("value", ""))
        shown = value[:100] + ("..." if len(value) > 100 else "")
        lines.append(f"  {name}: {shown}{suffix}")
    return f"Memori tersimpan ({len(records)}):\n" + "\n".join(lines)


def handle_memory_forget(args):
    key = str(args.get("key", "") or "").strip()
    if not key:
        return "Error: Parameter 'key' wajib diisi. Contoh: memory_forget(key=\"db_password\")"
    records = _read_memory_store()
    if key in records:
        del records[key]
        _write_memory_store(records)
        return f"Memory forgotten: {key}"
    return f"Memory '{key}' not found"

def handle_todo_create(args):
    items = _coerce_list(args.get("items"))
    if not items:
        return "Error: Parameter 'items' wajib diisi sebagai array. Contoh: todo_create(items=[\"Buat file\", \"Test\"])"
    _save_todo(items)
    lines = [f"  {i+1}. [ ] {item}" for i, item in enumerate(items)]
    return f"Rencana Pengerjaan dibuat ({len(items)} item):\n" + "\n".join(lines)


def handle_todo_done(args):
    indices = _coerce_indices(args.get("indices"))
    if not indices:
        return "Error: Parameter 'indices' wajib diisi sebagai array. Contoh: todo_done(indices=[1, 2])"
    items = _load_todo()
    marked = []
    for idx in indices:
        if 1 <= idx <= len(items):
            items[idx - 1] = f"✅ {items[idx - 1]}"
            marked.append(str(idx))
    _save_todo(items)

    visual_trigger = ""
    if indices and max(indices) == len(items):
        last_item = items[-1]
        if "Verifikasi" in last_item:
            visual_trigger = "\n\n[SISTEM] Deteksi item 'Verifikasi' di akhir Rencana Pengerjaan. Menyiapkan validasi visual..."

    return f"Item Rencana Pengerjaan {' dan '.join(marked)} selesai! {visual_trigger}\n" + "\n".join(
        f"  {i+1}. {item}" for i, item in enumerate(items))


def handle_todo_show(args):
    items = _load_todo()
    if not items:
        return "(Rencana Pengerjaan kosong)"
    lines = [f"  {i+1}. {item}" for i, item in enumerate(items)]
    done = sum(1 for i in items if i.startswith("✅"))
    return f"Rencana Pengerjaan ({done}/{len(items)} selesai):\n" + "\n".join(lines)
