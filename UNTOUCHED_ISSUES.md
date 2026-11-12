# Status Perbaikan Joki

Tanggal: 2026-11-10

Dokumen ini adalah status terkini setelah recovery source. Analisis awal 2026-11-06 dilampirkan di bagian bawah; status "belum diperbaiki" di dalam lampiran adalah status historis saat dokumen itu dibuat.

## Ringkasan status terkini

| Item | Status | Ringkasan |
| --- | --- | --- |
| 1. Klasifikasi mode tugas | Selesai | Override eksplisit, referensi file source, sinyal proyek, dan konteks permintaan sebelumnya dipakai; tidak lagi keyword-only. |
| 2. Cap 6 tool call | Selesai | Semua tool call yang dihasilkan model dipertahankan dan dieksekusi. |
| 3. Summarization/trimming konteks | Selesai | `[BUKTI KONTEKS]` deterministik menjaga request awal, path, error, hasil test, dan perintah terakhir. |
| 4. Memori sesi basi | Selesai | Memory v2: TTL, scope tugas/global, filter relevansi, batas injeksi, migrasi format lama, dan penandaan memori lama/kedaluwarsa. |
| 5. System prompt panjang | Selesai | `_SYSTEM_PROMPT_BASE` dipadatkan menjadi 5.707 karakter/61 baris; aturan berhenti lama dan auto-test yang tidak sinkron dihapus. |
| 6. Hard-stop iterasi/loop | Selesai | Batas iterasi dan hard cap berbasis progress file/evidence, loop identik dinudge, watchdog no-progress, dan pesan final tidak menyuruh user memberi petunjuk. |
| 7. Gate menyuruh user/reproduksi | Selesai | Deteksi fix lebih sempit, override user, path baca tahan invalidasi cache, dan status `[BELUM DIKERJAKAN]` bila model tetap handoff. Gate juga mencakup gejala fitur/UI seperti `perbaiki fitur chat`, `tidak ada scroll`, dan `tidak muncul`. |
| 8. Akurasi verifier | Selesai (inti) | Deteksi lint-only, output palsu/nol test, evidence relevan, hash/urutan bukti, judge independen, dan penolakan klaim tanpa mutasi/bukti. |

Validasi terakhir:

```text
524 passed, 6 skipped
compileall: lolos
ruff --select E9,F63,F7,F82: lolos
tests/test_verifier.py: 16 passed
```

Sisa risiko item 8: keputusan akhir tetap bergantung pada model judge; prompt judge dan filter deterministik hanya mengurangi, bukan menghilangkan, kemungkinan false positive/negative. Item 3 memakai regex dan batas snippet; item 4 memakai relevansi token sederhana; item 6 menganggap progress sebagai perubahan file/evidence, bukan kemajuan analisis murni.

---

# Lampiran: analisis awal 2026-11-06

# Bagian Joki yang Belum Tersentuh Perbaikan Hard-Stop Verifier dan Fallback Model

Tanggal: 2026-11-06

Dokumen ini mencatat bagian-bagian yang **belum** disentuh oleh dua perbaikan terakhir:

1. Hard-stop verifier berbasis progress.
2. Reorientasi model setelah fallback di tengah loop.

Tujuannya agar penyebab lain dari perilaku ngawur/ngelantur tidak terlupakan. Dokumen ini tidak mengubah kode.

## Ringkasan

Dua perbaikan sebelumnya hanya menutup:

- berhenti prematur saat model masih memperbaiki dan menghasilkan bukti baru;
- model fallback yang lanjut bekerja tanpa tahu konteks telah berganti model.

Bagian di bawah ini masih terbuka dan berpotensi menjadi sumber utama ngawur/ngelantur.

## 1. Klasifikasi mode tugas

Lokasi: `joki/cli.py:354`

```python
def _classify_task(user_message):
    msg_lower = (user_message or "").lower()

    code_keywords = [...]
    sysadmin_keywords = [...]
    security_keywords = [...]

    code_score = _count_kw_bounded(msg_lower, code_keywords)
    sysadmin_score = _count_kw_bounded(msg_lower, sysadmin_keywords)
    security_score = _count_kw_bounded(msg_lower, security_keywords)

    if security_score >= 1 and security_score >= sysadmin_score and security_score >= code_score:
        return MODE_SECURITY
    if sysadmin_score > code_score and sysadmin_score >= 2:
        return MODE_SYSADMIN
    if code_score >= 1:
        return MODE_CODE
    if sysadmin_score >= 1:
        return MODE_SYSADMIN
    return MODE_GENERAL
```

