# -*- coding: utf-8 -*-
"""APK 9.5.1.3 协议的提取常量。

这里集中存放从官方 APK native 库中还原出来的算法常量，使其可被审计、可被
diff，而不是藏在不透明的 `.so` 里。每组常量旁标注了来源库与反汇编地址，
可据此逐项复核；完整的还原过程记录含第三方专有代码细节，不随仓库分发。

所有常量都绑定 APK 9.5.1.3；喜马拉雅改版后需重新提取并更新。
"""
from __future__ import annotations

# ---- libnativelib.so：XUID / ticket ----
# 还原自 `xuidcc_get_xuid`(0x1f60c) 与 `xuidcc_get_ticket`(0x210ec)。
# 两者都是标准 SHA-256 + URL-safe Base64（无 padding），私有材料只有下面两个
# 内嵌常量。

# 32 字节，位于 .so 的 0x5a3dc
XUID_KEY = bytes.fromhex(
    "43948091bb379303193d958bd26fe98c"
    "9df3a2470e8cc9b10fc671c478404b1a"
)

# 29 字节，位于 .so 的 0x5981d
TICKET_KEY = bytes.fromhex(
    "b0b1a8ac34e66efa95c7f4157cf8b6ba"
    "33dfc2075e41fb964f2e12dcaa"
)

# XUID 签名从 SHA-256 摘要中抽取的字节下标（反汇编 0x1f750–0x1f790 的 ldrb 序列）
XUID_SIGN_INDEXES = (3, 6, 9, 12, 19, 22, 25, 28)

# ticket suffix 使用的三个标识，与 native `init_xuidcc` 实际读取的字段一致。
# 注意：init 还接收 APK 路径、厂商、机型，但 `init_xuidcc` 并不读取它们。
PACKAGE = "com.ximalaya.ting.android"
SDK_VERSION = "1.3.15"
APP_VERSION = "9.5.1"

# ---- libencrypt.so：下载地址解密 ----
# native 在该路径上只负责返回这个静态密钥（`CduekLxHQQ(ctx, "play_url_key")`，
# 密钥实际藏在 assets/drawable/x_m.png 中），解密本身是标准 AES-128-ECB/PKCS7，
# 密文为 URL-safe Base64。
# 与 platform.WIN_PLAY_URL_AES_KEY（PC 端）是不同的密钥，不可混用。
PLAY_URL_KEY = "5776f21b9e9911388aacfe448068f16a"

# downloadEncryptVersion == 2 的 `cYWOoJESuO`：Base64 解码后，末尾 16 字节
# 是随密文携带的循环 XOR key；前面的 payload 依次经过 substitution table、
# 32-byte 固定 key XOR、16-byte 动态 key XOR。常量位于 libencrypt.so 的
# 虚拟地址 0x9c980 / 0x9c9a0（文件偏移 0x8c980 / 0x8c9a0）。
DOWNLOAD_V2_XOR_KEY = bytes.fromhex(
    "802246a09acfc6ac4f546b03257e04735a046e0a51540adcc4f1678d95b95f31"
)
DOWNLOAD_V2_SUBSTITUTION = bytes.fromhex(
    "2eb9c9b8b136d3bc3fde7c4ea5b3dcc12c4f7b85bba91b1e549757ad1c4aa70f"
    "88b73ce8a3385e89288fac761d064098326d046ed9525b25eb8d9eae87932105"
    "da3d7ed6724d0366f6f7a0ab3ea8efccbfaf81496333b0ed83ec4362a1fa2a9c"
    "f54126753714cde16c64695f9948e7650e95b44723d5e3085642349f15177819"
    "7f9a1f5ac63b29b6a261d8f2ea44cff1f90bee0c2f531a6baac86fe4167782e0"
    "866a119bdd7a597110ca740024fe84fcd1df399df33a27f413fbc7075dbec47d"
    "c39073352b5179ff0d9692708e91678b5c4601d7e64b80dbcb0930bd60d2f00a"
    "a60255ba20e5e250c22db5cec0f84c4531b2d09412d42268a4c58afd18e98c58"
)

# 两个常量均已用运行时内存 dump 复核：仿真器中 0x9c980 / 0x9c9a0 处的字节与
# 上面的静态取值逐字节一致，SBOX 为真双射（可逆且非恒等置换）。

# ---- liblogin_encrypt.so：登录签名与手机号加密 ----
# 两者都把密码学委托回 Java API，参数由带日志的 JNI 代理层直接录得。

# `aXGGIioVBB`：SHA1(大写(规范串) + SIGN_PREFIX + 盐) 的小写 hex。
# 规范串是 TreeMap 按原始 key 排序后拼成的 "k=v&"，随后整体转大写（键与值都转）。
SIGN_PREFIX = "MOBILE-V1-"
SIGN_SALT_PRODUCT = (
    "PRODUCT-7D74899B338B4F348E2383970CC09991E8E8D8F2BC744EF0BEE94D76D718C089"
)
SIGN_SALT_TEST = (
    "TEST-63B2E1D0E0DD40928342D3D9BC8AC4956F9DD8637BF04853B49F0690FD3BE684"
)

# `wwXLkDFrOu`：RSA/ECB/PKCS1Padding + 标准 Base64。
# 162 字节 X.509 SubjectPublicKeyInfo，1024 位模数，公钥指数 65537。
# 由 JNI 代理层在 `X509EncodedKeySpec.<init>([B)` 处原样录得。
RSA_TRANSFORMATION = "RSA/ECB/PKCS1Padding"
RSA_PUBLIC_KEY_DER = bytes.fromhex(
    "30819f300d06092a864886f70d010101050003818d00308189028181009585a477"
    "3abeecb949701d49762f2dfab9599ba19dfe1e1a2fa200e32e0444f426da528912"
    "d9ea8669515f6f1014c454e1343b97abf7c10fe49d520a6999c66b230e0730c3f8"
    "02d136a892501ff2b13d699b5c7ecbbfef428ac36d3d83a5bd627f18746a7fdc77"
    "4c12a38de2760a3b95c653c10d7eb7f84722976251f649556b0203010001"
)
