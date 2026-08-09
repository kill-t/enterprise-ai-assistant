import asyncio

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr

import app.main as main_module
from app.core.config import AuthSettings
from app.infrastructure.task_queue.asyncio_runner import run_async
from app.local_server import get_event_loop_factory
from app.main import create_app


def test_health_returns_unified_success_response() -> None:
    """健康检查不依赖数据库，也必须返回统一成功响应。"""

    async def scenario():
        """通过 ASGI 传输直接调用健康检查接口。"""
        async with AsyncClient(
            transport=ASGITransport(app=create_app()),
            base_url="http://testserver",
        ) as client:
            return await client.get("/health")

    response = asyncio.run(scenario())

    assert response.status_code == 200
    assert response.json() == {
        "code": "SUCCESS",
        "message": "success",
        "data": {"status": "ok"},
    }


@pytest.mark.parametrize(
    ("secret_key", "expected_message"),
    [
        (None, "AUTH__SECRET_KEY"),
        ("too-short", "至少 32 字节"),
    ],
)
def test_auth_runtime_validation_rejects_missing_or_short_secret(
    secret_key: str | None,
    expected_message: str,
) -> None:
    """JWT 密钥配置错误必须在服务启动前被清晰拒绝。"""
    auth_settings = AuthSettings(
        secret_key=SecretStr(secret_key) if secret_key is not None else None,
    )

    with pytest.raises(RuntimeError, match=expected_message):
        auth_settings.validate_runtime()


def test_auth_runtime_validation_accepts_secure_secret() -> None:
    """长度足够的本地 JWT 密钥可以通过运行时校验。"""
    auth_settings = AuthSettings(
        secret_key=SecretStr("test-secret-key-at-least-32-bytes-long"),
    )

    auth_settings.validate_runtime()


def test_lifespan_validates_jwt_secret_before_accepting_requests(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """缺失密钥时，FastAPI 生命周期应在任何 HTTP 请求前终止启动。"""
    monkeypatch.setattr(main_module.settings, "auth", AuthSettings())

    async def scenario() -> None:
        """进入生命周期上下文时断言运行时配置校验抛出明确错误。"""
        with pytest.raises(RuntimeError, match="AUTH__SECRET_KEY"):
            async with main_module.lifespan(FastAPI()):
                raise AssertionError("配置错误时不应进入应用运行阶段")

    asyncio.run(scenario())


def test_windows_local_server_uses_selector_event_loop() -> None:
    """Windows 本地启动必须选择 psycopg 兼容的 Selector 事件循环。"""
    assert get_event_loop_factory("win32") is asyncio.SelectorEventLoop


def test_non_windows_local_server_uses_default_event_loop() -> None:
    """非 Windows 平台保持 Python 默认事件循环，避免额外平台分支。"""
    assert get_event_loop_factory("linux") is None


def test_windows_celery_task_uses_selector_event_loop() -> None:
    """Celery 同步任务桥接异步代码时也必须避开 Windows Proactor 循环。"""

    async def is_selector_loop() -> bool:
        """在协程内部读取真正运行该任务的事件循环类型。"""
        return isinstance(asyncio.get_running_loop(), asyncio.SelectorEventLoop)

    assert run_async(is_selector_loop(), platform_name="win32") is True
