# 爬虫接入、登录判定与暂停恢复（0.4.0）

正常请求 → 明确的站点判定 → 使用实际 Cookie 版本反馈。
确认 invalid → 持久化暂停 → 浏览器重新登录并推送 → 新版本先验证 → valid 后恢复。
不自动登录、不切换标签、不自动跟随重定向。站点响应规则须由实际爬虫提供。

## 在自己的爬虫中使用

```python
import os
from pathlib import Path

from cookie_http_seeder.client import ReceiverClient
from cookie_http_seeder.consumer import CookieConsumer, Observation, Response, ResponseRules

client = ReceiverClient(
    "http://127.0.0.1:18765", os.environ["COOKIE_HTTP_SEEDER_TOKEN"], sender_tag="home-pc",
)
rules = ResponseRules.from_document({
    "schema_version": 1,
    # 以下为示例结构，必须换成实际站点证据。
    "valid_json": [{"path": ["user", "loggedIn"], "equals": True}],
    "invalid_json": [{"path": ["code"], "equals": "SESSION_EXPIRED",
                      "reason_code": "session_expired"}],
    "login_redirect_paths": ["/login"],
})
consumer = CookieConsumer(source="mysite", data_dir=Path("data"), client=client, classify=rules)

# send(url, headers) 使用你的 HTTP 客户端；禁止自动重定向，并返回：
# Response(status=response.status_code, body=response.text, headers=dict(response.headers))
# outcome = consumer.request("https://example.com/account", send)
# 仅 outcome.observation.result == "valid" 时继续该账号的采集。
```

可以直接传自己的 `classify(response) -> Observation`，适配已有爬虫业务结构。
Observation 只接受现有固定枚举，不会把响应正文或异常信息上传到接收端。
调用方仍拥有请求方法、参数、响应解析和采集任务调度。

## 明确的响应规则

`ResponseRules.from_document` 支持：

| 字段 | 判定 |
|---|---|
| `valid_body_contains` | 2xx 响应中命中明确的登录成功正文标记 |
| `invalid_body_contains` | 命中明确的登录失效正文标记 |
| `valid_json` | JSON 的指定键路径与值严格匹配（true 不等于 1） |
| `invalid_json` | JSON 失效规则，可指定 `login_required/session_expired/account_mismatch` 原因 |
| `login_redirect_paths` | 3xx 的 Location 路径匹配明确的登录路径；只检查，不跟随 |

同组规则任一命中即可；invalid 优先于 valid，429 优先判定 `error/rate_limited`，
5xx 为 `error/unexpected_response`。未知响应、无成功证据均为 error；网络异常为 error/network_error。
JSON 键路径为 1–16 个键；每组最多 32 条。模板 `examples/response-rules.example.json` 是示例，
不声明任何真实网站采用这个接口。账号不匹配需要真实的站点账号证据，不能自动猜测。

## 暂停状态与反馈

消费者默认把无凭据状态存入 `$XDG_DATA_HOME/cookie-http-seeder/consumer-state/<标识>/state.json`，
该标识由数据根目录、接收端 origin、标签和来源计算，不包含 Token。
可以指定独立私有 `state_path`；目录 0700、状态文件 0600。保护消费者状态，勿在暂停时删除它。
数据卷可保持只读；消费者另需自己的可写状态目录。一份状态同一时刻只允许一个请求，
并发调用收到 `ConsumerBusy`。多进程使用同一状态文件共享暂停；文件锁不支持共享盘多节点。

- 相同 invalid 版本再次请求抛 `LoginRequired`，不会发送 HTTP 请求。
- 新版本允许验证；valid 才清除暂停。新版本暂时网络异常时可再次验证，但采集仍保持暂停。
- 缺失、清空、无适用 Cookie 抛 `CredentialsUnavailable`；不得回退到另一个标签。
- `wait_for_update(url, timeout=300, poll_interval=5)` 有限等待可用新版本，返回 True 不代表登录有效。
- 反馈先持久化，再尝试发送；`feedback_status=pending` 时可调用 `flush_feedback()`。
- 一个报告累计最多三次尝试；`exhausted` 需检查连接后重新进行有意义的验证，不紧密重试。
- 旧版本反馈 409 标记为 discarded，新版本验证记录不被覆盖。
- helper 保留请求响应供调用方使用，但不会把响应放进 repr、状态文件或反馈。
- 网络/限流错误不被当成登录失效；调用方按 outcome 决定普通任务重试策略。

## 可执行示例

```bash
python examples/crawl_with_feedback.py mysite --data-dir ./data --sender-tag home-pc \
  --url https://example.com/account --rules /private/path/mysite-rules.json \
  --endpoint http://127.0.0.1:18765 --state-file /private/path/mysite-consumer.json
```

Token 从环境或服务同根目录文件读取；显式路径用 `--token-file`，不要把 Token 放进命令行。
`--wait-seconds 300` 用于有界等待此前 invalid 后的新快照。示例只发送一次 GET，不跟随重定向，
不会打印 Cookie、Token、URL 或响应正文。退出码：0=本次明确 valid；2=登录暂停/等待超时；1=其他失败。
反馈失败通过 `feedback=` 单独说明，不把同步/反馈交付等同于真实登录状态。

## 监控与发送端选择

来源可配置 `stale_after_seconds`、`validation_ttl_seconds`（60–2592000 秒，缺省 86400）。
登录反馈有效期、新鲜度和 Cookie 的真实网站有效期是不同信息。
`status --all-senders --json`、鉴权 `GET /v1/senders` 和扩展概览只显示元数据；
爬虫须显式选择标签，账号切换需要业务侧明确决策。
飞书 invalid 提醒已由反馈触发，0.4 增加长期未同步提醒；浏览器提醒和低频同步恢复须自行开启。
