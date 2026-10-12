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
