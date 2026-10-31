import pytest
from sqlalchemy.exc import IntegrityError

from app.db.session import get_session_factory
from app.modules.students.model import Student

NIM = "NIM-SECRET-20210042"


async def test_sql_errors_do_not_leak_bound_parameters() -> None:
    """A failing statement's text names the SQL but not the bound values (UU PDP: students'
    NIM, name and phone must not end up in logs through ``logger.exception``)."""
    async with get_session_factory()() as db:
        db.add(Student(nim=NIM, name="Budi Santoso", phone="+6281234567890", phone_status="valid"))
        await db.commit()

    async with get_session_factory()() as db:
        db.add(Student(nim=NIM, name="Siti Rahma", phone="+6281234567891", phone_status="valid"))
        with pytest.raises(IntegrityError) as caught:
            await db.commit()

    text = str(caught.value)
    assert "INSERT INTO students" in text
    assert NIM not in text
    assert "Siti Rahma" not in text
    assert "+6281234567891" not in text
