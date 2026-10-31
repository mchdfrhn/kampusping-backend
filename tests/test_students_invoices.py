from datetime import UTC, date, datetime
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.api.deps import get_clock
from app.db.session import get_session_factory
from app.modules.audit.model import AuditEvent
from app.modules.invoices.model import Invoice
from app.modules.students.model import Student

STUDENTS_URL = "/api/v1/students"
INVOICES_URL = "/api/v1/invoices"
AUDIT_URL = "/api/v1/audit-events"


async def make_student(
    nim: str,
    name: str,
    *,
    study_program: str = "Teknik Informatika",
    cohort: str = "2022",
    phone: str | None = "+6281234567890",
    phone_status: str = "valid",
) -> int:
    async with get_session_factory()() as db:
        student = Student(
            nim=nim,
            name=name,
            study_program=study_program,
            cohort=cohort,
            phone=phone,
            phone_status=phone_status,
        )
        db.add(student)
        await db.commit()
        return student.id


async def make_invoice(
    student_id: int,
    code: str,
    *,
    semester: str = "2025/2026 Ganjil",
    amount: int = 5_000_000,
    due_date: date = date(2026, 11, 15),
    status: str = "unpaid",
    paid_via: str | None = None,
    paid_at: date | None = None,
) -> int:
    async with get_session_factory()() as db:
        invoice = Invoice(
            invoice_code=code,
            student_id=student_id,
            semester=semester,
            amount=amount,
            due_date=due_date,
            status=status,
            paid_via=paid_via,
            paid_at=paid_at,
            source="siakad",
        )
        db.add(invoice)
        await db.commit()
        return invoice.id


async def audit_events(action: str) -> list[AuditEvent]:
    async with get_session_factory()() as db:
        result = await db.scalars(select(AuditEvent).where(AuditEvent.action == action))
        return list(result.all())


# --- students ---------------------------------------------------------------------------------


async def test_students_list_search_filter_and_pagination(
    client: httpx.AsyncClient, staf_headers: dict[str, str]
) -> None:
    await make_student("2201", "Budi Santoso")
    await make_student("2202", "Siti Aminah", study_program="Akuntansi", cohort="2023")
    await make_student("2203", "Budi Hartono", phone=None, phone_status="missing")

    everything = await client.get(STUDENTS_URL, headers=staf_headers)
    assert everything.status_code == 200
    assert everything.json()["total"] == 3

    by_name = await client.get(STUDENTS_URL, params={"search": "budi"}, headers=staf_headers)
    assert {row["nim"] for row in by_name.json()["items"]} == {"2201", "2203"}

    by_nim = await client.get(STUDENTS_URL, params={"search": "2202"}, headers=staf_headers)
    assert [row["name"] for row in by_nim.json()["items"]] == ["Siti Aminah"]

    by_program = await client.get(
        STUDENTS_URL, params={"study_program": "Akuntansi"}, headers=staf_headers
    )
    assert [row["nim"] for row in by_program.json()["items"]] == ["2202"]

    by_cohort = await client.get(STUDENTS_URL, params={"cohort": "2022"}, headers=staf_headers)
    assert {row["nim"] for row in by_cohort.json()["items"]} == {"2201", "2203"}

    by_status = await client.get(
        STUDENTS_URL, params={"phone_status": "missing"}, headers=staf_headers
    )
    assert [row["nim"] for row in by_status.json()["items"]] == ["2203"]

    page = await client.get(
        STUDENTS_URL,
        params={"page": 2, "page_size": 2, "sort_by": "nim", "sort_order": "asc"},
        headers=staf_headers,
    )
    body = page.json()
    assert body["total"] == 3
    assert body["page"] == 2
    assert body["page_size"] == 2
    assert [row["nim"] for row in body["items"]] == ["2203"]


async def test_students_search_escapes_like_wildcards(
    client: httpx.AsyncClient, staf_headers: dict[str, str]
) -> None:
    await make_student("2201", "Budi Santoso")

    response = await client.get(STUDENTS_URL, params={"search": "%"}, headers=staf_headers)

    assert response.json()["total"] == 0


