from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from app.core.config import Settings
from app.infrastructure.database import create_db_engine, create_session_factory, database_url
from app.models import Base


def test_settings_defaults_and_environment_overrides(monkeypatch) -> None:
    """验证 PostgreSQL 默认配置及环境变量覆盖，并确认密码不出现在 repr。"""
    defaults = Settings(_env_file=None)
    assert defaults.postgres_db == "enterprise_agent"
    assert defaults.postgres_user == "enterprise_agent"
    assert defaults.postgres_password.get_secret_value() == "change_me"
    assert defaults.postgres_host == "localhost"
    assert defaults.postgres_port == 5432

    monkeypatch.setenv("POSTGRES_DB", "override_db")
    monkeypatch.setenv("POSTGRES_USER", "override_user")
    monkeypatch.setenv("POSTGRES_PASSWORD", "override_secret")
    monkeypatch.setenv("POSTGRES_HOST", "db.example")
    monkeypatch.setenv("POSTGRES_PORT", "5544")
    overridden = Settings(_env_file=None)
    assert overridden.postgres_db == "override_db"
    assert overridden.postgres_user == "override_user"
    assert overridden.postgres_password.get_secret_value() == "override_secret"
    assert overridden.postgres_host == "db.example"
    assert overridden.postgres_port == 5544
    assert "override_secret" not in repr(overridden)


def test_database_url_safely_encodes_credentials() -> None:
    """验证 URL builder 保留凭据值并安全编码特殊字符。"""
    settings = Settings(
        _env_file=None,
        postgres_user="user@name",
        postgres_password="p@ss:/?#secret",
    )

    url = database_url(settings)

    assert url.drivername == "postgresql+asyncpg"
    assert url.username == "user@name"
    assert url.password == "p@ss:/?#secret"
    assert url.render_as_string(hide_password=False).startswith(
        "postgresql+asyncpg://user%40name:p%40ss%3A%2F%3F%23secret@"
    )


def test_engine_and_factory_are_async_and_sessions_are_independent() -> None:
    """验证异步 Engine、Session 工厂类型和每次创建独立 Session。"""
    settings = Settings(_env_file=None)
    engine = create_db_engine(settings)
    factory = create_session_factory(engine)

    assert isinstance(engine, AsyncEngine)
    assert isinstance(factory, async_sessionmaker)
    assert factory() is not factory()
    assert set(Base.metadata.tables) == {
        "approval_application",
        "approval_record",
        "master_data_change",
        "master_data_entities",
        "users",
        "user_roles",
        "auth_sessions",
        "workflow_definitions",
    }
