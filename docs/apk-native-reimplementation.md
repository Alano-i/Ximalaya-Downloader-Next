# APK native 库纯代码化可行性调查

## 目的

现状：`source_backend=apk` 依赖 `vendor/apk_protocol/` 下四个从喜马拉雅 APK 9.5.1.3
提取的 ARM64 `.so`，由 Java/Unidbg sidecar 仿真执行。两个问题：

1. 仓库包含无法审计、无法二次开发的不透明二进制；
2. 在公开仓库再分发厂商专有二进制存在版权风险。

本文调查：这些 `.so` 的逻辑能否用可审计的纯代码替代。

本项目已有先例——WEB 后端的 `PySignProvider`（`src/xdl/adapters/sign/`）就是纯 Python
重写的签名实现，本调查沿用同一思路。

## 调查方法

四种手段，均不依赖反汇编：

| 手段 | 做法 | 用途 |
|---|---|---|
| JNI 调用追踪 | `-Dxmly.verbose=true`（`NativeSigner.java:62` 已接此开关） | 观测 native 回调 Java 的全部密码学操作 |
| 资产访问追踪 | `AssetManager->open` 钩子打印到 stderr（`NativeSigner.java:260`） | 确定各操作依赖哪些 asset |
| 密码学常量扫描 | 在 `.so` 字节流中搜索已知常量表 | 识别标准算法 |
| 输出稳定性实验 | 同一输入重复调用比对 | 判定密钥/输出是否为静态常量 |

关键前提：sidecar 本身是**完美的参考实现（oracle）**，任何重写都能对任意输入做
逐字节差分验证。这把"猜算法"变成可证伪的工程问题。

## 各库调查结果

### libc++\_shared.so（969 KB）

标准 C++ 运行时，无业务逻辑。纯代码化后**直接不需要**。

### liblogin\_encrypt.so（126 KB）→ 可还原

静态导出 `Java_com_ximalaya_ting_android_loginservice_LoginEncryptUtil_*`。
含 base64 标准字母表与字符串 `kEYfACTORY`。承载两个函数：

**`encryptMobile`（`wwXLkDFrOu`）— 43 次 JNI 调用，19 次密码学相关**

完整链路已由追踪暴露：

```
KeyFactory.getInstance("RSA")
  → X509EncodedKeySpec(<内嵌 DER 字节>)
  → KeyFactory.generatePublic(spec)  → RSAPublicKey
  → getModulus().bitLength()         → 0x400 = 1024 位
  → Cipher.getInstance(<变换串>) → doFinal
```

即 **RSA-1024 公钥加密 + base64**。还原只需提取内嵌公钥 DER 与 Cipher 变换串。
公钥本身按定义即为公开信息。

**`sign`（`aXGGIioVBB`）— 97 次 JNI 调用，仅 7 次密码学相关**

```
MessageDigest.getInstance("SHA-1") → update(<字节>) → digest()
```

Java 侧已知：入参是 `TreeMap` 排序后拼成的 `key=value&` 规范串
（`NativeSigner.java:86-90`）。90 次非密码学调用几乎全是字符串拼接，说明 native
侧的工作是"按规则再加工这个串（很可能拼入固定盐）然后 SHA-1"。

unidbg 默认只打印数组对象引用（`[B@4686afc2`）而非内容。给 JNI 代理层加参数日志
后即可录得确切字节——把 `NativeSigner.java` 复制一份加日志，用 `javac -cp <fat jar>`
编译，再以 `-cp <patched>:<fat jar>` 让同名类遮蔽 jar 内版本即可，**不需要 Maven**。
生成补丁源码与编译产物的脚本见 `.trace/`。

录得结果：

```text
preimage = UPPER(canonical) + "MOBILE-V1-" + <盐>
sign     = SHA1(preimage) 的小写 hex
```

- `canonical` 为 TreeMap 按**原始 key** 排序拼成的 `k=v&`；
- 随后**整体转大写**（键与值都转），这一步对应 `String->toUpperCase()` 钩子；
- `production=true` 用 `PRODUCT-<64位hex>` 盐，`false` 用 `TEST-<64位hex>` 盐，
  两个盐都已录得并存入 `config/apk.py`。

同一次录制也拿到了 `encryptMobile` 的全部材料：`Cipher.getInstance` 参数为
`RSA/ECB/PKCS1Padding`，`X509EncodedKeySpec.<init>([B)` 的 162 字节 DER 即内嵌
公钥（1024 位，e=65537），输出为标准 Base64。

### libencrypt.so（570 KB）→ 主路径可还原

含 MD5/SHA-1 IV、base64 标准字母表，以及字符串 `ReadSignature`（疑似 APK 签名
校验／反篡改，需留意）。

