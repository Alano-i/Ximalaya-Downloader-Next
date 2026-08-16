# -*- coding: utf-8 -*-
"""与平台强相关的数据化配置（见 docs/architecture.md §9）。

这里集中存放最易随平台变动的常量：接口地址、UA、解码置换表/密钥、
注入页面的脚本等。平台一变，原则上只改这个文件 + 解码适配器。
"""
import os
import shutil
import sys

BASE = "https://www.ximalaya.com"
HOME_URL = BASE + "/"
SOUND_URL = BASE + "/sound/{track_id}"
ALBUM_URL = BASE + "/album/{album_id}"

# 专辑曲目清单接口。注意用「非 v1」版：它免签名、可匿名翻全部页（每页 30 条）；
# 而 /revision/album/v1/getTracksList 反而要 webtk 签名且对自动化环境做风控
# （返回「当前环境异常」）。本接口仅给曲目元信息（id/标题/序号），不含 playUrl。
TRACKS_LIST_URL = BASE + "/revision/album/getTracksList"
TRACKS_PAGE_SIZE = 30   # 接口固定每页 30 条（传 pageSize 无效）

# ---- PC 桌面端接口 ----
# - play/v1/show：曲目列表，num 为 1 基页码，hasMore 控制翻页。
# - play/v1/audio：播放权限校验，返回 canPlay/isPaid/hasBuy。
# - track/quality：音质与播放地址（aod.cos.tx.xmcdn.com）。
PC_BASE = "https://pc.ximalaya.com"
PC_PLAY_SHOW_URL = PC_BASE + "/simple-revision-for-pc/play/v1/show"
PC_PLAY_AUDIO_URL = PC_BASE + "/simple-revision-for-pc/play/v1/audio"
PC_PLAY_PAGE_SIZE = 30

MOBILE_BASE = "https://mobile.ximalaya.com"
TRACK_QUALITY_URL = (
    MOBILE_BASE + "/mobile-playpage/playpage/track/quality/{track_id}/{ts}"
)

# PC 桌面端（device=win）baseInfo 的 playUrlList 密文解密：AES-ECB + PKCS7，
# 密文为 URL-safe Base64。密钥提取自官方客户端 4.0.14 asar 的 Gt 函数
# （Wt.AES.decrypt(... Hex.parse("aaad3e4fd540b0f79dca95606e72bf93") ...)）。
WIN_PLAY_URL_AES_KEY = "aaad3e4fd540b0f79dca95606e72bf93"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36")

REFERER = BASE + "/"

# 启动真实浏览器的参数（仅开调试端口、不带任何自动化标志，避免被风控识别为机器人）。
# 关键：必须自己干净启动浏览器再用 CDP 接管；若让 Playwright 直接 launch，会带上
# --enable-automation 等痕迹，baseInfo 会被 du_web_sdk 风控返回 1001/3005「系统繁忙」。
# Chrome 与 Edge 同为 Chromium，这些参数对两者一致。
CHROME_LAUNCH_ARGS = [
    "--no-first-run",
    "--no-default-browser-check",
    "--mute-audio",
    "--autoplay-policy=no-user-gesture-required",
]

BROWSER_CHOICES = ("auto", "chrome", "edge")
_BROWSER_NAMES = {"chrome": "Chrome", "edge": "Edge"}


def browser_display_name(browser: str) -> str:
    """用户可见的浏览器名（报错/提示文案统一走这里）。"""
    return _BROWSER_NAMES.get(browser, browser or "Chrome")


def _first_existing(candidates) -> str | None:
    for c in candidates:
        if c and os.path.exists(c):
            return c
    return None


