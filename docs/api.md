# 0.5 接收端元数据协议

现有V2完整快照/CAS保持兼容。除`/healthz`外，所有接口要求Bearer Token；
`X-Sender-Tag`选择标签，缺省default。标签共用Token，属于存储分组。
成功响应回显sender_tag；未知字段/来源/非法schema返回受控400，鉴权401/Origin403，
旧快照或配置冲突409，磁盘失败500。不反射请求正文或异常信息。

| 方法与路径 | 用途 | capability |
|---|---|---|
| GET /v1/sources | 配置、revision、协议/实际包版本及能力 | conditional_snapshots |
| GET /v1/status | 本标签快照/爬虫验证、clientHealth、incidents | validation_feedback |
| GET /v1/senders | 已初始化标签的安全元数据概览 | sender_status_summary |
| POST /v2/cookies | complete=true的结构化快照，expected_version/request_id/config_revision | conditional_snapshots |
| GET /v2/sync/{source} | 当前快照版本与配置revision | conditional_snapshots |
| POST /v1/feedback | 实际版本绑定的爬虫判定 | validation_feedback |
| POST/GET /v1/client-health | 上报/查询客户端健康 | client_health |
| GET /v1/incidents | 本标签活动事件与最近100条历史，只读 | source_incidents |
| POST /v1/incidents/{source} | 当前事件确认、静默、重新提醒 | source_incidents |

## 健康上报

```json
{
  "schema_version": 1,
  "client_version": "0.5.0",
  "interval_minutes": 15,
  "sources": {
    "mysite": {
      "approved": true,
      "permission_granted": true,
      "sync_phase": "succeeded",
      "error_code": "none",
      "last_success_at": null
    }
  }
}
```

字段精确匹配，不接收机器名、profile、URL、Token、Cookie或任意错误文本。
版本三段数字、最多15字符；间隔整数15–10080分钟；来源为当前标签配置来源、最多100项。
两个授权字段只能bool；last_success_at只能null或不在未来的UTC Z时间。
phase枚举：idle/pending/syncing/probing/retrying/succeeded/exhausted/blocked/paused。
error枚举：none/network_error/unauthorized/forbidden/configuration_changed/permission_denied/
snapshot_conflict/rate_limited/retry_exhausted/worker_interrupted/unknown_error。

接收端以自身时间记录receivedAt，`.client-health.json`按标签原子0600保存。
state为not_reported/recent/overdue；收报超过`2*interval_minutes*60+300`秒为overdue，
损坏数据为unknown/error。recent仅代表最近收到上报，不能推断当前在线。
健康与事件文件只另存来源配置的SHA-256作用域摘要，不保存域名/URL；读取按当前配置
核对。API修改或离线修改后重启，删除及同名来源域名变更均使旧元数据失效，查询不改文件。
阈值调整保留既有授权、健康、事件和快照。损坏的辅助元数据仍显示异常，不阻止删除来源。

## 事件操作

```json
{"incident_id":"0123456789abcdef0123456789abcdef","action":"acknowledge"}
```

snooze必须增加整数duration_seconds（60–86400）；reopen与acknowledge不接受duration。
必须使用当前活动incident_id，不接受旧ID、未知来源或额外字段。
kind为login_invalid/validation_expired/snapshot_stale；status为open/acknowledged/snoozed/
awaiting_validation/resolved。每标签活动事件和最多100条历史保存于`.incidents.json`，0600。

失效版本实际invalid开启事件；新版本推送只进入awaiting_validation，不代表恢复。
只有不同版本被实际爬虫报告valid才resolved。同版本valid不关闭该登录事件；旧反馈409。
确认/静默抑制重复提醒，同一未恢复事件在新版本再次invalid时保留处置；问题实际恢复
后再次发生才生成新ID。普通GET/CLI读取不推进状态。
接收端扫描即使no-notify/没有webhook也推进事件，只有启用通知时尝试消息。

```bash
cookie-http-seeder incidents --sender-tag home-pc --endpoint http://127.0.0.1:18765
cookie-http-seeder incident-action mysite --sender-tag home-pc \
  --incident-id CURRENT_EVENT_ID --action snooze --duration-seconds 3600
```

## 迁移与能力降级

旧公开Python导入和旧CLI保留。新ReceiverState显式传入build_handler/serve可运行多个隔离应用。
扩展无对应capability时不请求新接口；健康默认关闭。反馈outbox schema2、只读状态查询
及退出码见[消费者指南](consumer-recovery.md)。

## doctor诊断

`doctor --json`输出`runtime_version`（当前运行代码）、`installed_version`（当前解释器
环境的实际dist-info元数据）、`python_version`、`receiver_version`、经过过滤的
`capabilities`、`sourceThresholds`、`clientHealth`、`incidents`和`next_steps`。
`client_version`保留兼容。checkout的egg-info不算已安装版本；安装缺失或多个安装版本
冲突时installed_version为null。`metadata_origin`说明健康/事件来自local还是receiver。

下一步仅为受控枚举：configure_data_directory/fix_permissions/configure_sources/
configure_token/upgrade_receiver/align_configuration/check_connection/install_package/
use_installed_package/check_browser/push_snapshot/relogin/validate_snapshot/inspect_metadata。
诊断不导出URL、Cookie、Token或服务器任意字符串；确认或静默仍保留需要恢复的指引。
JSON保留UTC机器时间，默认人类输出把健康和事件时间转换为当地时间。
