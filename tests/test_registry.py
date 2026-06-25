from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from deepferry.config import AppConfig, SourceConfig
from deepferry.core.errors import ConfigError
from deepferry.core.models import HealthStatus, QueryRequest, StructuredResult
from deepferry.datasources.base import DataSource
from deepferry.datasources.registry import (
    SourceRegistry,
    _instantiate_source,
    register_source_type,
)


class FakeHTTPSource(DataSource):
    source_type = "http"

    def __init__(self, config: SourceConfig, token_manager: Any = None) -> None:
        super().__init__()
        self._config = config
        self.token_manager = token_manager

    async def connect(self) -> None: pass
    async def disconnect(self) -> None: pass
    async def execute(self, query: QueryRequest) -> StructuredResult: raise NotImplementedError
    async def list_resources(self) -> list[str]: return []
    async def schema_info(self, resource: str | None = None) -> Any: raise NotImplementedError
    async def health_check(self) -> HealthStatus:
        return HealthStatus(ok=True, latency_ms=1.0)


class FakeMySQLSource(DataSource):
    source_type = "mysql"

    def __init__(self, config: SourceConfig) -> None:
        super().__init__()
        self._config = config

    async def connect(self) -> None: pass
    async def disconnect(self) -> None: pass
    async def execute(self, query: QueryRequest) -> StructuredResult: raise NotImplementedError
    async def list_resources(self) -> list[str]: return []
    async def schema_info(self, resource: str | None = None) -> Any: raise NotImplementedError
    async def health_check(self) -> HealthStatus:
        return HealthStatus(ok=True, latency_ms=1.0)


def _http_config(sid: str = "api", auth: bool = False) -> SourceConfig:
    extra: dict[str, Any] = {}
    if auth:
        extra["auth"] = {
            "login_url": "https://api.example.com/login",
            "login_method": "POST",
            "login_body": {"username": "u", "password": "p"},
            "token_field": "access_token",
            "token_type": "bearer",
            "token_ttl": 3600,
        }
    return SourceConfig(
        id=sid, type="http", base_url="https://api.example.com", extra=extra,
    )


def _mysql_config(sid: str = "db") -> SourceConfig:
    return SourceConfig(
        id=sid, type="mysql",
        host="localhost", port=3306, database="d", user="u", password="p",
    )


class TestInstantiateSource:
    def setup_method(self) -> None:
        register_source_type("http", FakeHTTPSource)
        register_source_type("mysql", FakeMySQLSource)

    def test_http_injects_token_manager(self):
        cfg = _http_config()
        tm = MagicMock(name="token_manager")
        src = _instantiate_source(cfg, token_manager=tm)
        assert isinstance(src, FakeHTTPSource)
        assert src.token_manager is tm
        assert src.source_id == "api"

    def test_http_without_token_manager(self):
        cfg = _http_config()
        src = _instantiate_source(cfg)
        assert isinstance(src, FakeHTTPSource)
        assert src.token_manager is None

    def test_mysql_ignores_token_manager(self):
        cfg = _mysql_config()
        tm = MagicMock()
        src = _instantiate_source(cfg, token_manager=tm)
        assert isinstance(src, FakeMySQLSource)

    def test_unknown_type_raises(self):
        cfg = SourceConfig(id="x", type="nonexistent", base_url="https://x")
        with pytest.raises(ConfigError, match="registered for type"):
            _instantiate_source(cfg)


class TestEnsureTokenManager:
    async def test_no_auth_no_creation(self, monkeypatch, tmp_path):
        monkeypatch.setenv("DEEPFERRY_DB_PATH", str(tmp_path / "tokens.db"))
        reg = SourceRegistry()
        app_cfg = AppConfig(sources=[_mysql_config()])
        await reg._ensure_token_manager(app_cfg)
        assert reg._token_manager is None

    async def test_http_without_auth_no_creation(self, monkeypatch, tmp_path):
        monkeypatch.setenv("DEEPFERRY_DB_PATH", str(tmp_path / "tokens.db"))
        register_source_type("http", FakeHTTPSource)
        reg = SourceRegistry()
        app_cfg = AppConfig(sources=[_http_config(auth=False)])
        await reg._ensure_token_manager(app_cfg)
        assert reg._token_manager is None

    async def test_creates_for_http_auth(self, monkeypatch, tmp_path):
        monkeypatch.setenv("DEEPFERRY_DB_PATH", str(tmp_path / "tokens.db"))
        register_source_type("http", FakeHTTPSource)
        reg = SourceRegistry()
        app_cfg = AppConfig(sources=[_http_config(auth=True)])
        await reg._ensure_token_manager(app_cfg)
        assert reg._token_manager is not None
        assert reg._token_db is not None
        assert reg._token_http_client is not None
        await reg.shutdown()

    async def test_idempotent_across_calls(self, monkeypatch, tmp_path):
        monkeypatch.setenv("DEEPFERRY_DB_PATH", str(tmp_path / "tokens.db"))
        register_source_type("http", FakeHTTPSource)
        reg = SourceRegistry()
        app_cfg = AppConfig(sources=[_http_config(auth=True)])
        await reg._ensure_token_manager(app_cfg)
        first = reg._token_manager
        await reg._ensure_token_manager(app_cfg)
        assert reg._token_manager is first
        await reg.shutdown()


class TestShutdown:
    async def test_shutdown_releases_token_resources(self, monkeypatch, tmp_path):
        monkeypatch.setenv("DEEPFERRY_DB_PATH", str(tmp_path / "tokens.db"))
        register_source_type("http", FakeHTTPSource)
        reg = SourceRegistry()
        app_cfg = AppConfig(sources=[_http_config(auth=True)])
        await reg._ensure_token_manager(app_cfg)
        await reg.shutdown()
        assert reg._token_manager is None
        assert reg._token_db is None
        assert reg._token_http_client is None

    async def test_shutdown_without_token_manager_is_noop(self):
        reg = SourceRegistry()
        await reg.shutdown()
        assert reg._token_manager is None

    async def test_shutdown_disconnects_instances(self):
        register_source_type("mysql", FakeMySQLSource)
        reg = SourceRegistry()
        src = FakeMySQLSource(_mysql_config())
        src.source_id = "db"
        reg._instances["db"] = src
        await reg.shutdown()
        assert reg._instances == {}
