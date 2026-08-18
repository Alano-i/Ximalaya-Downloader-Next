# -*- coding: utf-8 -*-
"""PC 桌面端接口音源适配器（实现 Source 端口）。

通过桌面客户端使用的接口获取曲目列表、播放权限与播放地址：曲目列表走
`play/v1/show`，权限走 `play/v1/audio`，播放地址走 `track/quality`。
与网页端路径相比无需设备指纹与播放地址解密，登录态复用 `xdl login` 保存的会话。
"""
from __future__ import annotations

import asyncio
import os
import secrets
import threading
import time
import uuid
from datetime import datetime, timezone

import requests

try:
    from curl_cffi import requests as cffi_requests
    from curl_cffi.curl import CurlError
    from curl_cffi.requests.exceptions import RequestException as CffiRequestError
    _HAS_CURL_CFFI = True
except ImportError:
    cffi_requests = None
    CurlError = ()
    CffiRequestError = ()
    _HAS_CURL_CFFI = False

from ..config import platform, sign as sign_conf
from ..domain import Album, AlbumTrack, PlayUrl, Track
from ..errors import (ApiError, AuthError, ConfigError, DecodeError,
                      NetworkError, RiskControlError)
from ..risk import RiskEventRecorder
from .decoder import WinEcbDecoder
from .sign.py_sign import PySignProvider
from .sign.cookies import (build_cookie_header, clear_cookie_cache,
                           extract_cookies_from_profile,
                           ensure_pc_device_cookies, is_login_cookie,
                           load_cached_cookies, save_cookies)

_AUTH_RETS = {927}
_MAX_PAGES = 2000
_PAGE_SLEEP = 0.3

# track/quality 响应 debugInfo.detailTrackDto.result.playPathDto 的字段映射：
#   (playPathDto 字段名, 音质类型, 大小字段名)
_PLAY_PATH_FIELDS = (
    ("playPathAacV164", "M4A_64", "aacV164Size"),
    ("playPath64", "MP3_64", "mp364Size"),
    ("playPathHq", "MP3_128", "hqSize"),
    ("playPathAacV224", "M4A_24", "aacV224Size"),
    ("playPath32", "MP3_32", "mp332Size"),
    # 注意：不映射 downloadPath / downloadAacPath。这两类是"下载专用"地址
    # （download.ali.xmcdn.com），请求会 302 到 IP:port 直连；requests 走系统
    # 代理时对 IP 直连会 502。playPathAacV224 已提供同码率 M4A_24 播放地址。
    # 注意：不映射 originPlayPath。原始无损文件码率未知，会被 Quality 协商
    # 误判为最低档；且体积大、格式不统一，不属于常规下载目标。
)


def _random_xm_sign() -> str:
    """生成 simple-revision-for-pc 接口所需的 xm-sign。

    返回 ``毫秒时间戳&16位hex`` 格式的签名值。
    """
    return f"{int(time.time() * 1000)}&{secrets.token_hex(8)}"


def _raise_for_ret(ret, msg, *, authenticated: bool | None = None) -> None:
    """按 ret 语义把接口失败映射为类型化异常（与 HttpSource 口径一致）。"""
    msg_str = str(msg or "")
    is_busy = ret == 1001 or (ret == 3005 and "系统繁忙" in msg_str)
    if is_busy and authenticated is False:
        raise ApiError(
            f"未登录或匿名访问被拒（ret={ret} msg={msg}）。"
            "请先运行 `xdl login`，登录成功后直接重试。",
            ret=ret, retryable=False,
        )
    if is_busy:
        raise RiskControlError(
            f"触发风控（ret={ret} msg={msg}）。已停止继续派发请求。",
            ret=ret,
        )
    if ret in _AUTH_RETS:
        raise AuthError(
            f"无权访问该音频（ret={ret} msg={msg}）。"
            "可能需要登录或该内容在当前地区/账号下不可用。"
        )
    raise ApiError(
        f"未能获取播放信息（ret={ret} msg={msg}）。",
        ret=ret, retryable=True,
    )


