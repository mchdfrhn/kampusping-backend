from collections.abc import Sequence

from sqlalchemy import or_, select

from app.shared.pagination import ListParams
from app.shared.repository import BaseRepository
from app.shared.utils import like_pattern

from .model import User


class UserRepository(BaseRepository[User]):
    model = User
    sort_fields = {
        "email": User.email,
        "full_name": User.full_name,
        "created_at": User.created_at,
        "updated_at": User.updated_at,
        "last_login_at": User.last_login_at,
    }

    async def search(self, params: ListParams) -> tuple[Sequence[User], int]:
        stmt = self.select()
        if params.search:
            pattern = like_pattern(params.search)
            stmt = stmt.where(
                or_(
                    User.email.ilike(pattern, escape="\\"),
                    User.full_name.ilike(pattern, escape="\\"),
                )
            )
        return await self.paginate(stmt, params)

    async def get_by_email(self, email: str, *, include_deleted: bool = False) -> User | None:
        """Emails are stored lowercase. ``include_deleted`` is for uniqueness checks, since the
        database constraint also covers soft-deleted rows."""
        stmt = select(User) if include_deleted else self.select()
        return await self.db.scalar(stmt.where(User.email == email.lower()))
