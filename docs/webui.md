# WebUI 使用与接口

WebUI 是 XDL 的本机图形入口，复用 CLI 的同一套 `Settings`、`Facade`、任务库和下载目录，不维护第二份业务状态。

## 启动

```bash
xdl web
# 等价入口
xdl-web
```

默认地址为 `http://127.0.0.1:8787`，启动后自动打开浏览器。可用 `--no-open` 禁止自动打开，或用 `--port` 修改端口。

WebUI 没有远程访问认证，默认只监听回环地址。请勿用 `--host 0.0.0.0` 直接暴露到公网。

## 功能

- 登录与登出，并显示当前登录态。登录方式随「音源后端」变化：`http`/`pc`/`chrome` 走专用浏览器 Profile（Chrome 或 Edge）交互登录；`apk` 走 GeeTest 4 + 短信验证码或账号密码，支持多账号切换与删除。
- 下载单曲、整张专辑或指定序号区间，选择 `high`、`standard`、`low` 音质。
- 从 SQLite 分页展示进行中、待恢复、完成和失败任务，支持状态筛选、按专辑筛选、服务端搜索和打开对应目录。任务库很大时，页面仍只渲染当前的 100 条记录。
- 批量选择任务后删除，或重新加入恢复队列；删除前可先预览受影响的任务。选择集可跨分页按当前筛选条件取全量 ID。
- 恢复未完成任务；运行中的下载可请求优雅停止，进度保留到任务库和 `.part` 文件。
- 查看完全离线的风控报告，探测曲目可用格式。
- 刷新登录 Cookie、检查浏览器存储 key、生成签名和采集设备信息。
- 编辑下载、重试、路径与浏览器、音源后端和实验功能设置。设置页的「浏览器」决定登录与采集所用浏览器（自动 / Chrome / Edge）；切换后仍为自动值的浏览器路径与 Profile 目录会跟随重新探测，自定义路径保持不变，且需重新登录。「音源后端」在 `http` / `pc` / `chrome` / `apk` 间切换，改动会整套换掉登录体系，因此保存时会一并回传新后端的登录态。

所有长操作共用一个运行槽。已有操作执行时，新的下载或诊断请求会返回冲突提示；设置也只能在空闲时保存。这一约束与底层音源会话和任务库的生命周期一致。

实验功能区可设置换身冷却时间 `experiment_risk_cooldown_seconds`（默认 15 秒，设为 0 表示不等待），以及换身采集是否使用无头浏览器 `experiment_rotate_headless`（默认关闭，即强制显示浏览器窗口）。这两项只影响已开启的“风控后尝试刷新设备身份”实验，不改变登录或内容授权。

## JSON API

交互式接口文档位于 `/api/docs`。主要端点如下：

| 方法 | 路径 | 用途 |
|---|---|---|
| `GET` | `/api/health` | 存活探测，恒定返回 `{"ok": true}` |
| `GET` | `/api/bootstrap` | 设置、登录态、首个任务页和当前操作的轻量首屏快照 |
| `GET` | `/api/operation` | 当前/最近一次长操作；默认轻量快照，`include_result=true` 按需返回结果 |
| `GET` | `/api/risk-report` | 本地风控日志汇总 |
| `PUT` | `/api/settings` | 校验、保存设置并重建运行器；回传新设置与当前后端的登录态 |
| `POST` | `/api/open-downloads` | 在系统文件管理器中打开下载目录或某个任务所在目录 |

任务库：

| 方法 | 路径 | 用途 |
|---|---|---|
| `GET` | `/api/tasks` | 分页任务列表与全局状态计数；支持 `state`、`search`、`album_id`、`limit`、`offset` |
| `GET` | `/api/tasks/ids` | 按当前筛选条件取全量任务 ID，供跨分页全选 |
| `POST` | `/api/tasks/preview` | 预览给定 ID 的任务，用于删除前确认 |
| `POST` | `/api/tasks/delete` | 批量删除任务 |
| `POST` | `/api/tasks/requeue` | 批量重新加入恢复队列 |

登录态。`/api/operations/login` 用于浏览器交互登录（`switch_account=true` 表示换号）；`apk-auth` 一族仅在 `source_backend=apk` 时可用：

| 方法 | 路径 | 用途 |
|---|---|---|
| `POST` | `/api/operations/login` | 启动浏览器交互登录 |
| `POST` | `/api/auth/logout` | 清除当前后端的登录态 |
| `GET` | `/api/apk-auth/status` | APK 登录态与已保存账号列表 |
| `GET` | `/api/apk-auth/config` | 前端渲染 GeeTest 4 所需的参数 |
| `POST` | `/api/apk-auth/sms` | 发送短信验证码（附 GeeTest 结果） |
| `POST` | `/api/apk-auth/verify` | 提交短信验证码完成登录 |
| `POST` | `/api/apk-auth/password` | 账号密码登录 |
| `POST` | `/api/apk-auth/logout` | 登出当前 APK 账号 |
| `POST` | `/api/apk-auth/switch` | 切换到已保存的其他 APK 账号 |
| `POST` | `/api/apk-auth/accounts/delete` | 删除已保存的 APK 账号 |

长操作（返回 `202 Accepted`，共用同一个运行槽）：

| 方法 | 路径 | 用途 |
|---|---|---|
| `POST` | `/api/operations/download` | 启动单曲或专辑下载 |
| `POST` | `/api/operations/resume` | 恢复未完成任务 |
| `POST` | `/api/operations/stop` | 请求当前下载优雅停止 |
| `POST` | `/api/operations/formats` | 探测曲目音质格式 |
| `POST` | `/api/operations/gen-sign` | 生成签名用于本地链路检查 |
| `POST` | `/api/operations/refresh-cookies` | 从 Profile 刷新已登录 Cookie |
| `POST` | `/api/operations/inspect-storage` | 列出设备标识相关 storage key |
| `POST` | `/api/operations/extract-device` | 采集设备信息 |

前端在操作运行时较快刷新状态，空闲时自动降频；任务页只在到期或操作状态变化时刷新，页面进入后台后完全暂停轮询，回到前台再立即同步。任务内容未变化时不会重建表格，操作的大结果也只在终态读取一次。业务错误会返回结构化 `detail`；已有操作占用运行槽时返回 `409 Conflict`。

## 本地数据

WebUI 使用与 CLI 相同的 `~/.xdl` 数据目录，也支持 `XDL_HOME` 覆盖。设置写入 `webui-settings.json`；Cookie、任务、风控日志等路径和敏感性说明见项目 README。前端不会把 Cookie、播放 URL 或设备信息内容返回给浏览器。
