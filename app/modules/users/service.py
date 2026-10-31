from collections.abc import Collection, Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import CONFLICT, NOT_FOUND, VALIDATION_ERROR
from app.core.exceptions import AppException
from app.core.security import hash_password_async
from app.modules.auth.repository import RefreshTokenRepository
from app.modules.roles.model import Role
from app.modules.roles.repository import RoleRepository
from app.shared.pagination import ListParams
from app.shared.utils import utc_now

from .model import User
from .repository import UserRepository
from .schema import UserCreate, UserUpdate


class UserService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.users = UserRepository(db)
        self.roles = RoleRepository(db)
        self.tokens = RefreshTokenRepository(db)

    async def list(self, params: ListParams) -> tuple[Sequence[User], int]:
        return await self.users.search(params)

    async def get(self, user_id: int) -> User:
        user = await self.users.get(user_id)
        if user is None:
            raise AppException("Pengguna tidak ditemukan.", status_code=404, code=NOT_FOUND)
        return user

    async def create(self, data: UserCreate, actor: User) -> User:
        email = data.email.lower()
        if await self.users.get_by_email(email, include_deleted=True):
            raise AppException(
                "Pengguna dengan email ini sudah ada.", status_code=409, code=CONFLICT
            )

        user = User(
            email=email,
            full_name=data.full_name,
            password_hash=await hash_password_async(data.password),
            password_changed_at=utc_now(),
            is_active=data.is_active,
            roles=list(await self._resolve_roles(data.role_ids)),
            created_by=actor.email,
            updated_by=actor.email,
        )
        await self.users.add(user)
        await self.db.commit()
        return user

    async def update(self, user_id: int, data: UserUpdate, actor: User) -> User:
        user = await self.get(user_id)
        if user.id == actor.id and not data.is_active:
            raise AppException(
                "Anda tidak dapat menonaktifkan akun Anda sendiri.", status_code=409, code=CONFLICT
            )

        user.full_name = data.full_name
        user.is_active = data.is_active
        user.roles = list(await self._resolve_roles(data.role_ids))
        user.updated_by = actor.email
        user.updated_at = utc_now()  # role-only changes do not touch any column

        sessions_invalid = not data.is_active
        if data.password:
            user.password_hash = await hash_password_async(data.password)
            user.password_changed_at = utc_now()
            user.failed_login_attempts = 0
            user.locked_until = None
            sessions_invalid = True
        if sessions_invalid:
            await self.tokens.revoke_all_for_user(user.id)

        await self.db.commit()
        return user

    async def delete(self, user_id: int, actor: User) -> None:
        user = await self.get(user_id)
        if user.id == actor.id:
            raise AppException(
                "Anda tidak dapat menghapus akun Anda sendiri.", status_code=409, code=CONFLICT
            )
        await self.users.soft_delete(user, actor.email)
        await self.tokens.revoke_all_for_user(user.id)
        await self.db.commit()

    async def _resolve_roles(self, role_ids: Collection[int]) -> Sequence[Role]:
        unique_ids = set(role_ids)
        found = await self.roles.get_many(unique_ids)
        unknown = unique_ids - {role.id for role in found}
        if unknown:
            raise AppException(
                "ID peran tidak dikenal: " + ", ".join(str(i) for i in sorted(unknown)),
                status_code=422,
                code=VALIDATION_ERROR,
            )
        return found
