from datetime import UTC, date, datetime

import httpx
import pytest
from sqlalchemy import func, select

from app.db.session import get_session_factory
from app.modules.imports.model import ImportBatch, ImportRowError
from app.modules.invoices.model import Invoice
from app.modules.students.model import Student

from .imports_helpers import (
    HEADER,
    IMPORTS_URL,
    SEMESTER,
    SUMMARY_KEYS,
    apply,
    count,
    import_lines,
    invoice_by_code,
    line,
    make_csv,
    make_xlsx,
    preview,
    student_by_nim,
    upload,
)
from .test_students_invoices import audit_events, make_invoice, make_student

# --- preview ----------------------------------------------------------------------------------


async def test_preview_changes_no_business_data(
    client: httpx.AsyncClient, staf_headers: dict[str, str]
) -> None:
    await make_student("2201", "Nama Lama", phone="+6281111111111")
    await make_invoice(1, "INV-1", amount=1_000_000)

    body = await preview(
        client,
        staf_headers,
        line(nim="2201", name="Nama Baru", code="INV-1", amount="2500000"),
        line(nim="2202", name="Siti Aminah", code="INV-2"),
        line(nim="2203", name="Rudi", code="INV-3", amount="abc"),
    )

    assert body["status"] == "preview"
    assert body["source"] == "siakad"
    assert body["file_name"] == "tagihan.csv"
    assert body["semester"] == SEMESTER
    assert body["applied_at"] is None
    assert body["created_by"] == "staf@example.com"
    assert "rows" not in body
    assert body["summary"] == {
        "rows_total": 3,
        "rows_valid": 2,
        "rows_error": 1,
        "students_new": 1,
        "students_updated": 1,
        "invoices_new": 1,
        "invoices_updated": 1,
        "invoices_skipped": 0,
    }
    # Nothing in the business tables moved.
    assert await count(Student) == 1
    assert await count(Invoice) == 1
    assert (await student_by_nim("2201")).name == "Nama Lama"
    assert (await invoice_by_code("INV-1")).amount == 1_000_000

    errors = await client.get(f"{IMPORTS_URL}/{body['id']}/errors", headers=staf_headers)
    assert errors.status_code == 200
    assert errors.json()["total"] == 1
    assert errors.json()["items"] == [
        {"row_number": 4, "field": "amount", "message": "Nominal tidak valid."}
    ]
    assert len(await audit_events("import.previewed")) == 1


async def test_preview_prediction_counts_soft_deleted_rows_as_existing(
    client: httpx.AsyncClient, staf_headers: dict[str, str]
) -> None:
    student_id = await make_student("2201", "Budi")
    await make_invoice(student_id, "INV-1")
    async with get_session_factory()() as db:
        for model in (Student, Invoice):
            row = (await db.scalars(select(model))).one()
            row.deleted_at = datetime(2026, 10, 1, tzinfo=UTC)
        await db.commit()

    body = await preview(client, staf_headers, line(nim="2201", name="", code="INV-1"))

    assert body["summary"]["students_new"] == 0
    assert body["summary"]["students_updated"] == 1
    assert body["summary"]["invoices_new"] == 0
    assert body["summary"]["invoices_updated"] == 1
    assert body["summary"]["rows_error"] == 0


async def test_duplicate_invoice_code_in_file_is_a_row_error_for_the_second_row(
    client: httpx.AsyncClient, staf_headers: dict[str, str]
) -> None:
    batch = await preview(
        client,
        staf_headers,
        line(nim="2201", code="INV-1", amount="1000000"),
        line(nim="2202", name="Siti", code="INV-2"),
        line(nim="2203", name="Rudi", code="INV-1", amount="9000000"),
    )

    assert batch["summary"]["rows_total"] == 3
    assert batch["summary"]["rows_valid"] == 2
    assert batch["summary"]["rows_error"] == 1
    assert batch["summary"]["invoices_new"] == 2
    errors = (await client.get(f"{IMPORTS_URL}/{batch['id']}/errors", headers=staf_headers)).json()
    assert errors["items"] == [
        {
            "row_number": 4,
            "field": "invoice_code",
            "message": "Kode tagihan duplikat dalam berkas (baris 2).",
        }
    ]

    applied = (await apply(client, staf_headers, batch["id"])).json()
    assert applied["summary"]["invoices_new"] == 2
    assert (await invoice_by_code("INV-1")).amount == 1_000_000  # the first row wins
    assert await count(Student) == 2  # the duplicate row's student was not created


