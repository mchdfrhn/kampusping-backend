# HIS Standard Backend

Base backend resmi **PT. Hino Motors Sales Indonesia** untuk semua project HIS baru.

Stack: **FastAPI 0.138**, **SQLAlchemy 2**, **Alembic**, **Pydantic v2**, Python **>= 3.11**.

Tiga engine database didukung: **PostgreSQL**, **MySQL**, **SingleStore**. Bisa satu koneksi atau beberapa named connection (`PRIMARY`, `IAM`, `REFERENCE`).

Vendor dan IT internal **wajib** clone repo ini. ID IAM / menu / status **tidak boleh** di-hardcode di `router.py`.

## Menjalankan di lokal

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env
uvicorn app.main:app --reload
```

- Health: `GET /health`
- Swagger (non-production): `http://localhost:8000/docs`
- Contoh layering: `GET /api/v1/examples`

## Struktur folder

```text
app/
├── main.py                 # factory FastAPI, CORS, hide /docs di production
├── api/
│   ├── deps.py             # session PRIMARY / IAM / REFERENCE
│   └── v1/router.py        # daftar router versi 1
├── core/
│   ├── config.py           # Settings / .env
│   ├── constants/          # pagination, header, error code
│   ├── role_catalog.py     # tipe role (string), baca ID dari DB
│   ├── auth.py             # CurrentUser, get_current_user
│   ├── auth_session.py     # match IAM roles ↔ app_allowed_roles
│   ├── iam_verify.py       # parse payload IAM
│   ├── exceptions.py       # AppException + envelope error
│   ├── middleware.py
│   └── logging.py
├── db/
│   ├── session.py          # single engine (async/sync)
│   ├── multi_session.py    # named engines
│   ├── base.py             # Base, AuditMixin
│   └── models/             # tabel referensi (bukan ID di controller)
├── modules/
│   ├── example/            # pola wajib: router → schema → service → repository → model
│   ├── app_roles/          # CRUD katalog role IAM
│   ├── menu_mapping/       # CRUD menu_id ↔ slug
│   └── auth_context/       # GET /auth/session
└── shared/                 # response envelope, timezone Asia/Jakarta
alembic/
deploy/                     # PM2 + Nginx
```

Alur modul bisnis:

```text
router.py → schema.py → service.py → repository.py → model.py → database
```

Router harus tipis: HTTP + Depends saja. Query dan aturan bisnis tidak ditulis di controller.

## Peta konstanta (wajib)

| Jenis nilai | Tempat yang benar | Dilarang |
|---|---|---|
| IAM role ID numerik | tabel `app_allowed_roles` | literal di `router.py` / `service.py` |
| Tipe role (`super_admin`, `admin`, `dealer`, `employee`) | `app/core/role_catalog.py` | angka IAM di kode |
| Group-admin / module ID yang beda per environment | `.env` → `Settings` (`GROUP_ADMIN_ROLE_ID`) | `= 1` di file Python |
| Menu ID | tabel `menus_mapping` | router |
| Status/slug domain | `app/modules/{domain}/constants.py` | inline di controller |
| Pagination, nama header | `app/core/constants/` | tersebar |
| Kode error | `AppException` + `error_codes.py` | `HTTPException` ad-hoc |

Contoh benar: `GET /api/v1/examples/role-policy` membaca IAM ID **dari database** lewat `RoleCatalog.from_db(db)`.

Seed ID ke tabel referensi (SQL/Alembic data), bukan ke controller. Jangan menyalin ID project lain (misalnya 90/91) ke starter ini.

## Multi-database

1. **Single engine** — `DATABASE_ENGINE` + `DATABASE_URL`
2. **Named connections** — `DB_URL_PRIMARY`, `DB_URL_IAM`, `DB_URL_REFERENCE`

MySQL/SingleStore: session timezone `+07:00`. Waktu aplikasi: `app_now()` (`Asia/Jakarta`).

## Auth

- Bearer token → IAM `POST /v1/verify-user` → match `app_allowed_roles` → `CurrentUser`
- `GET /api/v1/auth/session` untuk frontend
- Debug (hanya `APP_DEBUG=true`): header `X-Username` dan `X-Role-Ids` — **tanpa default ID IAM**
- `GROUP_ADMIN_ROLE_ID` dari env

Production: `APP_ENV=production` menonaktifkan `/docs`.

## Cara menambah modul

1. Buat `app/modules/{nama}/` dengan kelima file layer.
2. Daftarkan di `app/api/v1/router.py`.
3. Status/slug ke `constants.py` modul tersebut.
4. Role dicek dengan `require_role_types(ROLE_TYPE_ADMIN)` — bukan `if role_id == 91`.
5. Migrasi Alembic jika ada tabel baru — **wajib**, jangan buat tabel manual.

```bash
alembic upgrade head
alembic revision -m "add table x"
```

Pola migration mengikuti `training-backend-ecertificate` (002, 004). Starter: `alembic/versions/001_reference_catalog.py` dengan `BIGINT AUTO_INCREMENT` dimulai dari 1. Lihat [`docs/alembic-sample.md`](docs/alembic-sample.md).

## Unit test (opsional, disarankan)

Jika timeline memungkinkan, tambahkan unit test per use case di `tests/` agar debugging mudah.

```bash
pytest
pytest tests/test_example_role_policy.py -v
```

Contoh: `tests/test_example_role_policy.py` — RoleCatalog dan constants modul.

## Deploy production (PM2 + Nginx)

```text
Browser → GCP Load Balancer (HTTPS) → Nginx :80 → PM2 uvicorn 127.0.0.1:8093
```

```bash
cd /var/www/his-standard-backend
cp deploy/.env.production.example .env
chmod +x deploy/*.sh
bash deploy/deploy-prod.sh --migrate --all
```

Ganti `server_name` di `deploy/nginx-prod.conf.example`. SSL di load balancer.

## Larangan

- Hardcode ID IAM, menu, workflow, atau status di controller
- Mencampur SQL di `router.py`
- Menyimpan host/password production di `.env.example`
- Mengaktifkan Swagger di production
