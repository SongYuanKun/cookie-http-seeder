# 全面升级完成审计：实施前

## 权威状态

用户目标仍是“全面升级功能，重构代码”，未缩减。

2026-10-04 再次检查：主干 `43e591f`，隔离分支
`codex/full-upgrade-0.5.0` 的方案提交 `96133e1`，检查时工作区干净。
包和 manifest 的实际版本仍为 0.4.0。0.5.0 七项实施任务均未开始。
当前没有正在运行的实施/验证任务，也没有 0.5.0 构建或部署作业；不把计划、
会话状态或旧0.4测试结果当成正在执行的0.5任务。

本轮收到用户“确认”，按原范围开始实施；本表保留实施前基线，最终将替换为直接验收证据。

## 逐项证据矩阵

| 要求 | 当前直接证据与判断 | 完成所需的新证据 | 计划 |
|---|---|---|---|
| 后端领域/HTTP/生命周期分离 | receiver.py仍550行；三项计划模块均不存在，未实现 | 两应用HTTP隔离测试、旧导入/CLI/锁/关闭/IPv6回归通过，模块职责与调用路径可直接核对 | Task 1 |
| 客户端健康协议与有界存储 | client_health.py不存在；现有路由未含健康接口，未实现 | schema/来源/时间/字段限制、0600、重启、标签隔离、recent/overdue正确；鉴权HTTP与capability协商通过 | Task 2 |
| 非Cookie心跳与worker恢复 | health_reporter.js不存在，未实现 | 默认关闭、无capability不上报、alarm恢复、连接切换/权限撤销/延迟响应测试；证明心跳不读取Cookie | Task 5 |
| 登录事件生命周期 | incidents.py与事件API不存在，未实现 | invalid→awaiting_validation→新版本valid resolved；同版本valid/旧反馈不假恢复；确认/静默/历史限制/重启/标签隔离测试 | Task 3 |
| 定期事件扫描与通知处置 | 当前监控仅有0.4 stale提醒，无新事件证据 | 不发真实消息的监控集成；无webhook也记录事件、ack/snooze抑制重复提醒且不伪造valid | Task 3 |
| 持久反馈outbox与旧状态迁移 | consumer.py仍schema1、单pending_feedback，未实现 | v1 blocked_version保留；16条边界、每记录3次、一次flush1条、终止错误blocked、旧反馈409丢弃、断网/磁盘/重启测试 | Task 4 |
| 正式爬虫消费命令 | CLI只有原有命令，无consume/consumer-status/consumer-flush，未实现 | 带显式规则的真实synthetic HTTP请求；无重定向/1MiB上限/有界等待/退出码；只读状态查询无需Token且无文件/网络副作用 | Task 4 |
| 分类/消费者持久化/编排边界 | response_rules.py与consumer_state.py不存在，未重构 | 兼容导出、既有消费行为、状态迁移/锁和请求编排的有意义测试通过 | Task 4 |
| 扩展配置/协议/Cookie采集拆分 | shared.js仍319行，计划三个模块均不存在，未重构 | shared旧导出兼容；授权、URL作用域、CAS、五次重试/恢复probe及升级迁移测试继续通过 | Task 5 |
| 统一健康dashboard和runtime controller | 两个模块与get-dashboard消息均不存在，未实现 | 本地/远端状态分开、断连不保留valid、旧代次/连接结果被丢弃、安全动作枚举、无秘密字段测试 | Task 5 |
| Options/Popup事件和操作界面 | 仍为0.4页面，无新界面验收，未实现 | DOM行为和受控Chrome：来源授权、推送、心跳、概览、确认/静默、显式打开来源、新快照实际valid恢复 | Task 6 |
| 版本/能力/阈值诊断 | 新诊断要求未实现 | checkout与实际安装版本不混淆；来源阈值、健康/事件/能力错误和下一步枚举；无凭据输出 | Task 2/7 |
| 0.4 settings/queue/profile/data兼容 | 只有已存在0.4测试证据，不能证明0.5迁移 | 旧storage/state迁移和0.5真实Chrome验证；不清空Token/授权/队列/快照；新健康选项0、原设置值保留 | Task 4/5/6 |
| V2/CAS/显式授权/标签边界 | 现有0.4实现和基线通过，但新代码尚无证据 | 新分支针对401/403/429/5xx、CAS、标签/连接变化、权限撤销及范围缩窄的跨组件回归 | Task 1–6 |
| 跨平台测试、一次独立审查 | 旧基线Python282/Node230通过；无0.5结果 | 当前最终分支Python3.11/3.12/3.13、Node22、Ruff、配置检查；一次整分支审查和真实发现的聚焦修复 | Task 7 |
| wheel/ZIP/文档和发布结果 | 版本0.4；只有方案/计划，未交付0.5 | 0.5元数据对齐、checkout外wheel导入、manifest依赖闭包、artifact哈希、完整准确文档和最终矩阵 | Task 7 |
| 生产与Grok客户端动作 | 0.4曾交付；0.5尚无可审核构建/动作 | 先完成上述交付，再提交具体0.5升级/备份/回滚/目标客户端验收动作，按规则取得批准 | Task 7 |

## 检查方式

- `git status --short`、`git log`和`git worktree list --porcelain`确认实际分支与修改。
- 用Path存在检查核对计划中的8个Python模块和6个扩展模块，检查时全部不存在。
- 查看当前receiver、shared、options的源码与行数，核对现有单文件职责。
- 查看consumer schema/pending字段、CLI add_parser、background消息和实际版本。
- 方案/计划中的未勾选项和模块缺失互相印证，但最终验收不能仅靠文件存在。

## 决策

当前证据明确表明全面升级尚未完成。保持原目标和R1–R5全部范围。
用户本轮确认继续，主线程连续实施七项任务，每项记录直接行为证据，最后做完整完成审计。
