> 本文描述 0.3 的可靠同步基础，当前应同时阅读 [发送端标签](sender-tags.md)。
> 下文默认示例未带标签时进入 default；非默认标签的请求增加 `X-Sender-Tag`，
> CLI 增加 `--sender-tag`，Python 读取与反馈使用同一 `sender_tag`。
> 标签不属于反馈 JSON 字段；配置、版本与状态都在所选标签内维护。
> 当前快速开始见 [README](../README.md)，当地时间与原始输出见 [时间显示](time-display.md)。

# 第二阶段：可靠同步、状态诊断、失效反馈（0.3.0）

## 范围

本次包含完整第一阶段实现，并新增变化后同步、持久化队列、有限重试、条件写入、内容去重、诊断与版本绑定反馈。
继续保留本地 JSON、Python 标准库运行时、明确的域名授权、手动清空并暂停。
不引入自动登录、任意网址验证器、localStorage 读取、Cookie 池或多账号合并。

## 扩展同步语义

变化同步默认关闭，必须在设置页面开启。只响应当前连接下已明确授权来源的 Cookie 事件；先合并事件再读取完整快照。
使用 30 秒合并窗口，连续事件最多将该批次的计划时间延长至最初事件之后 60 秒；Chrome 闹钟实际触发仍可能延后。
不依赖长期存活的 `setTimeout` 或 Service Worker 全局变量；来源、重试次数、下次时间写入 `chrome.storage.local`。
队列只含来源与状态，不含 Cookie、Token、请求体。每次尝试重新检查配置、授权、版本并重新采集。
设备关闭或休眠期间不会执行请求。每次 Service Worker 启动都检查并重建重要闹钟。

临时网络错误、读取异常、408/429/5xx、快照 CAS 冲突会重试。总计最多尝试 5 次（首次 + 最多 4 次自动重试）。
退避基数 30/60/120/240 秒，加 0–20% 抖动；支持数值型 Retry-After，最多 1 小时。
401/403、来源配置改变、权限撤销、协议不兼容为阻塞状态；不无限重试。
耗尽后不因 Cookie 事件或定时任务自动重置预算，用户修复问题后点击“立即推送 / 重试”。
关掉变化同步会取消尚未发送的变化任务；已在网络中发送的请求不能撤回。更换连接或 Token 后原授权失效，旧连接任务不会归属于新连接。
一次处理最多 3 个来源，其余交给闹钟。成功且内容未变的周期推送不触发成功通知。

## 接口与写入前置条件

`GET /v1/sources` 增加 capabilities：`conditional_snapshots`、`validation_feedback`。
`protocol_version` 继续为 2，数据文件 `schema_version` 继续为 2；客户端必须检测能力，不以版本号猜测支持情况。

`GET /v2/sync/{source}` 返回该来源的 `snapshot_version`（尚未产生版本时为 null）、配置 revision、启用状态，不返回 Cookie。

上传仍为 `POST /v2/cookies`，增加两个必填字段：

```json
{
  "schema_version": 2,
  "complete": true,
  "source": "mysite",
  "cookies": [],
  "config_revision": "从来源配置接口获取",
  "expected_version": null,
  "request_id": "每次请求独立生成的 UUID"
}
```

真实 Cookie 必须采用第一阶段定义的结构化格式。空列表表示一次成功的完整空快照，不表示读取异常。
先取版本，再采集快照；旧 expected_version 返回 409 / snapshot_conflict。重试必须重新采集，不能给旧请求体套一个新版本再发送。
缺少前置条件返回 428；配置版本不匹配返回 409 / configuration_changed。原始 Header 写入接口仍返回 410。

每次内容改变、主动清空、配置变更清空均产生随机快照版本；这是条件比较标识，不是大小可排序的序号。
相同 request_id 与相同最新内容可幂等重放；同一 request_id 携带不同内容被拒绝。
相同内容保留快照版本、Cookie 文件和已有验证记录，仅更新小型操作元数据中的最后接收时间。
快照文件与操作元数据分别原子写入，不是多文件事务。元数据写入失败可能发生在快照已接受之后；版本检查和幂等处理允许安全恢复。