async def test_students_rejects_unknown_sort_and_filter_values(
    client: httpx.AsyncClient, staf_headers: dict[str, str]
) -> None:
    bad_sort = await client.get(STUDENTS_URL, params={"sort_by": "phone"}, headers=staf_headers)
    assert bad_sort.status_code == 422

    bad_status = await client.get(
        STUDENTS_URL, params={"phone_status": "bogus"}, headers=staf_headers
    )
    assert bad_status.status_code == 422


async def test_get_student_and_not_found(
    client: httpx.AsyncClient, staf_headers: dict[str, str]
) -> None:
    student_id = await make_student("2201", "Budi Santoso")

    found = await client.get(f"{STUDENTS_URL}/{student_id}", headers=staf_headers)
    assert found.status_code == 200
    assert found.json()["nim"] == "2201"
    assert found.json()["phone"] == "+6281234567890"
    assert found.json()["phone_status"] == "valid"

    missing = await client.get(f"{STUDENTS_URL}/9999", headers=staf_headers)
    assert missing.status_code == 404


async def test_patch_student_phone_valid_normalises_and_audits(
    client: httpx.AsyncClient, staf_headers: dict[str, str]
) -> None:
    student_id = await make_student("2201", "Budi", phone=None, phone_status="missing")

    response = await client.patch(
        f"{STUDENTS_URL}/{student_id}", json={"phone": "0857-1111-2222"}, headers=staf_headers
    )

    assert response.status_code == 200
    assert response.json()["phone"] == "+6285711112222"
    assert response.json()["phone_status"] == "valid"

    events = await audit_events("student.phone_changed")
    assert len(events) == 1
    assert events[0].actor == "staf@example.com"
    assert events[0].entity == "student"
    assert events[0].entity_id == str(student_id)
    serialised = str(events[0].detail)
    assert "2222" in serialised
    assert "6285711112222" not in serialised  # never the full number
    assert "085711112222" not in serialised


async def test_patch_student_phone_invalid_is_rejected_and_unchanged(
    client: httpx.AsyncClient, staf_headers: dict[str, str]
) -> None:
    student_id = await make_student("2201", "Budi")

    response = await client.patch(
        f"{STUDENTS_URL}/{student_id}", json={"phone": "12345"}, headers=staf_headers
    )

    assert response.status_code == 422
    assert "nomor" in response.json()["error"]["message"].lower()
    after = await client.get(f"{STUDENTS_URL}/{student_id}", headers=staf_headers)
    assert after.json()["phone"] == "+6281234567890"
    assert after.json()["phone_status"] == "valid"
    assert await audit_events("student.phone_changed") == []


@pytest.mark.parametrize("cleared", [None, "", "   "])
async def test_patch_student_phone_can_be_cleared(
    client: httpx.AsyncClient, staf_headers: dict[str, str], cleared: str | None
) -> None:
    student_id = await make_student("2201", "Budi")

    response = await client.patch(
        f"{STUDENTS_URL}/{student_id}", json={"phone": cleared}, headers=staf_headers
    )

    assert response.status_code == 200
    assert response.json()["phone"] is None
    assert response.json()["phone_status"] == "missing"
    assert len(await audit_events("student.phone_changed")) == 1


async def test_patch_student_phone_requires_phone_field_and_allows_shared_numbers(
    client: httpx.AsyncClient, staf_headers: dict[str, str]
) -> None:
    first = await make_student("2201", "Kakak")
    second = await make_student("2202", "Adik", phone=None, phone_status="missing")

    missing_field = await client.patch(f"{STUDENTS_URL}/{first}", json={}, headers=staf_headers)
    assert missing_field.status_code == 422

    shared = await client.patch(
        f"{STUDENTS_URL}/{second}", json={"phone": "+6281234567890"}, headers=staf_headers
    )
    assert shared.status_code == 200  # siblings may share a number


