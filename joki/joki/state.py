import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time

from rich.console import Console

__version__ = "0.1.0"

MODE_CODE = "code"
MODE_SYSADMIN = "sysadmin"
MODE_SECURITY = "security"
MODE_GENERAL = "general"

__all__ = [
    "BACKUP_DIR",
    "MODE_CODE",
    "MODE_GENERAL",
    "MODE_SECURITY",
    "MODE_SYSADMIN",
    "_CURRENT_SESSION",
    "_HAS_TTY",
    "_IS_TTY",
    "_MONITOR_PAUSED",
    "_PERSISTENT_SHELL",
    "_PROTECTED_IDENTITY_PATHS",
    "_PROTECTED_SYSTEM_PATHS",
    "_PTY_LOCK",
    "_PTY_SESSION",
    "_READ_FILES",
    "_RENDER_KIND",
    "_RULE_KIND",
    "_SHELL_LOCK",
    "_STATUS_KIND",
    "_SUDO_PASSWORD",
    "_TEXT_KIND",
    "_THEME_DARK",
    "_THINK_KIND",
    "_TUI_ACTIVE",
    "_TUI_DARK",
    "_TUI_QUEUE",
    "_WORKSPACE_ROOT",
    "ConfigError",
    "JokiError",
    "LLMError",
    "ToolError",
    "__version__",
    "_bridge_send",
    "_console",
    "_current_model_config",
    "_current_task_mode",
    "_exhausted_keys",
    "_file_cache",
    "_file_change_log",
    "_is_protected_identity_path",
    "_is_protected_path",
    "_joki_cancel",
    "_joki_card",
    "_read_in_context",
    "_recent_tools",
    "_request_tui_password",
    "_resolve_theme",
    "_resolve_tui_password",
    "_set_theme",
    "_start_cancel_monitor",
    "_stop_cancel_monitor",
    "_tui_start",
    "_tui_stop",
]
BACKUP_DIR = os.path.join(tempfile.gettempdir(), "joki_backups")
_WORKSPACE_ROOT = os.getcwd()

# Tema: definisikan awal untuk hindari circular import (config.py import state)
_THEME_DARK = None

_PROTECTED_SYSTEM_PATHS = [
    "/etc/",
    "/usr/local/etc/",
    "/usr/share/",
    "/boot/",
    "/lib/",
    "/lib64/",
    "/usr/lib/",
    "/sys/",
    "/proc/",
    "/bin/",
    "/sbin/",
    "/usr/bin/",
    "/usr/sbin/",
]

_PROTECTED_IDENTITY_PATHS = [
    "/etc/shadow",
    "/etc/passwd",
    "/etc/gshadow",
    "/etc/sudoers",
    "/etc/sudoers.d/",
    "/etc/ssh/sshd_config",
    os.path.expanduser("~/.ssh/"),
]

def _normalize_path(path):
    return os.path.realpath(os.path.expanduser(os.path.abspath(str(path))))

def _is_protected_path(abs_path):
    abs_path = _normalize_path(abs_path)
    for protected in _PROTECTED_SYSTEM_PATHS:
        if protected.endswith(os.sep):
            if abs_path.startswith(protected):
                return True
        else:
            if abs_path == protected or abs_path.startswith(protected + os.sep):
                return True
    return False

def _is_protected_identity_path(abs_path):
    abs_path = _normalize_path(abs_path)
    for protected in _PROTECTED_IDENTITY_PATHS:
        if protected.endswith(os.sep):
            if abs_path.startswith(protected):
                return True
        else:
            if abs_path == protected or abs_path.startswith(protected + os.sep):
                return True
    return False

_IS_TTY = sys.stdout.isatty()

_HAS_TTY = _IS_TTY and importlib.util.find_spec("termios") is not None


# === TUI bridge (Textual) ===
# Ketika TUI aktif, semua output _console di-route ke queue sebagai renderable;
# JokiApp memakainya untuk menulis ke RichLog. Saat tidak TUI, _console berperilaku
# normal (menulis ke stdout) sehingga mode scrollback & tes tetap jalan.
_TUI_ACTIVE = False
_TUI_QUEUE = None
_TUI_DARK = True