Mode hanya ditentukan dari pesan user terakhir dan pencocokan keyword. Tidak ada analisis struktur proyek, riwayat tugas, atau konteks file. Contoh: `backup`, `install`, `config`, dan `port` masuk daftar sysadmin; `buat`, `tulis`, `bug`, dan `api` masuk daftar coding.

Dampak:

- `_current_tools()` di `joki/llm.py:16` memfilter tool berdasarkan mode:
  ```python
  def _current_tools():
      mode = _current_task_mode
      if mode == MODE_GENERAL:
          names = set(CORE_TOOLS) | set(_recent_tools)
          return [t for t in TOOLS if t["function"]["name"] in names]
      return get_tools_for_mode(mode)
  ```
- Mode yang salah bisa memberi tool set yang tidak sesuai.
- Gate yang bergantung pada mode bisa aktif atau tidak aktif secara keliru.
- Tugas campuran, misalnya “install dependensi lalu perbaiki kode”, bisa diklasifikasikan sebagai sysadmin padahal inti tugasnya coding.

Status: belum diperbaiki.

## 2. Pemangkasan tool call menjadi maksimal 6

Lokasi: `joki/llm.py:757`

```python
# Hemat 16k Nemotron: batasi 6 tool_calls per turn.
if len(_sorted_tc) > 6:
    _sorted_tc = _sorted_tc[:6]
    _tc_note = (
        "\n\n_[WARNING: 6+ tool_calls dipangkas agar muat 16384 "
        "max_tokens Nemotron. Pecah task jadi kecil.]_"
    )
```

Jika model mengirim lebih dari 6 tool call, hanya 6 pertama yang dieksekusi. Sisanya dibuang, dan model hanya menerima catatan bahwa ada pemangkasan.

Dampak:

- Edit multi-file bisa berhenti di tengah rencana.
- Model berikutnya melihat kondisi file yang belum lengkap.
- Auto-test bisa berjalan pada hasil yang belum selesai.
- Model bisa mengulang tool call yang sama karena mengira belum dijalankan.
- Tidak ada mekanisme eksplisit untuk melanjutkan sisa tool call yang dipangkas.

Status: belum diperbaiki.

## 3. Summarization dan trimming konteks

Lokasi utama: `joki/llm.py:377` dan `joki/llm.py:227`

Konstanta di `joki/llm.py:23`:

```python
SUMMARY_TRIGGER_RATIO = 0.65
SUMMARY_WORKING_SET = 8
REFACTOR_WORKING_SET = 20
_SUMMARY_MSG_CHARS = 2000
_SUMMARY_TOTAL_CHARS = 120000
```

Alur `_summarize_context`:

```python
total = sum(estimate_tokens(m) for m in messages)
if total <= int(budget * SUMMARY_TRIGGER_RATIO):
    return messages

_working_set = REFACTOR_WORKING_SET if _is_refactor_ctx else SUMMARY_WORKING_SET
...
summary = _call_summary(mc, old)
messages[:] = (
    messages[:proto]
    + [{"role": "system", "content": f"[RINGKASAN KONTEKS]\n{summary}"}]
    + messages[keep_start:]
)
```

Artinya:

- Konteks mulai diringkas saat mencapai 65% budget.
- Hanya 8 turn terakhir dipertahankan; 20 turn saat SOP refactor aktif.
- Setiap pesan lama dipotong maksimal 2000 karakter sebelum diringkas.
- Ringkasan dibuat oleh model yang sama, memakai prompt terpisah.
- Pesan system prefix seperti system prompt, project index, dan checkpoint dilindungi.

Trimming FIFO di `_trim_messages`:

```python
start, end = min(removable, key=lambda u: u[0])
for k in range(end - 1, start - 1, -1):
    total -= estimate_tokens(messages.pop(k))
```

Dampak:

- Output error asli, path file, dan bukti test bisa hilang.
- Ringkasan bisa salah, terlalu umum, atau menghilangkan detail penting.
- Model bisa “lupa” alasan suatu perubahan.
- Evidence dan hasil tool lama tidak punya perlindungan setara dengan checkpoint.
- Kesalahan dari ringkasan bisa menjadi konteks palsu untuk iterasi berikutnya.

Status: belum diperbaiki.

