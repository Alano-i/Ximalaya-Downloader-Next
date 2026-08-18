# -*- coding: utf-8 -*-
"""APK native 算法纯 Python 实现的契约与差分测试。

固定向量部分不需要 Java 或 `.so`；末尾的差分测试在缺少 sidecar 时自动跳过。
向量均由 vendor/apk_protocol 的 Unidbg sidecar 产出（准备方法见
vendor/apk_protocol/README.md）。
"""
from __future__ import annotations

import base64
import hashlib
import json
import shutil
import subprocess
import uuid
from pathlib import Path

import pytest
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad

from xdl.adapters.apk import native_py
from xdl.adapters.apk.native_bridge import ApkNativeBridge
from xdl.config import apk as apk_config
from xdl.errors import DecodeError, SignError

# ---- sidecar 产出的金标准向量 ----
XUID_VECTORS = [
    ("550e8400-e29b-41d4-a716-446655440000", "XAUVQ6EAOKbQdSnFkRmVUQAAGBO41VlIqFa"),
    ("00000000000000000000000000000000", "XAUAAAAAAAAAAAAAAAAAAAAAPdrCSMh-c7A"),
    ("ffffffffffffffffffffffffffffffff", "XAU______________________q3c1oCm_PA"),
    ("deadbeef-cafe-babe-0123-456789abcdef", "XAU3q2-78r-ur4BI0VniavN79FA55wI4c7j"),
]
TICKET_XUID = "XAUVQ6EAOKbQdSnFkRmVUQAAGBO41VlIqFa"
TICKET_ATTR = "b=downloadTrack&s=download&u=123456"
TICKET_TS = 1786951141
TICKET_RAND = bytes.fromhex("1aa18c4e369203110f92852d79062cc1")
TICKET_WANT = (
    "TACaoK15VUOhADim0HUpxZEZlVEAAAaoYxONpIDEQ-ShS15BizBu6DjD-V4-VsxE7SIk"
    "Xy7Di3wwfciOcjK_p6fDGxH_b9jb20ueGltYWxheWEudGluZy5hbmRyb2lkITEuMy4xNS"
    "E5LjUuMSFiPWRvd25sb2FkVHJhY2smcz1kb3dubG9hZCZ1PTEyMzQ1Ng"
)

VENDOR = Path(__file__).resolve().parents[1] / "vendor" / "apk_protocol"
_HAS_SIDECAR = shutil.which("java") is not None and (VENDOR / "native-signer.jar").is_file()
requires_sidecar = pytest.mark.skipif(
    not _HAS_SIDECAR, reason="需要 Java 与 vendor/apk_protocol 资产"
)


