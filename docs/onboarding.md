# 从 Chrome 到爬虫的接入案例

## 场景和边界

一台 Chrome 正常登录自己有权访问的网站，扩展把用户明确授权的 Cookie 同步给接收端，爬虫读取该来源的版本化快照。使用多个电脑时，按 `sender_tag` 分组。

下面使用保留域名 `example.com` 和虚构响应字段说明接入，不能直接用于判断某个真实网站的登录状态。实际来源应根据已获许可的业务响应配置规则。实际 Grok 客户端升级与受控 Chrome 验收已完成；它们分别证明版本/连接和受控流程，不代表所有网站都能跨环境登录。

## 1. 安装同一版本的两端

从 [v0.5.0 Release](https://github.com/SongYuanKun/cookie-http-seeder/releases/tag/v0.5.0) 下载 wheel、扩展 ZIP 和 `SHA256SUMS`，校验下载文件的 SHA256 后安装：

```bash
python -m venv .venv
.venv/bin/python -m pip install --no-deps cookie_http_seeder-0.5.0-py3-none-any.whl
.venv/bin/cookie-http-seeder --data-dir ./data init-token
.venv/bin/cookie-http-seeder --data-dir ./data serve
```

Windows 使用 `.venv\Scripts\python.exe` 和 `.venv\Scripts\cookie-http-seeder.exe`。`init-token` 仅在首次初始化时执行，它会向自己的终端显示敏感 Token；不要把它放进公共记录。

将扩展 ZIP 解压到固定目录，在 `chrome://extensions` 开启开发者模式并加载该目录。升级时更新原加载目录并点击重新加载，保留已有 profile、连接和授权。

## 2. 配置和授权来源

在扩展管理页填写回环接收端、自己的 Token 和 `home-pc` 标签；接收端在其他机器时先建立 [SSH 本地转发](../README.md)。新增来源 `mysite`，域名为实际站点域名，目标为有明确登录语义的业务 URL。只授权该来源，再正常登录并推送。

检查面板里的同步状态和接收时间。此时登录结果仍应显示“待验证”；同步成功不是有效登录反馈。

## 3. 接入真实响应规则

以 [response-rules.example.json](../examples/response-rules.example.json) 为结构模板，把有效、失效和错误响应条件替换为实际站点证据。不能仅凭 HTTP 200 判断有效，也不能把限流或未知页面归为登录失效。

```bash
cookie-http-seeder --data-dir ./data consume mysite --sender-tag home-pc \
  --url https://example.com/account --rules /private/mysite-rules.json \
  --endpoint http://127.0.0.1:18765 --state-path /private/mysite-consumer.json
```

实际使用时替换示例 URL、规则和私有路径。命令默认读取接收端数据目录的 Token；完整 Token 来源和参数见 [consumer-recovery.md](consumer-recovery.md)。消费者把反馈绑定到实际读取的 snapshot version，并在接收端暂不可用时保留有界 outbox。

## 4. 验证登录恢复

1. 在授权的测试来源实际失效时确认反馈为 `invalid`，消费者进入暂停。
2. 再次运行同版本消费时，确认没有重复向业务站点发请求。
3. 在原浏览器正常重新登录并推送新的快照。
4. 新版本先接受实际业务验证；只有 `valid` 才恢复。新推送或未知响应不能直接解除暂停。
5. 使用 `consumer-status` 查看本地暂停/队列；恢复接收端后用 `consumer-flush` 补发反馈。

管理员手动暂停和站点访问限制需由授权操作者处理，不能由登录恢复逻辑自动绕过。

## 5. 提供可复核反馈

在 [Usage feedback](https://github.com/SongYuanKun/cookie-http-seeder/issues/new?template=usage_feedback.md) 记录版本、系统、接入类型、实际状态转换和最小复现。只发布脱敏结果，不发布快照、Token、完整响应或浏览器 storage。受控演示、维护者自身使用和独立外部用户反馈应如实注明。