## 4. Memori sesi yang bisa basi

Lokasi: `joki/tools/memory.py:11` dan `joki/cli.py:3602`

```python
def _memory_path(name=None):
    name = name or _get_current_session() or "default"
    return os.path.join(_get_data_dir(), "memories", f"{name}.json")
```

Semua memori diinjeksi ke system prompt:

```python
def _build_system_prompt(include_memory=True):
    base = _SYSTEM_PROMPT_BASE
    ...
    if include_memory:
        memories = _load_memory()
        if memories:
            items = "\n".join(f"  - {k}: {v[:120]}" for k, v in memories.items())
            base += f"\n\nMemori tersimpan ({len(memories)}):\n{items}\n\n"
```

Dampak:

- Semua memori sesi dimasukkan tanpa filter relevansi.
- Tidak ada TTL, skor kedaluwarsa, atau pemisahan per tugas.
- Saat sesi yang sama di-resume, fakta dari tugas lama masih terbawa.
- Model bisa memakai asumsi lama untuk tugas baru yang sebenarnya berbeda.
- Memori yang salah atau sudah tidak berlaku tidak punya mekanisme koreksi otomatis.

Perbaikan checkpoint hanya membatasi merge saat tugas baru; isi `memories/*.json` tidak ikut dibersihkan.

Status: belum diperbaiki.

## 5. System prompt panjang dan saling bertentangan

Lokasi: `joki/cli.py:57`

```python
_SYSTEM_PROMPT_BASE = (
    "Kamu adalah Joki — AI agent ...\n\n"
    "ATURAN UTAMA — PATUHI ATAU KAMU DIHENTIKAN:\n"
    "1. KERJAKAN SATU TUGAS SAMPAI SELESAI. Jangan ngelantur ke hal lain.\n"
    ...
)
```

Ukuran aktual:

```text
SYSTEM_PROMPT_BASE chars: 14693
SYSTEM_PROMPT_BASE lines: 253
```

Belum termasuk tambahan dinamis:

- direktori kerja;
- memori tersimpan;
- skills;
- project index;
- checkpoint;
- SOP refactor;
- nudge verifikasi.

Contoh instruksi yang bertentangan dengan perilaku baru:

```python
"4. KALAU BUNTU (3x gagal berturut-turut), LAPORKAN KE USER — Jangan loop sendiri."

"MAKS 3 KALI EDIT PERCUMAAN. Kalo 3x masih gagal: BERHENTI, lapor ke user."

"AUTO-TEST: ... Maks 5 percobaan. Kalo mentok, lapor ke user."
```

Padahal auto-test di kode berjalan satu kali per edit, lalu mengandalkan iterasi agent. Prompt juga masih menyuruh “lapor ke user” dan “tanya petunjuk lanjutan”, yang bisa memicu handoff manual.

Dampak:

- Aturan penting seperti “jangan klaim selesai tanpa bukti” bersaing dengan ratusan baris panduan bahasa, framework, git, dan tool.
- Model bisa memilih instruksi yang salahprioritas.
- Prompt mendorong perilaku “berhenti dan lapor” meskipun agent masih punya tool dan konteks untuk melanjutkan.
- Teks auto-test tidak sinkron dengan implementasi aktual.

Status: belum diperbaiki.

## 6. Hard-stop iterasi dan loop

### Batas iterasi model

Lokasi: `joki/cli.py:2458`

```python
_max_iter = _current_model_config.get("max_iterations", 0)
if _max_iter > 0 and _iteration > _max_iter:
    messages.append({
        "role": "user",
        "content": (
            f"[ITERATION LIMIT] Kamu sudah mencapai batas iterasi ({_max_iter}). "
            f"Berikan jawaban TEXT saja — jangan panggil tool apapun. "
            f"Jelaskan progress yang sudah dicapai dan langkah selanjutnya ke user."
        )
    })
    msg = call_llm(messages)
    _exit_reason = f"iteration limit ({_max_iter})"
    _should_stop = True
```

### Hard-stop 120 iterasi

Lokasi: `joki/cli.py:2477`

```python
if _iteration >= 120:
    messages.append({
        "role": "user",
        "content": (
            "[FINAL STOP] Kamu sudah 120x berpikir tanpa menyelesaikan task. "
            "Berhenti. Berikan ringkasan progress ke user dan tanya petunjuk lanjutan."
        )
    })
    msg = call_llm(messages)
    _exit_reason = "hard stop (120 iterasi)"
    _should_stop = True
```

