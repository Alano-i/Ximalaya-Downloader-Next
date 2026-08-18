# -*- coding: utf-8 -*-
"""PcHttpSource 契约测试：PC 桌面端接口（play/v1/show + track/quality）。

离线：不发起真实网络请求；通过 monkeypatch `_http_get` 注入模拟响应。
"""
import json
import re
import asyncio
from types import SimpleNamespace

import pytest
import requests

from xdl.adapters.source_pc import PcHttpSource, _random_xm_sign
from xdl.adapters.sign.cookies import ensure_pc_device_cookies
from xdl.domain import Album, Track
from xdl.errors import ApiError, AuthError, NetworkError, RiskControlError

try:
    from curl_cffi.curl import CurlError
except ImportError:  # pragma: no cover
    CurlError = RuntimeError


class FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return json.loads(json.dumps(self._payload))

    @property
    def text(self):
        return json.dumps(self._payload)


PLAY_SHOW_PAGE = {
    "ret": 200,
    "data": {
        "albumId": 82080513,
        "albumName": "测试专辑",
        "pageNum": 1,
        "pageSize": 30,
        "hasMore": False,
        "tracksAudioPlay": [
            {"index": 1, "trackId": 111, "trackName": "第一集",
             "albumId": 82080513, "albumName": "测试专辑"},
            {"index": 2, "trackId": 222, "trackName": "第二集",
             "albumId": 82080513, "albumName": "测试专辑"},
        ],
    },
}

PLAY_AUDIO_OK = {
    "ret": 200,
    "data": {"trackId": 111, "canPlay": True, "isPaid": False,
             "hasBuy": True, "src": ""},
}

PLAY_AUDIO_DENIED = {
    "ret": 200,
    "data": {"trackId": 111, "canPlay": False, "isPaid": True,
             "hasBuy": False, "src": ""},
}

TRACK_QUALITY_OK = {
    "ret": 0,
    "data": {
        "trackQualities": [
            {"qualityLevel": 1, "qualityName": "高清音质", "fileSize": 18049820},
            {"qualityLevel": 0, "qualityName": "标准音质", "fileSize": 6983806},
        ],
        "debugInfo": {
            "debugDetailMap": {
                "detailTrackDto": {
                    "result": {
                        "title": "第一集",
                        "isPaid": False,
                        "playPathDto": {
                            "playPathAacV164": (
                                "http://aod.cos.tx.xmcdn.com/a/64.m4a"
                            ),
                            "aacV164Size": 18265227,
                            "playPath64": (
                                "http://aod.cos.tx.xmcdn.com/b/64.mp3"
                            ),
                            "mp364Size": 18049820,
                            "playPathAacV224": (
                                "http://aod.cos.tx.xmcdn.com/c/24.m4a"
                            ),
                            "aacV224Size": 6983806,
                            "playPath32": (
                                "http://aod.cos.tx.xmcdn.com/d/32.mp3"
                            ),
                            "mp332Size": 9025037,
                            "downloadPath": (
                                "http://download.ali.xmcdn.com/e.aac"
                            ),
                            "downloadSize": 9025364,
                            "originPlayPath": (
                                "http://aod.cos.tx.xmcdn.com/f/origin.mp3"
                            ),
                            "originSize": 54149373,
                        },
                    }
                }
            }
        },
    },
}

# VIP/付费曲目的 track/quality：playPathDto 只有 size 字段，明文地址全空
# （2026-08-16 对 sound/562111701 实测抓包形态）。
TRACK_QUALITY_VIP = {
    "ret": 0,
    "data": {
        "trackQualities": [
            {"qualityLevel": 1, "qualityName": "高清音质", "fileSize": 778633},
            {"qualityLevel": 0, "qualityName": "标准音质", "fileSize": 396184},
        ],
        "debugInfo": {
            "debugDetailMap": {
                "detailTrackDto": {
                    "result": {
                        "title": "VIP 测试曲目",
                        "isPaid": True,
                        "paidDto": {"price": "69.99"},
                        "playPathDto": {
                            "mp332Size": 510895,
                            "mp364Size": 1021536,
                            "hqSize": 1545465,
                            "aacV164Size": 778633,
                            "aacV224Size": 396184,
                            "originPlayPath": (
                                "storages/5bbc-audiofreehighqps/D2/CE/..."
                            ),
                        },
                    }
                }
            }
        },
    },
}

