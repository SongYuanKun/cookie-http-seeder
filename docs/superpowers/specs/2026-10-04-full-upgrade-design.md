# 0.5.0 全面功能升级与代码重构设计

## 目标与现状

用户目标是“全面升级功能，重构代码”。沿用已明确的实际场景：Grok Bot 的云端
Chrome 正常登录，扩展同步到接收端，用户的爬虫按发送端标签消费并反馈登录状态。
成功应覆盖功能、架构、迁移、真实界面、打包及文档，而不是只拆文件或增加测试。

2026-10-04 已检查：main/origin/main 为 `43e591f`、版本 0.4.0、工作区干净。
当前基线 Python 282 项通过，Node 230 项通过。现有失效暂停、CAS、来源阈值、
发送端概览、可选恢复探测和本地提醒均已有实现，不能作为本次新增功能重复交付。
主要缺口是客户端活性证据、失效事件处置闭环、正式消费 CLI、版本诊断、
多组件状态编排，以及可独立使用和验证的领域边界。

这份文件是拟执行方案，新增需求仍可由用户纠正。尚未合并的
`origin/codex/portable-session-bundle` 是额外的跨浏览器加密会话迁移项目，
会增加网页存储/请求头捕获、浏览器权限和可选运行依赖。本设计覆盖现有 Cookie
链路的全面升级；采纳该分支需要单独明确需求，不能用它替代登录失效闭环。

## 技术约束与兼容

- Python 3.11+、Chrome 120+、Node 22；Python 运行时继续只用标准库。
- Python 包与 Chrome manifest 一起升级为 0.5.0，V2 Cookie 快照协议保持兼容。
- `default` 根路径、非默认标签分目录、共享 Token 和标签信任边界保持不变。
- 保留已有公开 Python 导入路径、CLI 命令、CookieConsumer 请求接口及返回语义；
  模块迁移通过兼容导出完成。新增 API 通过 capability 协商，旧端有明确降级。
- 不清空 profile、Token、Cookie、来源、授权或旧队列。0.4 设置升级后保留，新增
  客户端健康上报默认关闭；现有恢复探测、登录提醒的开关和值原样保留。
- 队列、心跳、事件、诊断、通知、DOM 和错误中不得出现 Cookie、Token、Header、
  响应正文、webhook 或 storage 导出。只接受有界字段、已知枚举和配置内来源。
- 同步成功、文件存在、心跳或 HTTP 200/403 不证明登录有效。禁止自动登录、
  自动重定向携带凭据、账号/标签自动回退，以及自动扩大网站权限。
- 实现阶段只修改隔离工作区。生产安装、服务重启、部署和新权限启用在可审核的
  构建与验证结果准备好后，再按本机规则取得针对该版本的批准。

## 方案与架构

采用按领域逐步重构并交付完整闭环：现有接口作为兼容入口，新能力由独立服务
提供，扩展以统一的健康 DTO 渲染页面。另一种整体替换协议、存储和 UI 的方式
会要求同时迁移现有消费者和客户端；本次已有生产使用和数据保留要求，因此选用
兼容接口上的模块化升级。

后端边界：

- `receiver_state.py`：来源、发送端、快照/CAS、反馈与领域协调。
- `receiver_http.py`：鉴权、请求边界、路由、HTTP 错误映射和响应。
- `receiver_service.py`：服务器创建、生命周期、锁和监控线程。
- `receiver.py`：保留原导入入口与默认配置包装；新应用显式注入 state。
- `client_health.py`：严格健康上报 schema、元数据存储和时效计算。
- `incidents.py`：失效/验证过期/长期未同步事件、确认/静默/恢复与有界历史。
- `response_rules.py`：显式站点响应分类；由 `consumer.py` 兼容导出。
- `consumer_state.py`：持久化暂停和反馈 outbox、锁及旧状态迁移。
- `consumer.py`：一次请求、版本绑定分类、暂停与验证后恢复。
- `consumer_cli.py`：正式消费/等待/反馈处理命令；`cli.py` 负责命令路由。

扩展边界：

