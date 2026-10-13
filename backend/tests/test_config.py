from pydantic import ValidationError

from app.core.config import Settings, get_settings


def test_default_settings() -> None:
    settings = Settings(_env_file=None)

    assert settings.app_name == "enterprise-agent"
    assert settings.app_env == "development"
    assert settings.log_level == "INFO"
    assert settings.request_id_header == "X-Request-ID"


def test_environment_override(monkeypatch) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    get_settings.cache_clear()
    try:
        assert get_settings().app_env == "test"
    finally:
        get_settings.cache_clear()


def test_settings_load_project_root_env_file(tmp_path, monkeypatch) -> None:
    from app.core import config

    env_file = tmp_path / ".env"
    env_file.write_text("APP_NAME=from-root-env\n", encoding="utf-8")
    monkeypatch.setitem(config.Settings.model_config, "env_file", env_file)
    assert config.Settings().app_name == "from-root-env"


def test_auth_ttl_must_be_positive_and_demo_password_is_secret() -> None:
    """验证认证会话 TTL 必须为正数且 Demo 密码使用 SecretStr。"""
    import pytest
    from pydantic import SecretStr

    with pytest.raises(ValidationError):
        Settings(_env_file=None, auth_session_ttl_minutes=0)

    settings = Settings(_env_file=None, demo_user_password="local-only-password")
    assert isinstance(settings.demo_user_password, SecretStr)
    assert "local-only-password" not in repr(settings)
