# Changelog

重要变更记录。`Unreleased` 表示已进入代码但未在此声明独立发布的变更；
当前 Python 包和扩展元数据仍为 `0.3.0`，不据此虚构新版本或发布日期。

## [Unreleased]

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