def _unb64u(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


# ---- XUID ----

@pytest.mark.parametrize("stable_id,expected", XUID_VECTORS)
def test_create_xuid_matches_golden_vectors(stable_id, expected):
    assert native_py.create_xuid(stable_id) == expected


def test_create_xuid_ignores_hyphens():
    a, b = XUID_VECTORS[0][0], XUID_VECTORS[0][0].replace("-", "")
    assert native_py.create_xuid(a) == native_py.create_xuid(b)


def test_parse_xuid_round_trips_uuid():
    stable_id = XUID_VECTORS[0][0]
    xuid = native_py.create_xuid(stable_id)
    assert native_py.parse_xuid(xuid) == bytes.fromhex(stable_id.replace("-", ""))


@pytest.mark.parametrize("bad", ["", "short", "zz" * 16, "0" * 31, "0" * 33])
def test_create_xuid_rejects_non_hex32(bad):
    with pytest.raises(SignError):
        native_py.create_xuid(bad)


# ---- ticket ----

def test_ticket_matches_golden_vector():
    assert native_py.ticket(
        TICKET_ATTR, TICKET_XUID, now=TICKET_TS, rand16=TICKET_RAND
    ) == TICKET_WANT


def test_ticket_layout_and_signature_are_self_consistent():
    value = native_py.ticket(TICKET_ATTR, TICKET_XUID, now=TICKET_TS, rand16=TICKET_RAND)
    raw = _unb64u(value[3:])
    ts4, uuid16, rand16, sign32, suffix = (
        raw[:4], raw[4:20], raw[20:36], raw[36:68], raw[68:]
    )
    assert int.from_bytes(ts4, "big") == TICKET_TS
    assert uuid16 == native_py.parse_xuid(TICKET_XUID)
    assert rand16 == TICKET_RAND
    assert suffix.decode() == (
        f"{apk_config.PACKAGE}!{apk_config.SDK_VERSION}!"
        f"{apk_config.APP_VERSION}!{TICKET_ATTR}"
    )
    assert sign32 == hashlib.sha256(
        ts4 + uuid16 + rand16 + value[:3].encode()
        + apk_config.TICKET_KEY + suffix
    ).digest()


def test_ticket_sanitises_bang_in_attr():
    value = native_py.ticket("a!b", TICKET_XUID, now=TICKET_TS, rand16=TICKET_RAND)
    assert _unb64u(value[3:])[68:].decode().endswith("!a_b")


def test_ticket_prefix_follows_xuid_type_char():
    # 兜底型 XUID（第 2 位为 I）应产出 TIC 而非 TAC
    fallback = "XIU" + TICKET_XUID[3:]
    assert native_py.ticket(TICKET_ATTR, fallback, now=TICKET_TS,
                            rand16=TICKET_RAND).startswith("TIC")


def test_ticket_is_fresh_each_call():
    a = native_py.ticket(TICKET_ATTR, TICKET_XUID)
    b = native_py.ticket(TICKET_ATTR, TICKET_XUID)
    assert a != b, "随机 UUID 应使每次 ticket 不同"


# ---- 下载地址解密 ----

def test_decrypt_download_passes_through_plain_urls():
    assert native_py.decrypt_download("https://a/b.m4a", 0) == "https://a/b.m4a"
    assert native_py.decrypt_download("", 0) == ""


def test_decrypt_download_reverses_aes_ecb():
    url = "https://audio.example.com/track/12345.m4a?sign=abc"
    key = bytes.fromhex(apk_config.PLAY_URL_KEY)
    blob = AES.new(key, AES.MODE_ECB).encrypt(pad(url.encode(), AES.block_size))
    token = base64.urlsafe_b64encode(blob).rstrip(b"=").decode()
    assert native_py.decrypt_download(token, 0) == url


V2_DOWNLOAD_VECTORS = [
    ("ZwARIjNEVWZ3iJmqu8zd7v8", "a"),
    ("_eLZ7VYBEiM0RVZneImaq7zN3u8A", "hello"),
    ("jO-c5VGIUl344yTTrinN37KltZVEEPS-teG3pgg2OuEEfZZN5QITJDVGV2h5ipusvc7f8AE",
     "https://audio.example/path?id=123&x=9"),
    ("zszry9Woc4mX6X6yAxQlNkdYaXqLnK2-z-DxAg", "中文路径"),
]


@pytest.mark.parametrize("ciphertext,plaintext", V2_DOWNLOAD_VECTORS)
def test_decrypt_download_v2_matches_native_vectors(ciphertext, plaintext):
    assert native_py.decrypt_download(ciphertext, 2) == plaintext


def test_decrypt_download_supports_all_known_versions():
    assert all(native_py.supports_decrypt(version) for version in (0, 1, 2))
    assert not native_py.supports_decrypt(3)


def test_decrypt_download_reports_bad_ciphertext():
    with pytest.raises(DecodeError):
        native_py.decrypt_download("!!!not-base64!!!", 0)


# ---- 登录签名 ----

SIGN_VECTORS = [
    ({"a": "1", "b": "2"}, "2566cd9b30a09c2bbee969022feaac6aa6572f75"),
    ({"x": "abcDEF", "Zz": "MiXeD-vAl"}, "81f2a29016c245ee389c23e5946ba408a3e41f1c"),
    ({"nonce": "a1b2c3", "timestamp": "1786951141", "mobile": "AbC+/=", "biz": "1"},
     "f8459ce54c3a8bdc146580f569efe796e8bbdb80"),
    ({"single": "value"}, "99d0afc7c17a1cebaf297869691f63afce574c6a"),
    ({"unicode": "中文abc", "k2": "v2"}, "e9280f5cd0cb096858ed1ffb9f817b9a7a6ea3b7"),
]


@pytest.mark.parametrize("values,expected", SIGN_VECTORS)
def test_sign_matches_golden_vectors(values, expected):
    assert native_py.sign(values) == expected


def test_sign_uppercases_keys_and_values():
    assert native_py.sign({"k": "v"}) == native_py.sign({"K": "V"})


def test_sign_orders_by_original_key_not_uppercased():
    # TreeMap 按原始 key 排序："Zz"(0x5A) 先于 "x"(0x78)
    assert native_py.sign({"Zz": "1", "x": "2"}) == native_py.sign({"x": "2", "Zz": "1"})


def test_sign_test_salt_differs_from_production():
    values = {"a": "1"}
    assert native_py.sign(values, production=False) != native_py.sign(values)


def test_sign_is_sha1_hex():
    value = native_py.sign({"a": "1"})
    assert len(value) == 40 and int(value, 16) >= 0


# ---- 手机号 / 密码加密 ----

def test_encrypt_mobile_produces_1024bit_rsa_block():
    blob = base64.b64decode(native_py.encrypt_mobile("13800138000"))
    assert len(blob) == 128


def test_encrypt_mobile_is_randomised_by_pkcs1_padding():
    a = native_py.encrypt_mobile("13800138000")
    b = native_py.encrypt_mobile("13800138000")
    assert a != b, "PKCS#1 v1.5 填充带随机数，密文不应重复"


def test_encrypt_mobile_rejects_empty():
    with pytest.raises(SignError):
        native_py.encrypt_mobile("")


def test_rsa_public_key_matches_native_parameters():
    from Crypto.PublicKey import RSA
    key = RSA.import_key(apk_config.RSA_PUBLIC_KEY_DER)
    # native 侧 getModulus().bitLength() 返回 0x400
    assert key.size_in_bits() == 0x400
    assert key.e == 65537


# ---- bridge 不再需要 Java ----

def _bridge(**kw):
    missing = "/nonexistent/path"
    return ApkNativeBridge(
        java_path=missing, signer_jar=missing, libcxx=missing, login_so=missing,
        xuid_so=missing, encrypt_so=missing, asset_dir=missing, **kw
    )


def test_bridge_serves_every_op_without_any_native_asset():
    """路径全部不存在也应成功——证明所有已用 native 操作均已纯 Python 化。"""
    bridge = _bridge()
    assert bridge.requires_sidecar() is False
    xuid = bridge.create_xuid(XUID_VECTORS[0][0])
    assert xuid == XUID_VECTORS[0][1]
    assert bridge.ticket(TICKET_ATTR, xuid).startswith("TAC")
    assert bridge.decrypt_download("https://x/y.m4a", 0) == "https://x/y.m4a"
    assert bridge.sign({"a": "1", "b": "2"}) == SIGN_VECTORS[0][1]
    assert len(base64.b64decode(bridge.encrypt_mobile("13800138000"))) == 128


def test_bridge_serves_encrypt_version_2_without_sidecar():
    bridge = _bridge()
    ciphertext, plaintext = V2_DOWNLOAD_VECTORS[2]
    assert bridge.decrypt_download(ciphertext, 2) == plaintext


def test_bridge_still_defers_to_sidecar_when_disabled():
    bridge = _bridge(prefer_python=False)
    assert bridge.requires_sidecar() is True
    with pytest.raises(Exception):
        bridge.create_xuid(XUID_VECTORS[0][0])


# ---- 与 sidecar 的差分测试 ----

@requires_sidecar
def test_differential_against_sidecar():
    """随机 UUID 批量比对纯 Python 与 native 输出。"""
    process = subprocess.Popen(
        ["java", f"-Dxmly.asset.dir={VENDOR / 'assets'}", "-jar",
         str(VENDOR / "native-signer.jar"), str(VENDOR / "libc++_shared.so"),
         str(VENDOR / "liblogin_encrypt.so"), str(VENDOR / "libnativelib.so"),
         str(VENDOR / "libencrypt.so")],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL, text=True, bufsize=1,
    )
    try:
        def call(**payload):
            process.stdin.write(json.dumps(payload) + "\n")
            process.stdin.flush()
            while True:
                line = process.stdout.readline()
                if not line:
                    pytest.fail("sidecar 意外退出")
                if line.startswith("{") and '"ok"' in line:
                    return json.loads(line)

        for _ in range(12):
            stable_id = str(uuid.uuid4())
            native_xuid = call(op="createXuid", stableId=stable_id.replace("-", ""))["value"]
            assert native_py.create_xuid(stable_id) == native_xuid

            attr = f"b=downloadTrack&s=download&u={uuid.uuid4().int % 10**8}"
            native_tk = call(op="ticket", attr=attr, xuid=native_xuid)["value"]
            raw = _unb64u(native_tk[3:])
            assert native_py.ticket(
                attr, native_xuid,
                now=int.from_bytes(raw[:4], "big"), rand16=raw[20:36],
            ) == native_tk

            values = {"nonce": uuid.uuid4().hex[:8], "u": str(uuid.uuid4().int % 10**6),
                      "Mixed-Key": "Mixed Value"}
            assert native_py.sign(values) == call(
                op="sign", values=values, production=True
            )["value"]
    finally:
        process.stdin.close()
        process.wait(timeout=30)
