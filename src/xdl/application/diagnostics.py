# -*- coding: utf-8 -*-
"""跨前端复用的本地诊断与凭据维护操作。"""
from __future__ import annotations

import os
import time

from ..adapters import PySignProvider
from ..adapters import sign as sign_tools
from ..config import paths, platform
from ..errors import AuthError
from ..settings import Settings


def generate_signatures(device_info_path: str | None = None,
                        repeat: int = 1) -> dict:
    """生成一组 xm-sign 冒烟值；与下载播放信息无关。"""
    if repeat < 1:
        raise ValueError("重复次数必须大于 0。")
    signer = PySignProvider(device_info_path=device_info_path)
    signer.open()
    try:
        values = [signer.sign() for _ in range(repeat)]
    finally:
        signer.close()
    return {"repeat": repeat, "values": values}


def extract_device_identity(settings: Settings, *, output: str | None = None,
                            profile: str | None = None, headless: bool = True,
                            refresh: bool = False,
                            fresh_profile: bool = False) -> dict:
    """采集设备信息并只返回不含 Cookie/原始指纹值的摘要。"""
    result = sign_tools.refresh_device_identity_via_browser(
        profile_dir=profile or settings.chrome_profile_dir,
        chrome_path=settings.chrome_path,
        headless=headless,
        clear_device_state=refresh,
        fresh_profile=fresh_profile,
        browser=_resolved_browser(settings),
    )
    target = output or settings.device_info_path
    sign_tools.save_device_info(result.device_info, target)
    return {
        "output_path": target,
        "field_count": len(result.device_info),
        "identity": sign_tools.identity_fingerprint(result.device_info),
        "summary": sign_tools.summarize_extract(result),
        "used_temp_profile": result.used_temp_profile,
    }


def _resolved_browser(settings: Settings) -> str:
    """Settings.__post_init__ 解析出的实际浏览器（chrome/edge）。"""
    return getattr(settings, "resolved_browser", None) or "chrome"


def refresh_login_cookies(settings: Settings, *, headless: bool = True) -> dict:
    """从专用 Profile 刷新登录 Cookie；匿名结果不会覆盖现有缓存。"""
    cookies = sign_tools.extract_cookies_from_profile(
        profile_dir=settings.chrome_profile_dir,
        chrome_path=settings.chrome_path,
        headless=headless,
        browser=_resolved_browser(settings),
    )
    if not sign_tools.is_login_cookie(cookies):
        name = platform.browser_display_name(_resolved_browser(settings))
        raise AuthError(
            f"专用 {name} Profile 中未发现登录 token（1&_token）；"
            "未覆盖现有 Cookie 缓存。"
        )
    sign_tools.save_cookies(cookies, settings.cookies_cache_path)
    return {
        "output_path": settings.cookies_cache_path,
        "cookie_count": len(cookies),
        "authenticated": True,
    }


def _has_login_cache(path: str) -> bool:
    """该 Cookie 缓存文件里是否存在登录 token（不暴露 Cookie 名或值）。"""
    cookies = sign_tools.load_cached_cookies(path, max_age_seconds=10**12) or []
    return sign_tools.is_login_cookie(cookies)


def _other_browser_with_login(settings: Settings, current: str) -> str | None:
    """另一个浏览器是否有可用登录态；有则返回其名字（chrome/edge）。

    切换浏览器后当前浏览器必然显示未登录，只报告这一点会让用户以为登录态丢了。
    有了这个字段，前端才能说清"Edge 尚未登录，Chrome 里还在，切回即可"。
    只在用户没有自定义 Cookie 路径时才有意义——自定义路径不按浏览器分家。
    """
    if not paths.is_derived_path("cookies_cache_path", settings.cookies_cache_path):
        return None
    for name in ("chrome", "edge"):
        if name == current:
            continue
        if _has_login_cache(paths.browser_cookies_path(name)):
            return name
    return None


def login_cache_status(settings: Settings) -> dict:
    """报告**当前浏览器**的本地登录状态，不暴露 Cookie 名或值。

    登录态的存放位置按后端分家：http/pc 登录后把 Cookie 拷进缓存文件；chrome
    后端从头到尾只依赖专用 Profile 的 Cookie DB（不写缓存文件）。所以
    ``authenticated`` 必须按后端分别检查，否则 chrome 后端会恒报未登录——
    前端据此藏起登出入口、把"换账号"走成普通登录，旧账号被静默续存。
    """
    path = settings.cookies_cache_path
    exists = os.path.isfile(path)
    age_seconds = None
    if exists:
        try:
            modified = int(os.path.getmtime(path))
            age_seconds = max(0, int(time.time()) - modified)
        except OSError:
            age_seconds = None
    current = _resolved_browser(settings)
    if settings.source_backend == "chrome":
        # 延迟导入：diagnostics 是应用层模块，不能在导入期就拉上浏览器适配器。
        from ..adapters.source_chrome import _has_persisted_login_cookie
        authenticated = _has_persisted_login_cookie(settings.chrome_profile_dir)
    else:
        authenticated = _has_login_cache(path)
    return {
        "browser": current,
        "browser_name": platform.browser_display_name(current),
        "authenticated": authenticated,
        "cache_exists": exists,
        "cache_age_seconds": age_seconds,
        "profile_exists": os.path.isdir(settings.chrome_profile_dir),
        "other_browser_authenticated": _other_browser_with_login(settings, current),
    }
