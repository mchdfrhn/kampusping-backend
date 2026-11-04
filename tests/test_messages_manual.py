from datetime import UTC, date, datetime

import httpx
from sqlalchemy import select

from app.db.session import get_session_factory
from app.modules.invoices.model import Invoice
from app.modules.messages.model import Message
from app.modules.students.model import Student
from app.modules.templates.model import MessageTemplate

from .messages_helpers import FULL_PHONE, audit_events

MANUAL_OPTIONS_URL = "/api/v1/messages/manual/options"
MANUAL_PREVIEW_URL = "/api/v1/messages/manual/preview"
MANUAL_SEND_URL = "/api/v1/messages/manual/send"


async def create_template(name: str = "Pengingat Manual", is_active: bool = True) -> int:
    async with get_session_factory()() as db:
        tpl = MessageTemplate(
            name=name,
            body=(
                "Halo {{nama}}, tagihan {{semester}} sebesar Rp{{nominal}} "
                "jatuh tempo pada {{tanggal_jatuh_tempo}}."
            ),
            is_active=is_active,
            created_by="test",
            updated_by="test",
        )
        db.add(tpl)
        await db.commit()
        return tpl.id


async def setup_test_students_and_invoices():
    """Sets up 5 invoices with different eligibility statuses."""
    async with get_session_factory()() as db:
        # 1. Eligible student & invoice
        s1 = Student(
            nim="20230001",
            name="Andi Pratama",
            study_program="Teknik Informatika",
            cohort="2023",
            phone=FULL_PHONE,
            phone_status="valid",
        )
        db.add(s1)
        await db.flush()
        i1 = Invoice(
            invoice_code="INV-2023-01",
            student_id=s1.id,
            semester="Ganjil 2026/2027",
            amount=2_000_000,
            due_date=date(2026, 11, 20),
            status="unpaid",
            source="siakad",
        )
        db.add(i1)

        # 2. Paid invoice (excluded)
        s2 = Student(
            nim="20230002",
            name="Budi Santoso",
            study_program="Teknik Informatika",
            cohort="2023",
            phone=FULL_PHONE,
            phone_status="valid",
        )
        db.add(s2)
        await db.flush()
        i2 = Invoice(
            invoice_code="INV-2023-02",
            student_id=s2.id,
            semester="Ganjil 2026/2027",
            amount=2_000_000,
            due_date=date(2026, 11, 20),
            status="paid",
            source="siakad",
        )
        db.add(i2)

        # 3. Opted-out student (excluded)
        s3 = Student(
            nim="20230003",
            name="Citra Dewi",
            study_program="Sistem Informasi",
            cohort="2023",
            phone=FULL_PHONE,
            phone_status="valid",
            opted_out_at=datetime(2026, 11, 1, 10, 0, tzinfo=UTC),
        )
        db.add(s3)
        await db.flush()
        i3 = Invoice(
            invoice_code="INV-2023-03",
            student_id=s3.id,
            semester="Ganjil 2026/2027",
            amount=1_500_000,
            due_date=date(2026, 11, 20),
            status="unpaid",
            source="siakad",
        )
        db.add(i3)

        # 4. Exempt invoice (dispensation active until next year) (excluded)
        s4 = Student(
            nim="20230004",
            name="Doni Kusuma",
            study_program="Teknik Informatika",
            cohort="2023",
            phone=FULL_PHONE,
            phone_status="valid",
        )
        db.add(s4)
        await db.flush()
        i4 = Invoice(
            invoice_code="INV-2023-04",
            student_id=s4.id,
            semester="Ganjil 2026/2027",
            amount=2_000_000,
            due_date=date(2026, 11, 20),
            status="unpaid",
            source="siakad",
            exempt_until=date(2027, 1, 1),
            exempt_reason="Keringanan rektorat",
        )
        db.add(i4)

        # 5. Invalid phone student (excluded)
        s5 = Student(
            nim="20230005",
            name="Eka Putri",
            study_program="Sistem Informasi",
            cohort="2023",
            phone=None,
            phone_status="missing",
        )
        db.add(s5)
        await db.flush()
        i5 = Invoice(
            invoice_code="INV-2023-05",
            student_id=s5.id,
            semester="Ganjil 2026/2027",
            amount=1_800_000,
            due_date=date(2026, 11, 20),
            status="unpaid",
            source="siakad",
        )
        db.add(i5)

        await db.commit()
        return [i1.id, i2.id, i3.id, i4.id, i5.id]


