# -*- coding: utf-8 -*-
"""身份三件套按浏览器分家：路径派生、旧布局迁移、WebUI 归一与登录态显示。

一份身份 = {browser}-profile + {browser}-cookies.json + {browser}-device-info.json，
三者必须同源；跨浏览器混用会把一个浏览器的会话配上另一个的设备指纹。
"""
import json
import os
from dataclasses import asdict

import pytest

from xdl.application.diagnostics import login_cache_status
from xdl.config import paths, platform
from xdl.settings import Settings


@pytest.fixture
def home(tmp_path, monkeypatch):
    """隔离的 ~/.xdl，并把浏览器探测固定为两个都装了。"""
    target = tmp_path / "xdl-home"
    target.mkdir()
    monkeypatch.setenv("XDL_HOME", str(target))
    monkeypatch.setattr(platform, "find_chrome", lambda: "/chrome")
    monkeypatch.setattr(platform, "find_edge", lambda: "/edge")
    return target


def _write_login_cookies(path) -> None:
    """写一份含登录 token 的 Cookie 缓存。"""
    os.makedirs(os.path.dirname(str(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as stream:
        json.dump([{"name": "1&_token", "value": "abc",
                    "domain": ".ximalaya.com", "path": "/"}], stream)


# ---- Settings 路径派生 ----

@pytest.mark.parametrize("browser,expected", [
    ("auto", "chrome"), ("chrome", "chrome"), ("edge", "edge"),
])
def test_settings_derives_full_identity_per_browser(home, browser, expected):
    settings = Settings(browser=browser)
    assert settings.resolved_browser == expected
    assert settings.chrome_profile_dir == str(home / f"{expected}-profile")
    assert settings.cookies_cache_path == str(home / f"{expected}-cookies.json")
    assert settings.device_info_path == str(home / f"{expected}-device-info.json")


def test_settings_keeps_custom_identity_paths(home):
    settings = Settings(browser="edge",
                        cookies_cache_path="/custom/c.json",
                        device_info_path="/custom/d.json")
    assert settings.cookies_cache_path == "/custom/c.json"
    assert settings.device_info_path == "/custom/d.json"


def test_explicit_chrome_path_drives_identity_layout(home):
    """显式指向 Edge 可执行文件时，三件套也要落到 edge 布局。"""
    settings = Settings(chrome_path=r"C:\Edge\msedge.exe")
    assert settings.resolved_browser == "edge"
    assert settings.cookies_cache_path.endswith("edge-cookies.json")
    assert settings.device_info_path.endswith("edge-device-info.json")


# ---- 旧布局迁移 ----

def test_migrate_renames_legacy_caches(home):
    _write_login_cookies(home / "cookies.json")
    (home / "device-info.json").write_text("{}", encoding="utf-8")

    moved = paths.migrate_legacy_layout()

    assert len(moved) == 2
    assert not (home / "cookies.json").exists()
    assert (home / "chrome-cookies.json").exists()
    assert (home / "chrome-device-info.json").read_text(encoding="utf-8") == "{}"


def test_migrate_keeps_old_users_logged_in(home):
    """老用户升级后默认路径直接命中迁移结果，无需重新登录。"""
    _write_login_cookies(home / "cookies.json")
    paths.migrate_legacy_layout()

    assert login_cache_status(Settings())["authenticated"] is True


def test_migrate_is_idempotent_and_never_overwrites(home):
    _write_login_cookies(home / "cookies.json")
    (home / "chrome-cookies.json").write_text("[]", encoding="utf-8")

    assert paths.migrate_legacy_layout() == []
    # 目标已存在：新文件不被旧文件覆盖，旧文件原地保留
    assert (home / "chrome-cookies.json").read_text(encoding="utf-8") == "[]"
    assert (home / "cookies.json").exists()


def test_migrate_noop_on_clean_home(home):
    assert paths.migrate_legacy_layout() == []


# ---- is_derived_path ----

def test_is_derived_path_covers_both_browsers_and_legacy(home):
    for name in ("chrome-cookies.json", "edge-cookies.json", "cookies.json"):
        assert paths.is_derived_path("cookies_cache_path", str(home / name))
    assert not paths.is_derived_path("cookies_cache_path", "/custom/c.json")
    assert not paths.is_derived_path("cookies_cache_path", "")


# ---- WebUI 归一与切换 ----

def test_load_web_settings_unpins_legacy_paths(home):
    """老用户 webui-settings.json 里钉死的旧路径要归一到新布局。"""
    from xdl.frontends.web_config import load_web_settings

    target = home / "webui-settings.json"
    target.write_text(json.dumps({
        "browser": "auto",
        "cookies_cache_path": str(home / "cookies.json"),
        "device_info_path": str(home / "device-info.json"),
        "chrome_profile_dir": str(home / "chrome-profile"),
    }), encoding="utf-8")

    settings = load_web_settings(str(target))

    assert settings.cookies_cache_path == str(home / "chrome-cookies.json")
    assert settings.device_info_path == str(home / "chrome-device-info.json")
    assert settings.chrome_profile_dir == str(home / "chrome-profile")


def test_load_web_settings_keeps_custom_paths(home):
    from xdl.frontends.web_config import load_web_settings

    target = home / "webui-settings.json"
    target.write_text(json.dumps({
        "cookies_cache_path": "/custom/c.json",
    }), encoding="utf-8")

    assert load_web_settings(str(target)).cookies_cache_path == "/custom/c.json"


def test_rederive_blanks_identity_paths_on_switch(home):
    from xdl.frontends.web_runtime import _rederive_browser_paths

    old = Settings()
    values = asdict(old)
    values["browser"] = "edge"
    _rederive_browser_paths(values, old)

    rebuilt = Settings(**values)
    assert rebuilt.cookies_cache_path == str(home / "edge-cookies.json")
    assert rebuilt.device_info_path == str(home / "edge-device-info.json")


def test_rederive_keeps_custom_identity_paths_on_switch(home):
    from xdl.frontends.web_runtime import _rederive_browser_paths

    old = Settings(cookies_cache_path="/custom/c.json")
    values = asdict(old)
    values["browser"] = "edge"
    _rederive_browser_paths(values, old)

    assert values["cookies_cache_path"] == "/custom/c.json"


def test_switching_back_and_forth_preserves_each_identity(home):
    """chrome → edge → chrome：Chrome 的凭据全程不被 Edge 覆盖。"""
    from xdl.frontends.web_runtime import _rederive_browser_paths

    chrome = Settings(browser="chrome")
    _write_login_cookies(chrome.cookies_cache_path)

    values = asdict(chrome)
    values["browser"] = "edge"
    _rederive_browser_paths(values, chrome)
    edge = Settings(**values)
    _write_login_cookies(edge.cookies_cache_path)

    values = asdict(edge)
    values["browser"] = "chrome"
    _rederive_browser_paths(values, edge)
    back = Settings(**values)

    assert back.cookies_cache_path == chrome.cookies_cache_path
    assert login_cache_status(back)["authenticated"] is True
    assert login_cache_status(edge)["authenticated"] is True


# ---- 登录态显示 ----

def test_login_status_reports_current_browser(home):
    _write_login_cookies(home / "chrome-cookies.json")

    status = login_cache_status(Settings(browser="chrome"))

    assert status["browser"] == "chrome"
    assert status["browser_name"] == "Chrome"
    assert status["authenticated"] is True
    assert status["other_browser_authenticated"] is None


def test_login_status_points_at_other_browser(home):
    """切到 Edge 后：当前未登录，但要告诉用户 Chrome 里还在。"""
    _write_login_cookies(home / "chrome-cookies.json")

    status = login_cache_status(Settings(browser="edge"))

    assert status["browser_name"] == "Edge"
    assert status["authenticated"] is False
    assert status["profile_exists"] is False
    assert status["other_browser_authenticated"] == "chrome"


def test_login_status_ignores_other_browser_for_custom_path(home):
    """自定义 Cookie 路径不按浏览器分家，跨浏览器提示无意义。"""
    _write_login_cookies(home / "chrome-cookies.json")

    status = login_cache_status(
        Settings(browser="edge", cookies_cache_path=str(home / "mine.json")))

    assert status["other_browser_authenticated"] is None
