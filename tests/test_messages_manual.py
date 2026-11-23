from datetime import UTC, date, datetime, timedelta

import httpx
from fastapi import FastAPI
from sqlalchemy import select

from app.api.deps import get_clock
from app.db.session import get_session_factory
from app.modules.invoices.model import Invoice
from app.modules.messages.model import Message
from app.modules.students.model import Student
from app.modules.templates.model import MessageTemplate

from .messages_helpers import FULL_PHONE, audit_events

MANUAL_OPTIONS_URL = "/api/v1/messages/manual/options"
MANUAL_PREVIEW_URL = "/api/v1/messages/manual/preview"
MANUAL_SEND_URL = "/api/v1/messages/manual/send"


DEFAULT_BODY = (
    "Halo {{nama}}, tagihan {{semester}} sebesar Rp{{nominal}} "
    "jatuh tempo pada {{tanggal_jatuh_tempo}}."
)


async def create_template(
    name: str = "Pengingat Manual", is_active: bool = True, body: str = DEFAULT_BODY
) -> int:
    async with get_session_factory()() as db:
        tpl = MessageTemplate(
            name=name,
            body=body,
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
    assert "Balas STOP untuk berhenti." not in sample["body"]
    assert "Andi Pratama" in sample["body"]


async def test_manual_preview_fills_salam_from_the_current_time_but_queues_the_placeholder(
    app: FastAPI, client: httpx.AsyncClient, staf_headers: dict[str, str]
) -> None:
    # 14:00 in Jakarta: the preview shows what a send right now would say.
    app.dependency_overrides[get_clock] = lambda: lambda: datetime(2026, 11, 16, 7, 0, tzinfo=UTC)
    await setup_test_students_and_invoices()
    tpl_id = await create_template(body="{{salam}}, {{nama}}.")
    body = {"template_id": tpl_id, "cohort": "2023", "status": "all"}

    preview = (await client.post(MANUAL_PREVIEW_URL, headers=staf_headers, json=body)).json()
    assert preview["sample_preview"]["body"] == "Selamat siang, Andi Pratama."

    sent = await client.post(MANUAL_SEND_URL, headers=staf_headers, json=body)
    assert sent.status_code in (200, 201)
    async with get_session_factory()() as db:
        bodies = list((await db.scalars(select(Message.body))).all())
    assert bodies == ["{{salam}}, Andi Pratama."]  # the sender fills it at send time


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
    assert event.detail is not None
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


# --- eligibility must match the sender's re-check ------------------------------------------------


async def add_invoice(number: int, student_fields: dict | None = None, **invoice_fields) -> int:
    """One unpaid invoice of a student with a valid phone; returns the invoice id."""
    async with get_session_factory()() as db:
        student = Student(
            nim=f"2024{number:04d}",
            name=f"Mahasiswa {number}",
            study_program="Teknik Sipil",
            cohort="2024",
            phone=FULL_PHONE,
            phone_status="valid",
            **(student_fields or {}),
        )
        db.add(student)
        await db.flush()
        invoice = Invoice(
            invoice_code=f"INV-2024-{number:03d}",
            student_id=student.id,
            semester="Ganjil 2026/2027",
            amount=2_000_000,
            due_date=date(2026, 11, 20),
            status="unpaid",
            source="siakad",
            **invoice_fields,
        )
        db.add(invoice)
        await db.commit()
        return invoice.id


async def queued_manual_count() -> int:
    async with get_session_factory()() as db:
        return len((await db.scalars(select(Message).where(Message.kind == "manual"))).all())


async def test_manual_ignores_deleted_invoices_and_students(
    client: httpx.AsyncClient, staf_headers: dict[str, str]
) -> None:
    deleted_at = datetime(2026, 11, 1, 10, 0, tzinfo=UTC)
    live = await add_invoice(1)
    deleted_invoice = await add_invoice(2, deleted_at=deleted_at)
    deleted_student = await add_invoice(3, student_fields={"deleted_at": deleted_at})
    tpl_id = await create_template()
    body = {"template_id": tpl_id, "invoice_ids": [live, deleted_invoice, deleted_student]}

    preview = (await client.post(MANUAL_PREVIEW_URL, headers=staf_headers, json=body)).json()
    assert preview["total_matched"] == 1
    assert [item["invoice_id"] for item in preview["items"]] == [live]

    sent = (await client.post(MANUAL_SEND_URL, headers=staf_headers, json=body)).json()
    assert sent["total_matched"] == 1
    assert sent["queued_count"] == 1
    assert await queued_manual_count() == 1


async def test_manual_excludes_an_exemption_without_an_end_date(
    client: httpx.AsyncClient, staf_headers: dict[str, str]
) -> None:
    # The sender cancels it (AB-05), so the preview must not call it eligible.
    invoice_id = await add_invoice(1, exempt_reason="Cicilan", exempt_until=None)
    tpl_id = await create_template()
    body = {"template_id": tpl_id, "invoice_ids": [invoice_id]}

    preview = (await client.post(MANUAL_PREVIEW_URL, headers=staf_headers, json=body)).json()
    assert preview["eligible_count"] == 0
    assert preview["excluded_breakdown"]["exempt"] == 1
    assert (await client.post(MANUAL_SEND_URL, headers=staf_headers, json=body)).status_code == 400
    assert await queued_manual_count() == 0


async def test_manual_allows_an_exemption_that_has_ended(
    client: httpx.AsyncClient, staf_headers: dict[str, str]
) -> None:
    invoice_id = await add_invoice(1, exempt_reason="Keringanan", exempt_until=date(2020, 1, 1))
    tpl_id = await create_template()

    preview = (
        await client.post(
            MANUAL_PREVIEW_URL,
            headers=staf_headers,
            json={"template_id": tpl_id, "invoice_ids": [invoice_id]},
        )
    ).json()
    assert preview["eligible_count"] == 1
    assert preview["excluded_breakdown"]["exempt"] == 0


# --- no duplicate while a manual message is still waiting ----------------------------------------


async def test_manual_send_twice_queues_only_once_while_pending(
    client: httpx.AsyncClient, staf_headers: dict[str, str]
) -> None:
    invoice_id = await add_invoice(1)
    tpl_id = await create_template()
    body = {"template_id": tpl_id, "invoice_ids": [invoice_id]}

    first = await client.post(MANUAL_SEND_URL, headers=staf_headers, json=body)
    assert first.status_code == 200
    assert first.json()["queued_count"] == 1

    second = await client.post(MANUAL_SEND_URL, headers=staf_headers, json=body)
    assert second.status_code == 400
    assert await queued_manual_count() == 1

    preview = (await client.post(MANUAL_PREVIEW_URL, headers=staf_headers, json=body)).json()
    assert preview["eligible_count"] == 0
    assert preview["excluded_breakdown"]["already_queued"] == 1
    assert preview["items"][0]["exclude_reason"] == "Sudah dalam antrean"


async def test_manual_send_again_is_allowed_once_the_first_one_went_out(
    client: httpx.AsyncClient, staf_headers: dict[str, str]
) -> None:
    invoice_id = await add_invoice(1)
    tpl_id = await create_template()
    body = {"template_id": tpl_id, "invoice_ids": [invoice_id]}
    await client.post(MANUAL_SEND_URL, headers=staf_headers, json=body)

    async with get_session_factory()() as db:
        message = (await db.scalars(select(Message))).one()
        message.status = "sent"
        await db.commit()

    again = await client.post(MANUAL_SEND_URL, headers=staf_headers, json=body)
    assert again.status_code == 200
    assert again.json()["queued_count"] == 1
    assert await queued_manual_count() == 2


async def test_manual_allows_resend_after_delete_and_stale_sending_delete(
    client: httpx.AsyncClient, staf_headers: dict[str, str]
) -> None:
    invoice_id = await add_invoice(1)
    tpl_id = await create_template()
    body = {"template_id": tpl_id, "invoice_ids": [invoice_id]}

    first = await client.post(MANUAL_SEND_URL, headers=staf_headers, json=body)
    assert first.status_code == 200

    async with get_session_factory()() as db:
        msg = (await db.scalars(select(Message))).one()
        msg_id = msg.id
        # Simulate a worker dying mid-claim without getting a provider id.
        msg.status = "sending"
        msg.provider_message_id = None
        msg.updated_at = datetime.now(UTC) - timedelta(minutes=20)
        await db.commit()

    # Blocked while still pending
    blocked = (await client.post(MANUAL_PREVIEW_URL, headers=staf_headers, json=body)).json()
    assert blocked["eligible_count"] == 0
    assert blocked["excluded_breakdown"]["already_queued"] == 1

    # Deleting the stuck sending row unblocks manual sending immediately
    deleted = await client.delete(f"/api/v1/messages/{msg_id}", headers=staf_headers)
    assert deleted.status_code == 204

    unblocked = (await client.post(MANUAL_PREVIEW_URL, headers=staf_headers, json=body)).json()
    assert unblocked["eligible_count"] == 1
    assert unblocked["excluded_breakdown"]["already_queued"] == 0

    resent = await client.post(MANUAL_SEND_URL, headers=staf_headers, json=body)
    assert resent.status_code == 200
    assert resent.json()["queued_count"] == 1


async def test_manual_preview_and_send_include_siakad_rekap_breakdown(
    client: httpx.AsyncClient, staf_headers: dict[str, str]
) -> None:
    csv_text = (
        "No,PRODI,Jenis Kelas,NIM,Nama,No Hp,Tahun Akademik,Komponen Biaya,Rincian,Rekap\n"
        "1,55201 - TI,Kelas Pagi,20230099,Adie Jati,081234567890,2026/2027 Ganjil,"
        "001 - DU,200000,800000\n"
        ",,,,,,2026/2027 Ganjil,002 - SPP,500000,\n"
        ",,,,,,2026/2027 Ganjil,006 - Kegiatan,100000,\n"
    )
    up = await client.post(
        "/api/v1/imports",
        files={"file": ("siakad.csv", csv_text.encode("utf-8"))},
        data={"source": "siakad"},
        headers=staf_headers,
    )
    assert up.status_code == 201, up.text
    batch_id = up.json()["id"]
    ap = await client.post(
        f"/api/v1/imports/{batch_id}/apply",
        json={"mark_paid_invoice_ids": []},
        headers=staf_headers,
    )
    assert ap.status_code == 200, ap.text

    tpl_id = await create_template()
    preview = (
        await client.post(
            MANUAL_PREVIEW_URL,
            headers=staf_headers,
            json={"template_id": tpl_id},
        )
    ).json()
    assert preview["eligible_count"] == 1
    sample_body = preview["sample_preview"]["body"]
    assert "Rp800.000" in sample_body
    assert "Rincian tagihan:" in sample_body
    assert "- 001 - DU: Rp200.000" in sample_body
    assert "- 002 - SPP: Rp500.000" in sample_body
    assert "- 006 - Kegiatan: Rp100.000" in sample_body


async def test_manual_component_filter_customizes_billed_total_and_rincian(
    client: httpx.AsyncClient, staf_headers: dict[str, str]
) -> None:
    csv_text = (
        "No,PRODI,Jenis Kelas,NIM,Nama,No Hp,Tahun Akademik,Komponen Biaya,Rincian,Rekap\n"
        "1,55201 - TI,Kelas Pagi,20230099,Adie Jati,081234567890,2026/2027 Ganjil,"
        "001 - DU,200000,800000\n"
        ",,,,,,2026/2027 Ganjil,Biaya Praktikum,500000,\n"
        ",,,,,,2026/2027 Ganjil,Biaya Sidang Skripsi,100000,\n"
        "2,55201 - TI,Kelas Pagi,20230100,Budi Hanya SPP,081234567891,2026/2027 Ganjil,"
        "002 - SPP,1000000,1000000\n"
    )
    up = await client.post(
        "/api/v1/imports",
        files={"file": ("siakad.csv", csv_text.encode("utf-8"))},
        data={"source": "siakad"},
        headers=staf_headers,
    )
    assert up.status_code == 201
    batch_id = up.json()["id"]
    ap = await client.post(
        f"/api/v1/imports/{batch_id}/apply",
        json={"mark_paid_invoice_ids": []},
        headers=staf_headers,
    )
    assert ap.status_code == 200

    opts = (await client.get(MANUAL_OPTIONS_URL, headers=staf_headers)).json()
    assert opts["components"] == [
        "001 - DU",
        "002 - SPP",
        "Biaya Praktikum",
        "Biaya Sidang Skripsi",
    ]

    tpl_id = await create_template()
    # Select only Biaya Praktikum and Biaya Sidang Skripsi:
    # - Budi Hanya SPP is excluded (does not owe those components)
    # - Adie Jati is billed Rp600.000 (500.000 + 100.000), not Rp800.000
    preview = (
        await client.post(
            MANUAL_PREVIEW_URL,
            headers=staf_headers,
            json={
                "template_id": tpl_id,
                "components": ["Biaya Praktikum", "Biaya Sidang Skripsi"],
            },
        )
    ).json()
    assert preview["total_matched"] == 1
    assert preview["eligible_count"] == 1
    assert preview["items"][0]["student_name"] == "Adie Jati"
    assert preview["items"][0]["amount"] == 600_000
    assert preview["items"][0]["breakdown"] == [
        {"label": "Biaya Praktikum", "amount": 500_000},
        {"label": "Biaya Sidang Skripsi", "amount": 100_000},
    ]
    sample_body = preview["sample_preview"]["body"]
    assert "Rp600.000" in sample_body
    assert "Rp800.000" not in sample_body
    assert "- Biaya Praktikum: Rp500.000" in sample_body
    assert "- Biaya Sidang Skripsi: Rp100.000" in sample_body
    assert "001 - DU" not in sample_body

    send_res = await client.post(
        MANUAL_SEND_URL,
        headers=staf_headers,
        json={
            "template_id": tpl_id,
            "invoice_ids": [preview["items"][0]["invoice_id"]],
            "components": ["Biaya Praktikum", "Biaya Sidang Skripsi"],
        },
    )
    assert send_res.status_code == 200
    assert send_res.json()["queued_count"] == 1

    async with get_session_factory()() as db:
        msg = (await db.scalars(select(Message))).one()
        assert "Rp600.000" in msg.body
        assert "- Biaya Praktikum: Rp500.000" in msg.body
        assert "- Biaya Sidang Skripsi: Rp100.000" in msg.body
        assert "001 - DU" not in msg.body