def find_chrome() -> str | None:
    """探测本机 Google Chrome（或 Chromium）可执行文件路径。"""
    candidates: list[str] = []
    if sys.platform == "darwin":
        candidates = [
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            os.path.expanduser(
                "~/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
            "/Applications/Chromium.app/Contents/MacOS/Chromium",
        ]
    elif sys.platform.startswith("win"):
        candidates = [
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        ]
    else:  # linux
        for name in ("google-chrome", "google-chrome-stable",
                     "chromium", "chromium-browser"):
            found = shutil.which(name)
            if found:
                candidates.append(found)
    return _first_existing(candidates)


def _find_edge_windows() -> str | None:
    # 注册表 App Paths 最可靠（覆盖 per-user 安装与非系统盘安装）；winreg 仅 Windows 可用。
    try:
        import winreg
    except ImportError:
        winreg = None
    if winreg is not None:
        for root in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
            try:
                with winreg.OpenKey(
                        root,
                        r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\msedge.exe"
                ) as key:
                    path, _ = winreg.QueryValueEx(key, "")
            except OSError:
                continue
            if path and os.path.exists(path):
                return path
    candidates = [
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    ]
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        candidates.append(os.path.join(
            local_app_data, r"Microsoft\Edge\Application\msedge.exe"))
    return _first_existing(candidates)


def find_edge() -> str | None:
    """探测本机 Microsoft Edge 可执行文件路径。"""
    if sys.platform == "darwin":
        return _first_existing([
            "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
            os.path.expanduser(
                "~/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"),
        ])
    if sys.platform.startswith("win"):
        return _find_edge_windows()
    # linux
    for name in ("microsoft-edge", "microsoft-edge-stable"):
        found = shutil.which(name)
        if found:
            return found
    return None


def find_browser(prefer: str = "auto") -> tuple[str, str | None]:
    """按偏好探测浏览器，返回 (browser, path)；未找到时 path 为 None。

    prefer 为 "auto" 时 Chrome 优先、Edge 兜底（保持旧行为：装有 Chrome 的机器
    默认继续用 Chrome）；为 "chrome"/"edge" 时只探测指定浏览器。
    """
    # 在函数体内解析，保证测试可 monkeypatch find_chrome/find_edge。
    finders = {"chrome": find_chrome, "edge": find_edge}
    order = ("chrome", "edge") if prefer == "auto" else (prefer,)
    for name in order:
        finder = finders.get(name)
        if finder is None:
            continue
        path = finder()
        if path:
            return name, path
    return ((prefer if prefer in finders else "chrome"), None)


def infer_browser_from_path(path: str) -> str | None:
    """从可执行文件路径推断浏览器（显式 chrome_path 覆盖时归一文案/UA 用）。"""
    name = os.path.basename(str(path or "")).lower()
    if "msedge" in name or "microsoft edge" in name:
        return "edge"
    if "chrome" in name or "chromium" in name:
        return "chrome"
    return None


def is_known_browser_path(path: str) -> bool:
    """路径是否等于某浏览器在当前机器上的自动探测结果（含按安装布局枚举）。

    WebUI 切换浏览器时据此判断 chrome_path 是"程序自动填的"（应跟随重派生）
    还是用户自定义的（保留不动）。枚举值与 find_chrome/find_edge 同源。
    """
    text = str(path or "").strip()
    if not text:
        return False
    probes = {
        find_chrome(), find_edge(),
        # 跨平台标准安装布局：用户手动填的恰好是另一浏览器的标准路径时，
        # 切换浏览器同样应让它跟随重派生。
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
        "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    }
    norm = os.path.normcase(os.path.normpath(text))
    return any(
        probe and norm == os.path.normcase(os.path.normpath(str(probe)))
        for probe in probes
    )

# ---- www2/mweb2 音频 URL 解密所需的置换表与密钥 ----
PERMUTATION_TABLE_O = [
    183, 174, 108, 16, 131, 159, 250, 5, 239, 110, 193, 202, 153, 137, 251, 176,
    119, 150, 47, 204, 97, 237, 1, 71, 177, 42, 88, 218, 166, 82, 87, 94,
    14, 195, 69, 127, 215, 240, 225, 197, 238, 142, 123, 44, 219, 50, 190, 29,
    181, 186, 169, 98, 139, 185, 152, 13, 141, 76, 6, 157, 200, 132, 182, 49,
    20, 116, 136, 43, 155, 194, 101, 231, 162, 242, 151, 213, 53, 60, 26, 134,
    211, 56, 28, 223, 107, 161, 199, 15, 229, 61, 96, 41, 66, 158, 254, 21,
    165, 253, 103, 89, 3, 168, 40, 246, 81, 95, 58, 31, 172, 78, 99, 45,
    148, 187, 222, 124, 55, 203, 235, 64, 68, 149, 180, 35, 113, 207, 118, 111,
    91, 38, 247, 214, 7, 212, 209, 189, 241, 18, 115, 173, 25, 236, 121, 249,
    75, 57, 216, 10, 175, 112, 234, 164, 70, 206, 198, 255, 140, 230, 12, 32,
    83, 46, 245, 0, 62, 227, 72, 191, 156, 138, 248, 114, 220, 90, 84, 170,
    128, 19, 24, 122, 146, 80, 39, 37, 8, 34, 22, 11, 93, 130, 63, 154,
    244, 160, 144, 79, 23, 133, 92, 54, 102, 210, 65, 67, 27, 196, 201, 106,
    143, 52, 74, 100, 217, 179, 48, 233, 126, 117, 184, 226, 85, 171, 167, 86,
    2, 147, 17, 135, 228, 252, 105, 30, 192, 129, 178, 120, 36, 145, 51, 163,
    77, 205, 73, 4, 188, 125, 232, 33, 243, 109, 224, 104, 208, 221, 59, 9,
]

XOR_KEY_A = [
    204, 53, 135, 197, 39, 73, 58, 160, 79, 24, 12, 83, 180, 250, 101, 60,
    206, 30, 10, 227, 36, 95, 161, 16, 135, 150, 235, 116, 242, 116, 165, 171,
]
