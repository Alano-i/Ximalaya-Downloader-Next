# -*- coding: utf-8 -*-
"""XDL 用户数据目录与按浏览器分家的身份文件布局。

放在配置层的叶子模块中，避免 `settings` 与平台配置互相导入。

一份「身份」由三件套组成，必须同源于同一个浏览器 Profile：

    ~/.xdl/{browser}-profile/            浏览器专用用户目录（登录态）
    ~/.xdl/{browser}-cookies.json        从该 Profile 导出的登录 + 设备 Cookie
    ~/.xdl/{browser}-device-info.json    从该 Profile 采集的 du_web_sdk 指纹

Cookie 导出里同时含登录 token 与 `_xmLog`/`wfp`/`crystal` 等设备指纹 Cookie
（见 adapters/sign/cookies.py），device_info 的 `ew1` 又编码了完整 UA，
三者跨浏览器混用会把「Edge 的会话」配上「Chrome 的指纹」发出去。因此它们
统一按浏览器分文件，切换浏览器等于换一套完整身份，互不覆盖。
"""
from __future__ import annotations

import os

# 旧布局（浏览器只有 Chrome 时）→ 新布局的迁移映射。
_LEGACY_RENAMES = (
    ("cookies.json", "chrome-cookies.json"),
    ("device-info.json", "chrome-device-info.json"),
)


def xdl_home() -> str:
    """返回用户数据目录；可用 ``XDL_HOME`` 覆盖。"""
    return os.environ.get("XDL_HOME") or os.path.join(
        os.path.expanduser("~"), ".xdl"
    )


def browser_profile_dir(browser: str = "chrome") -> str:
    """浏览器专用 Profile 目录（~/.xdl/{browser}-profile）。"""
    return os.path.join(xdl_home(), f"{browser}-profile")


def browser_cookies_path(browser: str = "chrome") -> str:
    """该浏览器的登录 Cookie 缓存（~/.xdl/{browser}-cookies.json）。"""
    return os.path.join(xdl_home(), f"{browser}-cookies.json")


def browser_device_info_path(browser: str = "chrome") -> str:
    """该浏览器的设备指纹缓存（~/.xdl/{browser}-device-info.json）。"""
    return os.path.join(xdl_home(), f"{browser}-device-info.json")


# 字段名 → 按浏览器派生默认值的构造函数。settings 用它填默认值，
# WebUI 用它判断某个已持久化的路径是不是「程序自动填的」。
DERIVED_PATH_BUILDERS = {
    "chrome_profile_dir": browser_profile_dir,
    "cookies_cache_path": browser_cookies_path,
    "device_info_path": browser_device_info_path,
}

# 旧版本自动填入、且已被上面的新布局取代的默认值。归一时同样按「自动值」处理，
# 否则老用户 webui-settings.json 里钉死的绝对路径会让新布局永远不生效。
_LEGACY_DERIVED_DEFAULTS = {
    "chrome_profile_dir": ("chrome-profile",),
    "cookies_cache_path": ("cookies.json",),
    "device_info_path": ("device-info.json",),
}


def _same_path(left: str, right: str) -> bool:
    return (os.path.normcase(os.path.normpath(str(left)))
            == os.path.normcase(os.path.normpath(str(right))))


def is_derived_path(field: str, value: str, browsers=("chrome", "edge")) -> bool:
    """`value` 是否为 `field` 在任一浏览器下的自动派生默认值（含旧布局）。

    用于区分「程序自动填的路径」（切换浏览器时应跟随重派生）与「用户自定义
    路径」（一律保留）。
    """
    text = str(value or "").strip()
    if not text:
        return False
    builder = DERIVED_PATH_BUILDERS.get(field)
    if builder is None:
        return False
    candidates = [builder(name) for name in browsers]
    candidates += [os.path.join(xdl_home(), name)
                   for name in _LEGACY_DERIVED_DEFAULTS.get(field, ())]
    return any(_same_path(text, candidate) for candidate in candidates)


def migrate_legacy_layout(home: str | None = None) -> list[tuple[str, str]]:
    """把旧的浏览器无关缓存重命名到 Chrome 布局，返回实际迁移的 (旧, 新) 列表。

    旧版本只支持 Chrome，`~/.xdl/cookies.json` 与 `device-info.json` 必然是
    Chrome 采的，改名即可让老用户升级后零感知地继续用同一份登录态。

    用 rename 而非拷贝：留两份会让 CLI 与 WebUI 读到不同文件而分叉。仅在目标
    不存在时迁移，因此可重复调用；任何 OSError 都跳过（迁移失败最坏只是要求
    重新登录，不该阻断启动）。
    """
    base = home or xdl_home()
    moved: list[tuple[str, str]] = []
    for old_name, new_name in _LEGACY_RENAMES:
        old_path = os.path.join(base, old_name)
        new_path = os.path.join(base, new_name)
        if not os.path.isfile(old_path) or os.path.exists(new_path):
            continue
        try:
            os.replace(old_path, new_path)
        except OSError:
            continue
        moved.append((old_path, new_path))
    return moved
