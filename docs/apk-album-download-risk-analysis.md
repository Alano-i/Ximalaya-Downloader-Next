# APK 整专下载与风控分析

## 结论

当前实现已经采用了两个重要保护：APK 专辑下载强制单 worker；服务端返回已识别的风控业务码或关键词后，首个 `RiskControlError` 会熔断整批，不再立即逐集重试。

但仓库中没有原 APK、DEX、JADX Java 输出或 Smali，因此没有源码证据能支持“每集间隔 N 秒就安全”或某个固定请求/分钟阈值。本文能确认的是仓库实现的 APK 协议语义、迁移文档记录的 demo 行为，以及本项目自行实现的调度保护。任何运行参数都只能降低风险，不能保证永不触发服务端风控。

## 证据边界

| 证据类别 | 可以确认 | 不能据此确认 |
|---|---|---|
| APK 协议实现 | endpoint、`x-tk` scene、逐集 URL、Cookie/UA 形态 | 官方客户端内部限速常量 |
| 迁移文档中的 demo 记录 | 全局单 worker、连续三集失败暂停、短生命周期 URL | 服务端实际安全阈值 |
| 本项目源码 | 串行、错误分类、熔断、恢复轮询、重试行为 | 平台未公开的账号/设备/IP 风控模型 |

## 整专请求链

一次整专下载的受保护请求按以下顺序发生：

1. 获取专辑授权清单。每页生成 `ticket("b=downloadTrack&s=batch_download&u={uid}")`，作为 `x-tk` 请求 `mobile/download/v1/album/{albumId}/{page}/true/ts-{ms}`。
2. 对每个曲目重新生成 `ticket("b=downloadTrack&s=download&u={uid}")`，请求 `mobile/download/v2/track/{trackId}/ts-{ms}`，传入 `trackQualityLevel` 和 `device=android`。
3. 使用返回的明文 URL，或按 `downloadEncryptVersion` 解密本集 URL。
4. 立即下载媒体，CDN 请求带 `requestType: download`。URL 不跨集复用；`.part` 恢复也先重新解析。CDN 返回 `401/403/404` 时只刷新一次本集 URL。

因此，一个有 `P` 页、下载 `N` 集且没有 URL 失效的专辑，至少会产生 `P` 次清单请求、`N` 次单集授权/URL 请求和 `N` 次媒体请求。URL 失效会额外增加单集授权/URL 请求。

## 已实现的保护

### APK 强制串行

`ApkSource.max_consecutive_failures` 默认是 3，`DownloadAlbumUseCase` 检测到该能力后把并发强制设为 1。即使命令行传入更大的 `--concurrency`，默认 APK 配置下仍是单 worker。

单 worker 会等当前媒体文件下载完成后才开始下一集，所以大多数情况下媒体传输时间会自然隔开相邻两次单集授权请求。每个 worker 开始前还有 `0–0.3s` 随机错峰，但它不是完整的速率限制；文件很小或网络很快时，两次授权请求仍可能非常接近。

### 业务风控信号立即熔断

以下响应会映射为 `RiskControlError`：`ret` 为 `1001`、`3005` 或 `31009`，或者 `msg` 包含“繁忙”“频繁”“验证码”。单任务即时重试明确排除 `RiskControlError`。整专批次遇到首个明确风控信号后，后续项不再访问受保护接口，并保留为可恢复任务。

### 可选的低频恢复探针

`--risk-poll` 默认关闭。开启后，默认先等待 30 秒，然后按 2 倍指数退避；单次等待最多 900 秒、总时长默认 3600 秒。等待期间零请求，每轮只用一个真实待下载曲目探测一次。

该机制比立即 `resume` 温和，但默认 30 秒不是 APK 官方常量。保守运行时可将首次等待提高到 60–120 秒；这仍是工程建议，不是“安全阈值”。

## 仍存在的缺口

### P0：下载 API 的 HTTP 429 没有进入风控熔断

`ApkClient._request()` 在 `response.raise_for_status()` 后把所有 `requests.RequestException` 统一包装为 `NetworkError`。因此 HTTP 429 不会成为 `RiskControlError`，也没有读取 `Retry-After`；上层会按普通网络错误进行最多 3 次即时重试，等待约为 1.5 秒、3 秒并附加抖动。

源码中另有一个 `ret=429`，但它只表示短信发送的本地 180 秒冷却，不能保护专辑下载请求。

### P0：CDN HTTP 429 也被当成普通网络错误

`ApkMediaSink` 仅对 `401/403/404` 做 URL 刷新。其他 HTTP 状态（包括 429）都转换为 `NetworkError`。这会使上层可能重新解析 URL 并按普通网络策略重试，而不是立即熔断整批。

### P1：专辑分页没有间隔

