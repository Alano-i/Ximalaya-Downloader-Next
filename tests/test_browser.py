# -*- coding: utf-8 -*-
"""Edge 浏览器支持：探测、Settings 派生、channel 选择、UA 与 WebUI 重派生。"""
import asyncio
import os
import sys
from dataclasses import asdict
from types import SimpleNamespace

import pytest

from xdl.config import platform
from xdl.errors import AuthError, ConfigError
from xdl.settings import Settings


def run(coro):
    return asyncio.run(coro)


# ---- platform.find_browser ----

def _mock_found(monkeypatch, chrome=None, edge=None):
    monkeypatch.setattr(platform, "find_chrome", lambda: chrome)
    monkeypatch.setattr(platform, "find_edge", lambda: edge)


def test_find_browser_auto_prefers_chrome(monkeypatch):
    _mock_found(monkeypatch, chrome="/chrome", edge="/edge")
    assert platform.find_browser("auto") == ("chrome", "/chrome")


def test_find_browser_auto_falls_back_to_edge(monkeypatch):
    _mock_found(monkeypatch, chrome=None, edge="/edge")
    assert platform.find_browser("auto") == ("edge", "/edge")


def test_find_browser_auto_nothing_found(monkeypatch):
    _mock_found(monkeypatch)
    assert platform.find_browser("auto") == ("chrome", None)


def test_find_browser_explicit_only_probes_requested(monkeypatch):
    _mock_found(monkeypatch, chrome="/chrome", edge="/edge")
    assert platform.find_browser("edge") == ("edge", "/edge")
    assert platform.find_browser("chrome") == ("chrome", "/chrome")


def test_find_browser_explicit_missing_keeps_name(monkeypatch):
    _mock_found(monkeypatch, chrome="/chrome", edge=None)
    assert platform.find_browser("edge") == ("edge", None)


# ---- Windows 注册表探测 ----

class _FakeRegKey:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


def _fake_winreg(hklm_path=None, hkcu_path=None):
    current = {}

    class FakeWinreg:
        HKEY_LOCAL_MACHINE = "HKLM"
        HKEY_CURRENT_USER = "HKCU"

        @staticmethod
        def OpenKey(root, _name):
            path = hklm_path if root == "HKLM" else hkcu_path
            if path is None:
                raise OSError("not found")
            current["path"] = path
            return _FakeRegKey()

        @staticmethod
        def QueryValueEx(_key, _value_name):
            return (current["path"], 1)

    return FakeWinreg


def test_find_edge_windows_prefers_registry(monkeypatch, tmp_path):
    fake = tmp_path / "msedge.exe"
    fake.write_text("", encoding="utf-8")
    monkeypatch.setitem(sys.modules, "winreg",
                        _fake_winreg(hklm_path=str(fake)))
    monkeypatch.setattr(os.path, "exists", lambda p: p == str(fake))
    assert platform._find_edge_windows() == str(fake)


def test_find_edge_windows_falls_back_to_hkcu(monkeypatch, tmp_path):
    fake = tmp_path / "msedge.exe"
    fake.write_text("", encoding="utf-8")
    monkeypatch.setitem(sys.modules, "winreg",
                        _fake_winreg(hkcu_path=str(fake)))
    monkeypatch.setattr(os.path, "exists", lambda p: p == str(fake))
    assert platform._find_edge_windows() == str(fake)


def test_find_edge_windows_falls_back_to_fixed_paths(monkeypatch):
    class FailWinreg:
        HKEY_LOCAL_MACHINE = "HKLM"
        HKEY_CURRENT_USER = "HKCU"

        @staticmethod
        def OpenKey(_root, _name):
            raise OSError("not found")

        @staticmethod
        def QueryValueEx(_key, _name):
            raise OSError("not found")

    monkeypatch.setitem(sys.modules, "winreg", FailWinreg)
    monkeypatch.setattr(os.path, "exists", lambda _p: False)
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    assert platform._find_edge_windows() is None