async def test_patch_student_same_phone_writes_no_audit(
    client: httpx.AsyncClient, staf_headers: dict[str, str]
) -> None:
    student_id = await make_student("2201", "Budi")

    response = await client.patch(
        f"{STUDENTS_URL}/{student_id}", json={"phone": "081234567890"}, headers=staf_headers
    )

    assert response.status_code == 200
    assert await audit_events("student.phone_changed") == []


async def test_patch_student_not_found(
    client: httpx.AsyncClient, staf_headers: dict[str, str]
) -> None:
    response = await client.patch(
        f"{STUDENTS_URL}/9999", json={"phone": "081234567890"}, headers=staf_headers
    )
    assert response.status_code == 404


# --- invoices ---------------------------------------------------------------------------------


async def test_invoices_list_embeds_student_and_filters(
    client: httpx.AsyncClient, staf_headers: dict[str, str]
) -> None:
    budi = await make_student("2201", "Budi Santoso")
    siti = await make_student("2202", "Siti Aminah")
    await make_invoice(budi, "INV-1", due_date=date(2026, 11, 1))
    await make_invoice(budi, "INV-2", due_date=date(2026, 12, 1), semester="2025/2026 Genap")
    await make_invoice(siti, "INV-3", due_date=date(2026, 11, 20), status="paid", paid_via="sipema")

    everything = await client.get(INVOICES_URL, headers=staf_headers)
    assert everything.status_code == 200
    body = everything.json()
    assert body["total"] == 3
    first = next(row for row in body["items"] if row["invoice_code"] == "INV-1")
    assert first["student"] == {"id": budi, "nim": "2201", "name": "Budi Santoso"}
    assert first["amount"] == 5_000_000
    assert first["due_date"] == "2026-11-01"
    assert first["status"] == "unpaid"

    # default sort is due_date, newest first
    assert [row["invoice_code"] for row in body["items"]] == ["INV-2", "INV-3", "INV-1"]

    by_status = await client.get(INVOICES_URL, params={"status": "paid"}, headers=staf_headers)
    assert [row["invoice_code"] for row in by_status.json()["items"]] == ["INV-3"]

    by_semester = await client.get(
        INVOICES_URL, params={"semester": "2025/2026 Genap"}, headers=staf_headers
    )
    assert [row["invoice_code"] for row in by_semester.json()["items"]] == ["INV-2"]

    by_range = await client.get(
        INVOICES_URL,
        params={"due_from": "2026-11-10", "due_to": "2026-12-01"},
        headers=staf_headers,
    )
    assert {row["invoice_code"] for row in by_range.json()["items"]} == {"INV-2", "INV-3"}

    by_student = await client.get(INVOICES_URL, params={"student_id": siti}, headers=staf_headers)
    assert [row["invoice_code"] for row in by_student.json()["items"]] == ["INV-3"]

    searched = await client.get(INVOICES_URL, params={"search": "inv-2"}, headers=staf_headers)
    assert [row["invoice_code"] for row in searched.json()["items"]] == ["INV-2"]

    by_student_name = await client.get(
        INVOICES_URL, params={"search": "aminah"}, headers=staf_headers
    )
    assert [row["invoice_code"] for row in by_student_name.json()["items"]] == ["INV-3"]


async def test_invoices_pagination_and_sort_whitelist(
    client: httpx.AsyncClient, staf_headers: dict[str, str]
) -> None:
    student_id = await make_student("2201", "Budi")
    for index in range(5):
        await make_invoice(student_id, f"INV-{index}", amount=1000 * (index + 1))

    page = await client.get(
        INVOICES_URL,
        params={"page": 2, "page_size": 2, "sort_by": "amount", "sort_order": "asc"},
        headers=staf_headers,
    )
    body = page.json()
    assert body["total"] == 5
    assert [row["amount"] for row in body["items"]] == [3000, 4000]

    bad = await client.get(INVOICES_URL, params={"sort_by": "source"}, headers=staf_headers)
    assert bad.status_code == 422
    bad_status = await client.get(INVOICES_URL, params={"status": "bogus"}, headers=staf_headers)
    assert bad_status.status_code == 422