_RENDER_KIND = "render"
_TEXT_KIND = "text"
_RULE_KIND = "rule"
_STATUS_KIND = "status"
_THINK_KIND = "think"
_PASSWORD_KIND = "password"

# Permintaan password yang belum dijawab dari worker thread (TUI dialog).
# id -> {"event": threading.Event, "result": str|None}
_PASSWORD_REQUESTS = {}


class _BridgeConsole(Console):
    def print(self, *objects, **kwargs):
        if _TUI_ACTIVE and _TUI_QUEUE is not None:
            style = kwargs.pop("style", None)
            markup = kwargs.pop("markup", True)
            sep = kwargs.pop("sep", " ")
            end = kwargs.pop("end", "\n")
            kwargs = {}
            if len(objects) == 1 and isinstance(objects[0], (list, tuple)):
                objects = tuple(objects[0])
            try:
                _TUI_QUEUE.put((_RENDER_KIND, (tuple(objects), style, markup, sep, end)))
            except Exception:  # noqa: BLE001, S110
                pass
            return
        super().print(*objects, **kwargs)

    def rule(self, *args, **kwargs):
        if _TUI_ACTIVE and _TUI_QUEUE is not None:
            text = args[0] if args else kwargs.get("title", "")
            style = kwargs.get("style")
            if not text:
                text = "─" * 40
            try:
                _TUI_QUEUE.put((_RULE_KIND, (text, style)))
            except Exception:  # noqa: BLE001, S110
                pass
            return None
        return super().rule(*args, **kwargs)


def _bridge_send(kind, payload):
    if _TUI_ACTIVE and _TUI_QUEUE is not None:
        try:
            _TUI_QUEUE.put((kind, payload))
        except Exception:  # noqa: BLE001, S110
            pass


_PASSWORD_ID_COUNTER = 0


def _request_tui_password(message):
    """Minta password lewat dialog TUI dari worker thread (blokir sampai
    dijawab/dibatalkan). Mengembalikan string password atau None bila batal."""
    global _PASSWORD_ID_COUNTER
    if not (_TUI_ACTIVE and _TUI_QUEUE is not None):
        return None
    _PASSWORD_ID_COUNTER += 1
    rid = f"pw{_PASSWORD_ID_COUNTER}"
    holder = {"event": threading.Event(), "result": None}
    _PASSWORD_REQUESTS[rid] = holder
    _bridge_send(_PASSWORD_KIND, (rid, message))
    while not holder["event"].wait(0.2):
        if _joki_cancel.is_set():
            holder["result"] = None
            holder["event"].set()
            break
    _PASSWORD_REQUESTS.pop(rid, None)
    return holder["result"]


def _resolve_tui_password(rid, value):
    """Jawab permintaan password dari dialog TUI (panggil dari thread UI)."""
    holder = _PASSWORD_REQUESTS.get(rid)
    if holder is not None:
        holder["result"] = value
        holder["event"].set()


def _tui_start(queue, dark=True):
    global _TUI_ACTIVE, _TUI_QUEUE, _TUI_DARK
    _TUI_ACTIVE = True
    _TUI_QUEUE = queue
    _TUI_DARK = bool(dark)


def _tui_stop():
    global _TUI_ACTIVE, _TUI_QUEUE
    _TUI_ACTIVE = False
    _TUI_QUEUE = None


# === Tema (adaptif ke environment, gaya opencode "system") ===
# Nilai ter-resolve: True = gelap, False = terang. Di-resolve lazy sekali lewat
# _resolve_theme(); dipakai TUI (Textual: self.dark) & Syntax highlight (rich).
# Definisi awal di baris 78 untuk hindari circular import.

def _config_theme_setting():
    """Baca nilai `theme` dari config.json: 'system' (default) | 'light' | 'dark'."""
    try:
        from joki.config import _get_config_path  # deferred: hindari circular import
        with open(_get_config_path()) as _f:
            data = json.load(_f)
        value = str(data.get("theme", "system")).strip().lower()
        return value if value in ("light", "dark", "system") else "system"
    except Exception:  # noqa: BLE001
        return "system"


