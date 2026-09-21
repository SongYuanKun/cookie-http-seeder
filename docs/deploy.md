# 部署指南

一个接收进程管理一个根数据目录及其全部发送端标签。消费者仍读本地结构化 JSON，
不需要新增数据库或远程 Cookie 下载接口。标签规则见 [发送端标签](sender-tags.md)。

## 数据目录、配置与凭据

```text
$data/
  cookie-receiver.token             # 全标签共用，0600
  feishu-webhook                    # 可选，共用的通知配置，0600
  .receiver.lock                    # 运行中不可删除/替换
  sources.json                     # default 配置，可由显式 --sources 指定其他文件
  {source}-cookies.json             # default 历史路径
  .{source}-sync.json
  senders/
    home-pc/
      sources.json
      {source}-cookies.json
      .{source}-sync.json
    work-pc/
      sources.json
      {source}-cookies.json
      .{source}-sync.json
```

根目录解析：`--data-dir` → `COOKIE_HTTP_SEEDER_DATA` → 已存在的 `./data`
→ `$XDG_DATA_HOME/cookie-http-seeder`（通常为 `~/.local/share/cookie-http-seeder`）。

`--sources` 和 `COOKIE_HTTP_SEEDER_SOURCES` 指定 default 的可写配置文件；未指定时可从根目录、
checkout 示例或包内默认值加载，界面修改写入根目录 `sources.json`。
非默认标签首次通过鉴权访问时仅复制 default 网站配置，此后用自己的配置，不复制凭据。
更换默认配置不会自动更新已有标签。运行中手工修改配置后需要重启服务。

**Token 和 webhook 始终在根目录或显式覆盖路径，不需要为每个标签创建 Token。**
标签是共享 Token 下的存储分组，不限制其他 Token 持有者的访问，不能用作多租户隔离。

## Docker Compose

从仓库根目录初次部署：

```bash
mkdir -p data
# 仅首次且没有配置时创建；已有文件不得覆盖
if [ ! -e data/sources.json ]; then
  cp examples/sources.minimal.json data/sources.json
fi
docker compose -f deploy/docker-compose.yml up -d --build
# 只在自己的终端查看 Token，勿保存到公开日志
docker compose -f deploy/docker-compose.yml exec cookie-http-seeder \
  cookie-http-seeder --data-dir /data init-token
```

模板将 `../data` 挂载到容器 `/data`，容器内监听 `0.0.0.0:18765`，
宿主机只发布 `127.0.0.1:18765`，不是直接对公网开放。
消费者可挂载同一根数据卷为只读，使用 `sender_tag` 选分组；
运行用户必须能够读取 0600 文件，不要用放宽凭据权限的方法解决用户不匹配。

标签、Token、Cookie 和状态都在持久卷内，升级时不要删除：

```bash
docker compose -f deploy/docker-compose.yml stop cookie-http-seeder
docker compose -f deploy/docker-compose.yml up -d --build
```

更新前请用自己的部署方式保护好完整数据备份。升级不重新复制样例来源配置。

## systemd

下面是**初次部署**示例；已有服务账号、虚拟环境或配置时应复用，不覆盖已有数据。
仓库根目录可读，系统具备 Python venv 支持。示例使用 `/opt` 下虚拟环境，
而提供的 unit 默认可执行路径是 `/usr/local/bin/cookie-http-seeder`，因此用 drop-in 明确覆盖。

```bash
id -u cookie-seeder >/dev/null 2>&1 || \
  sudo useradd --system --home /var/lib/cookie-http-seeder --shell /usr/sbin/nologin cookie-seeder
sudo python3 -m venv /opt/cookie-http-seeder/.venv
sudo /opt/cookie-http-seeder/.venv/bin/pip install .
sudo install -d -o cookie-seeder -g cookie-seeder -m 700 /var/lib/cookie-http-seeder
# 仅首次执行；已有 sources.json 时跳过此步
sudo install -o cookie-seeder -g cookie-seeder -m 600 \
  examples/sources.minimal.json /var/lib/cookie-http-seeder/sources.json
sudo cp deploy/systemd/cookie-http-seeder.service /etc/systemd/system/
sudo mkdir -p /etc/systemd/system/cookie-http-seeder.service.d
sudo tee /etc/systemd/system/cookie-http-seeder.service.d/venv.conf >/dev/null <<'EOF'
[Service]
ExecStart=
ExecStart=/opt/cookie-http-seeder/.venv/bin/cookie-http-seeder serve
EOF
sudo -u cookie-seeder /opt/cookie-http-seeder/.venv/bin/cookie-http-seeder \
  --data-dir /var/lib/cookie-http-seeder init-token
sudo systemctl daemon-reload
sudo systemctl enable --now cookie-http-seeder
```