### Check-in 80 iterasi

Lokasi: `joki/cli.py:2494`

```python
if _iteration >= 80 and not _checkin_injected:
    _checkin_injected = True
    messages.append({
        "role": "user",
        "content": (
            "[CHECK-IN] Kamu sudah 80x berpikir. Sepertinya task ini kompleks. "
            "Berikan progress report singkat ke user ... dan tanya apakah mau "
            "dilanjutkan atau diubah pendekatannya."
        )
    })
```

### Deteksi loop tool

Lokasi: `joki/cli.py:3130`

```python
if len(_call_history) >= 5:
    last5 = _call_history[-5:]
    if all(c[0] == last5[0][0] and c[1] == last5[0][1] for c in last5):
        messages.append({
            "role": "user",
            "content": (
                f"[LOOP DETECTED] Kamu memanggil `{last5[0][0]}` dengan "
                "argumen yang sama 5x berturut-turut. Berhenti. Berikan "
                "jawaban TEXT — jelaskan kenapa kamu stuck dan apa yang "
                "butuh diubah user."
            )
        })
        _exit_reason = "loop detected (tool sama 5x)"
        _should_stop = True
```

Dampak:

- `max_iterations` default model utama adalah 40 (`joki/config.json`).
- Iterasi ke-121 langsung berhenti, walaupun model sedang memperbaiki dan punya progress.
- Loop detector hanya melihat tool dan argumen yang identik.
- Model yang berputar dengan argumen sedikit berbeda tidak terdeteksi.
- Pembacaan file yang sama berulang untuk alasan valid bisa dianggap loop.
- Pesan hard-stop menyuruh model “tanya petunjuk lanjutan”, yang kembali mendorong intervensi user.
- Tidak ada hubungan antara batas iterasi dan signature progress yang dipakai verifier.

Status: belum diperbaiki.

## 7. Gate “menyuruh user” dan gate reproduksi

### Detektor menyuruh user

Lokasi konstanta: `joki/cli.py:1611`

```python
_ASK_USER_MAX_RETRIES = 2
```

Lokasi gate: `joki/cli.py:3162`

```python
if (content and _ask_user_retries < _ASK_USER_MAX_RETRIES
        and _ASK_RUN_RE.search(content)
        and _SHELLISH_RE.search(content)):
    ...
    messages.append({
        "role": "user",
        "content": (
            "[AKSI SENDIRI] Jangan menyuruh user menjalankan perintah — "
            "kamu punya tool run_command. Jalankan sendiri SEKARANG..."
        ),
    })
```

Dampak:

- Setelah 2 kali dinudge, jika model tetap menyuruh user menjalankan perintah, Joki membiarkannya.
- Hasil akhir bisa berupa “silakan jalankan X”, yaitu handoff manual.
- Detektor berbasis regex bisa salah mendeteksi perintah yang memang harus dijalankan user karena alasan keamanan.

Status: belum diperbaiki.

### Gate reproduksi

Lokasi flag: `joki/cli.py:2317`

```python
_require_repro = bool(_repro_probe and _is_error_query(_repro_probe))
```

Lokasi gate: `joki/cli.py:2711`

```python
if (
    _require_repro
    and not _has_repro_run
    and name in ("edit_file", "write_file", "rename_symbol", "config_edit")
    and not (name == "edit_file" and _read_before_edit)
    and _repro_nudges < _REPRO_MAX_NUDGES
):
    result = (
        "[REPRODUKSI WAJIB] Kamu diminta memperbaiki error, tapi belum "
        "menjalankan perintah apapun untuk melihat error aslinya. "
        "Edit DITAHAN..."
    )
```

Dampak:

- Jika `_is_error_query` salah mendeteksi tugas biasa sebagai tugas error, edit bisa ditahan.
- Setelah edit pertama, cache baca bisa invalid, sehingga edit kedua dianggap belum membaca file dan ditahan lagi.
- Gate ini pernah membuat tes verifier progress gagal: edit kedua diblokir, jadi tidak ada progress.
- Setelah 2 nudge, gate terbuka, tetapi iterasi sudah terbuang.
- Tidak ada override eksplisit dari user selain menunggu nudge habis.

Status: belum diperbaiki.

## 8. Akurasi verifier sendiri

Perbaikan terakhir hanya mengubah **kapan berhenti**, bukan **bagaimana verifier menilai**.

