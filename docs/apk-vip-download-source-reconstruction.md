# APK 9.5.1.3 VIP 权限与下载逻辑还原

## 结论

已从用户提供的 `fm-9-5-1-3.apk` 恢复关键原类和调用链。APK SHA-256 为：

```text
8cd0bd691c6e53708428c1c3d42b7f2d12092411b43efb63da9f0ff458cc224e
```

当前可以确认：

1. `com.ximalaya.ting.android.host.manager.account.o.c()` = 已登录（登录模型存在且 token 非空）。
2. `o.e()` = 当前 uid，未登录时为 `0`。
3. `o.i()` = `LoginInfoModelNew.isVip()`。
4. APK 的 `canDownLoad` 是本地候选过滤；真正的 URL 与额度仍由服务端接口决定。
5. `canDownloadCount` 是当前 20/40 集选集中的可下载数，不是账号剩余额度。
6. APK 还有一套独立的非 VIP 激励广告下载次数 `remainTimes`；VIP 明确绕过它。
7. 下载原类中没有“账号每日 4000 集”本地计数器、日期键或重置任务。该阈值如属实，是服务端的另一层账号额度。

## 已恢复的原类

| 原类 | 用途 | 本地还原文件 |
|---|---|---|
| `com.ximalaya.ting.android.host.manager.account.o` | 登录、uid、VIP 状态 | `work/apk-vip-download-auth/jadx-exact/AccountManagerO.java` |
| `com.ximalaya.ting.android.main.manager.VipDownloadManager` | VIP 连续批量下载 | `work/apk-vip-download-auth/jadx-exact/VipDownloadManager.java` |
| `com.ximalaya.ting.android.host.manager.download.DownloadService` | 单曲/批量入队与非 VIP 扣次 | `work/apk-vip-download-auth/jadx-exact/DownloadService.java` |
| `com.ximalaya.ting.android.host.manager.download.j` | `DownloadIncentiveAdManager` 混淆类 | `work/apk-vip-download-auth/jadx-exact/DownloadIncentiveAdManager.java` |
| `com.ximalaya.ting.android.host.manager.request.CommonRequestM` | 请求方法与响应解析 | `work/apk-vip-download-auth/jadx-exact/CommonRequestM.java` |
| `com.ximalaya.ting.android.host.util.e.g` | URL 常量 | `work/apk-vip-download-auth/jadx-exact/UrlConstantsG.java` |
| `DownloadTrackDlgFragment` | 20/40/剩余全部选集 | `work/apk-vip-download-auth/jadx-dex4/sources/.../DownloadTrackDlgFragment.java` |
| `BatchDownloadFragmentNew` | V2 批量下载页 | `work/apk-vip-download-auth/jadx-dex4/sources/.../BatchDownloadFragmentNew.java` |
| `Track` | 曲目权限与下载字段 | `work/apk-vip-download-auth/jadx-dex7/sources/.../Track.java` |

`work/apk-vip-download-auth/reconstructed/ApkVipDownloadLogic.java` 是在拿到 APK 前写的证据等价模型，仍保留作为协议层参照，不是 JADX 原类。

## VIP 和逐曲可下载判断

`VipDownloadManager.canDownLoad` 的原始控制流如下（只将混淆方法名换成语义名）：

```java
boolean canDownLoad(Track track, long albumUid) {
    if (track == null) return false;
    if (Account.loggedIn() && albumUid == Account.uid()) return true;
    if (track.canNotDownload()) return false;
    if (!Account.isVip() && track.vipPriorListenStatus == 1) return false;

    boolean ximiAllowed = track.isAuthorized()
            || !track.isXimiTrack
            || track.ximiAuthorized;
    return ximiAllowed
            && !downloadService.alreadyExists(track)
            && track.isHasCopyRight();
}
```

`Track` 原类又定义：

```java
boolean isAuthorized() {
    return authorized
        || (getExpireTime() > 0 && getExpireTime() > System.currentTimeMillis());
}

boolean canNotDownload() {
    return isPaid && !authorized && !free;
}
```

注意 `canNotDownload()` 读的是原始 `authorized` 字段，而后续 `isAuthorized()` 还承认未过期的临时权限。这是 APK 原始实现的字段差异，不应在还原时擅自合并。

APK 还有一个独立的离线内容播放权检查 `DownloadedContentRightManager`。它请求 `mobile/download/track/play/check` 并解析 `DownloadTrackCheckModel(canPlay, vipCategory, vipSpuId, masterVip)`：`canPlay=true` 直接放行；`masterVip=true` 引导购买大会员；否则根据 `vipSpuId + vipCategory` 引导购买对应 VIP。它判断的是已下载内容能否继续播放，不是 4000/日下载额度。

## `canDownloadCount` 的确切含义

`DownloadTrackDlgFragment` 首先请求最多 40 条候选数据，然后：

