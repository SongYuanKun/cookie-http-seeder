# 贡献指南

感谢参与 cookie-http-seeder。修改基于最新 `main` 创建功能分支，保留无第三方运行时依赖的设计。

## 开发与验证

要求 Python 3.11+；扩展逻辑测试使用 Node.js 22，无 npm 安装依赖。

```bash
git clone https://github.com/SongYuanKun/cookie-http-seeder.git
cd cookie-http-seeder
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/python -m pytest
npm test
.venv/bin/ruff check cookie_http_seeder tests scripts
.venv/bin/python scripts/sync_extension_sources.py --check
```

Windows 将 `.venv/bin/` 换成 `.venv\Scripts\`。测试不要使用个人浏览器凭据。
CI 的 Python/Node 测试包含 mock；真实 Chrome 权限、后台恢复和跨系统部署要另做验收。

## 配置与文档同步

网站配置在接收端按标签管理，浏览器运行时显式申请域名权限。
**不存在需要手工同步的扩展 `SOURCES` 常量，不要为新增网站修改 manifest 的固定权限。**
维护默认示例时，以 `cookie_http_seeder/resources/sources.json` 为准：

```bash
python scripts/sync_extension_sources.py
python scripts/sync_extension_sources.py --check
```

脚本只同步 `examples/sources.json` 并检查扩展未硬编码站点权限；不会修改用户数据目录。
部署中的 default 配置和 `senders/{tag}/sources.json` 不是发行时覆盖的样例文件。

涉及功能、参数、存储布局、协议或权限变更时，同时核对：
README 快速开始、examples/README、docs/deploy、CHANGELOG、SECURITY，以及相应专题文档。
历史阶段文档应标注后续覆盖规则，不要把旧行为当成当前承诺。
文档以中文说明为主，保留代码和 API 原名；面向人的时间沿用 `YYYY-MM-DD HH:mm:ss`。

## 模块边界

| 路径 | 职责 |
|---|---|
| `cookie_http_seeder/receiver.py` | 鉴权、按标签路由与接收端管理 |
| `cookie_http_seeder/senders.py` | 标签校验、目录与枚举 |
| `cookie_http_seeder/store.py` / `cookies.py` | 结构化快照、按 URL 读取 |
| `cookie_http_seeder/sync_state.py` | 条件写入、幂等、反馈与新鲜度 |
| `cookie_http_seeder/process_lock.py` | 单数据根目录单写进程 |
| `extension/` | 域名授权、标签设置、同步队列和状态页面 |
| `tests/` | Python、本地 HTTP/进程与 Node Chrome API mock 测试 |
| `docs/` / `examples/` / `deploy/` | 使用说明、样例和部署模板 |

## 提交前

业务变更需要相应测试，尤其是 default 兼容、不同标签隔离、同标签冲突、切换标签丢弃旧任务、
授权撤销、空快照、重复/非法标签、旧接收端能力拒绝以及日志不包含凭据。
不要弱化鉴权、扩大浏览器采集范围或跳过失败测试来使 CI 通过。

请提交集中且可审查的修改，使用 PR 说明原因、验证结果和兼容影响，不直接改主干。
不提交 `data/`、Cookie、Token、webhook、包含凭据的备份/日志；仅使用明显的测试假值。
不把未执行的真实浏览器或操作系统测试标记为通过。安全问题按 [SECURITY](SECURITY.md) 私下报告。