def freeze_clock(app: FastAPI, instant: datetime) -> None:
    app.dependency_overrides[get_clock] = lambda: lambda: instant


@pytest.mark.parametrize(
    ("instant", "expected_paid_at"),
    [
        # 18:00 UTC is already the next day in Jakarta (UTC+7): the local date must win.
        (datetime(2026, 10, 31, 18, 0, tzinfo=UTC), "2026-11-01"),
        (datetime(2026, 10, 31, 16, 59, tzinfo=UTC), "2026-10-31"),
    ],
)
async def test_invoice_marked_paid_sets_paid_at_to_local_date(
    app: FastAPI,
    client: httpx.AsyncClient,
    staf_headers: dict[str, str],
    instant: datetime,
    expected_paid_at: str,
) -> None:
    student_id = await make_student("2201", "Budi")
    invoice_id = await make_invoice(student_id, "INV-1")
    freeze_clock(app, instant)

    response = await client.patch(
        f"{INVOICES_URL}/{invoice_id}/status", json={"status": "paid"}, headers=staf_headers
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "paid"
    assert body["paid_via"] is None
    assert body["paid_at"] == expected_paid_at
    events = await audit_events("invoice.status_changed")
    assert len(events) == 1
    assert events[0].detail == {"from": "unpaid", "to": "paid"}


async def test_student_phone_update_uses_injected_clock(
    app: FastAPI, client: httpx.AsyncClient, staf_headers: dict[str, str]
) -> None:
    student_id = await make_student("2201", "Budi")
    instant = datetime(2026, 10, 31, 18, 0, tzinfo=UTC)
    freeze_clock(app, instant)

    response = await client.patch(
        f"{STUDENTS_URL}/{student_id}", json={"phone": "0857-1111-2222"}, headers=staf_headers
    )

    assert response.status_code == 200
    assert response.json()["updated_at"].startswith("2026-10-31T18:00:00")


async def test_same_status_patch_leaves_paid_at_untouched(
    client: httpx.AsyncClient, staf_headers: dict[str, str]
) -> None:
    student_id = await make_student("2201", "Budi")
    invoice_id = await make_invoice(
        student_id, "INV-1", status="paid", paid_via="siakad", paid_at=date(2026, 10, 1)
    )

    response = await client.patch(
        f"{INVOICES_URL}/{invoice_id}/status", json={"status": "paid"}, headers=staf_headers
    )

    assert response.status_code == 200
    assert response.json()["paid_at"] == "2026-10-01"
    assert response.json()["paid_via"] == "siakad"
    assert await audit_events("invoice.status_changed") == []


async def test_invoice_status_revert_is_audited(
    client: httpx.AsyncClient, staf_headers: dict[str, str]
) -> None:
    student_id = await make_student("2201", "Budi")
    invoice_id = await make_invoice(
        student_id, "INV-1", status="paid", paid_via="siakad", paid_at=date(2026, 10, 1)
    )

    response = await client.patch(
        f"{INVOICES_URL}/{invoice_id}/status", json={"status": "unpaid"}, headers=staf_headers
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "unpaid"
    assert body["paid_at"] is None
    assert body["paid_via"] is None
    events = await audit_events("invoice.status_changed")
    assert len(events) == 1
    assert events[0].actor == "staf@example.com"
    assert events[0].entity == "invoice"
    assert events[0].entity_id == str(invoice_id)
    assert events[0].detail == {"from": "paid", "to": "unpaid"}


async def test_invoice_paid_keeps_existing_paid_via(
    client: httpx.AsyncClient, staf_headers: dict[str, str]
) -> None:
    student_id = await make_student("2201", "Budi")
    invoice_id = await make_invoice(student_id, "INV-1", paid_via="sipema")

    response = await client.patch(
        f"{INVOICES_URL}/{invoice_id}/status", json={"status": "paid"}, headers=staf_headers
    )

    assert response.json()["paid_via"] == "sipema"


async def test_invoice_same_status_is_noop_without_audit(
    client: httpx.AsyncClient, staf_headers: dict[str, str]
) -> None:
    student_id = await make_student("2201", "Budi")
    invoice_id = await make_invoice(student_id, "INV-1")

    response = await client.patch(
        f"{INVOICES_URL}/{invoice_id}/status", json={"status": "unpaid"}, headers=staf_headers
    )

    assert response.status_code == 200
    assert response.json()["status"] == "unpaid"
    assert await audit_events("invoice.status_changed") == []


async def test_invoice_status_rejects_unknown_value_and_missing_invoice(
    client: httpx.AsyncClient, staf_headers: dict[str, str]
) -> None:
    student_id = await make_student("2201", "Budi")
    invoice_id = await make_invoice(student_id, "INV-1")

    bad = await client.patch(
        f"{INVOICES_URL}/{invoice_id}/status", json={"status": "refunded"}, headers=staf_headers
    )
    assert bad.status_code == 422

    missing = await client.patch(
        f"{INVOICES_URL}/9999/status", json={"status": "paid"}, headers=staf_headers
    )
    assert missing.status_code == 404


async def test_invoice_code_is_unique_at_database_level() -> None:
    student_id = await make_student("2201", "Budi")
    await make_invoice(student_id, "INV-1")
    with pytest.raises(IntegrityError):
        await make_invoice(student_id, "INV-1")


# --- access control ---------------------------------------------------------------------------


READ_ENDPOINTS = [STUDENTS_URL, f"{STUDENTS_URL}/1", INVOICES_URL]
WRITE_ENDPOINTS: list[tuple[str, dict[str, Any]]] = [
    (f"{STUDENTS_URL}/1", {"phone": "081234567890"}),
    (f"{INVOICES_URL}/1/status", {"status": "paid"}),
]


@pytest.mark.parametrize("url", READ_ENDPOINTS)
async def test_read_endpoints_require_authentication(client: httpx.AsyncClient, url: str) -> None:
    assert (await client.get(url)).status_code == 401


@pytest.mark.parametrize(("url", "body"), WRITE_ENDPOINTS)
async def test_write_endpoints_require_authentication(
    client: httpx.AsyncClient, url: str, body: dict[str, Any]
) -> None:
    assert (await client.patch(url, json=body)).status_code == 401


@pytest.mark.parametrize("url", READ_ENDPOINTS)
async def test_pimpinan_cannot_read_students_or_invoices(
    client: httpx.AsyncClient, pimpinan_headers: dict[str, str], url: str
) -> None:
    assert (await client.get(url, headers=pimpinan_headers)).status_code == 403


@pytest.mark.parametrize(("url", "body"), WRITE_ENDPOINTS)
async def test_pimpinan_cannot_write(
    client: httpx.AsyncClient, pimpinan_headers: dict[str, str], url: str, body: dict[str, Any]
) -> None:
    assert (await client.patch(url, json=body, headers=pimpinan_headers)).status_code == 403


async def test_admin_can_read_students_and_invoices(
    client: httpx.AsyncClient, admin_headers: dict[str, str]
) -> None:
    assert (await client.get(STUDENTS_URL, headers=admin_headers)).status_code == 200
    assert (await client.get(INVOICES_URL, headers=admin_headers)).status_code == 200


async def test_audit_log_lists_new_events(
    client: httpx.AsyncClient, admin_headers: dict[str, str]
) -> None:
    student_id = await make_student("2201", "Budi")
    invoice_id = await make_invoice(student_id, "INV-1")
    await client.patch(
        f"{INVOICES_URL}/{invoice_id}/status", json={"status": "paid"}, headers=admin_headers
    )

    response = await client.get(
        AUDIT_URL, params={"action": "invoice.status_changed"}, headers=admin_headers
    )

    assert response.json()["total"] == 1
