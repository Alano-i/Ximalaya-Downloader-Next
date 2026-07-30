# -*- coding: utf-8 -*-
"""WebUI 运行设置的本地持久化。"""
from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, fields

from ..config import paths
from ..config.paths import xdl_home
from ..errors import ConfigError
from ..settings import Settings


_SETTING_NAMES = {field.name for field in fields(Settings)}


def default_settings_path() -> str:
    return os.path.join(xdl_home(), "webui-settings.json")


def _normalize_legacy_paths(values: dict) -> None:
    """把持久化的旧布局默认路径退回空值，交给 Settings 按新布局重新派生。

    `save_web_settings` 存的是 `asdict` 后的**解析结果**，所以老用户文件里
    `cookies_cache_path` 等字段是钉死的绝对路径（如 ~/.xdl/cookies.json）。
    只改默认值对他们不生效——必须在这里归一，否则新的按浏览器分家布局永远
    不会应用到存量 WebUI 用户。用户自定义的路径不匹配任何派生值，原样保留。
    """
    for field in paths.DERIVED_PATH_BUILDERS:
        if field in values and paths.is_derived_path(field, values[field]):
            values[field] = ""


def load_web_settings(path: str | None = None) -> Settings:
    target = path or default_settings_path()
    # 读设置前先把旧的浏览器无关缓存搬到 Chrome 布局，老用户升级后仍是已登录态。
    paths.migrate_legacy_layout()
    if not os.path.exists(target):
        return Settings()
    try:
        with open(target, "r", encoding="utf-8") as stream:
            raw = json.load(stream)
    except (OSError, ValueError) as exc:
        raise ConfigError(f"WebUI 设置不可读: {target}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError(f"WebUI 设置必须是 JSON 对象: {target}")
    values = {key: value for key, value in raw.items()
              if key in _SETTING_NAMES}
    _normalize_legacy_paths(values)
    try:
        return Settings(**values)
    except (TypeError, ValueError, ConfigError) as exc:
        raise ConfigError(f"WebUI 设置无效: {target}: {exc}") from exc


def save_web_settings(settings: Settings, path: str | None = None) -> str:
    target = path or default_settings_path()
    directory = os.path.dirname(os.path.abspath(target))
    os.makedirs(directory, exist_ok=True)
    fd, temp_path = tempfile.mkstemp(
        prefix=".webui-settings-", suffix=".tmp", dir=directory,
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(asdict(settings), stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_path, target)
    finally:
        if os.path.exists(temp_path):
            os.unlink(temp_path)
    return target


def settings_dict(settings: Settings) -> dict:
    return asdict(settings)
