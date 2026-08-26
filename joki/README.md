# Joki

**AI Agent CLI Otonom untuk Linux**

Joki (bahasa gaul Indonesia: "orang yang mengerjakan sesuatu untukmu") adalah AI agent berbasis terminal yang bisa mengeksekusi tugas sistem secara otonom — coding, manajemen server, database, web testing, reverse engineering, security scanning, kontrol hardware, dan processing media.

Dibuat oleh **Rahmad Budiman** ([budieqnet](https://github.com/budieqnet)).

Requirement: **Python 3.10+** (ditest dengan Python 3.12).

## Fitur Utama

- **75 tools bawaan** — baca/tulis/edit file (dengan undo & backup otomatis), shell commands, query database (MySQL/Postgres/MongoDB/SQLite/MSSQL/Oracle/Redis), web search/fetch/scrape + session login, port scanning, DNS enumeration, CVE search, web vulnerability scan, analisis JS/APK/binary, USB/serial/camera, audio/video transcribe & extract, UI automation (xdotool), git lengkap, linter, test runner, analisis dependency & impact, office files (DOCX/XLSX/PPTX/PDF), LSP code intelligence, todo management, dan memory persisten
- **Multi-model** — Nusa LLM (Qwen3 Coder, Qwen3.5, Ornith), Nemotron, GPT-OSS, DeepSeek V4 Flash, Gemma 4, Gemini (via OpenRouter), Ollama lokal; auto rotasi API key & fallback jika quota habis
- **Mode tugas otomatis** — klasifikasi permintaan ke Coding / Sysadmin / Security / General, dengan set tool yang disesuaikan
- **Auto-test & fix** — setelah menulis script, langsung di-run dan diperbaiki otomatis jika gagal (hingga 3-5 percobaan)
- **Context management** — project index, checkpoint tugas, dedup baca file, auto-summarization konteks, deteksi dry-run/stagnasi untuk mencegah loop
- **TUI & streaming** — interface terminal (Textual) dengan streaming respons + rendering Markdown
- **LSP pre-inject** — mendeteksi permintaan perbaikan error dan otomatis mengambil diagnostik LSP
- **Plugin system** — tambah tools kustom di `~/.local/share/joki/plugins/`
- **Memory** — memori jangka panjang lintas sesi
- **Keamanan** — konfirmasi untuk perintah destruktif/ireversibel, deteksi sandbox, sandbox execution, prediksi efek perintah (`predict_command`)

## Instalasi

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements_linux.txt
```

Opsional (untuk fitur tertentu, uncomment di `requirements_linux.txt`):
`pyserial`, `openai-whisper`, `pyautogui`.

## Konfigurasi

Config disimpan di `joki/config.json` (atau `~/.config/joki/config.json` pada build frozen). Auto-dibuat saat pertama kali dijalankan. File ini **tidak ikut di-version-control** (di-gitignore) karena berisi API key pribadi.

Salin template `joki/config.json.ori` menjadi `joki/config.json`, lalu isi API keys kamu:

```bash
cp joki/config.json.ori joki/config.json
```

Template menyediakan beberapa model siap pakai — isi setiap `"api_keys"` dengan key milikmu (atau biarkan kosong `""` untuk Ollama lokal). Struktur dasarnya:

```json
{
  "models": {
    "deepseek-v4-flash": {
      "name": "DeepSeek V4 Flash",
      "base_url": "https://api.deepseek.com",
      "model": "deepseek-v4-flash",
      "api_keys": [""],
      "provider": "openai",
      "max_tokens": 65536
    }
  }
}
```

API key juga bisa diisi lewat environment variable `JOKI_<NAMA_MODEL>_KEY` (atau `JOKI_OPENROUTER_KEY` untuk provider OpenRouter).

## Cara Pakai

```bash
python -m joki                          # mode REPL interaktif (TUI)
python -m joki "kerjakan sesuatu"        # one-shot
python -m joki /path/to/project "..."    # kerjakan di direktori tertentu
python -m joki --version                 # versi
python -m joki --update                  # update dari git
```

Atau via launcher:

```bash
python launcher.py
```

### Perintah slash

| Perintah | Fungsi |
|---|---|
| `/model` | Lihat/ganti model aktif, cek status API keys |
| `/model <nama>` | Ganti model (mis. `/model gemma4`) |
| `/baru` | Mulai session baru |
| `/reset_quota` | Reset state quota API key yang exhausted |
| `/reload` | Reload config.json |
| `/install-lsp` | Install LSP server |
| `/keluar` | Keluar |

Batal di tengah tugas: tekan **Esc Esc** (atau Ctrl+C).

## Struktur Proyek

```
launcher.py                          # entry point alternatif (python launcher.py)
requirements_linux.txt               # dependencies
tests/                               # pytest suite

joki/
  __main__.py                        # entry point (python -m joki)
  cli.py                             # loop agent, klasifikasi mode, perintah slash
  config.py, constants.py            # konfigurasi model & definisi tools
  executor.py                        # eksekusi tool
  llm.py                             # multi-model + auto rotasi key + context summarization
  tui.py, display.py, rich_display.py, thinking.py   # UI terminal
  session.py, state.py, utils.py     # session, state persistens, util
  plugins.py, project_index.py       # plugin system & indeks proyek
  tools/
    files.py, shell.py, database.py, web.py, security.py,
    reverse_eng.py, git.py, lint.py, lsp.py, deps.py,
    memory.py, ui.py, hardware.py, media.py, office.py
```

## Lisensi

MIT