class PcHttpSource:
    """纯 HTTP 音源：PC 桌面端接口，实现 `Source` 端口。

    登录态复用 `xdl login` 写入的专用浏览器 Profile（`1&_token` 一条 Cookie
    即可），不依赖 SignProvider / Decoder / du_web_sdk。
    """

    def __init__(
        self,
        chrome_path: str = "",
        profile_dir: str = "",
        cookies_cache_path: str = "",
        resolve_timeout: int = 40,
        chrome_headless: bool = True,
        risk_recorder: RiskEventRecorder | None = None,
        chrome_fallback=None,
        impersonate: str = "chrome146",
        browser: str = "chrome",
        cookie_max_age: int = 1800,
        decoder=None,
        sign_provider=None,
        device_info_path: str = "",
        win_decoder=None,
    ):
        self._chrome_path = chrome_path
        self._profile_dir = profile_dir
        self._cookies_cache_path = cookies_cache_path
        self._resolve_timeout = resolve_timeout
        self._chrome_headless = chrome_headless
        self._risk_recorder = risk_recorder
        self._chrome_fallback = chrome_fallback
        self._impersonate = impersonate
        self._browser_name = platform.browser_display_name(
            platform.infer_browser_from_path(chrome_path) or browser
        )
        self._cookie_max_age = cookie_max_age
        self._decoder = decoder
        self._sign_provider = sign_provider
        self._device_info_path = device_info_path
        self._win_decoder = win_decoder
        self._session_id = str(uuid.uuid4())
        self._request_index = 0
        # 线程级连接复用：curl_cffi 的 Session 非线程安全，asyncio.to_thread
        # 的并发 worker 各持一个 session，避免每集都新建 TLS 连接。
        # 200+ 集后偶发 TLS 错误的诱因之一是连接/句柄反复重建，复用它可
        # 显著减少握手次数；keep-alive 失效时 libcurl 会自动重连。
        self._local = threading.local()
        # 运行态
        self._cookie_header: str = ""
        self._authenticated: bool | None = None

    # ---- Source 端口：会话生命周期 ----
    async def open(self) -> None:
        await self._load_cookies()
        self._ensure_sign()

    async def close(self) -> None:
        session = getattr(self._local, "session", None)
        if session is not None:
            try:
                session.close()
            except Exception:
                pass
            self._local.session = None
        if self._sign_provider is not None:
            try:
                self._sign_provider.close()
            except Exception:
                pass

    def _get_session(self):
        """返回当前线程的 curl_cffi Session（懒创建、复用）。"""
        session = getattr(self._local, "session", None)
        if session is None:
            session = cffi_requests.Session(impersonate=self._impersonate)
            self._local.session = session
        return session

    def _ensure_sign(self):
        """惰性创建并打开 baseInfo 所需的 xm-sign 提供者。"""
        if self._sign_provider is None:
            self._sign_provider = PySignProvider(
                device_info_path=self._device_info_path or None,
            )
        self._sign_provider.open()

    def _ensure_win_decoder(self):
        """惰性创建 PC 端（device=win）playUrlList 解密器。"""
        if self._win_decoder is None:
            if isinstance(self._decoder, WinEcbDecoder):
                self._win_decoder = self._decoder
            else:
                self._win_decoder = WinEcbDecoder()
        return self._win_decoder

    async def _load_cookies(self) -> None:
        cached = (
            await asyncio.to_thread(
                load_cached_cookies, self._cookies_cache_path, self._cookie_max_age
            )
            if self._cookies_cache_path else None
        )
        if cached and is_login_cookie(cached):
            # Web 端登录态缺 PC 客户端设备 Cookie（install_id/1&_device/
            # channel），baseInfo 会因此风控；按客户端格式补全并回写缓存。
            self._cookies = ensure_pc_device_cookies(cached)
            if len(self._cookies) != len(cached) and self._cookies_cache_path:
                await asyncio.to_thread(
                    save_cookies, self._cookies, self._cookies_cache_path,
                )
        else:
            if not self._profile_dir or not os.path.isdir(self._profile_dir):
                raise ConfigError(
                    f"未找到 {self._browser_name} 专用 Profile 目录: "
                    f"{self._profile_dir!r}。请先运行 `xdl login` 创建并保存登录态"
                    f"（当前浏览器: {self._browser_name}）。"
                )
            self._cookies = await asyncio.to_thread(
                extract_cookies_from_profile,
                self._profile_dir,
                self._chrome_path,
                self._chrome_headless,
                browser=platform.infer_browser_from_path(self._chrome_path)
                or "chrome",
            )
            if is_login_cookie(self._cookies) and self._cookies_cache_path:
                self._cookies = ensure_pc_device_cookies(self._cookies)
                await asyncio.to_thread(
                    save_cookies, self._cookies, self._cookies_cache_path,
                )
        self._cookie_header = build_cookie_header(self._cookies)
        self._authenticated = is_login_cookie(self._cookies)
        if not self._authenticated:
            print("[warn] 当前 Cookie 中没有发现登录 token（1&_token），"
                  "PC 端接口可能被拒或返回空数据。")

    def _http_get(self, url: str, params: dict, headers: dict):
        """发 GET；curl_cffi 失败时兜底用标准 requests 重试一次。

        curl_cffi 偶发 TLS 错误（curl 35）在长批量下载中已实际观测到，且其
        异常不属于 XdlError，会绕过上层重试策略直接崩掉整个批次。这里统一
        收敛为 NetworkError（retryable），并把瞬时 TLS 失败降级到 requests
        再试一次，仍失败才交给重试策略。
        """
        try:
            if self._impersonate and _HAS_CURL_CFFI:
                try:
                    return self._get_session().get(
                        url, params=params, headers=headers,
                        timeout=self._resolve_timeout,
                    )
                except (CurlError, CffiRequestError) as e:
                    print(f"[warn] curl_cffi 请求失败，改用 requests 兜底: {e}")
                    # 丢弃可能损坏的线程级 session，下个请求重建连接。
                    self._local.session = None
            headers.setdefault("User-Agent", platform.UA)
            return requests.get(
                url, params=params, headers=headers,
                timeout=self._resolve_timeout,
            )
        except requests.RequestException as e:
            raise NetworkError(f"解析请求失败: {e}") from e

    def _headers(self, referer: str, origin: str, *, with_sign: bool) -> dict:
        headers = {
            "Accept": "*/*",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Referer": referer,
            "Origin": origin,
            "Cookie": self._cookie_header,
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-origin",
        }
        if with_sign:
            headers["xm-sign"] = _random_xm_sign()
        return headers

    # ---- Source 端口：专辑曲目列表 ----
    async def get_album(self, album_id: str) -> Album:
        return await asyncio.to_thread(self._get_album_sync, str(album_id))

    def _get_album_sync(self, album_id: str) -> Album:
        tracks: list[AlbumTrack] = []
        title: str | None = None
        page = 1
        while page <= _MAX_PAGES:
            params = {
                "id": album_id,
                "num": page,
                "sort": 0,
                "size": platform.PC_PLAY_PAGE_SIZE,
                "ptype": 0,
            }
            headers = self._headers(
                f"{platform.PC_BASE}/album/{album_id}",
                platform.PC_BASE,
                with_sign=True,
            )
            try:
                resp = self._http_get(platform.PC_PLAY_SHOW_URL, params, headers)
                body = resp.json()
            except requests.RequestException as e:
                raise NetworkError(f"获取专辑曲目清单失败: {e}") from e
            except ValueError as e:
                raise ApiError(
                    f"play/v1/show 响应不是 JSON：{resp.text[:200]!r}",
                    ret=None, retryable=True,
                ) from e
            ret = body.get("ret")
            data = body.get("data") or {}
            if ret != 200 or "tracksAudioPlay" not in data:
                self._record_album(album_id, "api_error", ret, body.get("msg"))
                _raise_for_ret(ret, body.get("msg"),
                               authenticated=self._authenticated)
            batch = data.get("tracksAudioPlay") or []
            if not batch:
                break
            for t in batch:
                tracks.append(AlbumTrack(
                    track_id=str(t.get("trackId")),
                    title=t.get("trackName") or str(t.get("trackId")),
                    index=int(t.get("index") or len(tracks) + 1),
                    is_paid=False,   # 付费状态由 play/v1/audio 单集判断
                ))
                if title is None and t.get("albumName"):
                    title = t["albumName"]
            if not data.get("hasMore"):
                break
            page += 1
            time.sleep(_PAGE_SLEEP)
        if not tracks:
            raise ApiError("专辑无可下载曲目或不存在。")
        return Album(
            album_id=album_id,
            title=title or album_id,
            total=len(tracks),
            tracks=tracks,
        )

    # ---- Source 端口：单曲 ----
    async def get_track(self, track_id: str) -> Track:
        return await asyncio.to_thread(self._get_track_sync, str(track_id))

    def _get_track_sync(self, track_id: str) -> Track:
        started = time.perf_counter()
        self._request_index += 1
        request_index = self._request_index
        try:
            # 1) 权限校验（play/v1/audio）
            auth_ok = self._check_play_authority(track_id)
            if not auth_ok:
                raise AuthError(
                    f"该音频不可播放（trackId={track_id}）。"
                    "可能是付费内容、地区限制或账号无权限。"
                )
            # 2) 播放地址（track/quality）
            track = self._fetch_track_quality(track_id)
            self._record(track_id, started, "success", None, None,
                         request_index)
            return track
        except (ApiError, AuthError, NetworkError, RiskControlError) as error:
            self._record(track_id, started, error.category,
                         getattr(error, "ret", None), str(error), request_index)
            raise
        except Exception as error:
            self._record(track_id, started, "unexpected", None,
                         type(error).__name__, request_index)
            raise

    def _check_play_authority(self, track_id: str) -> bool:
        """调 play/v1/audio 判断 canPlay；返回 False 表示无权播放。"""
        params = {"id": track_id, "ptype": 1}
        headers = self._headers(platform.PC_BASE, platform.PC_BASE, with_sign=True)
        try:
            resp = self._http_get(platform.PC_PLAY_AUDIO_URL, params, headers)
            body = resp.json()
        except requests.RequestException as e:
            raise NetworkError(f"play/v1/audio 请求失败: {e}") from e
        except ValueError as e:
            raise ApiError(
                f"play/v1/audio 响应不是 JSON：{resp.text[:200]!r}",
                ret=None, retryable=True,
            ) from e
        ret = body.get("ret")
        data = body.get("data") or {}
        if ret != 200 or "canPlay" not in data:
            _raise_for_ret(ret, body.get("msg"),
                           authenticated=self._authenticated)
        return bool(data.get("canPlay"))

    def _fetch_track_quality(self, track_id: str) -> Track:
        """调 track/quality 取音质与播放地址。"""
        url = platform.TRACK_QUALITY_URL.format(
            track_id=track_id, ts=int(time.time() * 1000),
        )
        headers = self._headers(platform.MOBILE_BASE, platform.MOBILE_BASE,
                                with_sign=False)
        try:
            resp = self._http_get(url, {}, headers)
            body = resp.json()
        except requests.RequestException as e:
            raise NetworkError(f"track/quality 请求失败: {e}") from e
        except ValueError as e:
            raise ApiError(
                f"track/quality 响应不是 JSON：{resp.text[:200]!r}",
                ret=None, retryable=True,
            ) from e
        ret = body.get("ret")
        data = body.get("data") or {}
        if ret != 0 or "trackQualities" not in data:
            _raise_for_ret(ret, body.get("msg"),
                           authenticated=self._authenticated)
        result = (
            (data.get("debugInfo") or {})
            .get("debugDetailMap") or {}
        ).get("detailTrackDto") or {}
        result = result.get("result") or {}
        play_path_dto = result.get("playPathDto") or {}

        play_urls: list[PlayUrl] = []
        for field, qtype, size_field in _PLAY_PATH_FIELDS:
            raw = play_path_dto.get(field)
            if not raw:
                continue
            play_urls.append(PlayUrl(
                type=qtype,
                # 保留平台原始 http 地址：aod.cos.tx.xmcdn.com 的 https 在本机
                # requests 环境下证书链校验失败，http 可直接拉流。
                url=str(raw),
                file_size=int(play_path_dto.get(size_field) or 0),
            ))
        if not play_urls:
            # VIP/付费曲目：playPathDto 通常只有 size 字段、明文地址全空，
            # 播放地址要走 baseInfo 加密 playUrlList 解密链路（PC 客户端
            # 同样先调 baseInfo 再解密，抓包记录不入库）。
            return self._fetch_paid_track(track_id)
        return Track(
            track_id=str(track_id),
            title=result.get("title") or str(track_id),
            play_urls=play_urls,
            is_paid=bool(result.get("isPaid")),
            is_authorized=True,
        )

    def _fetch_paid_track(self, track_id: str) -> Track:
        """付费曲目兜底：baseInfo 加密 playUrlList → WinEcbDecoder 解密。

        PC 客户端对 VIP 音频的实际链路：play/v1/audio 判权通过后，播放地址
        来自 track/v3/baseInfo 的加密 playUrlList（isAntiLeech=true），客户端
        用 AES-ECB（密钥见 platform.WIN_PLAY_URL_AES_KEY）解密后得到
        audiopay.cos.tx.xmcdn.com/download/...?sign=&buy_key=&timestamp=&token=
        &duration= 这类可直接 GET 的付费 CDN 地址；track/quality 对付费曲目
        不返回明文地址。device 与桌面客户端一致用 win。
        """
        self._ensure_sign()
        decoder = self._ensure_win_decoder()
        url = sign_conf.BASE_INFO_URL.format(ts=int(time.time() * 1000))
        params = {
            "device": "win",
            "trackId": str(track_id),
            "trackQualityLevel": "1",
        }
        headers = self._headers(
            platform.SOUND_URL.format(track_id=track_id),
            platform.BASE,
            with_sign=False,
        )
        headers["xm-sign"] = self._sign_provider.sign()
        try:
            resp = self._http_get(url, params, headers)
            body = resp.json()
        except requests.RequestException as e:
            raise NetworkError(f"baseInfo 请求失败: {e}") from e
        except ValueError as e:
            raise ApiError(
                f"baseInfo 响应不是 JSON：{resp.text[:200]!r}",
                ret=None, retryable=True,
            ) from e
        ret = body.get("ret")
        data = body.get("data") or {}
        track_info = data.get("trackInfo") or body.get("trackInfo") or {}
        play_url_list = track_info.get("playUrlList") or []
        if ret not in (0, 200):
            _raise_for_ret(ret, body.get("msg"),
                           authenticated=self._authenticated)

        play_urls: list[PlayUrl] = []
        for item in play_url_list:
            enc = item.get("url")
            if not enc:
                continue
            try:
                real = decoder.decode(enc)
            except DecodeError:
                continue
            if not real.startswith("http"):
                continue
            play_urls.append(PlayUrl(
                type=item.get("type", ""),
                url=real,
                file_size=int(item.get("fileSize", 0) or 0),
            ))
        if not play_urls:
            raise ApiError(
                f"baseInfo 未返回可解密播放地址（trackId={track_id}）。"
                "可能无播放权限或加密方式已变化。",
                ret=ret, retryable=False,
            )
        return Track(
            track_id=str(track_id),
            title=track_info.get("title") or str(track_id),
            play_urls=play_urls,
            is_paid=True,
            is_authorized=True,
        )

    # ---- 与音源后端无关的命令（委托给 ChromeSource 兜底） ----
    def interactive_login(self, **wait_options) -> str:
        """打开浏览器完成登录并保存到专用 Profile（共用 `xdl login` 流程）。"""
        if self._chrome_fallback is None:
            raise ConfigError(
                "未配置 chrome_fallback；无法在 pc 后端下交互登录。"
                "请确认装配根注入了 ChromeSource（见 composition.build_facade）。")
        # ChromeSource 的登录总是先清专用 Profile（见其实现 docstring），这里只需
        # 额外清掉 Cookie 缓存——只清一个都会被旧账号顶回来。
        wait_options.pop("reset", None)  # 向后兼容旧调用方；行为上已无差异
        clear_cookie_cache(self._cookies_cache_path)
        path = self._chrome_fallback.interactive_login(**wait_options)
        take_cookies = getattr(self._chrome_fallback, "take_login_cookies", None)
        if not callable(take_cookies):
            raise ConfigError(
                "chrome_fallback 未提供登录 Cookie 捕获结果；无法安全保存登录态。"
            )
        cookies = take_cookies()
        cookies = ensure_pc_device_cookies(cookies)
        if not is_login_cookie(cookies):
            raise AuthError(
                f"专用 {self._browser_name} Profile 中未发现登录 token（1&_token）；"
                "登录未完成或未持久化，未覆盖现有 Cookie 缓存。"
            )
        if self._cookies_cache_path:
            save_cookies(cookies, self._cookies_cache_path)
        self._cookies = cookies
        self._cookie_header = build_cookie_header(cookies)
        self._authenticated = True
        return path

    def logout(self) -> dict:
        """清掉本地登录凭据：Cookie 缓存 + 专用 Profile + 内存副本。

        三处缺一不可——只删缓存文件，下次 open() 会从 Profile 重新导出同一个
        账号；只删 Profile，内存里的 Cookie 头还在本进程里继续用。
        """
        removed_cache = clear_cookie_cache(self._cookies_cache_path)
        profile = ({} if self._chrome_fallback is None
                   else self._chrome_fallback.logout())
        self._cookies = []
        self._cookie_header = ""
        self._authenticated = False
        return {"cookie_cache_removed": removed_cache,
                "cookie_cache_path": self._cookies_cache_path, **profile}

    async def inspect_storage(self) -> dict:
        """诊断：列出 Profile 设备标识存储 key（不读 value），委托 ChromeSource。"""
        if self._chrome_fallback is None:
            raise ConfigError(
                "未配置 chrome_fallback；无法在 pc 后端下做 Profile 诊断。")
        return await self._chrome_fallback.inspect_storage()

    # ---- 内部：风控观测 ----
    def _record(self, track_id: str, started: float, outcome: str,
                ret, msg, request_index: int) -> None:
        if self._risk_recorder is None:
            return
        try:
            self._risk_recorder.record(
                track_id=str(track_id),
                elapsed_ms=round((time.perf_counter() - started) * 1000),
                outcome=outcome,
                ret=ret,
                msg=str(msg or ""),
                in_flight=1,
                started_at=datetime.now(timezone.utc).isoformat(),
                request_index=request_index,
                session_id=self._session_id,
                authenticated=self._authenticated,
                backend="pc",
            )
        # 只吞写盘失败。这里原本 catch Exception 并且传的是 `message=`（record()
        # 的参数叫 msg），于是每次调用都抛 TypeError 被静默吃掉——PC 后端从头到尾
        # 一条观测都没落盘。catch 收窄后，同类签名错误会立刻炸出来而不是装作没事。
        except OSError:
            pass

    def _record_album(self, album_id: str, outcome: str, ret, msg) -> None:
        if self._risk_recorder is None:
            return
        try:
            self._risk_recorder.record(
                track_id=f"album:{album_id}",
                elapsed_ms=0,
                outcome=outcome,
                ret=ret,
                msg=str(msg or ""),
                in_flight=1,
                started_at=datetime.now(timezone.utc).isoformat(),
                request_index=self._request_index,
                session_id=self._session_id,
                authenticated=self._authenticated,
                backend="pc",
            )
        except OSError:
            pass
