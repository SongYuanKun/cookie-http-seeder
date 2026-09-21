# 部署模板

| 文件 | 用途 |
|---|---|
| `docker-compose.yml` | 接收端容器、宿主机回环端口、根数据卷 `/data` |
| `systemd/cookie-http-seeder.service` | 单机服务模板，使用固定服务账号和数据目录 |
| `cookie-http-seeder.env.example` | 支持的环境变量样例；需要由部署方式显式加载 |

完整步骤见 [部署指南](../docs/deploy.md)。一个接收进程管理同一根目录下所有标签，
不要为每个标签对同一个目录启动多个实例。标签在扩展或读取命令中选择，不是 serve 环境变量。

持久卷应覆盖整个根目录，包括 `senders/`、根 Token、配置、webhook、快照和同步元数据。
升级先停旧服务，不覆盖已有来源配置或删除数据卷；systemd 初始化 Token 时显式使用服务数据目录。
本地时间显示跟随进程/容器时区，参见 [时间设置](../docs/time-display.md)。
