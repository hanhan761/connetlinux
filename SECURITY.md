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

`bootstrap_workstation.py` 遵守：

- 修改前备份 SSH、防火墙和 Tailscale 配置。
- OpenSSH 首次安装期间不会先以默认配置运行；安全配置验证成功后才启用服务。
- 保留本地控制台，直到管理员客户端第一次密钥登录成功。
- 不配置路由器端口映射；SSH 认证只允许来自 Tailscale 地址段。
- root、密码和键盘交互登录均关闭，只接受 Ed25519 公钥。
- 每一步可重复执行，失败时保留备份并给出回滚命令。

## 管理员权限边界

`codex-admin` 拥有 `NOPASSWD: ALL`，等同于完整 root 管理权限。这是让 Codex 能安装计算环境、管理服务和处理故障的明确设计，不应分配给普通计算用户。

- 管理员私钥只保存在受控客户端，不进入本仓库和目标服务器。
- Linux 目标机只接收 `.pub` 公钥。
- 普通多人账号后续单独创建，默认不授予 `sudo` 或 `docker` 组。
- `docker` 组本身近似 root 权限，只给 `codex-admin` 等受信管理员。
- Tailscale Tailnet 账号应启用多因素认证，并及时移除不再使用的设备。

本项目使用“传统 OpenSSH + Tailscale 网络层”，不启用 Tailscale SSH。这样仍由 `authorized_keys` 明确决定哪一把客户端密钥可以登录。

## 回滚

安装器每次执行会输出备份目录。使用同一脚本回滚：

```bash
sudo python3 bootstrap_workstation.py --rollback /var/backups/connetlinux/时间戳
```

回滚会恢复脚本管理的 SSH drop-in、sudoers、`authorized_keys`、UFW 规则和休眠状态。为避免误删数据，它不会卸载软件，也不会自动删除 `codex-admin` 的主目录；该账号仍没有可用密码。

## 漏洞报告

不要在漏洞报告中附带真实工作站报告、密钥或访问令牌。请只描述复现步骤和已脱敏日志。
