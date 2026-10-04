# 0.5 浏览器闭环验收

2026-10-04，Chrome for Testing 149.0.0.0，独立 Xvfb 与临时 profile；只使用本地合成 Cookie/Token，真实账号没有参与。

| 场景 | 直接结果 |
|---|---|
| 旧 0.4 storage → 0.5 | Token、connectionId、approvedSources、syncQueueV1 哈希全部一致；旧 blocked 队列保留；healthReportMinutes=0 |
| 浏览器授权 | UI点击“仅授权”，在原生 Chrome 权限提示中允许，permissions.contains=true |
| 手动推送 | succeeded，接收端1个合成Cookie；CAS产生有效版本 |
| worker alarm 心跳 | 强制下一次同名alarm到期，上报recent / sync_phase=succeeded；没有读取Cookie用于心跳 |
| 爬虫接入 | 正式consume真实请求合成站点，valid退出0；失效标记invalid退出3、paused=true |
| 确认/静默 | UI携带当前incident_id，acknowledged/snoozed成功；仍显示需要恢复 |
| 显式登录入口 | 点击打开已配置的本地/login，生成新的合成会话；没有自动登录动作 |
| 新快照 | validation=unverified，incident=awaiting_validation |
| 新版本实际验证 | consume返回valid/paused=false；活动事件0，历史resolved |
| Options / Popup | 同一dashboard DTO均显示“爬虫最近报告有效”；心跳alarm在显式worker重载后恢复 |

原始安全回执和截图保存在本工作树 `output/playwright/full-upgrade/`，不加入发布包。
截图：`recovered-health.png`、`popup.png`。升级比较回执：`migration-receipt.json`。

## 发现并修正

- Chrome存储改变对象字段顺序：批准比较改为规范字段顺序，保留真实域名/目标变化仍需重新授权。
- Chrome拒绝以split HTTP/HTTPS可选声明请求wildcard scheme：改为等价的`*://*/*`可选声明，实际申请仍限定来源域名。
- HTML pattern v模式要求转义连字符：已修正。
- 覆盖文件并重启浏览器会保留旧worker：必须在开发者模式显式“重新加载”扩展；文档写入该步骤。
- Dashboard刷新同步更新管理表，避免同页旧验证结果停留。

## 边界

建立0.4迁移基线时，临时旧扩展副本仅先应用了等价可选权限声明，以便在Chrome149完成原生授权；其原批准比较、队列和存储仍是0.4。基线的blocked队列由旧批准比较缺陷触发，升级后保留，并经显式手动推送恢复。
此验收不证明真实站点账号已经恢复；网站规则须由使用者按实际响应配置。未安装到Grok生产profile。
