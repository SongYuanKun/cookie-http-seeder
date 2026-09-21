# 示例配置与消费方法

| 文件 | 用途 |
|---|---|
| `sources.json` | 与包内默认配置同步的默认来源样例 |
| `sources.minimal.json` | 自建站点最小配置模板 |
| `consume_cookies.py` | 按标签读取快照，或向明确的 URL 发送一次请求 |

## 网站配置

普通用户在扩展网站管理页添加来源并授权，无须修改扩展源码或重新生成 manifest 权限。
初次部署可用最小模板创建 default 配置；不要用样例覆盖已有配置。
新标签初次初始化只复制 default 的网站配置，不复制 Cookie；随后各标签独立。

只有修改项目默认示例时才运行：

```bash
python scripts/sync_extension_sources.py
python scripts/sync_extension_sources.py --check
```

该脚本保持包内与示例默认值一致，不生成浏览器站点权限；参见 [贡献指南](../CONTRIBUTING.md)。

## 按发送端标签读取

以下命令在仓库根目录、安装了项目的环境中执行。`--data-dir` 是根目录，不是标签子目录。

```bash
# 只查看 home-pc 的快照信息，不发送网络请求
python examples/consume_cookies.py beike --data-dir ./data --sender-tag home-pc

# 发送一次明确 URL 的请求；示例不自动跟随重定向
python examples/consume_cookies.py beike --data-dir ./data --sender-tag home-pc --url https://www.ke.com/

# 不传标签时读取 default 的历史根目录文件
python examples/consume_cookies.py beike --data-dir ./data
```

目标 URL 必须在该来源域名范围内，Cookie 仍按 hostOnly、路径、Secure 和过期时间筛选。
缺少标签数据不会回退到 default；没有适用 Cookie 时不要发送未经检查的请求。
示例不是完整爬虫，不把 HTTP 状态码单独解释为登录结果。

```python
from pathlib import Path
from cookie_http_seeder.store import load_cookie_header, load_request_credentials

header = load_cookie_header(
    source="beike", sender_tag="home-pc", data_dir=Path("data"), url="https://www.ke.com/",
)
credentials = load_request_credentials(
    source="beike", sender_tag="home-pc", data_dir=Path("data"), url="https://www.ke.com/",
)
# credentials 同时包含实际读取的 cookie_header 和 snapshot_version。
# 不打印它们；反馈客户端也必须使用 sender_tag="home-pc"。
```

反馈的完整示例见 [README](../README.md) 和 [标签协议](../docs/sender-tags.md)。
面向人的更新时间使用当地 `YYYY-MM-DD HH:mm:ss`；文件/API 格式保持原样。
