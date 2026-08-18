# vendor/apk_protocol — APK 协议 native 资产（**不随仓库分发**）

本目录在版本库里是空的，只有这份说明。

## 正常使用不需要这里的任何文件

`source_backend=apk` 的下载链路默认走 `src/xdl/adapters/apk/native_py.py` 的
纯 Python 实现：XUID、ticket（x-tk）、登录签名、手机号加密、
downloadEncryptVersion 0/1/2 解密全部是标准算法 + 已提取的常量
（见 `src/xdl/config/apk.py`），不依赖 Java，也不加载任何 `.so`。

`ApkNativeBridge` 默认 `prefer_python=True`，此时 `requires_sidecar()` 返回
False，`ApkClient.open()` 不会启动 sidecar，本目录一次都不会被读取。

## 什么时候才需要填充本目录

只有一种场景：把 `prefer_python=False`，用官方 native 库跑**差分测试**，验证纯
Python 实现与 `.so` 的输出逐字节一致（`tests/test_apk_native_py.py` 末尾的
`test_differential_against_sidecar`；缺少这些文件时该用例自动跳过）。

这属于协议升级时的验证手段，不是运行时依赖。

## 如何自行准备

从你本地已授权安装的喜马拉雅 Android APK **9.5.1.3 (arm64-v8a)** 中提取，
放入本目录：

| 文件 | APK 内路径 | SHA-256 |
|---|---|---|
| `libc++_shared.so` | `lib/arm64-v8a/` | `3e6d529147ffd66bd31fab85c8902b526abd1c06e43554ce64b97519e255e4b3` |
| `libencrypt.so` | `lib/arm64-v8a/` | `06bb61e6c55f42c79fddd5460dc3d7bad899fb598320d52df468205b3f527e44` |
| `liblogin_encrypt.so` | `lib/arm64-v8a/` | `f7cc584fccf5341557c89ad009ab4f0c4e10c713501d61d2f697c97889aa90be` |
| `libnativelib.so` | `lib/arm64-v8a/` | `d80478260a9f6f3adb9f0916999501e1d52cf7c58506df0577b90965ff860ead` |
| `assets/na.czl` | `assets/` | `e3302e023616e1a4df27662b882b2d11fb28271bd12907d944a3deda38e51832` |
| `assets/drawable/x_m.png` | `res/drawable/`（或 `assets/`，随打包而异） | `74970b84f6e92442aa13fb31a00c9e3ea762f4f0d28fbea60f2febf5f1df50b4` |

这些文件版权归上海喜马拉雅科技有限公司所有，本仓库不再分发、不主张任何权利。
上表哈希只用于版本比对，确认你提取的是同一版本。

`native-signer.jar` 由本仓库 `native_signer/` 源码 `mvn package` 构建
（Apache-2.0 / MIT，见 `native_signer/README.md`）——它是构建产物，同样不入库。

准备好后，用 `Settings.apk_*` 字段指向它们，并放一份 `manifest.json`
（格式见 `native_signer/README.md`，`files` 键按上表文件名/相对路径记录
SHA-256）。`ApkNativeBridge.open()` 启动前逐项校验：**缺清单或指纹不符都会
抛 `ConfigError` 拒绝启动**——清单不是可选的，否则校验等于没有。

## 运行要求（仅差分测试）

- Java 17 或更高版本（`Settings.apk_java_path` 可指定具体路径）
- Windows / macOS / Linux 均由 Unidbg 仿真 ARM64，不要求宿主为 ARM 架构