async def test_new_student_without_name_is_row_error(
    client: httpx.AsyncClient, staf_headers: dict[str, str]
) -> None:
    await make_student("2200", "Sudah Ada")
    batch = await preview(
        client,
        staf_headers,
        line(nim="2201", name="", code="INV-1"),  # new, no name: error
        line(nim="2200", name="", code="INV-2"),  # existing student: fine without a name
        line(nim="2202", name="Siti", code="INV-3"),  # new, named
        line(nim="2202", name="", code="INV-4"),  # same student, named by the earlier row: fine
    )

    assert batch["summary"]["rows_error"] == 1
    assert batch["summary"]["rows_valid"] == 3
    assert batch["summary"]["students_new"] == 1
    assert batch["summary"]["students_updated"] == 1
    errors = (await client.get(f"{IMPORTS_URL}/{batch['id']}/errors", headers=staf_headers)).json()
    assert errors["items"] == [
        {
            "row_number": 2,
            "field": "name",
            "message": "Nama wajib diisi untuk mahasiswa baru.",
        }
    ]


async def test_preview_stores_at_most_500_errors_but_counts_all(
    client: httpx.AsyncClient, staf_headers: dict[str, str]
) -> None:
    lines = [line(nim=f"{3000 + i}", code=f"INV-{i}", amount="x") for i in range(520)]
    batch = await preview(client, staf_headers, *lines)

    assert batch["summary"]["rows_error"] == 520
    assert batch["summary"]["rows_valid"] == 0
    errors = (
        await client.get(
            f"{IMPORTS_URL}/{batch['id']}/errors", params={"page_size": 100}, headers=staf_headers
        )
    ).json()
    assert errors["total"] == 500
    assert [item["row_number"] for item in errors["items"]][:3] == [2, 3, 4]  # ascending default


async def test_preview_accepts_xlsx_and_takes_only_the_file_basename(
    client: httpx.AsyncClient, staf_headers: dict[str, str]
) -> None:
    content = make_xlsx(
        [
            HEADER.split(","),
            [
                "2201",
                "Budi",
                "TI",
                "2022",
                "081234567890",
                "INV-1",
                5000000,
                date(2026, 11, 15),
                "belum lunas",
                None,
                None,
            ],
        ]
    )

    response = await upload(
        client, staf_headers, content, filename="../../evil/dir/laporan.xlsx", source="sipema"
    )

    assert response.status_code == 201, response.text
    assert response.json()["file_name"] == "laporan.xlsx"
    assert response.json()["source"] == "sipema"
    assert response.json()["summary"]["rows_valid"] == 1


@pytest.mark.parametrize(
    ("content", "filename", "fragment"),
    [
        (b"a,b\n1,2\n", "tagihan.txt", "Format berkas tidak didukung"),
        (b"NIM,Nama\n1,x\n", "tagihan.csv", "Kolom wajib tidak ditemukan"),
        (b"   \n", "tagihan.csv", "Berkas kosong"),
        pytest.param(
            b"x" * (5 * 1024 * 1024 + 1),
            "tagihan.csv",
            "Ukuran berkas melebihi batas 5 MB",
            id="oversize",
        ),
    ],
)
async def test_unusable_file_is_422_with_indonesian_message(
    client: httpx.AsyncClient,
    staf_headers: dict[str, str],
    content: bytes,
    filename: str,
    fragment: str,
) -> None:
    response = await upload(client, staf_headers, content, filename=filename)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"
    assert fragment in response.json()["error"]["message"]
    assert await count(ImportBatch) == 0


async def test_unknown_source_is_422(
    client: httpx.AsyncClient, staf_headers: dict[str, str]
) -> None:
    response = await upload(client, staf_headers, make_csv(line()), source="excel")

    assert response.status_code == 422
    assert await count(ImportBatch) == 0


async def test_semester_comes_from_the_form_when_the_file_has_no_column(
    client: httpx.AsyncClient, staf_headers: dict[str, str]
) -> None:
    without = await preview(client, staf_headers, line(), semester=None)
    assert without["semester"] is None
    assert without["summary"]["rows_error"] == 1

    with_form = await import_lines(client, staf_headers, line(code="INV-2"))
    assert (await invoice_by_code("INV-2")).semester == SEMESTER
    assert with_form["semester"] == SEMESTER


# --- history ----------------------------------------------------------------------------------