# baseInfo 加密 playUrlList（2026-08-16 device=win 实测抓包原样，含 M4A_64 一档）
BASE_INFO_VIP = {
    "ret": 0,
    "trackInfo": {
        "trackId": 562111701,
        "title": "VIP 测试曲目",
        "isPaid": True,
        "isAuthorized": True,
        "isAntiLeech": True,
        "playUrlList": [
            {
                "type": "M4A_64",
                "fileSize": 778633,
                "url": (
                    "pX7rCko1ZPLJXbyU3qjcDqAp042BK5yCrhhNlUZEBd6lHKILemhbvHD1Ykh"
                    "Q7FDbZ6QeFPVzxfDH4ro44BplhjCgjgi1xHnlrJR4LEJnIxSIqEnytSBJZBu"
                    "LBAEdE9dCViixIhz8NdLDFMG_tpDh88M2_tnqioJ8JnJYj9h6bGNGrP3_G4o"
                    "OHrz6CGZi024CBS40BG45LdipAuHAOq7_CW_pp9Vb9GgSgezuKF1AZhO0tGh"
                    "EOgUN0QHjzR2N8BTTIFO9u4CefQFTcaN3xzaDV7v7sxPZBhl73fvYnF153Rg"
                ),
            },
        ],
    },
}

PAID_URL = (
    "https://audiopay.cos.tx.xmcdn.com/download/1.0.0/storages/"
    "3cf3-audiopay/85/1E/GKwRIasG02T_AA_K1QGTsZLv-aacv2-48K.m4a"
    "?sign=e3707760ec267ee5610476fb609dbde6&buy_key=FM"
    "&timestamp=1786865983207000&token=8989&duration=127"
)


def _make_source(monkeypatch, responses):
    """按 URL 关键字路由响应；未命中的抛断言。"""
    src = PcHttpSource(
        cookies_cache_path="",
        profile_dir="",
        impersonate="",
    )
    src._cookies = [
        {"name": "1&_token", "value": "tok", "domain": ".ximalaya.com",
         "path": "/"},
    ]
    src._cookie_header = "1&_token=tok"
    src._authenticated = True

    def fake_get(url, params, headers):
        for key, payload in responses.items():
            if key in url:
                return FakeResp(payload)
        raise AssertionError(f"未预期请求: {url}")

    monkeypatch.setattr(src, "_http_get", fake_get)
    return src


class FakeSignProvider:
    """返回固定 xm-sign，不发起任何上报请求。"""

    def open(self):
        pass

    def close(self):
        pass

    def sign(self):
        return "cadd&&sid"


def test_random_sign_format():
    sign = _random_xm_sign()
    assert re.fullmatch(r"\d{13}&[0-9a-f]{16}", sign)


def test_ensure_pc_device_cookies_formats():
    base = [{"name": "1&_token", "value": "tok", "domain": ".ximalaya.com",
             "path": "/"}]
    out = ensure_pc_device_cookies(base)
    names = {c["name"] for c in out}
    assert {"install_id", "channel", "1&_device"} <= names
    by_name = {c["name"]: c for c in out}
    device = by_name["1&_device"]["value"]
    install = by_name["install_id"]["value"]
    assert device == f"win32&{install}&4.0.14"
    assert by_name["channel"]["value"] == "99&100001"
    # 已有设备字段时原样保留，不重复生成
    again = ensure_pc_device_cookies(out)
    assert {c["name"]: c["value"] for c in again} == {
        c["name"]: c["value"] for c in out
    }
    assert len(again) == len(out)


def test_load_cookies_auto_adds_pc_device_cookies(monkeypatch):
    cached = [{"name": "1&_token", "value": "tok", "domain": ".ximalaya.com",
               "path": "/"}]
    saved = []
    monkeypatch.setattr(
        "xdl.adapters.source_pc.load_cached_cookies", lambda *a, **kw: cached,
    )
    monkeypatch.setattr(
        "xdl.adapters.source_pc.save_cookies",
        lambda cookies, path: saved.append(cookies),
    )
    src = PcHttpSource(cookies_cache_path=r"C:\tmp\c.json", profile_dir="",
                       impersonate="")
    asyncio.run(src._load_cookies())
    header = src._cookie_header
    assert "install_id=" in header
    assert "1&_device=" in header
    assert "channel=" in header
    assert saved and saved[0] is src._cookies


