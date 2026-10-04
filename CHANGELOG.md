# Changelog

重要变更记录。`Unreleased` 表示尚未发行的后续变更。
公开发行包、校验值和发行日期以 [GitHub Releases](https://github.com/SongYuanKun/cookie-http-seeder/releases) 为准；
部署状态由实际安装版本和现场验收确定。

## [0.5.0] - 2026-10-04

### Added

- 按标签保存的客户端健康 API/心跳，默认关闭；服务器收报时间计算 recent/overdue。
- 持久登录失效、反馈过期、长期未同步事件；确认/静默/重新提醒，不伪造登录恢复。
- 正式 `consume`、`consumer-status`、`consumer-flush`；schema1→2迁移、有界反馈outbox及终止错误blocked。
- 统一 Options/Popup dashboard、明确的来源登录入口和能力降级指引。
- doctor区分运行/实际安装/接收端版本，提供能力、独立阈值、健康/事件及受控下一步。
- 真实Chrome149临时profile升级、原生授权、worker alarm和爬虫恢复闭环验收。

### Changed

- 接收端拆为领域状态、HTTP和生命周期；允许显式state，多实例不依赖全局配置。
- 消费者拆为响应规则、持久化、请求编排与CLI；扩展拆分设置、协议、Cookie采集和面板。
- 可选权限声明规范为等价的HTTP+HTTPS通配scheme，实际申请仍逐来源限定域名。

### Fixed

- Chrome storage字段重排导致旧JSON字符串授权比较误判；现在按规范字段顺序比较。
- 新Chrome HTML pattern要求连字符转义。
- 断连/配置变更/晚到请求不沿用旧有效结论；确认/静默同时抑制接收端与浏览器提醒。
- 来源离线变更后重启使旧元数据失效；损坏健康/事件文件不再阻止删除来源。
- 同一未恢复事件保留确认/静默；HTTP429从队列到心跳保持rate_limited分类。

### Compatibility

- 保留公开Python导入、V2/CAS、default/标签目录、Token、来源授权和旧扩展队列。
- 新版本推送只进入awaiting_validation，实际不同版本valid反馈才关闭登录失效事件。
- 必须原路径更新并显式重新加载扩展，保留profile；不会自动开启心跳或自动登录。

## [0.4.0]

### Added

- `CookieConsumer`：原子读取版本与 Header、明确响应分类、版本绑定反馈、持久暂停、有限等待新快照和离线反馈补发。
- 完整爬虫接入示例与 JSON / 正文 / 登录跳转规则模板；未知响应不会被误判为登录失效。
- 每来源独立的新鲜度和反馈有效期阈值；仅调整阈值保留快照。
- 可选接收端未同步提醒、独立冷却；无凭据的 `/v1/senders`、CLI 和浏览器发送端概览。
- 默认关闭的低频恢复探测和浏览器登录状态提醒；保留既有有限重试与本机授权边界。
- 可重复生成的版本化插件 ZIP、CI 构建产物和保留设置的客户端升级指南。

### Compatibility

- 运行时保持 Python 标准库和 V2/CAS；默认标签及既有数据目录兼容。
- 新策略仍须检查能力字段；不会自动登录、选择其他标签或把 HTTP 200 当作登录成功。
- 实际站点规则需依据业务响应配置；客户端实机更新需核实目标机器和显示版本。

## [此前已合并变更]

以下项目已进入主干，原记录为 Unreleased；不据此补造历史发布版本。

### Added

- 按发送端标签分别保存来源配置、Cookie 快照、版本、反馈、新鲜度与通知冷却。
  非默认标签位于 `data/senders/{tag}/`，default 保持历史根目录布局。
- 扩展标签设置、`X-Sender-Tag` 请求头、`sender_tags` 能力协商及响应回显校验。
- Python 读取 helper、ReceiverClient、status/doctor/report/header 支持标签；新增 `senders` 命令。
- 结构化 Cookie 快照与按 URL 的域名、路径、Secure、过期筛选，保留不同路径同名 Cookie。
- 动态网站管理、运行时域名授权、来源配置导入/导出、完整空快照与主动清空并暂停。
- 变化后同步、持久化的无凭据任务队列、有限退避重试、CAS 条件写入、请求幂等与内容去重。
- `doctor` 诊断、快照版本绑定的爬虫反馈、可选失效通知与通知冷却。
- 网站状态面板、单来源重新授权、离线撤销本机授权、设置修改串行化。
- `.receiver.lock` 单根目录单接收进程保护及相关回归测试。
- IPv6 监听和适配网卡/端口暂不可用的绑定重试。
- 数据目录解析、原子写入、包内默认配置、Docker Compose 和 systemd 部署模板。

### Changed

- 面向人的时间使用当地 `YYYY-MM-DD HH:mm:ss`；存储/API 保持原值，CLI `--json` 保留机器格式。
- 来源同步脚本只保持包内与示例默认配置一致，不再修改扩展 `SOURCES` 或生成固定站点权限。
- 所有标签共用接收端 Token；标签是数据分组，不是权限隔离或多租户身份。
- 配置修改、清空、版本检查与反馈在对应标签内执行；切换标签需要重新授权，不迁移旧数据。
- README、部署/示例/贡献/安全说明统一补充当前标签用法，阶段文档注明历史范围和后续覆盖规则。

### Fixed

- 修复合并后接收模块的旧全局初始化及缺失的 Origin/配置冲突定义，恢复启动与错误处理。
- 启动日志改为实际的 `/v2/cookies`；保留已有 IPv6 监听与绑定重试。
- 修复测试辅助函数行长导致的 Ruff 失败。
- 部署文档明确 systemd 初始化 Token 必须使用服务相同的数据目录，升级不覆盖已有配置。

### Compatibility

- 缺少标签的客户端进入 default，历史数据无需搬迁；非默认标签不回退到 default。
- 旧 `POST /v1/cookies` 返回 410；缺少条件写入字段的 0.2 客户端返回 428。
- 新客户端使用非默认标签前检查能力和回显，不把数据静默发送到不支持标签的旧接收端。
- 详细边界见 [标签说明](docs/sender-tags.md)、[可靠同步](docs/phase2.md) 和 [安全说明](SECURITY.md)。

## [0.1.0] - 2026-09-16

### Added

- Initial release: Chrome MV3 extension, loopback receiver, CLI, optional Feishu notify.
