# 统一的当地时间显示

面向人的时间统一为 24 小时制 `YYYY-MM-DD HH:mm:ss`，补齐零，不显示 `T`、`Z` 或毫秒。
例如同一时刻 `2026-09-20T00:49:34Z`，在上海/新加坡显示为 `2026-09-20 08:49:34`。

## 覆盖范围

- 扩展弹窗：下次重试时间、上次成功时间，显示完整日期，避免跨日混淆。
- 网站管理页的诊断输出：队列时间、快照更新时间、最近接收时间等使用同一格式。
- CLI：`status`、`doctor`、`report` 默认将已知时间字段转换为当地时间。
- 消费示例的 `updatedAt`、飞书通知中的通知发送时间。

浏览器使用浏览器所在设备的时区，并在弹窗/诊断中标明时区。
CLI、消费示例和飞书通知使用运行 Python 的机器/容器的系统时区。
不硬编码 UTC+8，也不是简单移除 T/Z；跨日、偏移和夏令时由日期库处理。
例如服务器的系统时区为 UTC 时，终端和通知显示的仍是该服务器当地时间。
需要与北京时间一致时，应配置服务器/容器的系统时区；支持 POSIX TZ 的环境可使用：

```bash
TZ=Asia/Shanghai cookie-http-seeder --data-dir ./data status
TZ=Asia/Shanghai cookie-http-seeder --data-dir ./data serve
```

相应时区数据库需在系统中可用。Windows 使用系统时区设置。

## 脚本和协议保持原样

格式化只发生在显示边界，不改 Cookie 快照文件、UTC API 字段、毫秒重试截止时间、
Cookie expirationDate、版本检查、新鲜度计算或配置导出。无需迁移文件或重新推送 Cookie。
无时间/非法时间显示 `-`；不会把 null 当成 1970 年，也不会猜测无时区的日期。

CLI 默认输出仍是 JSON，但时间字段为供人阅读的当地时间。依赖原始 ISO 时间的脚本使用：

```bash
cookie-http-seeder status --json
cookie-http-seeder doctor --local-only --json
cookie-http-seeder report mysite --snapshot-version <version> \
  --result invalid --reason-code session_expired --json
```

这些命令的 `--json` 输出保留原始 ISO 时间和 null；HTTP API 始终保持原始格式。
当地时间可能在夏令时结束时重复，因此不能把显示字符串用于排序、版本标识或写回协议。
