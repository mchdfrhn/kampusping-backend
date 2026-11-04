from datetime import date
from typing import Any

import httpx
import pytest
from sqlalchemy import func, select

from app.db.session import get_session_factory
from app.modules.audit.model import AuditEvent
from app.modules.invoices.model import Invoice
from app.modules.schedules.model import ScheduleRule
from app.modules.settings.model import AppSettings
from app.modules.students.model import Student
from app.modules.templates.model import MessageTemplate
from app.modules.templates.rendering import (
    ALLOWED_VARIABLES,
    MONTH_NAMES_ID,
    OPT_OUT_FOOTER,
    SAMPLE_CONTEXT,
    build_message_body,
    format_date_id,
    format_rupiah,
    render_body,
    unknown_variables,
)
from app.modules.templates.seed import seed_default_templates_and_rules

TEMPLATES_URL = "/api/v1/templates"
RULES_URL = "/api/v1/schedule-rules"
AUDIT_URL = "/api/v1/audit-events"

SAMPLE_BODY = "Halo {{nama}}, tagihan {{nominal}} jatuh tempo {{tanggal_jatuh_tempo}}."


def make_student() -> Student:
    return Student(nim="2021001", name="Budi Santoso")


def make_invoice(
    *, source: str = "siakad", amount: int = 1_250_000, due: date = date(2026, 11, 15)
) -> Invoice:
    return Invoice(
        invoice_code="INV-1",
        semester="Ganjil 2026/2027",
        amount=amount,
        due_date=due,
        source=source,
    )


def make_settings(**overrides: str) -> AppSettings:
    values = {
        "campus_name": "Kampus Contoh",
        "finance_contact": "0812-0000-0000",
        "payment_link_siakad": "https://siakad.contoh.ac.id/bayar",
        "payment_guide_sipema": "Buka SIPEMA lalu pilih Pembayaran.",
    }
    values.update(overrides)
    return AppSettings(**values)


def template_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {"name": "Templat uji", "body": SAMPLE_BODY, "is_active": True}
    payload.update(overrides)
    return payload


