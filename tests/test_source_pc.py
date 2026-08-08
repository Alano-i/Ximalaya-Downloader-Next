# -*- coding: utf-8 -*-
"""PcHttpSource 契约测试：PC 桌面端接口（play/v1/show + track/quality）。

离线：不发起真实网络请求；通过 monkeypatch `_http_get` 注入模拟响应。
"""
import json
import re
from types import SimpleNamespace

import pytest

from xdl.adapters.source_pc import PcHttpSource, _random_xm_sign
from xdl.domain import Album, Track
from xdl.errors import AuthError, RiskControlError


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


def test_random_sign_format():
    sign = _random_xm_sign()
    assert re.fullmatch(r"\d{13}&[0-9a-f]{16}", sign)


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