**一个根数据目录及其全部标签只能有一个接收进程。** 后续完整性补全已加入 `.receiver.lock` 本地单实例保护，
见 [完整性说明](completeness-review.md)。CAS 和文件锁都不是分布式事务，不提供同标签多账号合并。

## 状态与反馈

`GET /v1/status` / `cookie-http-seeder status` 分别展示：

- 快照存在、Cookie 数量、最后内容更新时间、快照版本。
- 最近一次成功接收时间、年龄、新鲜度（missing/cleared/unknown/fresh/stale）。
- 验证状态（unverified/valid/invalid/error/expired）、爬虫报告原因与报告年龄。

新鲜度与报告时效目前均为 24 小时。Cookie 的真实服务器有效期无法由这些字段推断；`valid` 仅代表最近一次爬虫报告。

`POST /v1/feedback` 要求 Token，正文必须且只能包含：

```json
{
  "source": "mysite",
  "snapshot_version": "从实际使用的快照文件获取",
  "result": "invalid",
  "reason_code": "session_expired"
}
```

允许组合：valid/logged_in；invalid/login_required、session_expired、account_mismatch；
error/network_error、rate_limited、unexpected_response；unverified/manual_reset。
不接受响应正文、Cookie、任意错误消息等自由文本。新快照会重置验证记录；针对旧版本的结果返回 409；暂停来源拒绝报告。
空快照不能标为 valid。重复的无变化同步不会刷新验证时钟。

`load_request_credentials(...)` 从一次原子文件读取同时取得 `cookie_header` 与 `snapshot_version`。
爬虫应保存该版本直到请求结果返回；不要先读 Header、再单独读版本，否则可能混用两次快照。

```bash
cookie-http-seeder --data-dir ./data report mysite \
  --snapshot-version <实际请求使用的版本> \
  --result invalid --reason-code session_expired
```

启用通知并配置飞书 webhook 时，invalid 可触发提醒，按来源冷却 15 分钟。冷却先持久化再尝试发送；失败不会自动紧密重试轰炸。
通知是尽力交付，不保证送达，也不是自动重新登录。

## doctor

```bash
cookie-http-seeder --data-dir ./data doctor
cookie-http-seeder --data-dir ./data doctor --local-only
cookie-http-seeder --data-dir ./data doctor --endpoint https://receiver.example.com
```

检查目录可写性、配置格式、Token 存在与格式、Token 文件权限、快照状态、接收端鉴权、协议能力、两端配置一致性。
不自动创建 Token 或目录，不修改网站配置；目录可写测试只创建并移除一个空临时文件。
输出不包含 Cookie 或 Token。Python 客户端禁止重定向和环境代理，远程必须 HTTPS；HTTP 只允许 localhost/127.0.0.1。
有 error 返回非零退出码；只有 warning 时返回 0。代理/tunnel、TLS 或权限错误需要用户自行修复。

## 升级与测试边界

服务端和扩展一起更新到 0.3，Chrome 最低版本为 120。停止旧服务，应用代码，重新安装 Python 包，重新启动服务并重新加载扩展。
不要删除 data 目录。0.1 原始 Header、0.2 结构化文件仍保留既有读取语义；要使用新协议/反馈，请重新推送一次。
第一阶段 0.2 客户端不带写入前置条件，不能继续向 0.3 上传。

自动测试覆盖 Python 单元/本地 HTTP 和 Chrome API mock，不代表真实 Chrome 联调完成。
不支持分区 Cookie、多个 Cookie store 混合或多 profile；不模拟浏览器 SameSite 发送上下文，也不承诺所有网站跨环境会话通用。
测试状态以对应提交的 GitHub Actions 结果和单独的实机验收记录为准，不能把未运行检查标为通过。
历史交付包的 TEST_REPORT.md 只记录当时的本地验证，不代表后续提交的完整测试结果。

参考（Chrome 官方文档，核对于 2026-09-19）：
- https://developer.chrome.com/docs/extensions/reference/api/alarms
- https://developer.chrome.com/docs/extensions/reference/api/cookies
- https://developer.chrome.com/docs/extensions/develop/concepts/service-workers/lifecycle