- 将 shared 中的 settings/approval、协议请求和 Cookie 采集分别移入聚焦模块，
  shared 保留兼容导出，既有调用者与已打包依赖保持可用。
- 新增 runtime controller 和 dashboard 聚合；Options/Popup 使用相同的枚举、
  来源健康状态与操作指引，页面不再各自判断登录或拼装协议结果。
- 健康上报、登录提醒和同步状态机分别调度，复用连接快照/授权检查，避免 worker
  中断或连接切换时把旧结果归到新标签。

## R1：客户端健康上报

新增 capability `client_health`、鉴权 `POST /v1/client-health`、
鉴权 `GET /v1/client-health`。`/v1/status` 和 `/v1/senders` 添加健康元数据。

POST 文档精确字段为 `schema_version: 1`、`client_version`、`interval_minutes`、
`sources`。版本为三段数字且最长 15 字符；间隔为整数 15–10080 分钟。
sources 只含当前标签已配置的来源，最多 100 个；每项字段为布尔 `approved`、
布尔 `permission_granted`、`sync_phase`、`error_code`，以及可空的 UTC
`last_success_at`。phase 精确为 idle、pending、syncing、probing、retrying、
succeeded、exhausted、blocked、paused。error_code 精确为 none、network_error、
unauthorized、forbidden、configuration_changed、permission_denied、snapshot_conflict、
rate_limited、retry_exhausted、worker_interrupted、unknown_error；旧队列错误在客户端
映射为上述类别，绝不直接转发任意文本。未知字段、来源、非法时间或错误类别拒绝。
不发送 endpoint、机器指纹、profile 路径、站点 URL 或凭据。

接收端以自身时钟记录 `receivedAt`，每标签原子保存 `.client-health.json`（0600）。
状态是 `not_reported`、`recent`、`overdue`：超过 `2 * interval_minutes * 60 + 300`
秒为 overdue，损坏数据为 unknown/error，不能假装在线。recent 仅表示最近收到
上报，不能证明浏览器此刻仍运行；同标签多客户端仍是同一组，不增加设备所有权。

扩展新增 `healthReportMinutes`，默认 0，启用范围同上。无健康 capability 时显示
升级指引且不上报。worker 重建 alarm；关闭选项、切换连接或配置失效取消旧任务。
采集健康只读权限/队列元数据，绝不为心跳读取 Cookie。

## R2：登录恢复事件

新增 capability `source_incidents`。鉴权 `GET /v1/incidents` 返回当前标签的事件
和历史；`POST /v1/incidents/{source}` 接受精确的 `incident_id`、`action`，以及
snooze 时的 `duration_seconds`。action 是 `acknowledge`、`snooze`、`reopen`；
静默时长为整数 60–86400 秒。未知来源、旧 ID、非法动作和额外字段拒绝。

事件按 `(tag, source, kind)` 隔离；kind 是 `login_invalid`、`validation_expired`、
`snapshot_stale`。保留活动事件及每标签最近 100 条历史，原子持久化为 0600。
事件仅含受控 ID、来源、版本、状态、已知原因码和时间。状态为 open、acknowledged、
snoozed、awaiting_validation、resolved。

- 当前版本的可信 invalid 反馈开启登录事件；同一条件去重，不增加 Cookie 请求。
- 新快照到达后登录事件进入 awaiting_validation；只有不同于失效版本的新快照
  被实际爬虫报告 valid，才记录 resolved。推送、手动确认和静默不会伪造 valid。
- 同版本 valid 不把“等待新版本”的恢复事件关闭；旧版本反馈仍按 CAS 返回 409。
- TTL 过期和 snapshot stale 是单独的核验/同步事件，不自动判定登录失效。
- 确认/静默抑制该事件重复提醒，保留需要处理的状态；条件恢复后关闭事件，新一轮
  问题生成新事件。连接切换和标签间互不继承事件处置。
- 接收端周期扫描与反馈/写入路径推进事件，普通状态 GET 和 CLI 只读查询不写事件。
  无 webhook 或 --no-notify 时仍可记录事件，但不发送外部消息。