def _gsettings_color_scheme():
    """Deteksi skema warna sistem via GNOME gsettings. None jika tak tersedia."""
    try:
        out = subprocess.run(
            ["gsettings", "get", "org.gnome.desktop.interface", "color-scheme"],
            capture_output=True, text=True, timeout=1, check=False,
        ).stdout.strip().lower()
    except Exception:  # noqa: BLE001
        out = ""
    if "prefer-dark" in out:
        return "dark"
    if "prefer-light" in out:
        return "light"
    # 'default' / kosong → ikuti gtk-theme. Tema GNOME bawaan: terang kecuali
    # bernama ...-dark (mis. 'Yaru-dark', 'Adwaita-dark').
    try:
        theme = subprocess.run(
            ["gsettings", "get", "org.gnome.desktop.interface", "gtk-theme"],
            capture_output=True, text=True, timeout=1, check=False,
        ).stdout.strip().lower()
    except Exception:  # noqa: BLE001
        theme = ""
    if theme:
        if "dark" in theme:
            return "dark"
        return "light"
    return None


def _osc11_background(timeout=0.4):
    """Query warna background terminal via OSC 11. Return (r, g, b) 0-255 atau None."""
    try:
        import select
        import termios
        stdin_fd = sys.stdin.fileno()
        if not sys.stdin.isatty() or not sys.stdout.isatty():
            return None
        old = termios.tcgetattr(stdin_fd)
        raw = termios.tcgetattr(stdin_fd)
        raw[3] &= ~(termios.ECHO | termios.ICANON)
        termios.tcsetattr(stdin_fd, termios.TCSANOW, raw)
        try:
            sys.stdout.write("\x1b]11;?\x1b\\")
            sys.stdout.flush()
            if not select.select([sys.stdin], [], [], timeout)[0]:
                return None
            resp = os.read(stdin_fd, 256)
        finally:
            termios.tcsetattr(stdin_fd, termios.TCSANOW, old)
        match = re.search(
            r"rgb:([0-9a-fA-F]+)/([0-9a-fA-F]+)/([0-9a-fA-F]+)",
            resp.decode("latin1", "replace"),
        )
        if not match:
            return None

        def _channel(hexpart):
            if len(hexpart) == 1:
                return int(hexpart, 16) * 17
            return int(hexpart[:2], 16)

        return (_channel(match.group(1)), _channel(match.group(2)), _channel(match.group(3)))
    except Exception:  # noqa: BLE001
        return None


def _detect_dark():
    """Tentukan mode gelap/terang berdasarkan config + environment."""
    setting = _config_theme_setting()
    if setting == "light":
        return False
    if setting == "dark":
        return True
    g = _gsettings_color_scheme()
    if g == "dark":
        return True
    if g == "light":
        return False
    bg = _osc11_background()
    if bg is not None:
        lum = (0.2126 * bg[0] + 0.7152 * bg[1] + 0.0722 * bg[2]) / 255.0
        return lum < 0.5
    return True


def _resolve_theme():
    """Resolve sekali (lazy cache) dan kembalikan mode gelap/terang app."""
    global _THEME_DARK
    if _THEME_DARK is None:
        _THEME_DARK = _detect_dark()
    return _THEME_DARK


def _set_theme(setting):
    """Set mode gelap/terang langsung dan simpan ke config.json. Return dark: bool."""
    global _THEME_DARK
    if isinstance(setting, str):
        dark = setting.strip().lower() not in ("light", "terang")
    else:
        dark = bool(setting)
    _THEME_DARK = dark
    try:
        from joki.config import _get_config_path  # deferred: hindari circular import
        path = _get_config_path()
        with open(path) as f:
            data = json.load(f)
        data["theme"] = "dark" if dark else "light"
        with open(path, "w") as f:
            json.dump(data, f, indent=2)
    except Exception:  # noqa: BLE001, S110
        pass
    return dark


_console = _BridgeConsole()


