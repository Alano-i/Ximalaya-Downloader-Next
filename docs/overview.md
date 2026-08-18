# 项目现状与范围

Ximalaya-Downloader-Next 是一个面向个人授权内容的喜马拉雅音频下载工具，当前提供 WebUI、CLI 和 Python API。项目仍处于开发阶段，默认使用本地 Python `xm-sign` + HTTP 音源链路。

四个可选音源后端（`Settings.source_backend` / `--source-backend`）：

| 值 | 说明 |
|---|---|
| `http` | 默认。本地 `xm-sign` + 网页端 `baseInfo` |
| `pc` | 桌面客户端接口，纯 HTTP、不启动浏览器，支持 VIP 音频 |
| `chrome` | 浏览器/CDP 兼容路径，主要用于登录与诊断 |
| `apk` | Android APK 9.5.1.3 协议，身份与浏览器完全隔离 |

`http`、`pc`、`chrome` 共用 `xdl login` 保存的同一份浏览器身份；`apk` 用自己的短信登录与 `~/.xdl/apk/`。

安装和命令用法见 [README](../README.md)，内部结构见 [架构设计](./architecture.md)。

## 当前主流程

### 首次登录

```text
xdl login
  → 打开专用浏览器（Chrome 或 Edge，自动探测或 --browser 指定）
  → 用户完成登录
  → CDP 确认 token 并在活动上下文中捕获 Cookie
  → 正常关闭浏览器并验证 Cookie 已落盘
  → 将已捕获 Cookie 原子保存到 ~/.xdl/{browser}-cookies.json
  → 若该浏览器的设备信息尚不存在，从同一 Profile 只读采集一次
```

登录流程不会把“用户按了回车”当作成功。只有活动上下文已捕获登录 token、且专用 Profile 中的 Cookie 已确认持久化，命令才会成功返回；保存缓存时不会再次启动浏览器。两个浏览器的身份三件套（Profile、Cookie 缓存、设备信息）分别保存，互不覆盖；切换浏览器需重新登录一次，原浏览器的登录态完整保留。

### 下载

```text
WebUI / CLI / Python API
  → Facade
  → 下载用例与任务引擎
  → HttpSource
      ├─ PySignProvider：本地组装载荷并取得 cadd/sid
      ├─ Cookie 缓存：提供登录态
      └─ baseInfo：取得播放信息
  → Www2Decoder
  → FileSink + SqliteTaskStore
```

下载阶段命中新鲜 Cookie 缓存时不会启动浏览器；缓存失效时会短暂打开专用 Profile 读取已持久化 Cookie，但不会用 CDP 获取播放信息。专辑清单走公开、免签的非 v1 接口；逐集播放信息才使用登录 Cookie 和 `xm-sign`。

## 已实现能力

- 单曲、整张专辑和区间下载。
- `high`、`standard`、`low` 音质选择与缺失回退。
- 文件存在跳过、`.part` 字节级续传、SQLite 任务级恢复。
- `Ctrl-C` 触发优雅停止。
- 网络、签名、鉴权、API、风控和存储错误分类。
- 有界任务调度、失败退避和失败收尾轮。
- CLI 可通过 `--concurrency N` 调整专辑下载与恢复的异步并发数，默认保持 `1`。
- 最小化风控事件记录与离线 `risk-report`。
- 本地 Python `xm-sign` 实现及离线契约测试。
- `xdl track -F` 列出全部可用音质格式，只读播放元数据、不下载。
- PC 桌面端后端：纯 HTTP 无浏览器参与，付费/VIP 曲目走 `baseInfo` 加密 `playUrlList` 解密。
- Android APK 协议后端：独立短信登录与多账号切换，native 算法纯 Python 实现，运行时不需要 Java 或 `.so`。
- Chrome/CDP 音源兼容后端（接管的浏览器跟随 `--browser` 设置，Chrome 或 Edge）。
- 可选的设备信息刷新实验：识别到风控后可刷新本地设备信息并重试当前曲（默认关闭，不保证有效）。
- 本机 WebUI：下载任务、登录/登出、任务恢复与删除、音质探测、风控报告、设备/Cookie 诊断和完整运行设置。

## 明确限制

- `xm-sign` 不是登录 token，也不代表内容授权。
- 签名端点和 `baseInfo` 都是平台相关接口，可能随时变化。
- 当前自动化测试不会向真实平台发请求，因此只能证明本地算法、载荷和解析契约，不能证明线上持续可用。
- 历史 CDP 音源在特定环境下出现过验证码以及 `1001` / `3005`；它已降级为兼容路径。
- 设备信息刷新实验不能替代登录、内容授权或服务端风控判断，也不保证一定可继续下载。
- APK 协议常量绑定 9.5.1.3 版本，平台改版后需要重新提取；官方 native 库不随仓库分发，只在协议升级做差分验证时才需要自行准备。
- 三条链路的请求额度各自独立，任何后端都只能降低触发风控的概率，不能保证不触发。
- 当前没有内容搜索、桌面壳、单文件可执行程序或自动更新。

## 仓库结构

```text
src/xdl/
├─ domain/               领域模型、音质、区间与命名规则
├─ ports/                Source、SignProvider、Decoder、Sink、Store 等协议
├─ application/          Facade、下载/恢复用例与重试调度
├─ adapters/
│  ├─ sign/              xm-sign、Cookie 与设备信息适配器
│  ├─ apk/               APK 协议：客户端、纯 Python native 算法、身份状态
│  ├─ source_http.py     默认 HTTP 音源
│  ├─ source_pc.py       PC 桌面端音源
│  ├─ source_chrome.py   Chrome/CDP 兼容音源与登录实现
│  ├─ decoder.py         播放地址解码（网页端 / PC 端）
│  ├─ sink_file.py       文件下载与续传
│  ├─ apk_sink.py        APK 下载连接的按集重新解析
│  └─ store_sqlite.py    任务持久化
├─ config/               平台常量、签名常量、APK 提取常量和用户数据路径
├─ frontends/            CLI、Web API/运行器与静态前端
├─ composition.py        装配根
├─ risk.py               风控事件与离线汇总
└─ settings.py           运行设置
```

## 下一阶段

优先级按合并后的真实维护价值排序：

1. 用低频、授权的真实样本分别验证 HTTP、PC 与 APK 三个后端，并把结果记录为环境相关证据。
2. 把目前保留的高级诊断与实验性设备信息入口迁出普通运行路径，经过弃用周期后再删除或收敛公开入口。
3. 消除 `Facade.from_config` 与装配根之间的延迟导入环，并补充静态类型检查。
4. 为发布增加 CI、覆盖率报告、锁定的依赖测试矩阵和可安装包验证。
5. 在现有 WebUI 之上评估内容搜索、增量同步和桌面封装。
