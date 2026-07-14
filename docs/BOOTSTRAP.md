# 工作站管理通道

## 目标拓扑

```text
受控管理员客户端
    -> Tailscale WireGuard 私网
    -> 目标机 OpenSSH :22
    -> codex-admin（Ed25519 密钥）
    -> passwordless sudo
    -> 计算环境、服务和作业管理
```

不在路由器或云安全组开放公网 SSH，不依赖不断变化的公网 IP，也不依赖管理员电脑上的临时代理。

## 准备文件

将以下两个文件放在目标 Linux 的同一个普通目录中：

```text
bootstrap_workstation.py
codex-admin.pub
```

只传公钥。管理员客户端上的 `connetlinux_codex_ed25519` 私钥不得离开客户端。

## 预览和执行

预览不会调用任何修改系统的命令：

```bash
python3 bootstrap_workstation.py --admin-public-key-file codex-admin.pub
```

应用配置：

```bash
sudo python3 bootstrap_workstation.py --apply --admin-public-key-file codex-admin.pub
```

脚本仅支持 Ubuntu/Debian。执行中会安装 `openssh-server`、`tmux`、`curl`、`ca-certificates`，并在缺少 Tailscale 时下载其官方 Linux 安装脚本。Tailscale 官方安装文档：<https://tailscale.com/docs/install/linux>。

终端显示 Tailscale 登录 URL 后，在浏览器中完成认证。不要关闭本地终端；脚本随后创建管理员账号、验证 `sshd -t` 和有效配置，并检查 Tailscale IP 的 22 端口。

## SSH 限制

脚本生成 `/etc/ssh/sshd_config.d/00-connetlinux.conf`，关键约束为：

- 仅 `codex-admin` 可登录
- 仅接受 `100.64.0.0/10` 和 Tailscale IPv6 地址段的来源
- 仅接受 Ed25519 公钥
- 禁止 root、密码和键盘交互认证
- 允许本地端口转发，便于管理未暴露的 Web 服务
- 禁止 agent 转发、远程 GatewayPorts、X11 和 tunnel device

OpenSSH 安装阶段会临时 mask 尚未存在的 SSH 单元，避免软件包以默认配置先行监听。脚本验证配置后才 unmask 并启动 SSH。

## 首次客户端连接

脚本成功后会输出一个 `CONNETLINUX_ACCESS` 区块，包含 Tailscale IPv4、账号、公钥指纹、主机密钥指纹和备份目录。维护人员应先核对公钥指纹，再在客户端配置：

```sshconfig
Host connetlinux-workstation
    HostName 100.x.y.z
    User codex-admin
    IdentityFile ~/.ssh/connetlinux_codex_ed25519
    IdentitiesOnly yes
    ServerAliveInterval 30
    ServerAliveCountMax 3
```

测试：

```bash
ssh connetlinux-workstation 'id && sudo -n true && hostname'
```

第一次登录成功前保持 Linux 本地控制台开启。不要通过关闭本地会话来“测试”SSH。

## 备份和回滚

每次 `--apply` 都在 `/var/backups/connetlinux/<UTC 时间>/` 保存：

- 修改前系统和服务摘要
- 原 SSH drop-in
- 原 sudoers 文件
- 原 `authorized_keys`
- UFW 和休眠变更记录

回滚命令：

```bash
sudo python3 bootstrap_workstation.py --rollback /var/backups/connetlinux/<UTC 时间>
```

回滚不卸载 OpenSSH/Tailscale，也不删除管理员主目录，避免破坏其他软件或误删计算结果。恢复后账号仍没有可用密码；如确需删除，应先人工检查 `/home/codex-admin`，再单独处理。

## 管理通道建立后的工作

首次 SSH 验证通过后，再由 Codex 远程完成下列工作，不把它们塞进基础安装器：

1. 检查 `nvidia-smi`、内核模块、驱动和 CUDA 版本是否匹配。
2. 检查根分区大目录，先制定清理或扩容方案，不直接删除数据。
3. 安装 Docker Compose 插件并核对现有容器，不重建未知业务。
4. 设计普通计算用户、目录权限、配额和共享只读环境。
5. 根据并发规模选择 `tmux/systemd-run` 或 Slurm；不要一开始就引入调度集群复杂度。
6. 接入基础监控、磁盘告警和作业日志保留策略。
