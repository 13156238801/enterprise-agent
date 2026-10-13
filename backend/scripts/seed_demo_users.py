import asyncio

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlalchemy.orm import selectinload

from app.auth.security import PasswordHasher, normalize_username
from app.core.config import Settings, get_settings
from app.domain.auth.enums import UserRole
from app.infrastructure.database import create_db_engine, create_session_factory
from app.models.auth import User, UserRoleAssignment

_DEMO_USERS = (
    ("applicant", "Demo Applicant", UserRole.APPLICANT),
    ("manager", "Demo Manager", UserRole.APPROVER),
    ("data_admin", "Demo Data Admin", UserRole.DATA_ADMIN),
    ("admin", "Demo Admin", UserRole.ADMIN),
)


async def seed_demo_users(
    settings: Settings,
    session_factory: async_sessionmaker,
    password_hasher: PasswordHasher | None = None,
) -> int:
    """在单个事务中 reconcile 四个虚构账号并返回账号数。"""
    if settings.app_env == "production":
        raise RuntimeError("Demo seed is disabled in production")
    if settings.demo_user_password is None:
        raise ValueError("DEMO_USER_PASSWORD must be configured")
    password = settings.demo_user_password.get_secret_value()
    if not password:
        raise ValueError("DEMO_USER_PASSWORD must not be empty")

    hasher = password_hasher or PasswordHasher()
    async with session_factory() as session:
        async with session.begin():
            for raw_username, display_name, role in _DEMO_USERS:
                username = normalize_username(raw_username)
                result = await session.execute(
                    select(User)
                    .options(selectinload(User.role_assignments))
                    .where(User.username == username)
                )
                user = result.scalar_one_or_none()
                if user is None:
                    password_hash = await asyncio.to_thread(hasher.hash, password)
                    user = User(
                        username=username,
                        display_name=display_name,
                        password_hash=password_hash,
                        enabled=True,
                    )
                    session.add(user)
                    await session.flush()
                    session.add(UserRoleAssignment(user_id=user.id, role=role))
                    continue
                else:
                    try:
                        password_matches, updated_hash = await asyncio.to_thread(
                            hasher.verify_and_update, password, user.password_hash
                        )
                    except (ValueError, TypeError):
                        password_matches = False
                        updated_hash = None
                    if updated_hash is not None:
                        user.password_hash = updated_hash
                    elif not password_matches:
                        user.password_hash = await asyncio.to_thread(hasher.hash, password)
                    user.display_name = display_name
                    user.enabled = True

                expected_roles = {role}
                for assignment in list(user.role_assignments):
                    if assignment.role not in expected_roles:
                        await session.delete(assignment)
                if role not in {item.role for item in user.role_assignments}:
                    session.add(UserRoleAssignment(user=user, role=role))
            await session.flush()
    return len(_DEMO_USERS)


async def main() -> None:
    """读取运行配置、执行 seed，并确保临时 Engine 被释放。"""
    settings = get_settings()
    if settings.app_env == "production":
        raise RuntimeError("Demo seed is disabled in production")
    if settings.demo_user_password is None:
        raise ValueError("DEMO_USER_PASSWORD must be configured")
    if not settings.demo_user_password.get_secret_value():
        raise ValueError("DEMO_USER_PASSWORD must not be empty")
    engine = create_db_engine(settings)
    try:
        count = await seed_demo_users(settings, create_session_factory(engine))
        print(f"Reconciled {count} demo users.")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())

