# 爬虫接入、登录判定与暂停恢复（0.5.0）

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
- outbox最多16条，按版本合并；同一版本共用最多三次尝试预算，不通过新请求重置。
- 鉴权/协议/配置终止错误blocked；网络错误pending；exhausted需人工检查。旧记录只在409或本地接收端版本证据下丢弃，不静默覆盖invalid反馈。
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

## 0.5 正式 CLI 与只读查询

```bash
cookie-http-seeder --data-dir ./data consume mysite --sender-tag home-pc \
  --url https://example.com/account --rules /private/mysite-rules.json \
  --endpoint http://127.0.0.1:18765 --state-path /private/consumer.json \
  --wait-seconds 300 --poll-seconds 5
cookie-http-seeder --data-dir ./data consumer-status mysite --sender-tag home-pc \
  --endpoint http://127.0.0.1:18765 --state-path /private/consumer.json
cookie-http-seeder --data-dir ./data consumer-flush mysite --sender-tag home-pc \
  --endpoint http://127.0.0.1:18765 --state-path /private/consumer.json
```

正式CLI退出码：0=明确valid；2=缺失/清空/不适用凭据；3=需登录或等待超时；
4=站点验证异常；1=配置、状态锁或运行错误。与旧示例脚本退出码分开。
输出只含受控原因、版本、反馈与暂停状态，不含URL、凭据或正文。
等待0–86400秒、轮询0.1–3600秒，网络无自动重定向、响应上限1MiB。

`consumer-status` 不读取Cookie或Token，不联网，不创建目录、锁或文件；只在内存解释旧schema。
同样的数据根/endpoint/标签/来源决定身份；更改它们或把状态文件交给其他账号会拒绝身份不匹配。
`consumer-flush` 才加载Token并尝试最多一条反馈，累计预算先持久化。

## 登录事件与处置

invalid建立`login_invalid`事件；确认、静默不会恢复暂停。
新快照进入`awaiting_validation`，只有不同于失效版本的实际valid反馈关闭事件。
`validation_expired`与`snapshot_stale`独立；未同步或验证过期不等于登录失效。
普通状态查询不推进事件；接收端写入/反馈与低频扫描推进状态，即使未启用通知也记录。
面板可按事件ID确认、静默1小时或重新提醒；CLI/API见[协议文档](api.md)。
