# 0.5.0 全面升级实施计划

> 历史阶段记录：本文记录 2026-10-04 合并和部署前的状态。后续 PR #5 已合并至 main（`8cdb6e2`），GTR 接收端和现有 Grok 扩展均已升级至 0.5.0，保留检查通过。本文中的“待批准/未部署/旧 main”描述仅适用于当时；当前发行状态见 [GitHub Releases](https://github.com/SongYuanKun/cookie-http-seeder/releases)。私有现场验收含主机路径，不作为公开附件上传。

> **For agentic workers:** Use superpowers:executing-plans to implement this plan task by task. 主线程是唯一写者；按用户 AGENTS 仅在结尾进行一次独立分支审查，禁止逐任务重复审查。步骤以勾选状态记录真实进度。

**Goal:** 完成客户端健康、登录恢复事件、正式爬虫 CLI、持久反馈和统一面板，并重构后端/扩展边界，交付可验证的0.5.0。

**Architecture:** 保留V2快照、标签和公开兼容入口，状态/HTTP/生命周期分离。新增健康与事件作为不含凭据的元数据服务，扩展用同一个 dashboard 模型呈现，消费端持久化与分类独立。

**Tech Stack:** Python 3.11+ 标准库、Chrome 120+ MV3、Node 22，无新Python运行依赖。

**Spec:** `docs/superpowers/specs/2026-10-04-full-upgrade-design.md`

## Global Constraints

- 原数据布局、Token、来源授权、V2/CAS、default和公开导入/旧CLI兼容。
- 新健康上报默认0；启用范围15–10080分钟；过期阈值2倍间隔加300秒。
- 事件静默60–86400秒；每标签最多100条历史；新版本实际valid才关闭登录恢复事件。
- outbox最多16条，每记录最多3次尝试，单次flush最多处理1条；旧schema1暂停不丢失。
- URL逐次筛选，无自动重定向/登录/账号回退，无凭据运维状态。
- 保留0.4恢复/提醒设置；生产发布和浏览器新增权限启用另行取得具体批准。

## Review Focus

1. 多应用/多标签并发请求时，缓存、状态和事件不能串组（Task 1/2/3）。
2. 来源作用域缩窄后旧健康/事件/授权不能造成读取或推送越界（Task 2/3/5）。
3. 浏览器长时间休眠、worker重建、晚到请求不能伪造online或抹掉阻塞状态（Task 2/5）。
4. invalid事件到达后同版本valid或仅新快照到达不能假恢复（Task 3/4/6）。
5. pending反馈、旧状态损坏、磁盘写失败时不能丢暂停或泄露正文（Task 4）。

## Task 1：后端领域、HTTP与生命周期拆分

**Files:** 新增 `receiver_state.py`、`receiver_http.py`、`receiver_service.py`；更新 `receiver.py`、`cli.py`；测试 `tests/test_receiver_architecture.py` 和现有接收端/锁测试。

**Interfaces:** `ReceiverState` 构造/方法保留；`build_handler(token, *, notify=False, state=None)`、`serve(*, host: str, port: int, token: str, notify: bool = False, state=None)`允许显式应用状态；旧configure与receiver导入入口保留。

- [x] 测试两个显式state的HTTP server同时运行，配置、快照和反馈互不覆盖，旧导入入口仍可用。
- [x] 运行新增测试确认缺少隔离接口的失败：`python -m pytest tests/test_receiver_architecture.py`。
- [x] 拆分领域/传输/生命周期，保持单目录锁、线程关闭、IPv6/绑定重试和无凭据错误映射。
- [x] 运行新增及现有receiver/runtime/发送端测试，修复真实回归；提交该可独立运行的重构。

## Task 2：客户端健康协议及存储

**Files:** 新增 `client_health.py`、`tests/test_client_health.py`；更新领域state、HTTP路由、client、doctor及API文档。

**Interfaces:** `ClientHealthStore(data_dir, clock=time.time).accept(payload, sources)`和`.status()`；POST/GET `/v1/client-health`，capability `client_health`。

- [x] 写严格schema、未知来源、越界间隔、未来时间、secret字段拒绝、0600、重启时效和标签隔离测试。
- [x] 运行 `python -m pytest tests/test_client_health.py`，确认新协议/模块缺失失败。
- [x] 实现精确spec字段/枚举，状态recent/overdue/not_reported，无Cookie读取；接入status/senders/client。
- [x] 运行健康/接收端/诊断测试；doctor按来源阈值判断且正确识别版本；提交。

## Task 3：持久登录事件与确认/静默/恢复

**Files:** 新增 `incidents.py`、`tests/test_incidents.py`；更新领域反馈/快照协调、监控、HTTP、client及CLI。

**Interfaces:** `IncidentStore(data_dir, clock=time.time)`提供`observe(source, status)`、`list()`和`act(source, incident_id, action, duration_seconds=None)`；GET `/v1/incidents`、POST `/v1/incidents/{source}`，capability `source_incidents`。