async def create_template(
    client: httpx.AsyncClient, headers: dict[str, str], **overrides: Any
) -> dict[str, Any]:
    response = await client.post(TEMPLATES_URL, json=template_payload(**overrides), headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


def rule_payload(template_id: int, **overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "name": "H-5",
        "offset_days": -5,
        "template_id": template_id,
        "is_active": True,
    }
    payload.update(overrides)
    return payload


async def create_rule(
    client: httpx.AsyncClient, headers: dict[str, str], template_id: int, **overrides: Any
) -> dict[str, Any]:
    response = await client.post(
        RULES_URL, json=rule_payload(template_id, **overrides), headers=headers
    )
    assert response.status_code == 201, response.text
    return response.json()


# --- rendering (pure) ---------------------------------------------------------------------------


def test_render_body_formats_amount_and_date_in_indonesian() -> None:
    assert format_rupiah(1_250_000) == "1.250.000"
    assert format_date_id(date(2026, 11, 15)) == "15 November 2026"

    context = {"nominal": format_rupiah(1_250_000), "tanggal_jatuh_tempo": "15 November 2026"}
    rendered = render_body("Rp{{nominal}} jatuh tempo {{tanggal_jatuh_tempo}}", context)

    assert rendered == "Rp1.250.000 jatuh tempo 15 November 2026"


@pytest.mark.parametrize(
    ("amount", "expected"),
    [(0, "0"), (999, "999"), (1_000, "1.000"), (999_999_999, "999.999.999")],
)
def test_format_rupiah_edges(amount: int, expected: str) -> None:
    assert format_rupiah(amount) == expected


def test_format_date_id_uses_indonesian_month_names() -> None:
    names = [format_date_id(date(2026, month, 1)).split()[1] for month in range(1, 13)]

    assert names == list(MONTH_NAMES_ID)
    assert names[0] == "Januari"
    assert names[7] == "Agustus"
    assert names[11] == "Desember"
    assert format_date_id(date(2026, 3, 5)) == "5 Maret 2026"


def test_render_body_accepts_spaces_and_repeated_variables() -> None:
    rendered = render_body("{{ nama }} / {{nama}} / {{  nim  }}", {"nama": "Budi", "nim": "1"})

    assert rendered == "Budi / Budi / 1"


def test_render_body_leaves_unknown_placeholders_untouched() -> None:
    assert render_body("{{nama}} {{foo}}", {"nama": "Budi"}) == "Budi {{foo}}"


def test_render_body_does_not_escape_or_reexpand_values() -> None:
    rendered = render_body("{{nama}}", {"nama": "A & B <x> {{nim}}"})

    assert rendered == "A & B <x> {{nim}}"


def test_unknown_variables_keeps_first_appearance_order_and_unique() -> None:
    body = "{{b}} {{nama}} {{ a }} {{b}} {{c}} {{a}}"

    assert unknown_variables(body) == ["b", "a", "c"]
    assert unknown_variables("tanpa variabel") == []
    assert unknown_variables(" ".join("{{" + name + "}}" for name in ALLOWED_VARIABLES)) == []


def test_build_message_body_appends_footer_and_picks_link_by_source() -> None:
    template = MessageTemplate(
        name="t", body="{{nama}}|{{nim}}|{{semester}}|{{cara_bayar}}|{{link_pembayaran}}"
    )
    student, settings = make_student(), make_settings()

    siakad = build_message_body(
        template, student=student, invoice=make_invoice(source="siakad"), settings=settings
    )
    sipema = build_message_body(
        template, student=student, invoice=make_invoice(source="sipema"), settings=settings
    )

    assert siakad == (
        "Budi Santoso|2021001|Ganjil 2026/2027|SIAKAD/Civitas|"
        "https://siakad.contoh.ac.id/bayar\n\n" + OPT_OUT_FOOTER
    )
    assert sipema == (
        "Budi Santoso|2021001|Ganjil 2026/2027|SIPEMA|"
        "Buka SIPEMA lalu pilih Pembayaran.\n\n" + OPT_OUT_FOOTER
    )


def test_build_message_body_fills_amount_date_and_campus_context() -> None:
    template = MessageTemplate(
        name="t", body="{{nominal}} {{tanggal_jatuh_tempo}} {{kontak_keuangan}} {{nama_kampus}}"
    )

    text = build_message_body(
        template,
        student=make_student(),
        invoice=make_invoice(amount=0, due=date(2026, 8, 17)),
        settings=make_settings(),
    )

    assert text == "0 17 Agustus 2026 0812-0000-0000 Kampus Contoh\n\n" + OPT_OUT_FOOTER


def test_build_message_body_with_empty_settings_renders_empty_strings() -> None:
    template = MessageTemplate(
        name="t",
        body="[{{link_pembayaran}}][{{kontak_keuangan}}][{{nama_kampus}}]",
    )
    empty = make_settings(
        campus_name="", finance_contact="", payment_link_siakad="", payment_guide_sipema=""
    )

    for source in ("siakad", "sipema"):
        text = build_message_body(
            template, student=make_student(), invoice=make_invoice(source=source), settings=empty
        )
        assert text == "[][][]\n\n" + OPT_OUT_FOOTER


def test_footer_cannot_be_removed() -> None:
    student, invoice, settings = make_student(), make_invoice(), make_settings()
    bodies = [
        "Halo {{nama}}.",  # stored bodies never need the footer
        f"Halo {{{{nama}}}}.\n\n{OPT_OUT_FOOTER}",  # footer already present
        f"Halo {{{{nama}}}}.\n\n{OPT_OUT_FOOTER}\n\n{OPT_OUT_FOOTER}\n\n",  # repeated
        f"Halo {{{{nama}}}}.\n\n\n{OPT_OUT_FOOTER}   \n",  # extra blank lines
    ]

    for body in bodies:
        text = build_message_body(
            MessageTemplate(name="t", body=body),
            student=student,
            invoice=invoice,
            settings=settings,
        )
        assert text == "Halo Budi Santoso.\n\n" + OPT_OUT_FOOTER
        assert text.count(OPT_OUT_FOOTER) == 1


def test_footer_text_in_the_middle_of_a_body_is_kept() -> None:
    body = f"{OPT_OUT_FOOTER} Terima kasih."
    text = build_message_body(
        MessageTemplate(name="t", body=body),
        student=make_student(),
        invoice=make_invoice(),
        settings=make_settings(),
    )

    assert text == f"{OPT_OUT_FOOTER} Terima kasih.\n\n{OPT_OUT_FOOTER}"


def test_body_that_is_only_the_footer_yields_just_the_footer() -> None:
    text = build_message_body(
        MessageTemplate(name="t", body=OPT_OUT_FOOTER),
        student=make_student(),
        invoice=make_invoice(),
        settings=make_settings(),
    )

    assert text == OPT_OUT_FOOTER


# --- template API -------------------------------------------------------------------------------


async def test_unknown_variable_rejects_save_with_422_naming_it(
    client: httpx.AsyncClient, staf_headers: dict[str, str]
) -> None:
    response = await client.post(
        TEMPLATES_URL,
        json=template_payload(body="Halo {{nama}} {{foo}} {{bar}} {{foo}}"),
        headers=staf_headers,
    )

    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "validation_error"
    assert error["message"] == "Variabel tidak dikenal: {{foo}}, {{bar}}."
    assert error["details"] == ["foo", "bar"]

    # Nothing was saved.
    listing = await client.get(TEMPLATES_URL, headers=staf_headers)
    assert listing.json()["total"] == 0


async def test_unknown_variable_also_rejected_on_update(
    client: httpx.AsyncClient, staf_headers: dict[str, str]
) -> None:
    created = await create_template(client, staf_headers)

    response = await client.put(
        f"{TEMPLATES_URL}/{created['id']}",
        json=template_payload(body="Halo {{nama_lengkap}}"),
        headers=staf_headers,
    )

    assert response.status_code == 422
    assert response.json()["error"]["message"] == "Variabel tidak dikenal: {{nama_lengkap}}."
    fetched = await client.get(f"{TEMPLATES_URL}/{created['id']}", headers=staf_headers)
    assert fetched.json()["body"] == SAMPLE_BODY


async def test_template_crud_roundtrip(
    client: httpx.AsyncClient, staf_headers: dict[str, str]
) -> None:
    created = await create_template(client, staf_headers, name="  Pengingat A  ")
    assert created["name"] == "Pengingat A"
    assert created["is_active"] is True
    assert created["body"] == SAMPLE_BODY

    updated = await client.put(
        f"{TEMPLATES_URL}/{created['id']}",
        json=template_payload(name="Pengingat B", body="Halo {{ nama }}", is_active=False),
        headers=staf_headers,
    )
    assert updated.status_code == 200
    assert updated.json()["name"] == "Pengingat B"
    assert updated.json()["is_active"] is False

    deleted = await client.delete(f"{TEMPLATES_URL}/{created['id']}", headers=staf_headers)
    assert deleted.status_code == 204
    gone = await client.get(f"{TEMPLATES_URL}/{created['id']}", headers=staf_headers)
    assert gone.status_code == 404
    again = await client.delete(f"{TEMPLATES_URL}/{created['id']}", headers=staf_headers)
    assert again.status_code == 404


@pytest.mark.parametrize(
    "overrides",
    [
        {"name": ""},
        {"name": "x" * 101},
        {"body": ""},
        {"body": "   "},
        {"body": "x" * 2001},
    ],
)
async def test_template_field_limits_are_422(
    client: httpx.AsyncClient, staf_headers: dict[str, str], overrides: dict[str, Any]
) -> None:
    response = await client.post(
        TEMPLATES_URL, json=template_payload(**overrides), headers=staf_headers
    )

    assert response.status_code == 422


async def test_template_name_unique_among_non_deleted_and_reusable_after_delete(
    client: httpx.AsyncClient, staf_headers: dict[str, str]
) -> None:
    first = await create_template(client, staf_headers, name="Pengingat")

    duplicate = await client.post(
        TEMPLATES_URL, json=template_payload(name="Pengingat"), headers=staf_headers
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "conflict"
    assert duplicate.json()["error"]["message"] == "Templat dengan nama ini sudah ada."

    other = await create_template(client, staf_headers, name="Lain")
    rename = await client.put(
        f"{TEMPLATES_URL}/{other['id']}",
        json=template_payload(name="Pengingat"),
        headers=staf_headers,
    )
    assert rename.status_code == 409
    # Saving a template under its own name is fine.
    same = await client.put(
        f"{TEMPLATES_URL}/{first['id']}",
        json=template_payload(name="Pengingat", body="Halo {{nama}}"),
        headers=staf_headers,
    )
    assert same.status_code == 200

    # A soft-deleted template never blocks reuse of its name.
    await client.delete(f"{TEMPLATES_URL}/{first['id']}", headers=staf_headers)
    reused = await client.post(
        TEMPLATES_URL, json=template_payload(name="Pengingat"), headers=staf_headers
    )
    assert reused.status_code == 201


async def test_template_list_sorted_by_name_and_filterable(
    client: httpx.AsyncClient, staf_headers: dict[str, str]
) -> None:
    await create_template(client, staf_headers, name="B template")
    await create_template(client, staf_headers, name="A template", is_active=False)
    await create_template(client, staf_headers, name="C template")

    listing = await client.get(TEMPLATES_URL, headers=staf_headers)
    assert [row["name"] for row in listing.json()["items"]] == [
        "A template",
        "B template",
        "C template",
    ]

    descending = await client.get(
        TEMPLATES_URL, params={"sort_by": "name", "sort_order": "desc"}, headers=staf_headers
    )
    assert descending.json()["items"][0]["name"] == "C template"

    active = await client.get(TEMPLATES_URL, params={"is_active": "true"}, headers=staf_headers)
    assert active.json()["total"] == 2

    searched = await client.get(TEMPLATES_URL, params={"search": "b tem"}, headers=staf_headers)
    assert [row["name"] for row in searched.json()["items"]] == ["B template"]

    bad_sort = await client.get(TEMPLATES_URL, params={"sort_by": "body"}, headers=staf_headers)
    assert bad_sort.status_code == 422


async def test_preview_renders_sample_data_with_footer(
    client: httpx.AsyncClient, staf_headers: dict[str, str]
) -> None:
    body = (
        "{{nama}} ({{nim}}) {{semester}} Rp{{nominal}} {{tanggal_jatuh_tempo}} "
        "{{cara_bayar}} {{link_pembayaran}} {{kontak_keuangan}} {{nama_kampus}}"
    )

    response = await client.post(
        f"{TEMPLATES_URL}/preview", json={"body": body}, headers=staf_headers
    )

    assert response.status_code == 200
    assert response.json() == {
        "rendered": (
            "Budi Santoso (2021001) Ganjil 2026/2027 Rp1.250.000 15 November 2026 "
            "SIAKAD/Civitas https://siakad.contoh.ac.id/bayar 0812-0000-0000 Kampus Contoh"
            "\n\n" + OPT_OUT_FOOTER
        ),
        "unknown_variables": [],
    }


async def test_preview_reports_unknown_variables_without_failing(
    client: httpx.AsyncClient, staf_headers: dict[str, str]
) -> None:
    response = await client.post(
        f"{TEMPLATES_URL}/preview", json={"body": "{{nama}} {{foo}}"}, headers=staf_headers
    )

    assert response.status_code == 200
    assert response.json()["rendered"] == "Budi Santoso {{foo}}\n\n" + OPT_OUT_FOOTER
    assert response.json()["unknown_variables"] == ["foo"]


async def test_preview_does_not_double_the_footer(
    client: httpx.AsyncClient, staf_headers: dict[str, str]
) -> None:
    response = await client.post(
        f"{TEMPLATES_URL}/preview",
        json={"body": f"Halo\n\n{OPT_OUT_FOOTER}"},
        headers=staf_headers,
    )

    assert response.json()["rendered"] == "Halo\n\n" + OPT_OUT_FOOTER


async def test_preview_rejects_empty_or_oversize_body(
    client: httpx.AsyncClient, staf_headers: dict[str, str]
) -> None:
    for body in ("", "x" * 2001):
        response = await client.post(
            f"{TEMPLATES_URL}/preview", json={"body": body}, headers=staf_headers
        )
        assert response.status_code == 422


# --- schedule rules API -------------------------------------------------------------------------


async def test_rule_crud_embeds_template_and_sorts_by_offset(
    client: httpx.AsyncClient, staf_headers: dict[str, str]
) -> None:
    template = await create_template(client, staf_headers, name="Pengingat")
    await create_rule(client, staf_headers, template["id"], name="H+3", offset_days=3)
    created = await create_rule(client, staf_headers, template["id"], name="H-7", offset_days=-7)
    await create_rule(client, staf_headers, template["id"], name="H", offset_days=0)

    assert created["template"] == {"id": template["id"], "name": "Pengingat"}
    assert created["offset_days"] == -7

    listing = await client.get(RULES_URL, headers=staf_headers)
    assert [row["offset_days"] for row in listing.json()["items"]] == [-7, 0, 3]
    assert listing.json()["items"][0]["template"]["name"] == "Pengingat"

    desc = await client.get(RULES_URL, params={"sort_order": "desc"}, headers=staf_headers)
    assert [row["offset_days"] for row in desc.json()["items"]] == [3, 0, -7]

    other = await create_template(client, staf_headers, name="Tunggakan")
    updated = await client.put(
        f"{RULES_URL}/{created['id']}",
        json=rule_payload(other["id"], name="H-6", offset_days=-6, is_active=False),
        headers=staf_headers,
    )
    assert updated.status_code == 200
    assert updated.json()["template"] == {"id": other["id"], "name": "Tunggakan"}
    assert updated.json()["is_active"] is False

    fetched = await client.get(f"{RULES_URL}/{created['id']}", headers=staf_headers)
    assert fetched.json()["offset_days"] == -6

    deleted = await client.delete(f"{RULES_URL}/{created['id']}", headers=staf_headers)
    assert deleted.status_code == 204
    assert (
        await client.get(f"{RULES_URL}/{created['id']}", headers=staf_headers)
    ).status_code == 404


@pytest.mark.parametrize(
    "overrides",
    [
        {"offset_days": 61},
        {"offset_days": -61},
        {"offset_days": 1.5},
        {"name": ""},
        {"name": "x" * 51},
    ],
)
async def test_rule_field_limits_are_422(
    client: httpx.AsyncClient, staf_headers: dict[str, str], overrides: dict[str, Any]
) -> None:
    template = await create_template(client, staf_headers)

    response = await client.post(
        RULES_URL, json=rule_payload(template["id"], **overrides), headers=staf_headers
    )

    assert response.status_code == 422


async def test_rule_offset_boundaries_are_accepted(
    client: httpx.AsyncClient, staf_headers: dict[str, str]
) -> None:
    template = await create_template(client, staf_headers)

    await create_rule(client, staf_headers, template["id"], name="min", offset_days=-60)
    await create_rule(client, staf_headers, template["id"], name="max", offset_days=60)


async def test_duplicate_active_offset_409(
    client: httpx.AsyncClient, staf_headers: dict[str, str]
) -> None:
    """Ruling: duplicates are rejected among ALL non-deleted rules, active or not."""
    template = await create_template(client, staf_headers)
    first = await create_rule(client, staf_headers, template["id"], name="H-3", offset_days=-3)

    duplicate = await client.post(
        RULES_URL,
        json=rule_payload(template["id"], name="Lain", offset_days=-3),
        headers=staf_headers,
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "conflict"
    assert (
        duplicate.json()["error"]["message"] == "Sudah ada aturan jadwal dengan selisih hari ini."
    )

    # An inactive rule still holds its offset.
    await client.put(
        f"{RULES_URL}/{first['id']}",
        json=rule_payload(template["id"], name="H-3", offset_days=-3, is_active=False),
        headers=staf_headers,
    )
    still_taken = await client.post(
        RULES_URL,
        json=rule_payload(template["id"], name="Lain", offset_days=-3),
        headers=staf_headers,
    )
    assert still_taken.status_code == 409

    # Moving another rule onto a taken offset is rejected; keeping one's own offset is fine.
    second = await create_rule(client, staf_headers, template["id"], name="H-1", offset_days=-1)
    clash = await client.put(
        f"{RULES_URL}/{second['id']}",
        json=rule_payload(template["id"], name="H-1", offset_days=-3),
        headers=staf_headers,
    )
    assert clash.status_code == 409
    same = await client.put(
        f"{RULES_URL}/{second['id']}",
        json=rule_payload(template["id"], name="H-1 baru", offset_days=-1),
        headers=staf_headers,
    )
    assert same.status_code == 200

    # A deleted rule releases its offset.
    await client.delete(f"{RULES_URL}/{first['id']}", headers=staf_headers)
    await create_rule(client, staf_headers, template["id"], name="Baru", offset_days=-3)


async def test_rule_requires_existing_non_deleted_template(
    client: httpx.AsyncClient, staf_headers: dict[str, str]
) -> None:
    missing = await client.post(RULES_URL, json=rule_payload(9999), headers=staf_headers)
    assert missing.status_code == 422
    assert missing.json()["error"]["code"] == "validation_error"
    assert missing.json()["error"]["message"] == "Templat pesan tidak ditemukan."

    template = await create_template(client, staf_headers)
    await client.delete(f"{TEMPLATES_URL}/{template['id']}", headers=staf_headers)
    deleted = await client.post(RULES_URL, json=rule_payload(template["id"]), headers=staf_headers)
    assert deleted.status_code == 422

    live = await create_template(client, staf_headers, name="Hidup")
    rule = await create_rule(client, staf_headers, live["id"])
    moved = await client.put(
        f"{RULES_URL}/{rule['id']}", json=rule_payload(9999), headers=staf_headers
    )
    assert moved.status_code == 422


async def test_delete_template_used_by_active_rule_409(
    client: httpx.AsyncClient, staf_headers: dict[str, str]
) -> None:
    template = await create_template(client, staf_headers)
    rule = await create_rule(client, staf_headers, template["id"])

    blocked = await client.delete(f"{TEMPLATES_URL}/{template['id']}", headers=staf_headers)
    assert blocked.status_code == 409
    assert blocked.json()["error"]["code"] == "conflict"
    assert blocked.json()["error"]["message"] == "Template masih dipakai oleh aturan jadwal."

    # Inactive rules still reference the template.
    await client.put(
        f"{RULES_URL}/{rule['id']}",
        json=rule_payload(template["id"], is_active=False),
        headers=staf_headers,
    )
    still_blocked = await client.delete(f"{TEMPLATES_URL}/{template['id']}", headers=staf_headers)
    assert still_blocked.status_code == 409

    # Once the rule is gone the template can go too.
    await client.delete(f"{RULES_URL}/{rule['id']}", headers=staf_headers)
    allowed = await client.delete(f"{TEMPLATES_URL}/{template['id']}", headers=staf_headers)
    assert allowed.status_code == 204


# --- seed ---------------------------------------------------------------------------------------


async def test_seed_creates_defaults_once_and_never_overwrites_edits(
    client: httpx.AsyncClient, staf_headers: dict[str, str]
) -> None:
    async with get_session_factory()() as db:
        assert await seed_default_templates_and_rules(db) is True

    templates = (await client.get(TEMPLATES_URL, headers=staf_headers)).json()["items"]
    assert [row["name"] for row in templates] == [  # the API lists by name
        "Penagihan rutin",
        "Pengingat sebelum jatuh tempo",
        "Tunggakan setelah jatuh tempo",
    ]
    routine, reminder, arrears = templates
    assert (
        "Pembayaran dapat dilakukan melalui {{cara_bayar}}: {{link_pembayaran}}" in reminder["body"]
    )
    assert (
        "Mohon segera melakukan pembayaran melalui {{cara_bayar}}: {{link_pembayaran}}"
        in (arrears["body"])
    )
    # The routine template is not tied to the due date, so it must not claim either side of it.
    assert "akan jatuh tempo" not in routine["body"]
    assert "melewati" not in routine["body"]
    assert "{{" not in render_body(routine["body"], SAMPLE_CONTEXT)
    for row in templates:
        assert "SIAKAD" not in row["body"]
        assert OPT_OUT_FOOTER not in row["body"]
        assert unknown_variables(row["body"]) == []
        assert row["is_active"] is True

    rules = (await client.get(RULES_URL, headers=staf_headers)).json()["items"]
    assert [(r["name"], r["offset_days"], r["template"]["name"]) for r in rules] == [
        ("H-7", -7, reminder["name"]),
        ("H-3", -3, reminder["name"]),
        ("H-1", -1, reminder["name"]),
        ("H", 0, reminder["name"]),
        ("H+3", 3, arrears["name"]),
        ("H+7", 7, arrears["name"]),
    ]  # no rule uses the routine template yet: it serves manual sending

    # Staff edit a template and a rule, delete another rule; reseeding must not touch anything.
    await client.put(
        f"{TEMPLATES_URL}/{reminder['id']}",
        json=template_payload(name="Pengingat saya", body="Halo {{nama}}"),
        headers=staf_headers,
    )
    await client.delete(f"{RULES_URL}/{rules[0]['id']}", headers=staf_headers)

    async with get_session_factory()() as db:
        assert await seed_default_templates_and_rules(db) is False

    templates_after = (await client.get(TEMPLATES_URL, headers=staf_headers)).json()["items"]
    assert sorted(row["name"] for row in templates_after) == [
        "Penagihan rutin",
        "Pengingat saya",
        "Tunggakan setelah jatuh tempo",
    ]
    assert len((await client.get(RULES_URL, headers=staf_headers)).json()["items"]) == 5


async def test_seed_does_nothing_when_only_soft_deleted_templates_exist(
    client: httpx.AsyncClient, staf_headers: dict[str, str]
) -> None:
    template = await create_template(client, staf_headers)
    await client.delete(f"{TEMPLATES_URL}/{template['id']}", headers=staf_headers)

    async with get_session_factory()() as db:
        assert await seed_default_templates_and_rules(db) is False
        templates = await db.scalar(select(func.count()).select_from(MessageTemplate))
        rules = await db.scalar(select(func.count()).select_from(ScheduleRule))

    assert templates == 1
    assert rules == 0


async def test_reference_seed_does_not_create_defaults() -> None:
    """Only the CLI seeds defaults; ``seed_reference_data`` (used by every test) must not."""
    async with get_session_factory()() as db:
        templates = await db.scalar(select(func.count()).select_from(MessageTemplate))

    assert templates == 0


# --- audit --------------------------------------------------------------------------------------


async def test_template_and_rule_changes_write_audit_events(
    client: httpx.AsyncClient, staf_headers: dict[str, str]
) -> None:
    template = await create_template(client, staf_headers, name="Audit")
    await client.put(
        f"{TEMPLATES_URL}/{template['id']}",
        json=template_payload(name="Audit", body="Rahasia {{nama}}", is_active=False),
        headers=staf_headers,
    )
    rule = await create_rule(client, staf_headers, template["id"])
    await client.put(
        f"{RULES_URL}/{rule['id']}",
        json=rule_payload(template["id"], offset_days=-4),
        headers=staf_headers,
    )
    await client.delete(f"{RULES_URL}/{rule['id']}", headers=staf_headers)
    await client.delete(f"{TEMPLATES_URL}/{template['id']}", headers=staf_headers)

    async with get_session_factory()() as db:
        events = (await db.scalars(select(AuditEvent).order_by(AuditEvent.id))).all()

    actions = [(event.action, event.entity, event.entity_id) for event in events]
    assert actions == [
        ("template.created", "template", str(template["id"])),
        ("template.updated", "template", str(template["id"])),
        ("schedule_rule.created", "schedule_rule", str(rule["id"])),
        ("schedule_rule.updated", "schedule_rule", str(rule["id"])),
        ("schedule_rule.deleted", "schedule_rule", str(rule["id"])),
        ("template.deleted", "template", str(template["id"])),
    ]
    assert {event.actor for event in events} == {"staf@example.com"}
    assert events[1].detail == {"changed": ["body", "is_active"]}
    assert events[3].detail == {"changed": ["offset_days"]}
    # Message text never lands in the audit trail.
    assert all("Rahasia" not in str(event.detail) for event in events)


async def test_failed_writes_leave_no_audit_event(
    client: httpx.AsyncClient, staf_headers: dict[str, str]
) -> None:
    await client.post(TEMPLATES_URL, json=template_payload(body="{{foo}}"), headers=staf_headers)

    async with get_session_factory()() as db:
        count = await db.scalar(select(func.count()).select_from(AuditEvent))

    assert count == 0


# --- permissions --------------------------------------------------------------------------------


async def test_permission_matrix(
    client: httpx.AsyncClient,
    admin_headers: dict[str, str],
    staf_headers: dict[str, str],
    pimpinan_headers: dict[str, str],
) -> None:
    template = await create_template(client, staf_headers)
    rule = await create_rule(client, staf_headers, template["id"])
    requests: list[tuple[str, str, dict[str, Any] | None]] = [
        ("GET", TEMPLATES_URL, None),
        ("GET", f"{TEMPLATES_URL}/{template['id']}", None),
        ("POST", TEMPLATES_URL, template_payload(name="Baru")),
        ("POST", f"{TEMPLATES_URL}/preview", {"body": "Halo"}),
        ("PUT", f"{TEMPLATES_URL}/{template['id']}", template_payload()),
        ("GET", RULES_URL, None),
        ("GET", f"{RULES_URL}/{rule['id']}", None),
        ("POST", RULES_URL, rule_payload(template["id"], name="X", offset_days=9)),
        ("PUT", f"{RULES_URL}/{rule['id']}", rule_payload(template["id"])),
    ]

    for method, url, payload in requests:
        anonymous = await client.request(method, url, json=payload)
        assert anonymous.status_code == 401, (method, url)

        denied = await client.request(method, url, json=payload, headers=pimpinan_headers)
        assert denied.status_code == 403, (method, url)
        assert denied.json()["error"]["code"] == "forbidden"

    for headers in (staf_headers, admin_headers):
        for method, url, payload in requests[:2] + requests[3:5] + requests[5:7] + requests[8:]:
            allowed = await client.request(method, url, json=payload, headers=headers)
            assert allowed.status_code == 200, (method, url, allowed.text)

    # Deletes: 401 / 403 / 204.
    for url in (f"{RULES_URL}/{rule['id']}", f"{TEMPLATES_URL}/{template['id']}"):
        assert (await client.delete(url)).status_code == 401
        assert (await client.delete(url, headers=pimpinan_headers)).status_code == 403
    assert (
        await client.delete(f"{RULES_URL}/{rule['id']}", headers=staf_headers)
    ).status_code == 204
    assert (
        await client.delete(f"{TEMPLATES_URL}/{template['id']}", headers=admin_headers)
    ).status_code == 204
