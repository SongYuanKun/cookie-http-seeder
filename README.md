# cookie-http-seeder

[![CI](https://github.com/SongYuanKun/cookie-http-seeder/actions/workflows/ci.yml/badge.svg)](https://github.com/SongYuanKun/cookie-http-seeder/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

**将自己授权的浏览器 Cookie，同步给按 URL 发送请求的 HTTP 客户端。**

0.3 在可配置的结构化 Cookie 快照基础上，加入变化同步、持久化重试、条件写入、状态诊断和爬虫反馈。
浏览器负责登录；扩展负责采集明确授权的非分区 Cookie；接收端保存快照；客户端根据实际 URL 选择 Cookie。
不自动登录、不处理验证码、不主动探测目标网站登录状态，也不复制完整浏览器会话。登录有效性来自爬虫的版本绑定反馈。

```text
日常 Chrome / Chromium
  网站管理 → 用户授权域名 → 手动 / 定时 / 变化后同步
                         │ Bearer Token / HTTP(S)
                         ▼
                  Python 接收端
                  默认 127.0.0.1:18765
                         │ 原子替换、文件权限 0600
                         ▼
                data/{source}-cookies.json
                         │ load_cookie_header(..., url=实际请求地址)
                         ▼
                    HTTP 客户端
```

## 已实现功能

- 网站新增、编辑、删除、启用/暂停、配置导入导出，无须修改源码或重新打包扩展。
- 浏览器运行时逐域名授权；服务端配置与浏览器本地授权分离。
- 保留 Cookie 的 domain、path、hostOnly、secure、httpOnly、session、sameSite、storeId、expirationDate。
- 按 URL 筛选域名、路径、Secure 与过期时间；保留不同路径的同名 Cookie。
- 读取失败不清空；成功读取的空快照会清空旧值；主动清空同时暂停来源。
- 配置版本校验阻止旧配置的推送；清空/配置变更/服务重启使旧版本失效。
- 鉴权在读取正文之前完成；限制正文大小、读取超时、数据结构和域名范围；状态接口不返回凭据。
- Python 标准库运行时，保留 Docker Compose、systemd 和可选飞书通知。

## 安装与启动

要求 Python 3.11+、Chrome/Chromium 120+。测试扩展需要允许加载未打包扩展。

```bash
git clone https://github.com/SongYuanKun/cookie-http-seeder.git
cd cookie-http-seeder
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/cookie-http-seeder init-token
.venv/bin/cookie-http-seeder serve
```

`init-token` 会显示敏感 Token，仅在你自己的终端中执行，不要贴入日志或 issue。
在 Windows 中将 `.venv/bin/` 替换为 `.venv\Scripts\`。

浏览器打开 `chrome://extensions`，启用开发者模式并加载 `extension/` 目录。
打开扩展 → **管理网站与连接**：填写接收端地址、Token，保存并加载配置。
新增来源，填写允许读取的域名和实际目标 URL，然后点击 **保存并授权此来源**。
正常登录网站后点击 **立即推送**。预置房产网站只是示例，可以全部删除。

跨机器推荐 SSH 本地转发：

```bash
ssh -N -L 18765:127.0.0.1:18765 user@crawler-host
```

扩展仍填写 `http://127.0.0.1:18765`。直接远程连接必须使用 HTTPS；接收端本身不终止 TLS，需可信反向代理。

## 消费快照

**推荐每次请求都用实际 URL 重新生成 Header，而不是缓存后发给任意地址。**

```python
from pathlib import Path
from cookie_http_seeder.store import load_cookie_header

url = "https://example.com/account"
header = load_cookie_header(source="mysite", data_dir=Path("data"), url=url)
if not header:
    raise RuntimeError("没有适用于该 URL 的 Cookie，请检查配置或重新推送")
headers = {"Cookie": header}
# 把 headers 交给 HTTP 客户端。禁止自动携带此 Header 跟随重定向；
# 对每个新 URL 重新检查作用域和生成 Header。
```

带禁止重定向的标准库请求示例：

```bash
python examples/consume_cookies.py mysite --url https://example.com/account
```

仅查看本地状态、不发送请求：

```bash
cookie-http-seeder status
python examples/consume_cookies.py mysite
```

CLI `cookie-http-seeder header mysite --url https://example.com/account` 会打印敏感 Header；不要用于公开日志。

## 配置与存储

配置格式（导出不包含接收端地址、Token 或 Cookie）：

```json
{
  "schema_version": 1,
  "sources": {
    "mysite": {
      "label": "我的网站",
      "domains": ["example.com"],
      "target_url": "https://example.com/account",
      "enabled": true
    }
  }
}
```

域名包含其子域名；需要父域 Cookie 时，必须显式授权父域。使用 ASCII 域名（国际化域名使用 punycode），不要填写公共后缀。目标 URL 使用百分号编码，不应包含任何秘密。

服务端配置读取顺序：`--sources` → `COOKIE_HTTP_SEEDER_SOURCES` → 数据目录 `sources.json` → checkout 示例 → 包内默认值。
通过界面修改时，会写回显式 `--sources` / 环境变量指定的文件；否则保存到数据目录 `sources.json`。
配置文件必须可写。运行中请通过界面/API 修改，手工修改后需要重启服务。

数据目录：`--data-dir` → `COOKIE_HTTP_SEEDER_DATA` → 已存在的 `./data` → XDG 数据目录。
全局参数放在子命令前：

```bash
cookie-http-seeder --data-dir /srv/cookie-seeder serve
cookie-http-seeder --data-dir /srv/cookie-seeder status
cookie-http-seeder paths
```

部署模板仍位于 `deploy/`，参见 [部署指南](docs/deploy.md)。使用 Compose 时重新 build；数据卷不要删除。
定时推送默认关闭，启用后至少间隔 15 分钟。变化同步也默认关闭，可在连接设置中单独开启；使用约 30 秒合并窗口，浏览器可能延后闹钟。多账号不属于本阶段。

## 可靠同步与诊断（第二阶段）

扩展只持久化待同步来源和重试状态，不保存旧 Cookie 请求体。每次尝试先读取接收端版本，再重新采集浏览器 Cookie；接收端使用条件写入拒绝旧版本覆盖。
网络错误、408/429/5xx 和快照冲突最多尝试 5 次，间隔按 30/60/120/240 秒加随机抖动增长。
401/403、配置或权限错误停止重试，修复后点击“立即推送 / 重试”；5 次耗尽也需要手动重试。
浏览器关闭时不能发送，重新运行时恢复队列和闹钟。不保证实时同步或严格秒级唤醒。
相同内容不重写 Cookie 文件或反复发送成功通知，仅更新小型操作状态文件中的接收时间。

```bash
cookie-http-seeder --data-dir ./data doctor
cookie-http-seeder --data-dir ./data doctor --local-only
```

扩展“同步状态与诊断”展示任务、下一次尝试、权限、数据新鲜度和爬虫反馈。诊断不输出 Token 或 Cookie；客户端禁止重定向，不使用环境代理。
操作元数据位于 `data/.{source}-sync.json`。当前新鲜度和反馈时效阈值为 24 小时；后续可根据需求增加按来源配置。

爬虫应从**同一次快照读取**取得 Header 和版本，避免把旧请求的结果报到新版本上：

```python
import os
from pathlib import Path
from cookie_http_seeder.client import ReceiverClient
from cookie_http_seeder.store import load_request_credentials

credentials = load_request_credentials(
    source="mysite", data_dir=Path("data"), url="https://example.com/account"
)
# 使用 credentials["cookie_header"] 发出请求；由爬虫自己的站点规则判断结果。
# 确认确实要求重新登录时再报告，不能仅凭 HTTP 200 或任意 403 作判断：
client = ReceiverClient("http://127.0.0.1:18765", os.environ["COOKIE_HTTP_SEEDER_TOKEN"])
# client.report("mysite", credentials["snapshot_version"], "invalid", "session_expired")
```

`valid/logged_in`、`invalid/session_expired` 等结构化反馈只针对对应版本。
旧版本报告会返回 409；新快照重置为未验证；相同内容重新同步不会把旧验证变新。
失效提醒可复用飞书 webhook，并按来源设置持久化的 15 分钟通知冷却。

从第一阶段 0.2 升级时，旧数据文件仍可读取，但扩展和接收端应一起升级：新客户端要求 `conditional_snapshots` 能力，旧客户端缺失条件字段的上传返回 428。
详见 [第二阶段协议与边界](docs/phase2.md)。

## 从 0.1 升级

**服务端和扩展需要一起升级到 0.3。** 旧网络接口 `POST /v1/cookies` 返回 410，不接受无域名信息的 Header 写入。
旧版文件仍可按旧接口读取，但没有 URL 作用域，不能安全地转换成结构化快照。请在管理页面确认目标 URL、重新授权并推送。
老配置缺少 `target_url` 时仍能加载；此时不会生成非空兼容 Header，消费者必须显式提供 URL，或补全配置。

V2 文件仍可包含 `cookie_header`，但它只针对配置的 `target_url`，且只在推送时计算。需要实时过滤过期 Cookie 的消费者应使用 `load_cookie_header(..., url=...)`。
主动清空写入持久化空快照并暂停来源，不删除浏览器 Cookie，也不代替网站注销。

详见 [协议、迁移与边界](docs/phase1.md)。

## 开发与验证

```bash
python -m pytest
npm test
ruff check cookie_http_seeder tests scripts
python scripts/sync_extension_sources.py --check
```

CI 在 Python 3.11/3.12/3.13 上运行测试，并使用 Node 22 测试扩展逻辑。JS 测试使用 Chrome API mock，不代替真实扩展权限和 Service Worker 联调。
`scripts/sync_extension_sources.py` 现在只保持包内默认值与示例一致，不再修改扩展代码或固定权限。

## 安全边界

只处理本人有权使用的会话。Cookie 和 Token 都是凭据，默认仅本机访问，不要将数据目录、Token、飞书 webhook 或真实 Cookie 提交到 Git。
本阶段是单用户、单接收进程、单 Cookie store、非分区 Cookie 工具；不支持跨浏览器 profile 合并，不实现 SameSite 浏览器发送上下文，也不维护公共后缀数据库。
状态分别展示新鲜度和爬虫反馈：传输成功不是登录验证；`valid` 只是最近一次针对该版本的爬虫报告，不是有效性保证。

MIT — see [LICENSE](LICENSE)。安全问题请参阅 [SECURITY.md](SECURITY.md)。
