import httpx

from app.core.constants import CSRF_HEADER
from app.core.security import hash_password
from app.db.session import get_session_factory
from app.modules.roles.repository import RoleRepository
from app.modules.users.model import User

PASSWORD = "Correct-horse-9"
CSRF = {CSRF_HEADER: "XMLHttpRequest"}


async def create_user(email: str, role: str | None, *, is_active: bool = True) -> User:
    async with get_session_factory()() as db:
        roles = await RoleRepository(db).get_by_names([role]) if role else []
        user = User(
            email=email,
            full_name=email.split("@")[0].title(),
            password_hash=hash_password(PASSWORD),
            is_active=is_active,
            roles=list(roles),
        )
        db.add(user)
        await db.commit()
        return user


async def login(client: httpx.AsyncClient, email: str, password: str = PASSWORD) -> httpx.Response:
    return await client.post("/api/v1/auth/login", json={"email": email, "password": password})


async def auth_headers(client: httpx.AsyncClient, email: str) -> dict[str, str]:
    response = await login(client, email)
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}
