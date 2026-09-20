> 本文记录 0.2 的第一阶段协议。0.3 新增必填写入前置条件和反馈协议，请以 [第二阶段说明](phase2.md) 为准。

# 0.2 / 第一阶段：协议、迁移与边界

## 设计与接口

所有 `/v1/*`、`/v2/*` 接口均要求 `Authorization: Bearer <token>`。
只有 `GET /healthz` 不要求鉴权。服务端不返回 Cookie 读取 API；消费者通过受保护的本地文件读取。

| 接口 | 行为 |
|---|---|
| `GET /v1/sources` | 配置文档，含 `schema_version:1`、`protocol_version:2`、`sources`、不透明 `revision` |
| `PUT /v1/sources` | 全量替换配置，正文为 `{ "sources": {...}, "revision": "..." }` |
| `POST /v2/cookies` | 以完整、结构化快照替换一个来源 |
| `DELETE /v2/cookies/{source}` | `If-Match` 放当前 revision；持久化清空并暂停来源 |
| `GET /v1/status` | 文件/数量/更新时间元数据，`validation` 始终是 `unverified` |
| `POST /v1/cookies` | 410；旧插件必须升级 |

来源文档是管理配置的唯一服务端依据，浏览器还有独立的本地审批记录和 Chrome 权限。
从服务器拉取来源不会自动授权，也不会自动采集新域名。变更接收端地址或 Token 会清除旧的本地审批。

V2 上传示例（示例值不是实际凭据）：

```json
{
  "schema_version": 2,
  "source": "mysite",
  "config_revision": "从 GET /v1/sources 返回的 revision",
  "complete": true,
  "cookies": [
    {
      "name": "sid", "value": "example-only",
      "domain": "example.com", "path": "/",
      "hostOnly": true, "secure": true, "httpOnly": true,
      "session": true, "sameSite": "lax", "storeId": "0"
    }
  ]
}
```

持久 Cookie 必须是 `session:false` 并包含有限的 `expirationDate`（Unix 秒）；会话 Cookie 不包含过期时间。
相同 `(domain,path,name,storeId)` 的一致重复项会合并；冲突重复项会拒绝。不同路径的同名 Cookie 不会丢失。
分区 Cookie 的 `partitionKey` 非空、多 store 混合、非法值/CRLF、白名单外域名会拒绝整个快照。

## URL 选择

域名 Cookie 允许自身域和子域；hostOnly 仅精确主机。路径使用边界匹配：`/api` 匹配 `/api/me`，不匹配 `/apix`。
Secure 仅用于 HTTPS；读取时过滤已过期 Cookie。Header 中较长路径在前，相同长度保留输入顺序。
扩展按 Chrome 返回顺序采集，合并重叠的白名单查询以免破坏顺序；不自行猜测账号或挑选“最新”的同名 Cookie。

消费者必须传入实际请求 URL。示例禁止自动跟随重定向；另一个地址需要重新选择 Cookie，不能复用上一跳 Header。
URL 必须为 ASCII/百分号编码，不能含凭据或 fragment；含点路径段的 URL 必须由调用方先规范化。
本阶段不支持 IPv6 目标主机配置（接收端本身可监听 `::1`，扩展 HTTP 端点使用 localhost/127.0.0.1）。

## 空值、清空与配置修改

| 场景 | 结果 |
|---|---|
| 完整采集成功且 `cookies:[]` | 写空快照；来源仍启用，后续正常登录可再次同步 |
| 权限不足、读取异常或权限中途撤回 | 不发送快照，旧文件不变，显示错误 |
| `complete` 不为 true | 拒绝请求，旧文件不变 |
| 用户主动清空 | 写持久化空快照并暂停；必须显式重新启用 |
| 修改/删除来源配置 | 清空所有受影响来源的旧快照，防止旧作用域 Header 残留 |

清空后的 V2 文件会覆盖旧的环境变量 Header，防止从备用路径再次拿出旧凭据。
每次配置成功写入和服务启动都生成新的随机 revision；即使配置内容先改掉再改回，旧 revision 也不会恢复有效。
上传和管理操作在同一进程锁内串行检查和落盘。409 要求重新加载并由用户审批，不自动绕过冲突。

只运行一个接收进程管理同一数据目录。配置与 Cookie 文件分别原子替换，不构成跨文件事务。
写配置失败前可能已经清空受影响的 Cookie，此时显示失败并需要重新推送；不会为保留数据而继续提供不安全的旧 Header。
不提供多设备快照顺序协议、自动重试或分布式锁；这些留到后续阶段。

## 基础安全

- 默认 loopback；远程使用 SSH 隧道或经过认证配置的 HTTPS 反向代理。扩展拒绝远程明文 HTTP、凭据 URL、重定向。
- 单个管理 Token 拥有配置/写入权限，不等同于多租户授权；后续阶段再增加设备级 Token。
- 先鉴权再读取正文，恒定时间比较 Token，拒绝重复认证字段。Origin 若存在必须是 Chrome 扩展来源；Origin 不是身份凭据，仍需 Token。
- 正文上限 512 KiB；读取超时 5 秒；Cookie 数最多 1000；Cookie 值最长 16384；来源最多 100。
- 拒绝多 Content-Length、Transfer-Encoding、重复 JSON 键、非法 JSON 常量和反射敏感错误；状态/HTTP访问日志不输出 Cookie/Token。
- 文件通过临时文件+原子替换写入，POSIX 上使用 0600、目录 0700。Windows 文件 ACL 不由 POSIX mode 完整表达，需要自行限制数据目录访问。
- 本工具不调用目标网站验证接口，因此没有用户配置 URL 的服务端代请求功能。
- 配置导出仅含来源。不要在 label、target_url 或域名字段中放 Token、签名链接或其他秘密。

## 明确不支持

不读取 localStorage/sessionStorage，不尝试自动登录/验证码；Chrome 默认 store 中的非分区 Cookie 是采集范围。
CHIPS、跨 profile、多 store、完整 SameSite 上下文、浏览器指纹与设备绑定不在第一阶段范围。
浏览器 HttpOnly 是 Cookie 元数据，并不保证该 Cookie 可跨机器使用。
白名单校验使用主机/子域边界，不维护 Public Suffix List；用户应填自己需要的实际站点，而非 `co.uk` 等公共后缀。

## 升级与回滚

1. 停止旧接收端，保存受保护的数据目录备份，不把备份提交到 Git。
2. 更新 Python 包与未打包扩展，重新启动服务并重新加载扩展。
3. 在网站管理页检查来源域名、目标 URL、接收端，重新授权后推送。
4. 将客户端改为 `load_cookie_header(..., url=实际地址)`。旧文件缺少作用域时会报迁移错误，而不是猜测。

旧版读取接口仍能读取旧文件；V2 的兼容 `cookie_header` 只针对固定目标，不能重定向复用，过期过滤需要新 helper。
回滚程序到 0.1 前应停止新版并用受保护的旧数据备份恢复；不要期望 0.1 能正确理解 V2 的结构化语义。

## 验证

Python 单元与本地 HTTP 集成：`python -m pytest`。
扩展逻辑（Chrome API mock）：`node --test tests/extension.test.mjs`。
静态检查：`ruff check cookie_http_seeder tests scripts`；配置一致性：`python scripts/sync_extension_sources.py --check`。

真实浏览器验收应另做：加载扩展、点击可选域名授权/拒绝、HttpOnly 采集、同名不同路径、撤销权限、清空暂停、重启后重载配置。
自动化 mock 测试不声称这些真实 Chrome 权限交互已经完成。