- 对前 20 集逐曲调用 `canDownLoad`，通过数写入第一个 `BatchTrackModel.canDownloadCount`。
- 对前 40 集做同样统计，写入第二个模型。
- `vipCount` 统计 `vipPriorListenStatus == 1` 或特定付费未授权的 VIP 可解锁数。
- UI 用这两个数决定是否展示 VIP 弹窗和是否允许提交。

因此 `canDownloadCount` 只是“本次选中的候选里有多少条通过本地过滤”，没有 uid、日期或扣减行为。

## 批量下载调用链

### V2 批量页

```text
BatchDownloadFragmentNew
  -> GET mobile/download/v2/album/{albumId}/{pageId}/{isAsc}/ts-{ms}
     pageSize=200, device=android, trackQualityLevel=...
  -> AlbumM.parseTracks(...)
  -> 用户选集
  -> 对选中 Track 设置 antiLeech
  -> IDownloadService.a(List<Track>, callback)
  -> DownloadService
  -> 入队并启动下载任务
```

### 快捷 20/40/剩余全部

```text
DownloadTrackDlgFragment
  -> GET mobile/download/rest/{albumId}/{orderNo}/{isAsc}/{size}/ts-{ms}
     size=40 用于预览，size=100 用于连续分批
  -> 解析 data.album + data.tracks
  -> canDownLoad 逐曲过滤
  -> IDownloadService.a(List<Track>, callback)
```

`VipDownloadManager` 保存“剩余全部”的续传状态，每页按 100 条判断是否继续。它的请求错误计数阈值为 5，未完成下载任务的启动阈值为 20。这些数字都不是日额度。

## 非 VIP 激励下载次数

APK 的 `DownloadIncentiveAdManager` 是一套独立机制：

```text
GET requestDownloadConfig
  -> server match/downloadIntercept
  -> 仅对 !isVip 生效

GET requestDownloadInfo(albumId)
  -> remainTimes + rewardCount

批量下载
  -> remainTimes 不足时弹窗
  -> POST decreaseDownloadTimes
       trackIds, downloadCount, requestId, ts, retry, signature
  -> 服务端返回新 remainTimes
  -> 成功后才真正入队

观看激励广告
  -> POST rewardDownloadTimes
  -> 服务端增加 remainTimes
```

这说明官方 APK 确实会消费一种服务端下载次数，但它的适用条件是非 VIP。用户观测到的 VIP 账号每日约 4000 集，应是不同的服务端限制。

## 接口路径

| 用途 | APK 路径 |
|---|---|
| 专辑批量下载 V1 | `mobile/download/v1/album/` |
| 专辑批量下载 V2 | `mobile/download/v2/album/` |
| 单曲 V2 下载信息 | `mobile/download/v2/track/{trackId}/ts-{ms}` |
| 剩余分批下载 | `mobile/download/rest/` |
| 已购未下载曲目 | `mobile/download/v1/album/paid` |
| 离线下载播放权检查 | `mobile/download/track/play/check/ts-{ms}?trackId=...` |
| 非 VIP 次数配置 | `incentive/ting/downloadConfig/ts-{ms}` |
| 非 VIP 剩余次数 | `incentive/ting/downloadInfo/ts-{ms}` |
| 非 VIP 扣减次数 | `incentive/ting/decreaseDownloadTimes/ts-{ms}` |
| 广告奖励次数 | `incentive/ting/rewardDownloadTimes/ts-{ms}` |

## 4000/日与风控的边界

静态源码中没有找到：

- `uid + yyyyMMdd` 之类的本地计数键。
- 计数达到 4000 后本地拒绝的分支。
- 自然日或滑动 24 小时重置任务。
- 专门为规避风控设计的随机延迟、多账号轮换或设备轮换逻辑。

官方客户端能看到的仅是分页/分批策略和服务端返回结果。因此对当前项目最稳妥的实现是：保持单账号串行获取授权 URL，以官方页大小作为上限，对额度错误首次出现立即停止整个账号批次，不对同一拒绝自动重试。这不能提高服务端额度，但可避免额度耗尽后继续产生无效请求。

4000 的精确 `ret`、重置时区和窗口类型，仅能由触发时的脱敏响应确认：保留 endpoint 类型、HTTP status、`ret`、`msg`、响应时间和 `data` 字段名，不保留 token、Cookie、`x-tk` 或下载 URL。

## 对当前项目的直接修正建议

1. `DownloadLimitError` 首次出现就应熔断当前账号全批；当前默认还会继续请求两集。
2. 不要把 `canDownloadCount`、`remainTimes` 和服务端 VIP 日额度合并成一个字段。
3. 专辑列表的 `isAuthorized` 不能代替单曲下载接口的最终结果；已有 fixture 证明 `ret=0 + 有效加密 URL` 可与 `isAuthorized=false` 并存。
4. 额度错误要保留脱敏 `ret/msg/endpoint`，不要将它降级为普通网络重试。