async def test_list_and_get_history(
    client: httpx.AsyncClient, staf_headers: dict[str, str]
) -> None:
    first = await preview(client, staf_headers, line(), filename="a.csv")
    second = await preview(client, staf_headers, line(code="INV-2"), filename="b.csv")
    await apply(client, staf_headers, second["id"])

    listing = await client.get(IMPORTS_URL, headers=staf_headers)

    assert listing.status_code == 200
    body = listing.json()
    assert body["total"] == 2
    assert [item["id"] for item in body["items"]] == [second["id"], first["id"]]  # newest first
    assert set(body["items"][0]) == {
        "id",
        "source",
        "file_name",
        "semester",
        "status",
        "summary",
        "created_at",
        "created_by",
        "applied_at",
        "has_change_log",
        "rolled_back_at",
        "rollback_block",
    }
    assert set(body["items"][0]["summary"]) == SUMMARY_KEYS

    by_name = await client.get(
        IMPORTS_URL, params={"sort_by": "file_name", "sort_order": "asc"}, headers=staf_headers
    )
    assert [item["file_name"] for item in by_name.json()["items"]] == ["a.csv", "b.csv"]
    bad_sort = await client.get(IMPORTS_URL, params={"sort_by": "rows"}, headers=staf_headers)
    assert bad_sort.status_code == 422

    single = await client.get(f"{IMPORTS_URL}/{first['id']}", headers=staf_headers)
    assert single.status_code == 200
    assert single.json()["file_name"] == "a.csv"
    assert "rows" not in single.json()


# --- access -----------------------------------------------------------------------------------


async def test_imports_require_authentication(client: httpx.AsyncClient) -> None:
    for method, url in [
        ("GET", IMPORTS_URL),
        ("GET", f"{IMPORTS_URL}/1"),
        ("GET", f"{IMPORTS_URL}/1/errors"),
        ("POST", f"{IMPORTS_URL}/1/apply"),
        ("POST", f"{IMPORTS_URL}/1/discard"),
        ("DELETE", f"{IMPORTS_URL}/1"),
    ]:
        assert (await client.request(method, url)).status_code == 401, (method, url)
    response = await client.post(
        IMPORTS_URL, files={"file": ("t.csv", make_csv(line()))}, data={"source": "siakad"}
    )
    assert response.status_code == 401


async def test_pimpinan_cannot_use_imports(
    client: httpx.AsyncClient, pimpinan_headers: dict[str, str], staf_headers: dict[str, str]
) -> None:
    batch = await preview(client, staf_headers, line())

    assert (await upload(client, pimpinan_headers, make_csv(line()))).status_code == 403
    assert (await client.get(IMPORTS_URL, headers=pimpinan_headers)).status_code == 403
    assert (
        await client.get(f"{IMPORTS_URL}/{batch['id']}", headers=pimpinan_headers)
    ).status_code == 403
    assert (
        await client.get(f"{IMPORTS_URL}/{batch['id']}/errors", headers=pimpinan_headers)
    ).status_code == 403
    assert (await apply(client, pimpinan_headers, batch["id"])).status_code == 403
    assert (
        await client.post(f"{IMPORTS_URL}/{batch['id']}/discard", headers=pimpinan_headers)
    ).status_code == 403
    assert (
        await client.delete(f"{IMPORTS_URL}/{batch['id']}", headers=pimpinan_headers)
    ).status_code == 403
    assert await count(Student) == 0


async def test_staf_and_admin_can_import_and_audit_events_are_recorded(
    client: httpx.AsyncClient, staf_headers: dict[str, str], admin_headers: dict[str, str]
) -> None:
    staf_batch = await import_lines(client, staf_headers, line(code="INV-1"))
    admin_response = await upload(client, admin_headers, make_csv(line(code="INV-2")))

    assert admin_response.status_code == 201
    previewed = await audit_events("import.previewed")
    applied = await audit_events("import.applied")
    assert len(previewed) == 2
    assert len(applied) == 1
    assert applied[0].actor == "staf@example.com"
    assert applied[0].entity == "import_batch"
    assert applied[0].entity_id == str(staf_batch["id"])
    assert applied[0].detail is not None
    assert set(applied[0].detail) == SUMMARY_KEYS  # counts only, no row data
    assert await count(ImportBatch) == 2
    async with get_session_factory()() as db:
        assert await db.scalar(select(func.count()).select_from(ImportRowError)) == 0