`ApkSource._get_album_sync()` 会连续请求第 1 页到最后一页，中间没有 sleep、jitter 或 `Retry-After` 处理。大专辑会在实际媒体下载前集中请求多个受保护清单页。

### P1：没有成功曲目之间的最小授权请求间隔

当前只有 worker 启动前的 `0–0.3s` jitter，并依赖媒体下载时间自然节流。没有以“下一次 `track_download_info` 开始时间”为基准的 `min_track_interval`。

### P1：APK 风控观测尚未接入实际 Source

当前 `ApkSource` 不接受 `risk_recorder`，`composition.py` 构建 APK Source 时也没有传入 recorder。因此 `risk-report` 目前不能可靠给出 APK 清单页、逐集授权请求的时间线。工作区中的新增测试已经描述了期望行为，但对应生产实现尚未存在，这两个测试当前会失败。

### P2：设备档案完整性无法验证

实现保持稳定的 `device_id`、`xuid`、uid/token 和 Android UA/Cookie 档案，这比频繁轮换身份更接近正常客户端行为。但 Cookie 中 `qimei36` 和 `XIM` 为空；由于仓库没有原 APK Java/Smali，无法确认 9.5.1.3 正式客户端是否还会注入其他设备字段，也无法证明这些空字段是否参与风控。

## 当前最稳妥的运行方式

保持 APK 默认单 worker，不增大并发：

```bash
xdl --source-backend apk --concurrency 1 album <albumId-or-url>
```

建议按以下方式操作：

1. 保持同一账号、`device_id`、`xuid` 和登录态稳定，不频繁切换设备身份、账号、IP 或反复登录。
2. 先按 `1 → 3 → 小批量` 做授权内容的低频验收，使用 `--range` 分段；例如先 `--range 1`、再 `--range 2-4`。迁移文档也采用这一验收顺序。
3. 大专辑可保守地按 10–30 集分段，并在批次间留出观察窗口。这是缺少真实阈值时的工程策略，不是 APK 源码规定。
4. 一旦出现“繁忙/频繁/验证码”、`ret=1001/3005/31009`，停止当前批次，不要立即反复 `resume`。
5. 若确需自动恢复，使用较长首次等待，例如：

   ```bash
   xdl --source-backend apk --concurrency 1 \
     --risk-poll --risk-poll-initial-wait 120 \
     --risk-poll-max-duration 3600 album <albumId-or-url>
   ```

6. 在 APK 风控观测真正接入前，不要用 `xdl risk-report` 推断 APK 的安全频率；仓库目前没有真实 APK 风控事件样本。

## 建议改进顺序

1. 将 API 和 CDN 的 HTTP 429 映射为 `RiskControlError`，解析 `Retry-After` 并立即熔断整批。
2. 为专辑分页增加可配置间隔和 jitter。
3. 增加 APK 专属 `min_track_interval`，以相邻两次授权/URL 请求的开始时间限速，而不是只在 worker 启动前随机等待。
4. 自动恢复优先遵循服务端 `Retry-After`，缺失时再使用指数退避。
5. 风控事件补充 endpoint kind、HTTP status 和脱敏后的 retry-after；继续禁止记录 URL、Cookie、token 和完整 header。
6. 把 `RiskEventRecorder` 接入 `ApkSource` 和 `composition.py`，覆盖清单页、逐集解析、URL 刷新和风控结果。
7. 使用授权账号按 1 曲、3 曲、小专辑顺序采集本地观测，再根据数据选择默认间隔。

## 源码索引

- `src/xdl/adapters/apk/client.py:35`：业务响应风控分类。
- `src/xdl/adapters/apk/client.py:75`：稳定设备档案和 Cookie/UA。
- `src/xdl/adapters/apk/client.py:112`：HTTP 错误统一包装为 `NetworkError`。
- `src/xdl/adapters/apk/client.py:259`：按 scene 和 uid 生成 ticket。
- `src/xdl/adapters/apk/client.py:265`：专辑分页授权清单。
- `src/xdl/adapters/apk/client.py:282`：逐集下载信息。
- `src/xdl/adapters/apk/source.py:46`：连续专辑分页，且当前未接风险 recorder。
- `src/xdl/adapters/apk_sink.py:17`：CDN header、URL 单次刷新和 HTTP 分类。
- `src/xdl/application/usecases.py:76`：风控恢复轮询参数。
- `src/xdl/application/usecases.py:360`：APK 强制串行。
- `src/xdl/application/usecases.py:499`：整批风控熔断和可选恢复。
- `src/xdl/application/usecases.py:562`：`0–0.3s` jitter、首个风控信号和连续失败停止。
- `docs/apk-protocol-migration.md:114`：迁移记录中的协议链和短生命周期 URL。
- `docs/apk-protocol-migration.md:369`：真实小样本验收顺序。
