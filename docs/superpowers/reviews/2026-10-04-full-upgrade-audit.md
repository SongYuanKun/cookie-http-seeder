# 0.5.0 全面升级最终验收

> 历史阶段记录：本文记录 2026-10-04 合并和部署前的状态。后续 PR #5 已合并至 main（`8cdb6e2`），GTR 接收端和现有 Grok 扩展均已升级至 0.5.0，保留检查通过。本文中的“待批准/未部署/旧 main”描述仅适用于当时；当前发行状态见 [GitHub Releases](https://github.com/SongYuanKun/cookie-http-seeder/releases)。私有现场验收含主机路径，不作为公开附件上传。

2026-10-04，分支 `codex/full-upgrade-0.5.0`，基线 `43e591f`。
R1–R5、七项实施任务的代码、文档、受控验收与构建已交付。
生产安装与Grok现有profile更新是下一步操作；本记录不把开发验收当生产登录恢复。

## 逐项证据矩阵

| 要求 | 实现与直接证据 | 结果与边界 |
|---|---|---|
| 后端状态/HTTP/生命周期分离 | receiver_state / receiver_http / receiver_service；receiver兼容入口；两独立HTTP应用与原运行测试 | 多实例、标签、锁、关闭、IPv6及旧导入回归通过 |
| R1 严格客户端健康协议 | test_client_health、HTTP/运行测试；schema、来源、枚举、时间、0600、重启/老化 | recent仅代表收报时间；损坏为unknown；不读取Cookie用于健康 |
| R1 配置作用域失效 | 持久来源策略摘要；删除、改名、同名域名变更后重启测试；阈值编辑保留测试 | 查询只读；旧元数据不再出现在当前来源；摘要不含原URL/域名 |
| R1 心跳/worker调度 | health_reporter.test；实际Chrome同名alarm到期回执 | 默认0，无能力不请求；断连可恢复；连接切换/晚到响应、撤权正确 |
| R2 登录事件恢复 | test_incidents；实际consume invalid→新快照unverified→新版本valid | 同版本valid/旧反馈不能关闭事件；新快照仅awaiting_validation |
| R2 处置/历史/隔离 | ack/snooze/reopen、旧ID、100条历史、0600、跨标签与重启测试 | 同一未恢复事件在新版本再次invalid时保留确认/静默，实际恢复后新事件新ID |
| R2 扫描/通知/只读 | test_monitoring与test_incidents；无webhook/no-notify扫描、状态文件字节比较 | 事件仍记录；处置抑制重复通知；普通GET/CLI读不推进或写事件 |
| R2 损坏元数据与删来源 | 两种辅助文件损坏的配置删除回归 | 不阻止删除/缩窄；异常仍显示，未假装文件正常 |
| R3 正式消费CLI | test_consumer_cli及实际本地站点consume；消费、状态、flush | 显式来源/标签/规则；退出0/1/2/3/4；不跟随重定向；1MiB和等待边界通过 |
| R3 持久反馈与迁移 | test_consumer_outbox/consumer；schema1→2保留暂停、共享版本预算、磁盘失败、离线/重启 | 最多16记录/每版本3次/一次flush1条；409丢弃，终止blocked，临时pending |
| R3 真正只读状态 | consumer-status无Token/网络/快照/目录/锁/文件写入测试 | 损坏为受控错误；不会借查询增加反馈次数 |
| R3 模块与兼容 | response_rules / consumer_state / consumer_cli；consumer公开兼容导出、旧示例回归 | Python标准库运行，无新增运行依赖 |
| R4 扩展领域边界 | settings / receiver_client / cookie_collector；shared兼容导出 | 授权/URL作用域/CAS/五次重试和原低频恢复探测保持 |
| R4 dashboard/controller | dashboard、runtime_controller、DOM与连接/代次测试 | Options/Popup同模型；断连不沿用valid；无任意远端操作、文本或秘密 |
| R4 界面操作 | 实际Chrome授权、推送、刷新、确认/静默、配置登录入口、Options/Popup | 显式用户打开页面；未自动登录、提交表单或扩大来源权限 |
| R4 doctor | test_diagnostics及仓库外实际wheel；运行/安装/Python/接收端版本、能力、阈值、健康/事件、下一步 | checkout egg-info不算安装；远端字符串/错误类型过滤；JSON UTC、人类当地时间 |
| R4 429分类 | 实际Response429→SyncEngine持久队列→HealthReporter测试 | rate_limited保留，可退避和低频探测；不再误报network_error |
| 0.4升级兼容 | 实际Chrome迁移回执；旧schema迁移/公开接口/旧队列测试 | 原Token/connection/授权/队列哈希一致；新心跳0，保留原恢复/提醒设置 |
| R5 跨版本与审查 | 下列最终测试；一次独立整分支审查，四处真实发现均补充RED/GREEN | 没有反复复审；未把已有0.4能力当新增 |
| R5 发布闭包与文档 | 版本0.5对齐、manifest闭包、ZIP/wheel、仓库外安装导入；README/API/consumer/upgrade/CHANGELOG/CI | 代码与构建可审阅；CI配置更新，当前分支尚未远程运行 |
| R5 生产/Grok升级准备 | 具体目标、备份、核验和回滚见0.5-rollout.md；生产只读版本探测 | 生产实际运行/安装/HTTP版本0.4，服务active且healthz status=ok；未安装0.5到Grok |

