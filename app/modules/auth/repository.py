from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.shared.utils import utc_now

from .model import RefreshToken


class RefreshTokenRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def add(self, token: RefreshToken) -> RefreshToken:
        self.db.add(token)
        await self.db.flush()
        return token

    async def get_by_hash(self, token_hash: str) -> RefreshToken | None:
        return await self.db.scalar(
            select(RefreshToken).where(RefreshToken.token_hash == token_hash)
        )

    async def revoke_family(self, family_id: str) -> None:
        await self.db.execute(
            update(RefreshToken)
            .where(RefreshToken.family_id == family_id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=utc_now())
        )

    async def revoke_all_for_user(self, user_id: int) -> None:
        await self.db.execute(
            update(RefreshToken)
            .where(RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=utc_now())
        )

    async def purge_expired(self) -> int:
        """Delete expired tokens only. Revoked-but-unexpired rows are kept so that replaying a
        rotated token is still detected (and its whole family revoked) until it would expire."""
        result = await self.db.execute(
            delete(RefreshToken).where(RefreshToken.expires_at < utc_now())
        )
        return result.rowcount or 0  # type: ignore[attr-defined]
