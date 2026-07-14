# Security

## 采集边界

`collect_linux_info.py` 是只读采集器。默认不使用 sudo、不访问外部网站，也不修改 Linux 系统。

只有用户显式传入以下参数时才扩展检查：

- `--network-check`：对 GitHub、Tailscale 和 PyPI 执行 DNS 与 TCP 443 出站连通检查。
- `--allow-sudo`：仅使用 `sudo -n` 读取有效 SSH/UFW/firewalld 状态；不会请求或读取 sudo 密码。

## 报告处理

真实报告会包含主机名、用户名、本地 IP、Tailscale IP、磁盘和硬件信息，应按私有运维资料处理：

- 通过可信私密渠道传输。
- 不要提交到 GitHub Issue、公开聊天或公共仓库。
- 用完后可以删除；采集器本身不上传报告。
- 仓库 `.gitignore` 已忽略 `workstation-report*.json` 和 `reports/`。

## 安装器安全原则

第二阶段安装器必须遵守：

- 修改前备份 SSH、防火墙和 Tailscale 配置。
- 先验证密钥登录，再关闭密码登录。
- 保留本地控制台或第二条 SSH 会话，避免锁死。
- 默认不开放公网 22，只允许 Tailscale 私网访问。
- 使用独立普通用户运行计算，不给共享用户 sudo。
- 每一步可重复执行，失败时给出回滚命令。

## 漏洞报告

不要在漏洞报告中附带真实工作站报告、密钥或访问令牌。请只描述复现步骤和已脱敏日志。