class _SideCard:
    def __init__(self, body, title, border_style="blue", style=None):
        self.body = body
        self.title = title
        self.border_style = border_style
        self.style = style

    def __rich_console__(self, console, options):
        from rich.text import Text
        border = console.get_style(self.border_style)

        if self.title:
            try:
                # Judul selalu hitam (terang) / putih (gelap); border tetap warna aslinya.
                title_color = "#FFFFFF" if _resolve_theme() else "#000000"
                head = Text.from_markup(
                    f"[bold {title_color}]{self.title}[/bold {title_color}]"
                )
            except Exception:  # noqa: BLE001
                head = Text(str(self.title))
            line = Text()
            line.append("│ ", style=border)
            line.append_text(head)
            if self.style:
                line.stylize(self.style)
            yield line

        body = self.body
        if isinstance(body, str):
            try:
                body = Text.from_markup(body)
            except Exception:  # noqa: BLE001
                body = Text(body)
        for segments in console.render_lines(body, options, pad=False):
            line = Text()
            line.append("│ ", style=border)
            for segment in segments:
                line.append(segment.text, segment.style)
            if self.style:
                line.stylize(self.style)
            yield line


def _joki_card(body, title, border_style="blue"):
    return _SideCard(body, title, border_style, style=_joki_card_bg())


def _joki_card_bg():
    """Background putih saat tema terang agar semua card konsisten."""
    return None if _resolve_theme() else "on white"


_current_model_config = {}
_CURRENT_SESSION = None
_joki_cancel = threading.Event()
_MONITOR_PAUSED = threading.Event()
_cancel_thread_running = threading.Event()
_cancel_monitor = None
_monitor_stdin_snapshot = {"old": None, "fd": None}
# Ditandai oleh _stop_cancel_monitor setelah thread utama restore termios
# secara sinkron. Dipakai agar thread monitor yang baru sadar dari read tidak
# menimpa mode terminal milik prompt_toolkit.
_monitor_restore_done = threading.Event()
_exhausted_keys = set()
_SUDO_PASSWORD = None
_PERSISTENT_SHELL = None
_SHELL_LOCK = threading.RLock()
_PTY_SESSION = None
_PTY_LOCK = threading.Lock()
_READ_FILES = set()
_CURRENT_SPINNER = None
_current_task_mode = MODE_GENERAL
_LSP_CLIENTS = {}
_LSP_LOCK = threading.Lock()

# === Manajemen konteks & memori kerja ===
# Tool yang pernah dipakai sesi ini (untuk mengurangi jumlah schema tool yang dikirim)
_recent_tools = set()
# Cache isi file: abs_path -> {"hash", "mtime"}
_file_cache = {}
# Tool result read_file yang masih tersedia di history pesan:
# abs_path -> {"hash", "idx", "head"}
_read_in_context = {}
# File yang berubah sejak notifikasi terakhir (untuk mekanisme perubahan file)
_file_change_log = set()

class JokiError(Exception): pass
class ToolError(JokiError): pass
class LLMError(JokiError): pass
class ConfigError(JokiError): pass


# === Monitor cancel bersama (Esc+Esc / Ctrl+C) ===
# Satu-satunya pembaca stdin saat agent_loop berjalan. Thread lain (spinner,
# call_llm) TIDAK boleh menyentuh stdin/termios — cukup cek `_joki_cancel`.