## R3：正式爬虫接入与持久反馈

新增 `consume`：明确来源、标签、URL、站点规则文件、接收端和消费者状态路径；
可指定有界等待（0–86400 秒，轮询 0.1–3600 秒）。标准库传输不跟随重定向，
响应上限 1 MiB，反馈/输出只含结果、原因、版本、反馈状态和暂停状态。
退出码：0=valid，2=凭据不可用，3=需登录/等待超时，4=业务验证异常，1=配置/运行错误。
新增 `consumer-status` 与 `consumer-flush`，提供不含凭据的运维入口。
`consumer-status` 是真正只读的本地查询：不要求 Token，不访问接收端，不读取
Cookie 快照，也不创建目录/锁/状态文件。身份由数据根、来源、endpoint 和标签
计算，或用显式状态路径核对；损坏状态只返回受控错误码。`consumer-flush` 才
加载鉴权凭据并尝试网络反馈，不能借查询动作增加反馈次数。

旧 schema 1 暂停状态可读并迁移为 schema 2，identity 和 blocked_version 保持。
反馈 outbox 最多 16 条，按版本合并；每条最多 3 次网络尝试，flush 单次最多尝试
一个记录。旧版本 409 丢弃；鉴权/协议/配置终止错误停为 blocked，网络错误保留
pending。新请求不得无声覆盖未交付的登录失效反馈；可丢弃的旧版本须有版本证据。
状态仍不保存请求 URL、Cookie、Token、响应或业务正文。

## R4：健康面板、诊断与操作指引

统一 dashboard DTO 分开提供：连接/能力、应用授权、Chrome 权限、队列/下次尝试、
最后同步、新鲜度、爬虫验证、客户端上报时效、事件和允许执行的操作枚举。
断连清除可用的远端有效性结论，保留本机可撤销授权视图；异步代次和连接身份
检查避免晚到结果覆盖新连接。不得将服务端任意字符串渲染为操作或错误说明。

Options/Popup 支持查看健康、刷新、推送、确认/静默事件、打开当前已配置来源的
登录页面。打开页面仍是显式用户操作，不提交表单或自动登录。旧端不支持的面板
明确标为不可用；现有同步和提醒继续工作。

doctor 增加 runtime/installed/receiver 版本、能力、来源独立阈值、客户端健康、
事件和下一步枚举。检查安装版本时使用实际分发元数据，不把当前 checkout 的
egg-info 当生产已安装版本。JSON 保留机器时间，人类输出使用当地时间。

## R5：验证与发布

完整基线继续通过，新增行为以有意义的故障/恢复/迁移测试验证。必须覆盖：
旧 storage/consumer state 升级，两个应用实例隔离、连接切换中途结果、权限撤销、
401/403/429/5xx、CAS 冲突、离线反馈和重启、新快照未验证、同版本假恢复、
事件确认/静默后再发生、旧端能力缺失，以及敏感字段不进入运维存储/通知/DOM。

使用受控本地站点、临时数据和独立 Chrome profile 做真实扩展界面验收：设置
迁移、来源授权、推送、健康上报、概览、事件处置、invalid→新快照→valid。
不使用真实站点 Cookie、Token、webhook，不将 synthetic 测试冒充真实站点登录验证。

Python 3.11/3.12/3.13、Node 22、Ruff、配置同步、wheel 导入和 manifest 可达 ZIP
构建通过；只做一次全分支独立审查，针对真实发现补充聚焦验证。更新 README、
API/使用/升级文档、示例和 CHANGELOG；记录完整验收矩阵及发布 artifact 哈希。
交付代码、测试、文档和0.5构建后，再准备具体生产/客户端升级动作供最终批准。

## 完成判定

R1–R5 以及上述模块化重构均须有当前分支的直接证据。每项必须在实施计划和最终
验收矩阵中标明命令/场景、结果和边界；不能以测试数量、编译成功、设计文档或
已存在的0.4功能代替完整交付。生产部署或真实网站重新登录若需要用户操作，
应如实区分代码完成、受控验收和生产/目标客户端验收。
