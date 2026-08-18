<div align="center">

# Ximalaya-Downloader-Next

**喜马拉雅音频下载器 · 重启版**

![status](https://img.shields.io/badge/status-WIP-orange)
![python](https://img.shields.io/badge/python-3.10+-blue)
![license](https://img.shields.io/badge/license-AGPL--3.0-blue)

</div>

下载**你有权访问的**喜马拉雅内容。支持单曲、整张专辑、区间下载、断点续传、失败重试和任务恢复，提供 WebUI、CLI 和 Python API 三种用法。

> 本工具不破解付费内容、不绕过登录与授权。能不能下载取决于你的账号权限。

## 快速开始

需要 Python 3.10+，以及 Google Chrome 或 Microsoft Edge（用于登录；只用 APK 后端时不需要浏览器）。

```bash
pip install -e .
xdl web
```

浏览器会自动打开 `http://127.0.0.1:8787`。点击页面顶部的「尚未登录」完成登录，之后就能新建下载、选音质与区间、恢复或删除任务、查看风控报告和调整设置——常规使用不需要碰命令行。

也可以纯用 CLI：

```bash
xdl login                      # 首次登录（浏览器打开，按终端提示确认）
xdl track <链接或 trackId>      # 下载单个音频
xdl album <链接或 albumId>      # 下载整张专辑
```

默认下载到当前目录下的 `downloads`。

## 常用命令

```bash
xdl web                                # 启动本地 WebUI
xdl login                              # 登录 / 重新登录
xdl logout                             # 清除本机登录凭据（换号：先登出再登录）
xdl track <链接或ID>                    # 下载单个音频
xdl track -F <链接或ID>                 # 只列出可用音质格式，不下载
xdl album <链接或ID>                    # 下载整张专辑
xdl album <链接或ID> --range 1-20       # 只下载指定区间（1-20 / 5- / -10 / 7）
xdl album <链接或ID> --quality high     # high / standard / low
xdl resume                             # 继续上次未完成的下载
xdl risk-report                        # 汇总本地风控记录（离线，不发请求）
xdl gen-sign                           # 自检签名链路
```

全局选项必须写在子命令**之前**：

```bash
xdl --download-dir D:\Audio album <链接或ID>   # 指定下载目录
xdl --concurrency 3 album <链接或ID>           # 并发数（默认 1）
xdl --browser edge login                      # 指定浏览器
xdl --source-backend pc album <链接或ID>       # 指定音源后端
```

## 音源后端

同一份下载/恢复逻辑下有四条取播放地址的路径，用 `--source-backend` 或 WebUI 设置页的「音源后端」切换：

| 后端 | 说明 |
|---|---|
| `http` | **默认**。本地生成 `xm-sign` 后走网页端接口，无需浏览器常驻 |
| `pc` | 桌面客户端接口，纯 HTTP。批量下载解析更稳定，支持 VIP 音频 |
| `apk` | Android APK 协议。身份完全独立，用短信验证码登录 |
| `chrome` | 浏览器/CDP 兼容路径，只建议在前几种都不可用时临时排查 |

`http`、`pc`、`chrome` 共用 `xdl login` 保存的同一份浏览器会话；`apk` 有自己的登录与身份，两边互不影响。

APK 后端的登录在 WebUI 里完成最省事：设置页把「音源后端」改成「Android APK 协议」并保存，然后点顶部登录状态，按提示过图形验证与短信验证码。该后端**不需要 Java，也不加载任何 `.so`**，所有算法都是 `src/xdl/adapters/apk/native_py.py` 里的纯 Python 实现，常量集中在 `src/xdl/config/apk.py` 供审计。

### 浏览器选择

默认自动探测，Chrome 优先、没装 Chrome 时用 Edge，无需配置。两个都装了又想指定：`xdl --browser edge login`。

每个浏览器的登录态、Cookie 和设备指纹各自独立保存，互不覆盖。换浏览器需要重新登录一次，原浏览器的登录态会完整保留，切回即可恢复。

## 下载行为

- 已存在的完整文件跳过；未完成的 `.part` 文件按 HTTP Range 续传。
- 音质缺失时自动回退到可用规格。
- 下载中按 `Ctrl-C` 会保存进度并优雅退出，之后 `xdl resume` 接着下。
- 专辑下载和恢复默认 **1 个并发**。提高 `--concurrency` 会同时放大请求量，更容易触发平台风控。
- 遇到已识别的风控信号会停下整批，同一批次只提示一次，其余项目保留待恢复。
- 加 `--risk-poll`（默认关闭）可在风控后自动等待并低频探测，解除后按原并发继续；超时则回落为等待人工 `xdl resume`。等待参数见 `--risk-poll-initial-wait`、`--risk-poll-max-duration`，WebUI 在「风控自动恢复」区可配置。

WebUI 默认只监听本机回环地址，**没有远程访问认证**，不要直接暴露到公网。

## 本地数据

默认位于 `~/.xdl`，可用环境变量 `XDL_HOME` 改根目录。

| 路径 | 用途 |
|---|---|
| `{chrome,edge}-profile/` | 专用浏览器登录会话 |
| `{chrome,edge}-cookies.json` | 登录 Cookie 缓存，**敏感数据** |
| `{chrome,edge}-device-info.json` | 设备信息；不存在时用包内模板 |
| `apk/` | APK 后端的独立设备身份与登录态（`accounts.json` 权限 `0600`） |
| `tasks.db` | 下载任务、进度和恢复状态 |
| `risk-events.jsonl` | 最小化请求结果观测（供 `risk-report`），不含 Cookie 或播放 URL |

Profile、Cookie 缓存和设备信息共同构成**一份身份**，必须同源于同一个浏览器，所以统一按浏览器分文件保存。从旧版本升级时会自动迁移，登录态不受影响。

## Python API

```python
from xdl import Facade

app = Facade.from_config()
app.download_track("<链接或ID>", quality="standard")
app.download_album("<链接或ID>", quality="standard", range_="1-20")
app.resume()
```

`Facade` 是同步接口，内部负责异步音源与任务生命周期。

## 开发与验证

```bash
pip install -e '.[dev]'
python -m pytest -q
```

测试默认使用替身，不访问真实登录态或平台接口。离线测试通过不等于真实平台验收通过。

更多文档：

- [项目现状与范围](./docs/overview.md)
- [架构设计](./docs/architecture.md)
- [WebUI 使用与接口](./docs/webui.md)

## 免责声明与许可证

本项目仅供学习研究。请遵守平台服务条款和相关法律法规，尊重内容创作者版权，勿用于侵权或商业用途。使用本工具产生的后果由使用者自行承担。

[AGPL-3.0](./LICENSE)