**`decryptDownload` v0/v1 — 已确认可还原**

native 在此路径上**只负责返回一个静态密钥**：`CduekLxHQQ(context, "play_url_key")`，
解密本身是 Java 侧标准 `AES/ECB/PKCS5Padding`（`NativeSigner.java:129-137`）。

实验证实：该密钥**跨调用完全稳定**，长度 32 hex 字符 = **AES-128**。
取密钥时会打开 `assets/drawable/x_m.png`——即密钥藏在这张 PNG 里，`.so` 负责取出。

结论：提取密钥常量一次，之后纯 Python `AES-128-ECB` 即可，运行时不再需要
`libencrypt.so` 与 `x_m.png`。

**`decryptDownload` v2（`cYWOoJESuO`）— 已还原并闭环验证**

入口 `0x263e0` 经 JNI `GetStringUTFChars` 后调用 `0x39808`；实际字节变换在
`0x38efc`。密文是标准或 URL-safe Base64（生产形态无 padding），解码结果的末尾
16 字节是随密文携带的循环 XOR key，前面是 payload：

```text
raw = base64_decode(ciphertext)
payload, dynamic_key = raw[:-16], raw[-16:]
plain[i] = SBOX[payload[i]] ^ FIXED_KEY[i % 32] ^ dynamic_key[i % 16]
```

`FIXED_KEY` 位于虚拟地址 `0x9c980`（32 字节），`SBOX` 位于 `0x9c9a0`
（256 字节置换表）。它不是 AES，也没有 IV、nonce 或 tag；`CduekLxHQQ` 和
`play_url_key` 不在调用路径上。对该变换取逆即可构造密文，4 组 ASCII/URL/中文
样本均由 Python 加密后交给 native 解密，结果逐字节一致。完整证据见
`.trace/codex-findings-v2.md`。

### libnativelib.so（439 KB）→ 已完整还原并验证

导出十余个 `Java_com_ximalaya_xuid_nativelib_NativeLib_*`，相关三个：

| 混淆名 | 作用 | 入参 |
|---|---|---|
| `kCONeLyBJV([String)I` | 初始化 | `[包名, "1.3.15", "9.5.1", apk路径, "Xiaomi", "M2102J2SC"]` |
| `dxbPWlbbFU([String)String` | createXuid | `["U", stableId]` |
| `vDMzsjQFqU([String)String` | ticket | `[attr, xuid]` |

字节扫描命中 **SHA-256 轮常量、MD5/SHA-1 IV、base64url 字母表**。

**`createXuid` 与 `ticket` 各 54 次 JNI 调用中，密码学相关 0 次**——完全在 native
内部计算，不借道 Java API，观测法对本库失效。因此该库的静态分析派发给 codex 独立
执行（任务书 `.trace/codex-task.md`，原始结论 `.trace/codex-findings.md`）。

codex 完成了 ARM64 反汇编，判定**无 VM、无控制流平坦化、无自修改代码、无反调试、
无 APK 签名校验、不依赖设备指纹、不依赖 `na.czl`**，并还原出算法。经本地在生产
输入上复核修正后，**两个算法均已 4/4 逐字节验证通过**。

关键函数：`xuidcc_get_xuid`(`0x1f60c`)、`xuidcc_get_ticket`(`0x210ec`)、
SHA-256(`0x2d164`，标准实现，IV 在 `0x5a9a0`)、`cc_base64_encode`(`0x23030`，
URL-safe 字母表在 `0x5a54c`，无 padding)。库内的 MD5/SHA-1 常量**不在**这两条
路径上，不能因常量存在而推断算法。

#### XUID

```text
prefix   = "X" + <类型字符> + attr        # 真实 32-hex UUID → "XAU"
preimage = prefix + uuid16 + XUID_KEY32
sign8    = SHA256(preimage)[[3,6,9,12,19,22,25,28]]
result   = prefix + base64url_nopad(uuid16 + sign8)
```

#### ticket

```text
prefix   = "T" + <类型字符> + "C"         # 真实 XUID → "TAC"
suffix   = package + "!" + sdk_version + "!" + app_version + "!" + attr.replace("!","_")
ts4      = uint32_be(unix_seconds)
rand16   = uuid_v4_bytes XOR uuid16
preimage = ts4 + uuid16 + rand16 + prefix + TICKET_KEY29 + suffix
sign32   = SHA256(preimage)
result   = prefix + base64url_nopad(ts4 + uuid16 + rand16 + sign32 + suffix)
```

两个内嵌常量（XUID 32 字节 @ `0x5a3dc`、ticket 29 字节 @ `0x5981d`）是全部私有
材料，其余均为标准算法。