## 最终验证命令与结果

工作目录：`/home/kun/.codex/worktrees/crawler-session-recovery/cookie-http-seeder`。
命令在审查修复后的最终代码执行，全部退出0。

| 验证 | 命令/环境 | 结果 |
|---|---|---|
| Python 3.11.15 | /home/kun/.local/bin/python3.11 -m pytest | 337 passed，28.34秒 |
| Python 3.12.3 | /home/kun/vs_code/cookie-http-seeder/.venv/bin/python -m pytest | 337 passed，28.72秒 |
| Python 3.13.16 | 临时独立解释器 -m pytest | 337 passed，28.38秒 |
| Node 22.23.3 | node --test tests/*.test.mjs | 246 passed，0 failed，435.70毫秒 |
| Ruff | ruff check cookie_http_seeder tests scripts | All checks passed |
| 默认配置同步 | python scripts/sync_extension_sources.py --check | in sync；无硬编码网站权限 |
| 差异格式 | git diff --check | 无错误 |
| 扩展构建 | python scripts/build_extension.py | ZIP成功；24个manifest可达文件与模块完整 |
| wheel构建 | uv build --wheel --out-dir dist | 成功，标准隔离构建，无Python运行依赖 |
| wheel仓库外安装 | 私有验证venv安装wheel，/tmp下python -I | 实际installed/runtime=0.5；新模块、包内配置、健康/事件/作用域失效验证通过 |

Python 3.11/3.13使用已有pytest纯Python依赖，关闭第三方pytest插件自动加载。
临时Python3.13与Node22的官方发行包下载SHA-256已核对，未安装到系统PATH。
过程日志存于忽略目录 `.superpowers/sdd/2026-10-04-full-upgrade/*-final.log`。

## 一次独立审查与修复

`upgrade_05_final_review`按基线到`65fbed2`及Task7当时工作区只读审查，结论Needs Changes。
四项真实发现及最终修复：

1. 配置重启/损坏元数据路径：来源策略摘要绑定；只读过滤，损坏辅助文件不阻止删除。
2. 同一未恢复事件重置处置：保留ack/snooze，仅实际恢复后另起新ID。
3. 429误分类：独立rate_limited、安全队列及恢复类别，贯穿真实Response到心跳。
4. 诊断缺少契约：实际dist-info版本、完整安全元数据/能力/阈值和下一步枚举。

新增故障测试先看到失败，修复后通过；最终全套测试包含所有修复。
没有要求或声称第二次独立审查。

## 发布构建

| 文件 | SHA-256 |
|---|---|
| dist/cookie-http-seeder-extension-0.5.0.zip | 8fb0608719253a13a2c1a07c4e668a223ce0cb3cb0e8e3782ef1ec97b0003b65 |
| dist/cookie_http_seeder-0.5.0-py3-none-any.whl | f504aa2c916ef53cf79cb42db2b3bc2f435b4ba6fabd27ac043535e43f1fbd1d |

产物不含测试profile、Cookie、Token、消费者状态或数据目录。
wheel包含构建器的正常license元数据弃用提示；本次构建成功，未为消除提示扩大版本兼容变更。

## 实际浏览器与剩余边界

详细真实Chrome149验收与迁移限制见[浏览器记录](2026-10-04-browser-acceptance.md)。
它验证独立profile、本地合成站点，没有使用或证明真实网站账号恢复。
审查修复后重新执行了全套行为回归和最终包验证；没有重做已通过的整套浏览器流程。
原始回执/截图位于忽略目录 `output/playwright/full-upgrade/`。

生产和Grok更新尚待本版本的最终具体动作批准，分支未推送/未合并/main未改变。
实际站点响应规则仍应按业务配置；需要用户登录的网站不能由同步成功替代登录证明。
额外的跨浏览器加密完整会话分支未包含在本次Cookie链路范围。