def test_find_edge_mac_layout(monkeypatch):
    monkeypatch.setattr(platform.sys, "platform", "darwin")
    mac_path = "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"
    monkeypatch.setattr(os.path, "exists", lambda p: p == mac_path)
    assert platform.find_edge() == mac_path


# ---- infer / known path ----

def test_infer_browser_from_path():
    assert platform.infer_browser_from_path(
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe") == "edge"
    assert platform.infer_browser_from_path(
        "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge") == "edge"
    assert platform.infer_browser_from_path(
        r"C:\Program Files\Google\Chrome\Application\chrome.exe") == "chrome"
    assert platform.infer_browser_from_path("/usr/bin/chromium") == "chrome"
    assert platform.infer_browser_from_path("/opt/weird/browser") is None
    assert platform.infer_browser_from_path("") is None


def test_is_known_browser_path(monkeypatch):
    _mock_found(monkeypatch)
    assert platform.is_known_browser_path(
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")
    assert platform.is_known_browser_path(
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
    assert not platform.is_known_browser_path(r"D:\MyTools\browser.exe")
    assert not platform.is_known_browser_path("")


# ---- Settings 派生 ----

def test_settings_rejects_unknown_browser():
    with pytest.raises(ConfigError, match="未知浏览器"):
        Settings(browser="firefox")


def test_settings_auto_default_keeps_chrome_layout(monkeypatch):
    """auto + 本机有 Chrome：与旧行为完全一致（chrome-profile 目录）。"""
    _mock_found(monkeypatch, chrome="/chrome", edge="/edge")
    monkeypatch.setenv("XDL_HOME", "/tmp/xdl-test")
    settings = Settings()
    assert settings.browser == "auto"
    assert settings.resolved_browser == "chrome"
    assert settings.chrome_path == "/chrome"
    assert settings.chrome_profile_dir == os.path.join(
        "/tmp/xdl-test", "chrome-profile")


def test_settings_edge_derives_edge_profile(monkeypatch):
    _mock_found(monkeypatch, chrome="/chrome", edge="/edge")
    monkeypatch.setenv("XDL_HOME", "/tmp/xdl-test")
    settings = Settings(browser="edge")
    assert settings.resolved_browser == "edge"
    assert settings.chrome_path == "/edge"
    assert settings.chrome_profile_dir == os.path.join(
        "/tmp/xdl-test", "edge-profile")


def test_settings_auto_edge_only_machine(monkeypatch):
    _mock_found(monkeypatch, chrome=None, edge="/edge")
    monkeypatch.setenv("XDL_HOME", "/tmp/xdl-test")
    settings = Settings()
    assert settings.resolved_browser == "edge"
    assert settings.chrome_profile_dir.endswith("edge-profile")


def test_settings_explicit_path_infers_browser(monkeypatch):
    _mock_found(monkeypatch, chrome="/chrome", edge="/edge")
    monkeypatch.setenv("XDL_HOME", "/tmp/xdl-test")
    settings = Settings(chrome_path=r"C:\Edge\msedge.exe")
    assert settings.resolved_browser == "edge"
    assert settings.chrome_path == r"C:\Edge\msedge.exe"
    assert settings.chrome_profile_dir.endswith("edge-profile")


def test_settings_explicit_unknown_path_defaults_chrome(monkeypatch):
    _mock_found(monkeypatch, chrome="/chrome", edge="/edge")
    settings = Settings(chrome_path="/opt/weird/browser")
    assert settings.resolved_browser == "chrome"
    assert settings.chrome_path == "/opt/weird/browser"


def test_settings_explicit_profile_dir_untouched(monkeypatch):
    _mock_found(monkeypatch, chrome="/chrome", edge="/edge")
    settings = Settings(browser="edge", chrome_profile_dir="/custom/profile")
    assert settings.chrome_profile_dir == "/custom/profile"


def test_resolved_browser_not_persisted(monkeypatch):
    _mock_found(monkeypatch, chrome="/chrome", edge=None)
    assert "resolved_browser" not in asdict(Settings())


# ---- Playwright channel 选择 ----

def test_extractor_launch_kwargs_channel():
    from xdl.adapters.sign.extractor import _launch_kwargs

    assert _launch_kwargs("p", "", True)["channel"] == "chrome"
    assert _launch_kwargs("p", "", True, browser="edge")["channel"] == "msedge"
    explicit = _launch_kwargs("p", "/custom/browser", True, browser="edge")
    assert explicit.get("executable_path") == "/custom/browser"
    assert "channel" not in explicit


def test_extract_cookies_uses_edge_channel(tmp_path, monkeypatch):
    from playwright import sync_api
    from xdl.adapters.sign.cookies import extract_cookies_from_profile

    profile = tmp_path / "profile"
    profile.mkdir()

    class _Context:
        def new_page(self):
            raise AssertionError("默认导出不应创建导航页面")

        def cookies(self):
            return [{"name": "1&_token", "value": "persisted",
                     "domain": ".ximalaya.com", "path": "/"}]

        def close(self):
            pass

    launch_kwargs = {}

    class _Chromium:
        def launch_persistent_context(self, **kwargs):
            launch_kwargs.update(kwargs)
            return _Context()

    class _Playwright:
        chromium = _Chromium()

    class _Manager:
        def __enter__(self):
            return _Playwright()

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr(sync_api, "sync_playwright", lambda: _Manager())

    cookies = extract_cookies_from_profile(str(profile), browser="edge")

    assert [c["name"] for c in cookies] == ["1&_token"]
    assert launch_kwargs["channel"] == "msedge"


# ---- ChromeSource：浏览器名与 UA ----

class _CdpSession:
    def __init__(self, sink):
        self._sink = sink

    async def send(self, method, params):
        self._sink[method] = params


class _UaProbePage:
    def __init__(self, ua):
        self._ua = ua
        self.overrides = {}

        class _Ctx:
            async def new_cdp_session(_self, _page):
                return _CdpSession(self.overrides)

        self.context = _Ctx()

    async def evaluate(self, _script, *args):
        return self._ua


def _make_source(browser="chrome", chrome_path="browser-bin"):
    from xdl.adapters.source_chrome import ChromeSource

    class _Decoder:
        def decode(self, url):
            return url

    return ChromeSource(_Decoder(), chrome_path, "profile", browser=browser)


def test_ua_override_edge_brand():
    from xdl.adapters.source_chrome import _client_hints_platform

    source = _make_source(browser="edge")
    page = _UaProbePage(
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) HeadlessChrome/150.0.0.0 Safari/537.36 Edg/150.0.0.0")

    run(source._apply_ua_override(page))

    override = page.overrides.get("Network.setUserAgentOverride")
    assert override is not None
    metadata = override["userAgentMetadata"]
    assert {"brand": "Microsoft Edge", "version": "150"} in metadata["brands"]
    assert "HeadlessChrome" not in override["userAgent"]
    assert "Edg/150.0.0.0" in override["userAgent"]
    # platform 按本机系统生成，不再写死 Windows。
    os_name, os_version, arch = _client_hints_platform()
    assert metadata["platform"] == os_name
    assert metadata["platformVersion"] == os_version
    assert metadata["architecture"] == arch


def test_ua_override_chrome_brand_kept():
    source = _make_source(browser="chrome")
    page = _UaProbePage(
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) HeadlessChrome/150.0.0.0 Safari/537.36")

    run(source._apply_ua_override(page))

    metadata = page.overrides["Network.setUserAgentOverride"]["userAgentMetadata"]
    assert {"brand": "Google Chrome", "version": "150"} in metadata["brands"]


def test_source_infers_edge_from_path():
    source = _make_source(browser="chrome",
                          chrome_path=r"C:\Edge\msedge.exe")
    assert source._browser == "edge"
    assert source._browser_name == "Edge"


def test_require_chrome_message_names_browser():
    source = _make_source(browser="edge", chrome_path="")
    with pytest.raises(ConfigError, match="未找到 Edge"):
        source._require_chrome()


def test_client_hints_platform_matches_os():
    from xdl.adapters.source_chrome import _client_hints_platform

    os_name, os_version, arch = _client_hints_platform()
    if sys.platform == "darwin":
        assert os_name == "macOS"
    elif sys.platform.startswith("win"):
        assert (os_name, os_version) == ("Windows", "15.0.0")
    else:
        assert os_name == "Linux"
    assert arch in ("x86", "arm")


# ---- WebUI：切换浏览器时的路径重派生 ----

def _old_settings(monkeypatch, chrome_path=r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                  profile_dir=None, browser="chrome"):
    _mock_found(monkeypatch, chrome="/chrome", edge="/edge")
    monkeypatch.setenv("XDL_HOME", "/tmp/xdl-test")
    kwargs = {"browser": browser, "chrome_path": chrome_path}
    if profile_dir is not None:
        kwargs["chrome_profile_dir"] = profile_dir
    return Settings(**kwargs)


def test_rederive_noop_when_browser_unchanged(monkeypatch):
    from xdl.frontends.web_runtime import _rederive_browser_paths

    old = _old_settings(monkeypatch)
    values = asdict(old)
    _rederive_browser_paths(values, old)
    assert values["chrome_path"] == old.chrome_path
    assert values["chrome_profile_dir"] == old.chrome_profile_dir


def test_rederive_blanks_auto_paths_on_switch(monkeypatch):
    from xdl.frontends.web_runtime import _rederive_browser_paths

    old = _old_settings(monkeypatch)
    values = asdict(old)
    values["browser"] = "edge"   # 表单全量提交：路径字段与旧值相同
    _rederive_browser_paths(values, old)
    assert values["chrome_path"] == ""
    assert values["chrome_profile_dir"] == ""
    # 置空后由 Settings.__post_init__ 按 edge 重新派生
    rebuilt = Settings(**values)
    assert rebuilt.resolved_browser == "edge"
    assert rebuilt.chrome_path == "/edge"
    assert rebuilt.chrome_profile_dir.endswith("edge-profile")


def test_rederive_keeps_custom_paths_on_switch(monkeypatch):
    from xdl.frontends.web_runtime import _rederive_browser_paths

    old = _old_settings(monkeypatch, chrome_path=r"D:\MyTools\browser.exe",
                        profile_dir="/custom/profile")
    values = asdict(old)
    values["browser"] = "edge"
    _rederive_browser_paths(values, old)
    assert values["chrome_path"] == r"D:\MyTools\browser.exe"
    assert values["chrome_profile_dir"] == "/custom/profile"


def test_rederive_keeps_freshly_edited_path(monkeypatch):
    """同一次保存中用户手改了路径（提交值≠旧值）：即使是自动值也不动。"""
    from xdl.frontends.web_runtime import _rederive_browser_paths

    old = _old_settings(monkeypatch)
    values = asdict(old)
    values["browser"] = "edge"
    values["chrome_path"] = r"D:\Edge\custom-msedge.exe"
    _rederive_browser_paths(values, old)
    assert values["chrome_path"] == r"D:\Edge\custom-msedge.exe"


def test_update_settings_switch_browser_end_to_end(tmp_path, monkeypatch):
    from xdl.frontends.web_runtime import WebRuntime

    _mock_found(monkeypatch, chrome="/chrome", edge="/edge")
    monkeypatch.setenv("XDL_HOME", str(tmp_path / "xdl-home"))
    settings = Settings(
        task_db_path=str(tmp_path / "tasks.db"),
        risk_log_path=str(tmp_path / "risk.jsonl"),
        cookies_cache_path=str(tmp_path / "cookies.json"),
        device_info_path=str(tmp_path / "device.json"),
    )

    class _FakeFacade:
        def close(self):
            pass

    runtime = WebRuntime(settings, facade=_FakeFacade(),
                         facade_factory=lambda _s: _FakeFacade(),
                         persist_settings=False)
    updated = runtime.update_settings({"browser": "edge"})

    assert updated["browser"] == "edge"
    assert updated["chrome_path"] == "/edge"
    assert updated["chrome_profile_dir"].endswith("edge-profile")


# ---- diagnostics 透传 ----

def test_diagnostics_passes_browser_to_extract(monkeypatch):
    import xdl.application.diagnostics as diagnostics
    import xdl.adapters.sign as sign_tools

    _mock_found(monkeypatch, chrome="/chrome", edge="/edge")
    monkeypatch.setenv("XDL_HOME", "/tmp/xdl-test")
    settings = Settings(browser="edge")
    captured = {}

    def fake_extract(**kwargs):
        captured.update(kwargs)
        return [{"name": "1&_token", "value": "t"}]

    monkeypatch.setattr(sign_tools, "extract_cookies_from_profile", fake_extract)
    saved = {}
    monkeypatch.setattr(sign_tools, "save_cookies",
                        lambda cookies, path: saved.setdefault("path", path))

    result = diagnostics.refresh_login_cookies(settings)

    assert captured["browser"] == "edge"
    assert result["authenticated"] is True


def test_diagnostics_refresh_cookies_error_names_browser(monkeypatch):
    import xdl.application.diagnostics as diagnostics
    import xdl.adapters.sign as sign_tools

    _mock_found(monkeypatch, chrome="/chrome", edge="/edge")
    monkeypatch.setenv("XDL_HOME", "/tmp/xdl-test")
    settings = Settings(browser="edge")
    monkeypatch.setattr(sign_tools, "extract_cookies_from_profile",
                        lambda **_kw: [])

    with pytest.raises(AuthError, match="专用 Edge Profile"):
        diagnostics.refresh_login_cookies(settings)


# ---- CLI --browser ----

def test_cli_parser_accepts_browser():
    import xdl.frontends.cli as cli

    args = cli.build_parser().parse_args(["--browser", "edge", "login"])
    assert args.browser == "edge"
    assert cli.build_parser().parse_args(["login"]).browser is None


def test_cli_main_constructs_settings_with_browser(monkeypatch):
    import xdl.frontends.cli as cli

    captured = {}

    def fake_settings(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace()

    monkeypatch.setattr(cli, "Settings", fake_settings)
    monkeypatch.setattr(cli, "generate_signatures",
                        lambda device_info, repeat: {"repeat": repeat,
                                                     "values": ["x"]})

    code = cli.main(["--browser", "edge", "gen-sign"])

    assert code == 0
    assert captured["browser"] == "edge"


def test_cli_extract_device_constructs_settings_with_browser(monkeypatch):
    import xdl.frontends.cli as cli

    captured = {}

    def fake_settings(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace()

    monkeypatch.setattr(cli, "Settings", fake_settings)
    monkeypatch.setattr(cli, "extract_device_identity",
                        lambda settings, **kw: {
                            "output_path": "out.json", "field_count": 1,
                            "identity": "id", "summary": "ok",
                        })

    args = SimpleNamespace(output=None, profile=None, no_headless=False,
                           refresh=False, fresh_profile=False, browser="edge")
    code = cli._cmd_extract_device(None, args)

    assert code == 0
    assert captured["browser"] == "edge"


def test_login_hint_only_when_both_browsers(monkeypatch, capsys):
    import xdl.frontends.cli as cli

    _mock_found(monkeypatch, chrome="/chrome", edge="/edge")
    cli._maybe_print_browser_hint(SimpleNamespace(browser=None))
    assert "--browser edge" in capsys.readouterr().out

    _mock_found(monkeypatch, chrome="/chrome", edge=None)
    cli._maybe_print_browser_hint(SimpleNamespace(browser=None))
    assert capsys.readouterr().out == ""

    _mock_found(monkeypatch, chrome="/chrome", edge="/edge")
    cli._maybe_print_browser_hint(SimpleNamespace(browser="edge"))
    assert capsys.readouterr().out == ""
