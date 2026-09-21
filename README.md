# cookie-http-seeder

[![CI](https://github.com/SongYuanKun/cookie-http-seeder/actions/workflows/ci.yml/badge.svg)](https://github.com/SongYuanKun/cookie-http-seeder/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

**把自己授权的浏览器 Cookie，按发送端标签同步给 HTTP 客户端。**

浏览器负责正常登录，扩展采集明确授权的非分区 Cookie，接收端保存结构化快照，
消费者按实际请求 URL 选择适用 Cookie。不自动登录、不处理验证码、不复制完整浏览器会话。
登录是否有效由爬虫按站点规则反馈，不能由“同步成功”推断。

当前代码包含结构化快照、动态网站配置、可靠同步、当地时间显示、单实例保护和发送端标签。
包与扩展的版本元数据仍为 `0.3.0`；标签支持通过 `sender_tags` 能力协商判断，
不能仅凭版本号判断是否支持。变更记录见 [CHANGELOG](CHANGELOG.md)。

```text
家用 Chrome（标签 home-pc） ─┐
工作 Chrome（标签 work-pc） ─┴─ 共享接收端 Token + X-Sender-Tag
                                ↓
                       Python 接收端
                       默认 127.0.0.1:18765
                                ↓
              data/senders/{tag}/{source}-cookies.json
                                ↓
       load_cookie_header(sender_tag=标签, source=来源, url=实际地址)
                                ↓
                           HTTP 客户端
```

未设置标签时使用 `default`，继续保存到 `data/{source}-cookies.json`，旧文件不迁移。
**标签是存储分组，不是身份或权限边界。** 持有共享 Token 的客户端仍可选择其他标签。

## 快速开始

要求 Python 3.11+、Chrome/Chromium 120+；开发测试另外使用 Node.js 22。
在仓库根目录执行，Python 虚拟环境路径在 Windows 下改用 `.venv\Scripts\`：

```bash
git clone https://github.com/SongYuanKun/cookie-http-seeder.git
cd cookie-http-seeder
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/cookie-http-seeder --data-dir ./data init-token
.venv/bin/cookie-http-seeder --data-dir ./data serve
```

`init-token` 会在你的终端显示敏感 Token，只复制到自己的扩展设置，不要贴入 issue 或日志。
浏览器打开 `chrome://extensions`，启用开发者模式，加载 `extension/`。
进入“管理网站与连接”，填写接收端地址、Token 和发送端标签，例如 `home-pc`。
保存连接并加载配置，新增或选择网站来源，确认域名与目标 URL，显式授权后推送。
不同发送端需要独立保存时使用不同标签；相同标签、相同来源仍操作同一份快照。

浏览器和接收端不在同一机器时，推荐 SSH 本地转发：

```bash
ssh -N -L 18765:127.0.0.1:18765 user@crawler-host
```

扩展仍填写 `http://127.0.0.1:18765`。远程直连要求 HTTPS，接收端本身不终止 TLS；
可信反向代理必须保留 `Authorization` 和 `X-Sender-Tag`。完整部署步骤见 [部署指南](docs/deploy.md)。

## 发送端标签与数据布局

标签为 1–32 位小写字母、数字、`-`、`_`，首位是字母或数字；不接受路径、空白、中文、
大写或 Windows 设备保留名。标签不会被静默转换成另一个名字。

```text
data/
  cookie-receiver.token                 # 所有标签共用的接收端 Token
  feishu-webhook                        # 可选，所有标签共用
  .receiver.lock                       # 接收进程持有；运行中不可删除或替换
  sources.json                         # default 配置；首次管理前可能尚未落盘
  beike-cookies.json                   # default 的旧路径
  .beike-sync.json
  senders/
    home-pc/
      sources.json
      beike-cookies.json
      .beike-sync.json
    work-pc/
      sources.json
      beike-cookies.json
      .beike-sync.json
```

隔离单位为 `(sender_tag, source)`：来源配置、配置版本、快照、快照版本、反馈、
新鲜度和通知冷却分别维护。首次通过鉴权访问新标签时，从 default 复制网站配置，
**不复制 Cookie、反馈或 Token**。之后配置互不跟随。最多自动创建 100 个非默认标签。

切换标签会使旧本机授权和旧队列失效，需要重新授权；不改名、迁移或删除旧标签数据。
读取缺失标签不回退到 default 或其他发送端。详情见 [发送端标签](docs/sender-tags.md)。

## 消费快照与反馈

`data_dir` 始终传根目录；使用 `sender_tag` 选择分组，不要再把标签子目录重复拼入路径。

```python
from pathlib import Path
from cookie_http_seeder.store import load_cookie_header

url = "https://www.ke.com/"
header = load_cookie_header(
    source="beike", sender_tag="home-pc", data_dir=Path("data"), url=url,
)
if not header:
    raise RuntimeError("当前标签没有适用于该 URL 的 Cookie，请检查配置或重新推送")
headers = {"Cookie": header}
# 不打印 headers；每个请求都按实际 URL 重新筛选。
# 不携带此 Header 自动跟随重定向；新 URL 必须重新检查作用域。
```

需要反馈登录状态时，从**同一次原子文件读取**取得 Header 和版本，且反馈使用同一标签：

```python
import os
from pathlib import Path
from cookie_http_seeder.client import ReceiverClient
from cookie_http_seeder.store import load_request_credentials

credentials = load_request_credentials(
    source="beike", sender_tag="home-pc", data_dir=Path("data"),
    url="https://www.ke.com/",
)
client = ReceiverClient(
    "http://127.0.0.1:18765", os.environ["COOKIE_HTTP_SEEDER_TOKEN"],
    sender_tag="home-pc",
)
# 使用 credentials["cookie_header"] 发请求，再由爬虫自己的站点规则判断。
# 仅在确实确认会话过期时执行，不要只看 HTTP 200/403：
# client.report("beike", credentials["snapshot_version"], "invalid", "session_expired")
```

旧版本反馈返回 409；新快照重置验证记录；相同内容重复同步不刷新旧验证的时效。
允许的结果与原因见 [可靠同步协议](docs/phase2.md)。不存在远程下载 Cookie 的读取 API。

## 常用命令

全局参数在子命令前，`--sender-tag` 放在支持它的子命令后。

```bash
cookie-http-seeder --data-dir ./data paths
cookie-http-seeder --data-dir ./data senders
cookie-http-seeder --data-dir ./data status --sender-tag home-pc
cookie-http-seeder --data-dir ./data doctor --sender-tag home-pc --local-only
cookie-http-seeder --data-dir ./data doctor --sender-tag home-pc
cookie-http-seeder --data-dir ./data status --sender-tag home-pc --json
python examples/consume_cookies.py beike --data-dir ./data --sender-tag home-pc
```

`senders` 枚举本地已初始化标签，不表示接收端联网状态或登录验证结果。
`serve`、`init-token`、`paths`、`notify-needed` 不接受 `--sender-tag`；一个接收进程管理全部标签，
Token 和 webhook 位于根目录。`status`、`doctor`、`report`、`header` 支持按标签选择。

`header` 会打印敏感 Header，只在受控本地环境使用：

```bash
cookie-http-seeder --data-dir ./data header beike --sender-tag home-pc --url https://www.ke.com/
```

示例脚本不传 `--url` 时只检查文件，传入后才发送一个不跟随重定向的请求；见 [示例说明](examples/README.md)。

## 网站配置、授权与可靠同步

网站通过管理页或来源配置文件维护，不再编辑扩展源码中的 `SOURCES`。
服务端配置不会自动赋予浏览器权限；“仅授权此来源”不改接收端配置、不清空快照。
“撤销本机授权”可离线使用，只停止本机后续采集；已发出的请求不能撤回，接收端副本保留。
需要清空接收端副本时使用“清空并暂停”，它只影响当前标签的对应来源。

来源配置导入/导出仅含 `schema_version` 和 `sources`，不包含连接信息、标签、Token 或 Cookie。
导入替换当前选中标签的全部来源，修改/删除的来源会清空旧快照，不影响其他标签。
目标 URL、标签和显示名称不应包含秘密。域名包含其子域名；需要父域 Cookie 时显式授权父域。
使用 ASCII 域名（国际化域名用 punycode），不要授权公共后缀。

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

根数据目录解析顺序：`--data-dir` → `COOKIE_HTTP_SEEDER_DATA` → 已存在的 `./data` → XDG 目录。
default 配置解析顺序：`--sources` → `COOKIE_HTTP_SEEDER_SOURCES` → 根目录 `sources.json`
→ checkout 示例 → 包内默认值。显式配置路径需要可写，界面会写回该文件；未显式配置时写根目录。
非默认标签初始化后使用自己的 `senders/{tag}/sources.json`。运行中手工改配置后需重启。

手动、定时和变化同步均可使用。定时与变化同步默认关闭，定时间隔至少 15 分钟。
变化事件先合并约 30 秒再读取完整快照；浏览器休眠、关闭期间不能执行，不保证秒级唤醒。
队列只保存来源与重试状态，不保存旧 Cookie 请求体；每次尝试先读取版本，再重新采集。
临时故障最多尝试 5 次，401/403、授权或配置错误需手动修复。CAS 防止旧版本直接覆盖，
不是设备所有权或同标签多账号合并策略。相同内容不反复改写快照或发送成功通知。

状态面板分别显示授权、同步、新鲜度和爬虫反馈；断连后不沿用旧的“有效”结果。
新鲜度和反馈时效阈值为 24 小时；invalid 提醒按标签/来源冷却 15 分钟。
飞书为可选且尽力交付，不保证送达，也不会自动重新登录。

## 当地时间与升级

面向人的时间统一为 `YYYY-MM-DD HH:mm:ss`，浏览器跟随设备时区，CLI/飞书跟随接收端系统时区。
协议、快照文件和重试截止时间仍保留机器格式；依赖原始时间的脚本使用 `status/doctor/report --json`。
不要把显示字符串写回协议。服务器或容器使用 UTC 时不会自动变成北京时间，见 [时间显示](docs/time-display.md)。

升级前先停止旧接收端并保护好数据备份，更新 Python 包和扩展，重启服务并重新加载扩展。
旧客户端缺少标签时仍使用 default，无需迁移根目录旧文件。非默认标签必须由支持 `sender_tags`
能力及标签回显的新版两端配合；旧接收端不能假装接收标签。更换标签后需重新授权并推送。

从 0.1 升级：旧上传接口返回 410；原始 Header 文件无 URL 作用域，需重新授权并推送。
从 0.2 升级：缺少条件写入字段的客户端返回 428，扩展和接收端一起更新。
V2 文件中的兼容 `cookie_header` 只在推送时针对固定 URL 计算，实时过期筛选应使用读取 helper。

一个根数据目录只允许一个新版接收进程，包括它下面所有标签。文件锁不是分布式锁；
升级时必须停掉不遵守此锁的旧进程，不支持 NFS/SMB 多节点写入。详见 [完整性补全](docs/completeness-review.md)。

## 开发、安全和文档索引

```bash
python -m pytest
npm test
ruff check cookie_http_seeder tests scripts
python scripts/sync_extension_sources.py --check
```

CI 使用 Python 3.11/3.12/3.13 和 Node 22。Chrome API mock 测试不代替真实浏览器授权、
后台唤醒或 Windows/macOS 实机验收；以具体提交的 Actions 结果为准。
同步脚本只核对包内与示例默认值，不再生成扩展站点权限。

只处理有权使用的会话。不要提交数据目录、Token、webhook 或真实 Cookie。
不支持分区 Cookie、跨 Cookie store/profile 自动合并、设备独立 Token、标签所有权、
完整 SameSite 浏览器上下文或公共后缀数据库。跨环境登录态并非对所有网站有效。

| 文档 | 用途 |
|---|---|
| [发送端标签](docs/sender-tags.md) | 分组、协议兼容、标签切换和读取 |
| [部署指南](docs/deploy.md) | Docker、systemd、数据目录和更新 |
| [时间显示](docs/time-display.md) | 当地时间与机器格式边界 |
| [完整性补全](docs/completeness-review.md) | 启动修复、单实例锁、授权撤销和状态面板 |
| [第二阶段协议](docs/phase2.md) | 条件写入、重试、状态反馈；结合标签说明阅读 |
| [第一阶段记录](docs/phase1.md) | 历史 0.2 结构化 Cookie 规则，不单独作为当前协议 |
| [示例](examples/README.md) / [贡献指南](CONTRIBUTING.md) | 消费接入与开发检查 |
| [安全说明](SECURITY.md) / [变更记录](CHANGELOG.md) | 信任边界与尚未独立发布的变更 |

MIT — see [LICENSE](LICENSE)。
