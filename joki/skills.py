import glob
import os

import yaml  # noqa: F401  # dibutuhkan untuk parse frontmatter markdown

from joki.config import _get_data_dir
from joki.display import _color_error, stream_print
from rich.markup import escape


# ============================================================
# DEFAULT (bawaan) SKILLS
# ============================================================
_DEFAULT_SKILLS = {
    "verifikasi-eksekusi.md": """\
---
name: Verifikasi Eksekusi Sebelum Klaim Selesai
always_on: true
---

Sebelum menyatakan sebuah task/fix selesai, WAJIB lakukan verifikasi berikut —
jangan cukup mengandalkan test suite atau linter lolos, karena keduanya
terbukti berkali-kali bisa lolos padahal ada bug nyata di jalur eksekusi:

1. **Eksekusi jalur kode yang diubah secara langsung**, bukan cuma
   import/syntax check. Kalau mengubah fungsi yang dipanggil saat runtime
   (misal tool handler, agent loop, request builder), jalankan skenario
   nyata yang melewati kode itu dan lihat hasilnya — bukan cuma asumsi dari
   membaca kode.

2. **Kalau menyentuh sistem yang punya 2 sisi yang harus selalu sinkron**
   (skema tool vs handler eksekusi, `requirements_linux.txt` vs
   `pyproject.toml`, nama key di config vs yang dirujuk kode/test) —
   cross-check keduanya secara eksplisit, jangan cuma edit salah satu sisi.

3. **Kalau membuat modul/file baru**, pastikan modul itu benar-benar
   dipanggil dari jalur yang aktif (entry point, fungsi yang tereksekusi
   tiap sesi) — bukan cuma didefinisikan lalu diimpor oleh dirinya sendiri
   atau modul lain yang juga tidak pernah dipanggil. Trace dari
   `__main__.py`/entry point sampai ke kode barumu, pastikan benar-benar
   tersambung.

4. **Jangan mengubah scope di luar yang diminta** tanpa menyebutkannya
   secara eksplisit ke user — terutama file konfigurasi (`config.json`) atau
   penghapusan test yang sudah ada. Kalau ada perubahan tambahan yang
   dirasa perlu, sebutkan terpisah dan biarkan user memutuskan, jangan
   digabung diam-diam ke task utama.

5. **Setelah verifikasi, laporkan secara spesifik apa yang dites dan
   hasilnya** (bukan cuma "sudah saya perbaiki") — supaya user bisa menilai
   tingkat keyakinan atas klaim itu.
""",
}


def _seed_default_skills():
    """Salin skill bawaan ke ~/.local/share/joki/skills/ jika belum ada
    (first-run). Mengikuti pola _auto_create_config() yang embedded template
    lalu di-write."""
    skill_dir = os.path.join(_get_data_dir(), "skills")
    try:
        os.makedirs(skill_dir, exist_ok=True)
    except Exception:  # noqa: BLE001
        return
    for fname, content in _DEFAULT_SKILLS.items():
        dest = os.path.join(skill_dir, fname)
        if not os.path.exists(dest):
            try:
                with open(dest, "w", encoding="utf-8") as fh:
                    fh.write(content)
            except Exception as e:  # noqa: BLE001
                stream_print(
                    f"[{_color_error()}]Gagal menulis skill bawaan {fname}: {escape(str(e))}[/{_color_error()}]\n"
                )


def _load_skills() -> str:
    """Muat semua skill always-on dari ~/.local/share/joki/skills/*.md,
    gabungkan isinya jadi satu blok teks untuk disisipkan ke system prompt.

    Format tiap file skill: frontmatter YAML (---...---) diikuti isi markdown.
    Frontmatter wajib punya field 'name' dan 'always_on' (bool).
    """
    _seed_default_skills()
    skill_dir = os.path.join(_get_data_dir(), "skills")
    os.makedirs(skill_dir, exist_ok=True)

    combined = []
    for f in sorted(glob.glob(os.path.join(skill_dir, "*.md"))):
        try:
            with open(f, "r", encoding="utf-8") as fh:
                raw = fh.read()
            if not raw.startswith("---"):
                continue
            _, fm_raw, body = raw.split("---", 2)
            meta = yaml.safe_load(fm_raw) or {}
            if not meta.get("always_on", False):
                continue  # skill non-always-on: belum didukung di v1, skip
            name = meta.get("name", os.path.basename(f))
            combined.append(f"### Skill: {name}\n{body.strip()}")
        except Exception as e:  # noqa: BLE001
            stream_print(f"[{_color_error()}]Gagal memuat skill {f}: {escape(str(e))}[/{_color_error()}]\n")

    if not combined:
        return ""
    return "\n\n---\n\n".join(combined)