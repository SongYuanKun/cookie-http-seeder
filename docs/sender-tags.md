# 按发送端标签分别存储

标签是用户填写的存储分组，不是新的 Token、账号身份或权限边界。
同一个接收端仍只接受已配置的 Bearer Token；不同 Token 不会因为标签有效而通过鉴权。

## 配置与使用

当前版本元数据仍为 0.3.0，是否支持本功能必须检查 `sender_tags` 能力和标签回显。
接收端和扩展一起更新。在扩展“管理网站与连接”中填写发送端标签，
例如 `home-pc`、`work-pc`。保存连接后重新授权来源，再推送。
标签使用 1–32 位小写字母、数字、横线或下划线，首位为字母或数字；
路径、空白、中文、大写和 Windows 设备保留名会被拒绝，不会悄悄规范化成另一标签。

```text
data/
  cookie-receiver.token             # 所有标签共用的接收端鉴权凭据
  sources.json                     # default 区域配置
  beike-cookies.json               # default 旧文件，保持原路径
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

隔离单位是 `(发送端标签, 网站来源)`。快照、条件写入版本、请求幂等记录、
登录反馈、新鲜度、通知冷却和来源配置均在对应标签内维护。
两个标签同时上传 `beike` 不互相覆盖；清空/暂停/删除其中一个标签的来源不影响其他标签。
配置版本也按标签独立，另一个标签修改配置不会阻塞本标签的上传。

新标签第一次通过鉴权访问时，从 default 复制一份网站配置作为起点并保存；
**不复制任何 Cookie、快照、反馈或 Token**。之后各标签的配置独立维护。
已存在的标签重启后读取自己的配置；default 后续的配置修改不会自动覆盖它。
最多自动创建 100 个非默认标签，目录及配置不能通过符号链接指向其他位置。
一个根数据目录只允许一个接收进程。

相同标签表示相同存储分组，不会拒绝第二台设备使用相同标签。
两台设备使用相同标签、同一来源时，仍通过原有 CAS 协议写入同一快照；
最后成功提交的数据生效。因此需要独立保存的设备必须填写不同标签。

更换标签会建立新的本机连接标识、作废原授权和旧重试队列；不会将旧队列归到新标签。
它不迁移/重命名/删除旧标签数据。已发出的请求可能仍完成，但只携带旧标签。

## 读取与反馈

```python
from pathlib import Path
from cookie_http_seeder.store import load_cookie_header, load_request_credentials
from cookie_http_seeder.client import ReceiverClient

# 根数据目录保持不变，通过 sender_tag 选择发送端。
header = load_cookie_header(
    source="beike", sender_tag="home-pc", data_dir=Path("data"),
    url="https://www.ke.com/",
)
# 返回 None 时，不会回退到其他标签、default 或旧环境变量。
# 不要打印 header，不要携带它自动跟随到其他 URL。

credentials = load_request_credentials(
    source="beike", sender_tag="home-pc", data_dir=Path("data"),
    url="https://www.ke.com/",
)
# 实际请求返回后，按真实业务结果反馈；仍使用同一个标签。
# client = ReceiverClient(endpoint, token, sender_tag="home-pc")
# client.report("beike", credentials["snapshot_version"], "valid", "logged_in")
```

```bash
cookie-http-seeder --data-dir ./data senders
cookie-http-seeder --data-dir ./data status --sender-tag home-pc
cookie-http-seeder --data-dir ./data doctor --sender-tag home-pc --local-only
cookie-http-seeder --data-dir ./data report beike --sender-tag home-pc \
  --snapshot-version <实际使用的版本> --result invalid --reason-code session_expired
```

`--sender-tag` 只用于 status/doctor/report/header 等按标签选择的子命令；
serve、init-token、paths 和 notify-needed 不接受它。不要创建臆造的标签环境变量。
`status`/`doctor`/`report` 的当地时间显示和 `--json` 原始时间输出规则不变。
`header <source> --url <url> --sender-tag <tag>` 可用于本地脚本，但会打印敏感 Header。

## 协议兼容和边界

请求使用 `X-Sender-Tag`，成功响应回显 `sender_tag`；`/v1/sources` 声明 `sender_tags` 能力。
反向代理必须保留此请求头。重复/无效标签返回 400；鉴权失败仍返回 401。

未传标签的老客户端等同于 `default`，继续读写原路径。旧文件无需迁移。
新版客户端使用非默认标签时，会先验证能力和回显；旧接收端忽略标签时停止操作，
不会先把 Cookie 提交到默认区域再提示错误。

拥有共享 Token 的客户端仍可选择别人的标签并操作其中的数据。
这只是数据分组，不提供多租户安全隔离、标签所有权、设备身份校验或多 Token 管理。
第一版也不做标签改名、跨标签合并、自动选择“最新发送端”或默认回退。

本改动包含此前完整性补丁中的启动修复、单进程目录锁、来源状态和本机授权撤销。
最初开发基线是 `ddfe74d`，已通过 PR #2 合并到 main（合并提交 `171eae6`）。
当前快速开始与部署请见 [README](../README.md) 和 [部署指南](deploy.md)。