#### 一处必须修正的结论

codex 判定前缀恒为 `XIU` / `TIC`。**这是错的**，原因是它唯一能比对的样本
（`.trace/jni-full.log`）恰好是库的 **fallback 输出**——当时的探针传入
`stableId="probe"`、`xuid="X"` 均非法，库返回了内置默认值（其 suffix 里的
`test_!1.0.0_!2.0.0` 即硬编码占位）。

用合法 32-hex stable ID 实测，前缀实为 `XAU` / `TAC`，且**preimage 中嵌入的是
实际前缀而非字面量 `XIU`/`TIC`**。按此修正后，4 组不同 UUID（含全 0、全 ff）的
XUID 与 ticket 签名全部逐字节一致。

教训：用 fallback 路径的样本验证算法会得到自洽但错误的结论；差分测试必须使用
生产形态输入。

## 结论

**六个 native 操作已全部还原**，实现见 `src/xdl/adapters/apk/native_py.py`，
提取常量集中于 `src/xdl/config/apk.py`。

| 库 | 操作 | 状态 |
|---|---|---|
| `libc++_shared.so` | — | 纯代码化后不需要 |
| `libnativelib.so` | `createXuid` / `ticket` | ✅ 已还原，随机 UUID 差分逐字节一致 |
| `liblogin_encrypt.so` | `sign` | ✅ 已还原，差分逐字节一致 |
| `liblogin_encrypt.so` | `encryptMobile` | ✅ 已还原（结构验证，见下） |
| `libencrypt.so` | 解密 v0/v1 | ✅ 已还原，双向验证 |
| `libencrypt.so` | **解密 v2** | ✅ substitution + 两轮循环 XOR，闭环验证 |

实测：`ApkNativeBridge` 的所有生产操作现在均可在 `vendor/apk_protocol/` 路径全部
不存在时运行。**运行时不再需要 Java、asset 或任何 `.so`。**

关于 `encryptMobile` 的验证强度：RSA/ECB/PKCS1Padding 的填充含随机数，密文每次
不同，**与 native 逐字节比对在数学上不可能**。因此该项采用结构验证——公钥 DER
由代理层从 native 原样录得（非推测），模数位数与 native `getModulus().bitLength()`
返回的 0x400 一致，指数 65537，密文长度同为 128 字节。这是缺少私钥时可达到的
最强验证；真正的端到端确认需服务端接受该密文。

## 建议推进顺序

1. **移植 XUID / ticket**（已完成还原）：实现 + 固定向量 + sidecar 大批量差分。
   注意兼容 `xuidcc_parse_xuid` 对 16–36 长度旧格式的宽松处理，以及非法输入的
   fallback 行为是否需要复刻。
2. **提取 RSA 公钥 DER 与 `play_url_key`**，纯 Python 化 `encryptMobile` 与
   `decryptDownload` v0/v1。工作量小。
3. **攻 `sign`**：给 JNI 钩子加参数日志（`javac` + 现成 fat jar，无需 Maven），
   录出 `update()` 入参，反推拼接规则，再用 oracle 差分验证。
4. **v2 解密**（已完成）：静态恢复 `0x38efc`，用逆变换自产密文并经 native 闭环。

每一步都应以差分测试收口：同一批输入，Python 实现与 sidecar 输出必须逐字节一致，
且**测试输入必须是生产形态**（合法 32-hex stable ID、合法 XUID），不能用会触发
fallback 的探针值。

## 本次调查的局限

诚实标注未覆盖之处：

- v2 已用自产合法密文闭环验证，但尚未取得线上响应样本做额外差分；
- `sign` 的 preimage 拼接规则**尚未确定**，只知落到 SHA-1；
- XUID/ticket 的验证覆盖 4 组 UUID，**未做大批量随机差分**，也未验证
  `xuidcc_parse_xuid` 的 16–36 长度旧格式分支；
- ticket 的 `rand16` 依赖 libc `rand()` 序列，本次验证采用"解码字段后重算 sign32"
  的方式，**未复刻 native 的 PRNG 序列**（生产实现也不需要）；
- `assets/na.czl` 在所测六个操作中**从未被打开**；v2 有效密文路径同样零 asset
  访问，且核心 `0x38efc` 不接收 Context；
- 密码学常量扫描中 "RC4/ident tbl" 一项是 `00 01 02 ... 0f`，该序列在任意二进制中
  都极常见，**属噪声，不作为证据**；
- `ReadSignature` 暗示可能存在 APK 签名校验／反篡改，未深入；
- 未评估喜马拉雅升级 APK 版本后这些算法的稳定性。
