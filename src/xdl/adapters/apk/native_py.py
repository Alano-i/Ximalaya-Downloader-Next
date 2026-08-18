"""APK native 算法的纯 Python 实现。

覆盖：

- `libnativelib.so`：XUID、ticket（x-tk）生成；
- `libencrypt.so`：下载地址解密，downloadEncryptVersion 0/1/2；
- `liblogin_encrypt.so`：登录签名 `sign` 与手机号/密码加密 `encryptMobile`。

算法常量与其反汇编来源见 ``xdl.config.apk``；逐字节验证方法见
``tests/test_apk_native_py.py``（固定向量 + 可选的 sidecar 差分测试）。
"""
from __future__ import annotations

import base64
import hashlib
import re
import time
import uuid

from Crypto.Cipher import AES, PKCS1_v1_5
from Crypto.PublicKey import RSA
from Crypto.Util.Padding import unpad

from ...config import apk as apk_config
from ...errors import DecodeError, SignError

_HEX32 = re.compile(r"[0-9a-fA-F]{32}")

# XUID/ticket 前缀第 2 位是 stable ID 的类型字符：合法 32-hex UUID 解析成功时为
# "A"，native 走内置兜底值时为 "I"。该字符会参与 SHA-256 preimage，因此必须与
# 实际输出一致。本实现只产出合法 UUID，故恒为 "A"；ticket 则沿用所属 XUID 的
# 类型字符，从而在兜底 XUID 上也能保持一致。
_PARSED_UUID_TYPE = "A"
_XUID_PREFIX_LEN = 3


