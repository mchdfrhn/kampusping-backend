"""Operational commands.

python -m app.cli seed                 # sync permissions + system roles (idempotent)
python -m app.cli create-admin         # first administrator (prompts / reads INITIAL_ADMIN_*)
python -m app.cli purge-tokens         # delete expired refresh tokens
"""

import argparse
import asyncio
import getpass
import sys

from pydantic import TypeAdapter, ValidationError

from app.core.config import settings
from app.core.permissions import ADMIN_ROLE
from app.core.security import StrongPassword, hash_password_async
from app.db.seed import seed_reference_data
from app.db.session import get_engine, get_session_factory
from app.modules.auth.repository import RefreshTokenRepository
from app.modules.roles.repository import RoleRepository
from app.modules.users.model import User
from app.modules.users.repository import UserRepository
from app.shared.utils import utc_now


async def _seed() -> None:
    async with get_session_factory()() as db:
        await seed_reference_data(db)
    print("Permissions and system roles are up to date.")


async def _create_admin(email: str, full_name: str, password: str) -> None:
    try:
        TypeAdapter(StrongPassword).validate_python(password)
    except ValidationError as exc:
        raise SystemExit(
            f"Weak password: {exc.errors()[0]['msg'].removeprefix('Value error, ')}"
        ) from exc

    async with get_session_factory()() as db:
        await seed_reference_data(db)
        users = UserRepository(db)
        if await users.get_by_email(email, include_deleted=True):
            raise SystemExit(f"A user with email {email} already exists.")
        admin_role = (await RoleRepository(db).get_by_names([ADMIN_ROLE]))[0]
        await users.add(
            User(
                email=email.lower(),
                full_name=full_name,
                password_hash=await hash_password_async(password),
                password_changed_at=utc_now(),
                is_active=True,
                roles=[admin_role],
                created_by="cli",
                updated_by="cli",
            )
        )
        await db.commit()
    print(f"Administrator {email} created.")


async def _purge_tokens() -> None:
    async with get_session_factory()() as db:
        removed = await RefreshTokenRepository(db).purge_expired()
        await db.commit()
    print(f"Removed {removed} expired refresh token(s).")


def _resolve_password() -> str:
    if settings.initial_admin_password:
        return settings.initial_admin_password.get_secret_value()
    if not sys.stdin.isatty():
        raise SystemExit("Set INITIAL_ADMIN_PASSWORD or run this command in an interactive shell.")
    first = getpass.getpass("Password: ")
    if first != getpass.getpass("Repeat password: "):
        raise SystemExit("Passwords do not match.")
    return first


async def _run(args: argparse.Namespace) -> None:
    try:
        if args.command == "seed":
            await _seed()
        elif args.command == "purge-tokens":
            await _purge_tokens()
        elif args.command == "create-admin":
            email = args.email or settings.initial_admin_email
            if not email:
                raise SystemExit("Provide --email or set INITIAL_ADMIN_EMAIL.")
            name = args.name or settings.initial_admin_name
            await _create_admin(email, name, _resolve_password())
    finally:
        await get_engine().dispose()


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m app.cli", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("seed", help="sync permissions and system roles")
    sub.add_parser("purge-tokens", help="delete expired refresh tokens")
    admin = sub.add_parser("create-admin", help="create the first administrator")
    admin.add_argument("--email")
    admin.add_argument("--name")
    asyncio.run(_run(parser.parse_args()))


if __name__ == "__main__":
    main()
