"""Demo 用户 seed 安全边界测试。"""

import asyncio

import pytest
from pydantic import SecretStr

from app.core.config import Settings
from scripts.seed_demo_users import seed_demo_users


def test_seed_refuses_production_before_database_access() -> None:
    """验证 production 环境在访问数据库前拒绝 Demo seed。"""
    with pytest.raises(RuntimeError, match="disabled in production"):
        asyncio.run(
            seed_demo_users(
                Settings(
                    _env_file=None,
                    app_env="production",
                    demo_user_password=SecretStr("unused"),
                ),
                None,  # type: ignore[arg-type]
            )
        )


def test_seed_requires_configured_demo_password() -> None:
    """验证未配置 DEMO_USER_PASSWORD 时 seed 明确失败。"""
    with pytest.raises(ValueError, match="DEMO_USER_PASSWORD"):
        asyncio.run(
            seed_demo_users(Settings(_env_file=None, demo_user_password=None), None)  # type: ignore[arg-type]
        )