- [x] 写invalid去重、确认/静默持久化、旧ID拒绝、100条历史、跨标签隔离、旧反馈409不影响新事件测试。
- [x] 写invalid→新unverified→新valid恢复，以及同版本valid不恢复、stale/expired独立语义、无webhook不发消息测试。
- [x] 运行 `python -m pytest tests/test_incidents.py` 确认失败。
- [x] 实现有界元数据状态机和周期扫描，GET只读；通知尊重事件处置并保留冷却，配置缩窄失效。
- [x] 运行事件/monitoring/sync/receiver测试，确认无真实消息发送；提交。

## Task 4：消费模块拆分、outbox迁移与正式CLI

**Files:** 新增 `response_rules.py`、`consumer_state.py`、`consumer_cli.py`、`tests/test_consumer_cli.py`、`tests/test_consumer_outbox.py`；更新consumer、cli、examples和consumer文档。

**Interfaces:** 现有`Observation/Response/RequestOutcome/ResponseRules/CookieConsumer`兼容导出；`CookieConsumer.status()`提供只读安全状态；CLI `consume/consumer-status/consumer-flush`。

- [x] 写v1→v2迁移保留blocked_version、16条outbox、每条3次预算、pending invalid不被新请求覆盖测试。
- [x] 写配置/鉴权终止错误blocked、旧版本反馈discarded、磁盘异常和重启锁、安全CLI退出码/无正文输出测试；consumer-status在无Token/只读目录下不联网、不读Cookie、不创建文件。
- [x] 运行 `python -m pytest tests/test_consumer_outbox.py tests/test_consumer_cli.py` 确认失败。
- [x] 分离分类/持久化/编排；实现标准库传输、1MiB限制、无重定向、有界等待及明确exit 0/1/2/3/4。
- [x] 运行新测试、现有consumer与两个examples测试；更新正式接入示例；提交。

## Task 5：扩展模块边界、统一dashboard与健康上报

**Files:** 新增 `settings.js`、`receiver_client.js`、`cookie_collector.js`、`dashboard.js`、`runtime_controller.js`、`health_reporter.js`和对应Node测试；更新shared/background/sync/manifest可达依赖。

**Interfaces:** shared保留既有export；runtime消息新增`get-dashboard`；`HealthReporter`和controller由注入Chrome API/clock验证。

- [x] 写旧settings/队列升级不丢授权、health默认关闭、无capability不上报、权限撤销和连接切换抛弃旧结果测试。
- [x] 写alarm重建、休眠后单次报告、禁止读取Cookie用于健康、字段/错误文本不泄露测试。
- [x] 运行新增Node测试确认失败，之后拆分shared并实现聚合模型与reporter。
- [x] 保留SyncEngine五次上限/CAS/恢复probe及现有LoginMonitor降级路径；统一安全操作枚举。
- [x] 运行全部Node测试和builder依赖闭包测试；提交。

## Task 6：管理页、popup、事件操作与受控浏览器闭环

**Files:** 更新options/popup及HTML；新增聚焦视图/controller模块、DOM测试与`tests/browser/`受控验收脚本；更新UI/使用说明。

**Interfaces:** 页面消费dashboard DTO，事件命令按incident ID发送；旧接收端功能明确不可用；打开登录页只用已配置目标URL且来自显式用户点击。

- [x] 写无任意错误文本/secret DOM、断连不沿用valid、刷新晚到结果、ack/snooze与来源范围变化测试。
- [x] 运行新增测试确认失败，之后实现健康、事件、操作指引和新设置的界面。
- [x] 在临时profile和本地合成站点，验收授权→推送→心跳→invalid→确认/静默→新快照→valid恢复。
- [x] 记录版本、每场景结果、截图和真实边界；保留默认关闭及既有用户设置；提交。

## Task 7：0.5.0交付和完整审计

**Files:** pyproject、manifest、README、CHANGELOG、协议/consumer/upgrade/部署文档、builder/CI和验收矩阵。

- [x] 对齐0.5.0，更新能力协商、迁移、使用/升级说明及scope限制。
- [x] 运行全Python/Node、Ruff、source-sync；构建wheel和extension ZIP并从checkout外导入wheel。
- [x] 逐项核对R1–R5、重构、迁移、受控浏览器和打包证据，不能用通过测试数量替代交付。
- [x] 一次独立整分支审查；只对真实发现追加聚焦修复/验证。
- [x] 提交构建哈希和最终矩阵，提供可审阅代码/发布结果；准备具体生产及Grok客户端升级动作供用户最终批准。

## 当前证据与执行状态

- 基线：main `43e591f`；Python282通过（26.82秒），Node230通过（421毫秒）。
- 隔离路径：`/home/kun/.codex/worktrees/crawler-session-recovery/cookie-http-seeder`。
- 分支：`codex/full-upgrade-0.5.0`；只使用现有工具/依赖，没有生产改动。
- Task 1–7完成；最终337项Python/246项Node通过，构建与一次审查修复证据见最终矩阵。主线程唯一写者。
- 当前完成判定和逐项所需证据见 `docs/superpowers/reviews/2026-10-04-full-upgrade-audit.md`。生产与Grok本版本更新动作已备好，等待最终具体批准。
