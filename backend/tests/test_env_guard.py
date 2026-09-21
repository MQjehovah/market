import logging

import pytest

from app.core.env_guard import WEAK_VALUES, is_production, require_secret
from app.seed import _resolve_seed_password


def test_production_拒绝弱值(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    with pytest.raises(RuntimeError, match="JWT_SECRET"):
        require_secret("JWT_SECRET", "change-me")


def test_production_拒绝带空白的弱值(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    with pytest.raises(RuntimeError, match="JWT_SECRET"):
        require_secret("JWT_SECRET", " change-me ")


def test_production_拒绝空值(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    with pytest.raises(RuntimeError, match="SEED_ADMIN_PASSWORD"):
        require_secret("SEED_ADMIN_PASSWORD", "")


def test_development_放行但告警(monkeypatch, caplog):
    monkeypatch.setenv("APP_ENV", "development")
    with caplog.at_level(logging.WARNING):
        assert require_secret("JWT_SECRET", "change-me") == "change-me"
    assert any(
        record.levelname == "WARNING" and "JWT_SECRET" in record.getMessage()
        for record in caplog.records
    )


def test_正常值直接返回(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    assert require_secret("JWT_SECRET", "a-very-long-random-secret") == "a-very-long-random-secret"


def test_is_production_识别(monkeypatch):
    for v in ("production", "PROD", "Prod"):
        monkeypatch.setenv("APP_ENV", v)
        assert is_production() is True
    monkeypatch.setenv("APP_ENV", " production ")
    assert is_production() is True
    monkeypatch.setenv("APP_ENV", " development ")
    assert is_production() is False
    monkeypatch.setenv("APP_ENV", "development")
    assert is_production() is False


def test_弱值清单包含已知默认值():
    for weak in ("change-me-in-production", "xzyz2022!", "123456", "default-key"):
        assert weak in WEAK_VALUES


def test_开发态种子口令弱值随机生成并告警(monkeypatch, caplog):
    monkeypatch.setenv("APP_ENV", "development")
    with caplog.at_level(logging.WARNING):
        pw = _resolve_seed_password("change-me", "SEED_PUBLISHER_PASSWORD", "发布者")
    assert pw and pw != "change-me"
    assert any(
        record.levelname == "WARNING" and "SEED_PUBLISHER_PASSWORD" in record.getMessage()
        for record in caplog.records
    )


def test_生产态种子口令弱值拒绝并点名变量(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    with pytest.raises(RuntimeError, match="SEED_USER_PASSWORD"):
        _resolve_seed_password("change-me", "SEED_USER_PASSWORD", "普通用户")


def test_生产态种子口令空值拒绝并点名变量(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    with pytest.raises(RuntimeError, match="SEED_PUBLISHER_PASSWORD"):
        _resolve_seed_password("", "SEED_PUBLISHER_PASSWORD", "发布者")


def test_显式强口令直接沿用(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    strong = "a-strong-publisher-password-32b!"
    assert _resolve_seed_password(strong, "SEED_PUBLISHER_PASSWORD", "发布者") == strong
