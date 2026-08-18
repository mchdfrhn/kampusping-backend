# Contoh Alembic Migration (pola e-certificate)

Semua perubahan skema database **wajib** lewat Alembic. Jangan membuat tabel manual di server production.

Referensi pola yang sudah diverifikasi: `training-backend-ecertificate/alembic/versions/`

- `002_create_menus_mapping.py`
- `004_create_app_allowed_roles.py`

Starter ini menyediakan migrasi gabungan: [`alembic/versions/001_reference_catalog.py`](../alembic/versions/001_reference_catalog.py).

## Aturan ID auto-increment

| Engine | Kolom `id` | Catatan |
|---|---|---|
| SingleStore | `BIGINT AUTO_INCREMENT PRIMARY KEY` | Gunakan `CREATE ROWSTORE REFERENCE TABLE` untuk tabel referensi kecil |
| PostgreSQL / MySQL | `sa.BigInteger(), autoincrement=True` | ID dimulai dari 1 secara default |

## Perintah

```bash
alembic upgrade head
alembic revision -m "add your table"
alembic downgrade -1
```

## Seed data

- ID IAM role dan menu ID **hanya** di migration seed atau insert admin — tidak di `router.py`.
- Ganti contoh `1001`, `1002`, `9001` di `001_reference_catalog.py` dengan nilai IAM project Anda.
- Jangan menyalin ID numerik dari project e-certificate (90, 91, 640, 641) ke project baru.