async def test_manual_options_returns_distinct_filters(
    client: httpx.AsyncClient, staf_headers: dict[str, str]
) -> None:
    await setup_test_students_and_invoices()
    resp = await client.get(MANUAL_OPTIONS_URL, headers=staf_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert "2023" in data["cohorts"]
    assert "Teknik Informatika" in data["study_programs"]
    assert "Ganjil 2026/2027" in data["semesters"]


async def test_manual_preview_calculates_eligibility_and_breakdown(
    client: httpx.AsyncClient, staf_headers: dict[str, str]
) -> None:
    await setup_test_students_and_invoices()
    tpl_id = await create_template()

    resp = await client.post(
        MANUAL_PREVIEW_URL,
        headers=staf_headers,
        json={"template_id": tpl_id, "cohort": "2023", "status": "all"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_matched"] == 5
    assert data["eligible_count"] == 1
    assert data["excluded_count"] == 4
    assert data["excluded_breakdown"]["paid"] == 1
    assert data["excluded_breakdown"]["opted_out"] == 1
    assert data["excluded_breakdown"]["exempt"] == 1
    assert data["excluded_breakdown"]["invalid_phone"] == 1

    sample = data["sample_preview"]
    assert sample is not None
    assert sample["student_name"] == "Andi Pratama"
    assert "Balas STOP untuk berhenti." in sample["body"]
    assert "Andi Pratama" in sample["body"]


async def test_manual_send_queues_messages_and_records_audit(
    client: httpx.AsyncClient, staf_headers: dict[str, str]
) -> None:
    await setup_test_students_and_invoices()
    tpl_id = await create_template("Templat Tagihan")

    resp = await client.post(
        MANUAL_SEND_URL,
        headers=staf_headers,
        json={"template_id": tpl_id, "cohort": "2023", "status": "all"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["queued_count"] == 1
    assert data["excluded_count"] == 4
    assert data["total_matched"] == 5
    batch_ref = data["batch_ref"]
    assert batch_ref.startswith("MANUAL-")

    # Verify message in database
    async with get_session_factory()() as db:
        messages = list(
            (await db.scalars(select(Message).where(Message.batch_ref == batch_ref))).all()
        )
        assert len(messages) == 1
        msg = messages[0]
        assert msg.kind == "manual"
        assert msg.rule_id is None
        assert msg.status == "queued"
        assert "Andi Pratama" in msg.body

    # Verify audit event
    events = await audit_events("messages:manual_send")
    assert len(events) >= 1
    event = next(e for e in events if e.entity_id == batch_ref)
    assert event.actor == "staf@example.com"
    assert event.detail["queued_count"] == 1


async def test_manual_send_fails_when_no_eligible_recipients(
    client: httpx.AsyncClient, staf_headers: dict[str, str]
) -> None:
    tpl_id = await create_template()
    # Filter for non-existent cohort
    resp = await client.post(
        MANUAL_SEND_URL,
        headers=staf_headers,
        json={"template_id": tpl_id, "cohort": "1999"},
    )
    assert resp.status_code == 400


async def test_manual_send_requires_active_template(
    client: httpx.AsyncClient, staf_headers: dict[str, str]
) -> None:
    tpl_id = await create_template("Templat Nonaktif", is_active=False)
    resp = await client.post(
        MANUAL_SEND_URL,
        headers=staf_headers,
        json={"template_id": tpl_id},
    )
    assert resp.status_code == 422


async def test_manual_send_forbidden_for_pimpinan(
    client: httpx.AsyncClient, pimpinan_headers: dict[str, str]
) -> None:
    tpl_id = await create_template()
    resp = await client.post(
        MANUAL_SEND_URL,
        headers=pimpinan_headers,
        json={"template_id": tpl_id},
    )
    assert resp.status_code == 403