def _monitor_cancel_loop():
    _stdin_old = None
    _read_fd = None
    try:
        import select
        import termios
        _stdin_fd = sys.stdin.fileno()
        _stdin_old = termios.tcgetattr(_stdin_fd)
        _monitor_stdin_snapshot["old"] = _stdin_old
        _monitor_stdin_snapshot["fd"] = _stdin_fd
        _new = termios.tcgetattr(_stdin_fd)
        # Only set input-related raw flags — keep OPOST (output processing) ON
        # so stdout \n → \r\n conversion still works
        _new[0] &= ~(termios.BRKINT | termios.ICRNL | termios.INPCK | termios.ISTRIP | termios.IXON)
        _new[2] &= ~(termios.CSIZE | termios.PARENB)
        _new[2] |= termios.CS8
        _new[3] &= ~(termios.ECHO | termios.ICANON | termios.IEXTEN | termios.ISIG)
        _new[6][termios.VMIN] = 1
        _new[6][termios.VTIME] = 0
        termios.tcsetattr(_stdin_fd, termios.TCSANOW, _new)
        # Baca dari handle terminal yang DIBUKA BARU (bukan dup/fd 0) yang dibuat
        # non-blocking. Ini mencegah race select→read di mana byte sudah dikonsumsi
        # pembaca lain (mis. subprocess dry-test yang mewarisi tty) sehingga
        # os.read blok selamanya dan thread jadi zombie yang menelan keystroke
        # prompt `joki>` setelah agent_loop selesai. Wajib dibuka baru, bukan dup:
        # dup/dup2 berbagi open file description yang sama (termasuk flag
        # O_NONBLOCK) — stdout biasanya dup dari tty yang sama dengan stdin, jadi
        # set O_NONBLOCK di sana akan bikin stdout non-blocking juga dan rich
        # display gagal menulis (BlockingIOError: EAGAIN) saat buffer tty penuh.
        try:
            import fcntl
            _read_fd = os.open(os.ttyname(_stdin_fd), os.O_RDONLY | os.O_NOCTTY)
            _flags = fcntl.fcntl(_read_fd, fcntl.F_GETFL)
            fcntl.fcntl(_read_fd, fcntl.F_SETFL, _flags | os.O_NONBLOCK)
        except Exception:  # noqa: BLE001
            _read_fd = _stdin_fd
    except Exception:  # noqa: BLE001, S110
        pass
    _esc_count = 0
    _last_esc = 0.0
    try:
        while _cancel_thread_running.is_set() and not _joki_cancel.is_set():
            if _MONITOR_PAUSED.is_set():
                time.sleep(0.1)
                continue
            if _stdin_old is not None:
                import select
                if select.select([sys.stdin], [], [], 0.1)[0]:
                    try:
                        key = os.read(_read_fd, 1)
                    except (BlockingIOError, OSError):
                        continue
                    if key == b'\x1b':
                        now = time.time()
                        if now - _last_esc > 1.0:
                            _esc_count = 0
                        _esc_count += 1
                        _last_esc = now
                        if _esc_count >= 2:
                            _joki_cancel.set()
                            break
                    elif key == b'\x03':
                        _joki_cancel.set()
                        break
                    else:
                        _esc_count = 0
            else:
                time.sleep(0.1)
    except Exception:  # noqa: BLE001, S110
        pass
    finally:
        # Jangan restore termios kalau thread utama (via _stop_cancel_monitor)
        # sudah restore secara sinkron — mencegah thread zombie menimpa mode
        # terminal milik prompt_toolkit.
        if _stdin_old is not None and not _monitor_restore_done.is_set():
            try:
                import termios
                termios.tcsetattr(_stdin_fd, termios.TCSANOW, _stdin_old)
            except Exception:  # noqa: BLE001, S110
                pass
        if _read_fd is not None and _read_fd != _stdin_fd:
            try:
                os.close(_read_fd)
            except Exception:  # noqa: BLE001, S110
                pass

def _start_cancel_monitor():
    global _cancel_monitor
    if _TUI_ACTIVE:
        # Textual memiliki event loop & key binding sendiri; stdin tidak boleh
        # dipegang thread monitor raw.
        return
    if _cancel_monitor is not None and _cancel_monitor.is_alive():
        return
    _cancel_thread_running.set()
    _monitor_restore_done.clear()
    _cancel_monitor = threading.Thread(target=_monitor_cancel_loop, daemon=True, name="joki-cancel-monitor")
    _cancel_monitor.start()

def _stop_cancel_monitor():
    global _cancel_monitor
    if _TUI_ACTIVE:
        return
    _cancel_thread_running.clear()
    if _cancel_monitor is not None:
        # Restore termios SECARA SINKRON di thread utama supaya terminal tidak
        # pernah tertinggal di mode raw setelah agent_loop selesai — ini yang
        # membuat prompt `joki>` tak bisa diketik setelah dry-run stop.
        _snap_old = _monitor_stdin_snapshot.get("old")
        _snap_fd = _monitor_stdin_snapshot.get("fd")
        if _snap_old is not None and _snap_fd is not None:
            try:
                import termios
                termios.tcsetattr(_snap_fd, termios.TCSANOW, _snap_old)
            except Exception:  # noqa: BLE001, S110
                pass
        _monitor_restore_done.set()
        for _ in range(10):
            _cancel_monitor.join(timeout=0.1)
            if not _cancel_monitor.is_alive():
                break
        _cancel_monitor = None
