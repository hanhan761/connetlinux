# Workstation Report

报告采用 JSON，当前 `schema_version` 为 `1.0`。

采集器只会把报告写入已经存在的目录，不会创建工作目录。未指定 `--output` 时，报告写入当前目录并使用带主机名和时间戳的文件名。

## 顶层字段

| 字段 | 作用 |
| --- | --- |
| `collector` | 采集器版本和 UTC 时间 |
| `collection_options` | 是否启用了网络或 sudo 只读检查 |
| `system` | 发行版、内核、架构、systemd 和虚拟化 |
| `identity` | 当前用户、UID/GID、用户组和主目录 |
| `cpu` / `memory` / `storage` / `gpu` | 计算资源与容量 |
| `network` / `tailscale` | 本地网络和私网接入状态 |
| `ssh` / `firewall` | SSH 服务、有效配置摘要和防火墙摘要 |
| `power` | 机型、电池、默认 target 和休眠 target |
| `tools` | Docker、tmux、CUDA、Slurm 等工具状态 |
| `readiness` | 生成安装方案所需的布尔检查结果 |
| `warnings` | 需要人工确认的项目，不代表脚本自动修改 |

## 数据最小化

报告只保留配置决策需要的数据。例如：

- 网卡保留 IP，不保留 MAC 地址。
- SSH 主机密钥只保留公开指纹，不保留公钥正文，更不读取私钥。
- 防火墙只保留状态、服务、端口以及 SSH 相关 UFW 行，不导出完整 nftables/iptables 规则。
- Tailscale 不保留登录用户、认证 URL 或密钥。
- Docker 只检查版本和守护进程是否可访问，不读取容器及环境变量。

## 兼容性

- 推荐 Python 3.8 或更新版本。
- Linux 命令不存在时，对应字段会标记未安装或返回空列表，采集不会因此失败。
- 采集器可以在非 Linux 系统执行基本自检，但该报告不能用于生成 Linux 安装器。