Verifier masih bisa:

- memberi PASS palsu;
- memberi FAIL palsu;
- SKIP karena timeout, quota, atau error;
- menilai berdasarkan evidence yang sudah tidak lengkap karena trimming atau summarization;
- menerima bukti yang secara semantik tidak relevan tetapi lolos pemeriksaan bentuk.

Dampak:

- Hard-stop yang lebih toleran tidak memperbaiki kualitas penilaian.
- Klaim ngawur bisa tetap lolos jika verifier memberi PASS.
- Pekerjaan benar bisa tetap tertahan jika verifier memberi FAIL.
- SKIP tidak sama dengan PASS, tetapi dalam kondisi tertentu Joki tetap lanjut dengan label belum terverifikasi.

Status: belum diperbaiki.

## 9. Ringkasan prioritas

| Bagian | Dampak ke ngawur/ngelantur | Prioritas |
|---|---|---|
| Klasifikasi mode keyword-only | Tool/gate salah, edit diblokir | Tinggi |
| Cap 6 tool call | Pekerjaan multi-file terpotong | Tinggi |
| Summarization/trimming | Bukti dan konteks hilang | Tinggi |
| Memori sesi tanpa TTL/filter | Asumsi lama terbawa | Tinggi |
| Prompt panjang + kontradiksi | Model melanggar aturan penting, memilih handoff | Tinggi |
| Hard-stop iterasi/loop non-progress | Berhenti prematur, atau tidak mendeteksi loop halus | Sedang-tinggi |
| Detektor “menyuruh user” | Handoff manual tetap lolos | Sedang |
| Gate reproduksi | Edit valid ditahan, iterasi terbuang | Sedang |
| Akurasi verifier | PASS/FAIL/SKIP salah | Sedang-tinggi |

## 10. Saran urutan perbaikan

1. **Konteks dan bukti**
   - Simpan evidence terstruktur di luar message history.
   - Jangan biarkan trimming/summarization membuang error output, path, dan hasil test terakhir.
   - Buat ringkasan deterministik untuk data penting, bukan hanya ringkasan LLM.

2. **Mode tugas**
   - Gunakan konteks proyek dan riwayat tugas, bukan hanya keyword.
   - Sediakan override eksplisit dari user.
   - Catat alasan klasifikasi mode untuk debugging.

3. **Tool-call cap**
   - Buat batas konfigurabel per model/context window.
   - Beri mekanisme melanjutkan tool call yang dipangkas.
   - Beri tahu model secara eksplisit tool mana yang belum dijalankan.

4. **Memori**
   - Tambahkan TTL, scope tugas, dan filter relevansi.
   - Jangan inject semua memori ke system prompt.
   - Tandai memori yang berasal dari tugas lama.

5. **Prompt**
   - Pendekkan `_SYSTEM_PROMPT_BASE`.
   - Pindahkan panduan bahasa/framework ke skills atau referensi terpisah.
   - Sinkronkan teks auto-test dengan implementasi.
   - Hapus instruksi “lapor ke user setelah 3x gagal” yang bertentangan dengan perilaku otonom.

6. **Hard-stop non-verifier**
   - Buat batas iterasi dan deteksi loop berbasis progress, bukan hanya jumlah iterasi atau argumen identik.
   - Beri grace period saat masih ada file/evidence baru.
   - Jangan paksa model “tanya petunjuk lanjutan” saat masih bisa melanjutkan.

7. **Gate menyuruh user dan reproduksi**
   - Perbaiki presisi `_is_error_query`.
   - Izinkan override user untuk gate reproduksi.
   - Jangan biarkan cache baca yang invalid membuat edit kedua diblokir.
   - Setelah batas nudge, hasilkan status eksplisit, bukan jawaban “silakan jalankan”.

8. **Verifier**
   - Perkuat validasi evidence dan relevansi.
   - Bedakan SKIP, FAIL, dan PASS secara tegas di semua jalur.
   - Tambahkan pengujian untuk false PASS dan false FAIL.

## 11. Catatan pengujian

Per 2026-11-06:

```text
494 passed, 6 skipped
compileall: lolos
ruff --select E9,F63,F7,F82: lolos
```

Tes tersebut memakai LLM/verifier palsu. Belum ada perbandingan sesi nyata sebelum dan sesudah perubahan, sehingga dampak ke ngawur/ngelantur di produksi belum terukur.