def test_get_album_parses_tracks(monkeypatch):
    src = _make_source(monkeypatch, {"play/v1/show": PLAY_SHOW_PAGE})
    album = src._get_album_sync("82080513")
    assert isinstance(album, Album)
    assert album.album_id == "82080513"
    assert album.title == "测试专辑"
    assert album.total == 2
    assert album.tracks[0].track_id == "111"
    assert album.tracks[0].title == "第一集"
    assert album.tracks[0].index == 1
    assert album.tracks[1].track_id == "222"


def test_get_album_paginates_until_has_more_false(monkeypatch):
    page1 = json.loads(json.dumps(PLAY_SHOW_PAGE))
    page1["data"]["hasMore"] = True
    page2 = json.loads(json.dumps(PLAY_SHOW_PAGE))
    page2["data"]["pageNum"] = 2
    page2["data"]["hasMore"] = False
    page2["data"]["tracksAudioPlay"] = [
        {"index": 3, "trackId": 333, "trackName": "第三集",
         "albumId": 82080513, "albumName": "测试专辑"},
    ]
    seen = {"num": 0}

    def fake_get(url, params, headers):
        page = params["num"]
        seen["num"] = page
        return FakeResp(page1 if page == 1 else page2)

    src = PcHttpSource(impersonate="")
    src._cookies = []
    src._cookie_header = ""
    src._authenticated = True
    monkeypatch.setattr(src, "_http_get", fake_get)

    album = src._get_album_sync("82080513")
    assert [t.track_id for t in album.tracks] == ["111", "222", "333"]
    assert seen["num"] == 2


def test_get_track_parses_quality_and_excludes_download(monkeypatch):
    src = _make_source(monkeypatch, {
        "play/v1/audio": PLAY_AUDIO_OK,
        "track/quality": TRACK_QUALITY_OK,
    })
    track = src._get_track_sync("111")
    assert isinstance(track, Track)
    assert track.title == "第一集"
    urls = track.available_play_urls()
    types = [p.type for p in urls]
    # 常规播放地址全部在
    assert "M4A_64" in types and "MP3_64" in types
    assert "M4A_24" in types and "MP3_32" in types
    # downloadPath / originPlayPath 不得进入播放列表
    assert "AAC_24" not in types and "ORIGIN" not in types
    assert all(p.url.startswith("http://aod.cos.tx.xmcdn.com") for p in urls)
    m4a64 = next(p for p in urls if p.type == "M4A_64")
    assert m4a64.file_size == 18265227


def test_get_track_vip_falls_back_to_base_info(monkeypatch):
    """VIP 曲目 track/quality 无明文地址时，走 baseInfo 解密兜底。"""
    seen = {}

    def fake_get(url, params, headers):
        if "baseInfo" in url:
            seen["params"] = params
        for key, payload in {
            "play/v1/audio": PLAY_AUDIO_OK,
            "track/quality": TRACK_QUALITY_VIP,
            "baseInfo": BASE_INFO_VIP,
        }.items():
            if key in url:
                return FakeResp(payload)
        raise AssertionError(f"未预期请求: {url}")

    src = _make_source(monkeypatch, {
        "play/v1/audio": PLAY_AUDIO_OK,
        "track/quality": TRACK_QUALITY_VIP,
        "baseInfo": BASE_INFO_VIP,
    })
    src._sign_provider = FakeSignProvider()
    monkeypatch.setattr(src, "_http_get", fake_get)

    track = src._get_track_sync("562111701")
    assert isinstance(track, Track)
    assert track.is_paid is True
    assert track.is_authorized is True
    assert seen["params"]["device"] == "win"
    urls = track.available_play_urls()
    assert [p.type for p in urls] == ["M4A_64"]
    assert urls[0].url == PAID_URL
    assert urls[0].file_size == 778633