**初始化 Token 的命令必须显式使用 `/var/lib/cookie-http-seeder`。**
单纯 `sudo -u cookie-seeder cookie-http-seeder init-token` 不保证继承 unit 的工作目录/环境，
可能在另一个数据目录生成 Token。unit 中已设置正确的工作目录与 `COOKIE_HTTP_SEEDER_DATA`。

更新安装前先停止服务，安装新代码后再启动；不要重新覆盖配置：

```bash
sudo systemctl stop cookie-http-seeder
sudo /opt/cookie-http-seeder/.venv/bin/pip install .
sudo systemctl start cookie-http-seeder
```

## 裸进程与跨机器连接

```bash
cookie-http-seeder --data-dir ./data init-token
cookie-http-seeder --data-dir ./data serve
```

同根目录第二个新版接收进程即便换端口也会被拒绝。锁是本地协作锁，不是分布式锁；
不支持 NFS/SMB 多节点写入。升级前停止旧版，不能假设旧版也遵守新锁。

从浏览器机器转发：

```bash
ssh -N -L 18765:127.0.0.1:18765 user@crawler-host
```

扩展填写 `http://127.0.0.1:18765`，然后设置该发送端标签并授权来源。
远程直连必须用可信 HTTPS 反向代理并限制网络访问，保留 `Authorization` 与 `X-Sender-Tag`，
禁用凭据记录与缓存。接收端自身不提供 TLS，也不应直接暴露到不可信网络。

服务端可配置 IPv6 监听，例如 `serve --host ::1`；这不代表扩展和 Python 客户端的
接收端 URL 校验已支持 IPv6 字面量。跨机器优先使用上面的 IPv4 本地隧道。

## 验收、当地时间与通知

```bash
cookie-http-seeder --data-dir ./data paths
cookie-http-seeder --data-dir ./data senders
cookie-http-seeder --data-dir ./data status --sender-tag home-pc
cookie-http-seeder --data-dir ./data doctor --sender-tag home-pc
cookie-http-seeder --data-dir ./data status --sender-tag home-pc --json
```

先从扩展建立标签并推送，再用 CLI 查看该标签；未初始化的标签不会自动回退到 default。
`/healthz` 只说明进程能响应，不说明 Token、标签数据或网站登录有效。
服务和读取命令必须使用同一根目录，读取与反馈使用同一标签和同一快照版本。

浏览器显示跟随设备时区；CLI 和飞书跟随接收端系统/容器时区。支持 POSIX TZ 且具有相应
时区数据库的环境可设置 `TZ=Asia/Shanghai`，例如：

```bash
TZ=Asia/Shanghai cookie-http-seeder --data-dir ./data serve
```

Compose 中需在服务 `environment` 显式添加 `TZ: Asia/Shanghai` 并确认镜像具备时区数据；
systemd 可在 drop-in 的 `[Service]` 中设置 `Environment=TZ=Asia/Shanghai` 后重启。
不能只改宿主机就假定容器自动跟随。显示采用 `YYYY-MM-DD HH:mm:ss`，
脚本用 `--json` 保留机器格式，详见 [时间显示](time-display.md)。

飞书 webhook 文件放根目录 `feishu-webhook`，内容为受支持的机器人地址，文件权限必须为 0600。
不要把实际 webhook 提交到仓库；无此文件时跳过通知，`serve --no-notify` 可关闭通知。
通知包含标签/来源以区分发送端，失效通知冷却分别维护；通知不保证送达。

## 升级兼容与安全检查

未带标签的旧客户端使用 default；新标签不迁移旧文件，也不共享旧 Cookie。
使用非默认标签时两端都需要 `sender_tags` 能力及回显检查；不要只看 0.3.0 版本号。
更新后重新加载扩展，切换标签时重新授权。0.1 上传接口返回 410，缺条件写入字段返回 428。

保护整个数据根目录和备份：POSIX 目录 0700、凭据文件 0600，Windows 另设 ACL。
不同可信边界需要独立 Token/服务/目录，不能靠标签提供权限隔离。
不把数据卷、Cookie、Token 或通知 URL 上传到 Git；详见 [安全说明](../SECURITY.md)。
