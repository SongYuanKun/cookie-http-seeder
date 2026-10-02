# 更新插件客户端到 0.4.0

应先更新接收端，再更新当前已安装的 Chrome 扩展。保存原数据目录、Token、标签、配置和本机授权；
不要卸载扩展或换一个无关目录重新安装，这可能丢失本机设置/改变扩展 ID。
接收端更新涉及运行服务，按本机部署方式执行并保持数据卷不变。

## 获取经过检查的客户端

```bash
git pull --ff-only origin main
python3 scripts/build_extension.py
```

生成 `dist/cookie-http-seeder-extension-0.4.0.zip` 并输出 SHA256。
CI 的 Python 3.11 作业同时上传 `cookie-http-seeder-extension` artifact，内含相同版本 ZIP；
使用成功 CI 对应的 main 提交，勿拿其他分支或旧作业的包。
构建器只包含 manifest 引用的页面、图标和模块依赖，不包含数据目录或额外文件。

## 原路径更新与重新加载

如果扩展原本加载自这个 checkout 的 `extension/`，pull 后在 `chrome://extensions` 找到该扩展，
点击重新加载。若原加载目录在其他位置，把 ZIP 内容更新到**原扩展目录**，然后重新加载。
不要覆盖 Cookie/Token/Chrome profile；ZIP 是纯扩展代码，解压后根目录应包含 manifest.json。

本机授权仅会因连接/标签变化而失效。保持原 endpoint、Token 和 sender tag；
0.4 新的恢复探测、登录提醒默认关闭，不替用户开启。
仅修改来源提醒阈值不会清空 Cookie 或要求新增域名授权；来源作用域变化仍须重新授权。

## 完成证据

1. 设置页显示 `插件客户端版本：0.4.0`；Chrome 扩展详情同样显示 0.4.0，且无后台模块加载错误。
2. 接收端 `/healthz` 返回 `receiver_version: 0.4.0`；实际鉴权配置返回新能力和正确标签回显。
3. 原 endpoint/标签仍正确；状态查询可用，快照保留；按需要手动推送授权来源。
4. 全发送端概览能查询，来源阈值可见；恢复探测和登录提醒须用户明确开启。
5. 如做登录恢复验收，使用受控测试来源：invalid 后爬虫停止同版本请求，新快照经 valid 验证后恢复。

回执只提供主机、扩展 ID/加载路径、版本、对应 main commit、各项结果和失败原因代码。
不得回传 Token、Cookie、webhook、响应正文或浏览器 storage 导出。

## 给 grok bot 的更新任务

目标：把该用户现有 Cookie HTTP Seeder Chrome 插件原地更新为 main 的 0.4.0。
先核对实际客户端主机、原加载目录和 main 提交，再按上面的顺序更新与重新加载。
服务、数据和凭据沿用原配置；不得清空 profile、卸载扩展、重建 Token 或变更来源授权。
完成后返回“完成证据”中的无凭据回执。机器人身份和路由必须由当前会话或可信配置明确确认，
不能凭名字猜测收件人。只有收到回执或现场确认上述证据，才能标记客户端更新完成。