def _b64u(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _unb64u(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _stable_uuid(stable_id: str) -> bytes:
    """stable ID → 16 字节 UUID（对应 native 的 hexStringToBytes 分支）。"""
    text = str(stable_id).replace("-", "").strip()
    if not _HEX32.fullmatch(text):
        raise SignError(
            "APK stable ID 必须是 32 位十六进制（可含连字符）；"
            "其他长度走 native 兼容分支，纯 Python 实现暂不支持。"
        )
    return bytes.fromhex(text)


def create_xuid(stable_id: str, attr: str = "U") -> str:
    """还原 `xuidcc_get_xuid`。

    preimage = prefix + uuid16 + XUID_KEY，SHA-256 后按固定下标抽 8 字节作签名。
    """
    uuid16 = _stable_uuid(stable_id)
    prefix = "X" + _PARSED_UUID_TYPE + attr
    digest = hashlib.sha256(prefix.encode("ascii") + uuid16 + apk_config.XUID_KEY).digest()
    sign8 = bytes(digest[i] for i in apk_config.XUID_SIGN_INDEXES)
    return prefix + _b64u(uuid16 + sign8)


def parse_xuid(xuid: str) -> bytes:
    """从 XUID 取回 16 字节 UUID（对应 `xuidcc_parse_xuid` 的自身格式分支）。"""
    text = str(xuid)
    if len(text) <= _XUID_PREFIX_LEN:
        raise SignError("APK XUID 格式非法。")
    try:
        raw = _unb64u(text[_XUID_PREFIX_LEN:])
    except (ValueError, TypeError) as exc:
        raise SignError(f"APK XUID 解析失败: {exc}") from exc
    if len(raw) < 16:
        raise SignError("APK XUID 长度不足。")
    return raw[:16]


def ticket(attr: str, xuid: str, *, now: int | None = None,
           rand16: bytes | None = None) -> str:
    """还原 `xuidcc_get_ticket`。

    raw = ts4 + uuid16 + rand16 + sign32 + suffix，整体 URL-safe Base64。
    ``now`` 与 ``rand16`` 可注入，仅供测试固定向量使用。
    """
    uuid16 = parse_xuid(xuid)
    # 前缀类型字符沿用 XUID，保证与 native 在兜底路径上的行为一致
    prefix = f"T{str(xuid)[1]}C"
    suffix = "!".join((
        apk_config.PACKAGE, apk_config.SDK_VERSION, apk_config.APP_VERSION,
        str(attr).replace("!", "_"),
    )).encode("utf-8")
    ts4 = int(now if now is not None else time.time()).to_bytes(4, "big")
    if rand16 is None:
        # native 用 libc rand() 生成 uuid4 后与 uuid16 逐字节 XOR；随机源本身
        # 不是服务端共享秘密，故直接用系统 uuid4。
        rand16 = bytes(a ^ b for a, b in zip(uuid.uuid4().bytes, uuid16))
    sign32 = hashlib.sha256(
        ts4 + uuid16 + rand16 + prefix.encode("ascii")
        + apk_config.TICKET_KEY + suffix
    ).digest()
    return prefix + _b64u(ts4 + uuid16 + rand16 + sign32 + suffix)


def supports_decrypt(version: int) -> bool:
    """当前 APK 的 downloadEncryptVersion 0/1/2 均已纯 Python 化。"""
    return int(version or 0) in (0, 1, 2)


def decrypt_download(value: str, version: int = 0) -> str:
    """还原 downloadEncryptVersion 0/1/2 的下载地址解密。"""
    text = str(value or "")
    if not text or text.startswith(("http://", "https://")):
        return text
    try:
        if int(version or 0) == 2:
            raw = _unb64u(text)
            if len(raw) < 16:
                raise ValueError("v2 密文解码后不足 16 字节")
            payload, dynamic_key = raw[:-16], raw[-16:]
            fixed_key = apk_config.DOWNLOAD_V2_XOR_KEY
            substitution = apk_config.DOWNLOAD_V2_SUBSTITUTION
            plain = bytes(
                substitution[value] ^ fixed_key[index % len(fixed_key)]
                ^ dynamic_key[index % len(dynamic_key)]
                for index, value in enumerate(payload)
            )
            return plain.decode("utf-8")
        if not supports_decrypt(version):
            raise ValueError(f"不支持 downloadEncryptVersion={version}")
        encrypted = _unb64u(text)
        plain = unpad(
            AES.new(bytes.fromhex(apk_config.PLAY_URL_KEY), AES.MODE_ECB).decrypt(encrypted),
            AES.block_size,
        )
        return plain.decode("utf-8")
    except (ValueError, TypeError, UnicodeDecodeError) as exc:
        raise DecodeError(f"APK 下载地址解密失败: {exc}") from exc


def sign(values: dict[str, str], production: bool = True) -> str:
    """还原 `aXGGIioVBB`（登录参数签名）。

    规范串按原始 key 排序拼成 ``k=v&``，整体转大写后拼接固定前缀与盐，
    取 SHA-1 的小写十六进制。``production=False`` 走 TEST 盐。
    """
    canonical = "".join(
        f"{key}={values[key]}&" for key in sorted(values, key=str)
    ).upper()
    salt = (apk_config.SIGN_SALT_PRODUCT if production
            else apk_config.SIGN_SALT_TEST)
    return hashlib.sha1(
        (canonical + apk_config.SIGN_PREFIX + salt).encode("utf-8")
    ).hexdigest()


def encrypt_mobile(value: str) -> str:
    """还原 `wwXLkDFrOu`（手机号/密码 RSA 加密）。

    RSA/ECB/PKCS1Padding + 标准 Base64。PKCS#1 v1.5 填充带随机数，因此每次
    输出不同，与 native 无法逐字节比对——这是算法本身的性质，不是实现差异。
    """
    text = str(value or "")
    if not text:
        raise SignError("APK 加密输入为空。")
    try:
        key = RSA.import_key(apk_config.RSA_PUBLIC_KEY_DER)
        blob = PKCS1_v1_5.new(key).encrypt(text.encode("utf-8"))
    except (ValueError, TypeError) as exc:
        raise SignError(f"APK RSA 加密失败: {exc}") from exc
    return base64.b64encode(blob).decode("ascii")