def test_get_track_vip_base_info_empty_raises(monkeypatch):
    """track/quality 空且 baseInfo 也拿不到地址时，报不可重试 ApiError。"""
    src = _make_source(monkeypatch, {
        "play/v1/audio": PLAY_AUDIO_OK,
        "track/quality": TRACK_QUALITY_VIP,
        "baseInfo": {"ret": 0, "trackInfo": {"playUrlList": []}},
    })
    src._sign_provider = FakeSignProvider()

    with pytest.raises(ApiError) as exc:
        src._get_track_sync("562111701")
    assert exc.value.retryable is False
    assert "baseInfo" in str(exc.value)


def test_get_track_denied_raises_auth_error(monkeypatch):
    src = _make_source(monkeypatch, {"play/v1/audio": PLAY_AUDIO_DENIED})
    with pytest.raises(AuthError):
        src._get_track_sync("111")


def test_get_track_risk_control_raises(monkeypatch):
    src = _make_source(monkeypatch, {
        "play/v1/audio": {"ret": 1001, "msg": "系统繁忙，请稍后再试!"},
    })
    with pytest.raises(RiskControlError):
        src._get_track_sync("111")


def test_http_get_falls_back_to_requests_on_curl_error(monkeypatch):
    src = PcHttpSource(impersonate="chrome146")
    src._cookies = []
    src._cookie_header = ""

    class FakeSession:
        def get(self, *a, **kw):
            raise CurlError("TLS connect error", 35)

    def fake_requests_get(url, params, headers, timeout):
        return FakeResp({"ret": 200})

    monkeypatch.setattr(src, "_get_session", lambda: FakeSession())
    monkeypatch.setattr("xdl.adapters.source_pc.requests.get",
                        fake_requests_get)
    resp = src._http_get("https://pc.ximalaya.com/x", {}, {})
    assert resp.json() == {"ret": 200}


def test_http_get_raises_network_error_when_both_fail(monkeypatch):
    src = PcHttpSource(impersonate="chrome146")
    src._cookies = []
    src._cookie_header = ""

    class FakeSession:
        def get(self, *a, **kw):
            raise CurlError("TLS connect error", 35)

    def fake_requests_get(url, params, headers, timeout):
        raise requests.RequestException("boom")

    monkeypatch.setattr(src, "_get_session", lambda: FakeSession())
    monkeypatch.setattr("xdl.adapters.source_pc.requests.get",
                        fake_requests_get)
    with pytest.raises(NetworkError) as exc:
        src._http_get("https://pc.ximalaya.com/x", {}, {})
    assert exc.value.retryable is True


def test_risk_events_are_actually_written_and_tagged(tmp_path):
    """PC 后端必须真的把观测落盘，并标上 backend。

    回归钉子：`_record` 曾经传 `message=`，而 `RiskEventRecorder.record()` 的参数
    叫 `msg` —— 每次调用都抛 TypeError，又被 `except Exception: pass` 吞掉，于是
    PC 后端从头到尾一条事件都没记过，风控日志里全是 HTTP 时期的数据。参数名写错
    是静默的，只有断言"文件里确实出现了这条事件"才拦得住。
    """
    from xdl.risk import RiskEventRecorder

    log = tmp_path / "risk.jsonl"
    src = PcHttpSource(impersonate="", risk_recorder=RiskEventRecorder(str(log)))
    src._authenticated = True

    src._record("123", 0.0, "success", None, None, 1)
    src._record("124", 0.0, "risk_control", 1001, "系统繁忙，请稍后再试!", 2)
    src._record_album("77", "api_error", 500, "boom")

    rows = [json.loads(line) for line in log.read_text("utf-8").splitlines()]
    assert [r["track_id"] for r in rows] == ["123", "124", "album:77"]
    assert [r["outcome"] for r in rows] == ["success", "risk_control", "api_error"]
    assert {r["backend"] for r in rows} == {"pc"}
    assert rows[1]["ret"] == 1001
    assert rows[1]["msg"] == "系统繁忙，请稍后再试!"
    assert rows[0]["authenticated"] is True
    assert len({r["session_id"] for r in rows}) == 1


def test_record_surfaces_signature_errors_instead_of_swallowing(tmp_path):
    """观测层只允许吞写盘失败；调用约定不匹配必须炸出来。"""
    class PickyRecorder:
        def record(self, **kwargs):
            raise TypeError("record() got an unexpected keyword argument")

    src = PcHttpSource(impersonate="", risk_recorder=PickyRecorder())
    with pytest.raises(TypeError):
        src._record("123", 0.0, "success", None, None, 1)
