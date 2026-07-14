# connetlinux

把一台现有 Linux 电脑整理成可通过 SSH 使用的多人计算工作站。

项目采用两阶段流程，避免一条未经检查的安装命令误改 SSH、防火墙或显卡环境：

1. **只读体检**：收集系统、硬件、SSH、Tailscale、防火墙、GPU、存储和休眠状态。
2. **建立管理通道**：根据报告安装 OpenSSH/Tailscale，创建可审计、可回滚的专用管理员入口。

采集器不会安装软件、修改配置、重启服务或开放端口。第二阶段安装器默认也只显示计划，必须显式传入 `--apply` 才会修改系统。

## 手动传入脚本后执行

你自己创建一个文件夹，把 `collect_linux_info.py` 传入该文件夹，然后进入该文件夹。

直接执行：

```bash
python3 collect_linux_info.py --output workstation-report.json
```

这是默认的离线采集模式，不访问 GitHub、Tailscale 登录服务或 PyPI。执行时会逐项显示当前检查进度。

当前文件夹最终包含：

```text
collect_linux_info.py
workstation-report.json
```

执行完成后，把下面这个文件**私下提供给维护人员**：

```text
./workstation-report.json
```

不要把真实报告提交到 GitHub。报告包含本机用户名、主机名和局域网/Tailscale 地址，但不包含密码、Token、环境变量和 SSH 私钥。

只有后续确实需要检查出站网络时，才运行：

```bash
python3 collect_linux_info.py --network-check --output workstation-report.json
```

## 可选的只读 sudo 检查

普通用户无法读取某些发行版的有效 SSH 和防火墙配置。只有已经配置免密码 sudo，并且明确需要这部分信息时，才使用：

```bash
python3 collect_linux_info.py --allow-sudo --output workstation-report.json
```

`--allow-sudo` 只会调用 `sudo -n`，不会弹出密码输入，也不会修改配置。

如果 `--output` 指向不存在的目录，脚本会直接报错，不会自动创建目录。

## 收集内容

- Linux 发行版、内核、架构、虚拟化和启动时间
- CPU、内存、Swap、磁盘与挂载点
- NVIDIA GPU、驱动和显存；没有 NVIDIA 时记录显示控制器
- 网卡、本地 IP、默认路由和 Tailscale 状态
- OpenSSH 安装、服务、监听端口、白名单配置项和主机公钥指纹
- UFW/firewalld 状态中与 SSH 有关的摘要
- Docker、Compose、tmux、编译器、CUDA 和 Slurm 工具可用性
- systemd 休眠目标、机型、电池和时间同步状态

## 明确不收集

- SSH 私钥、密码、Token、Cookie、API Key
- `.env` 内容和完整环境变量
- Shell 历史、浏览器数据、项目代码和业务文件
- 公网 IP 查询和任何入站端口扫描
- Docker 容器配置、镜像环境变量和应用日志

## 建立 Codex 管理通道

完成体检并确认目标机是受支持的 Ubuntu/Debian 后，只把下面两个文件放进目标 Linux 上同一个新文件夹：

```text
bootstrap_workstation.py
codex-admin.pub
```

`codex-admin.pub` 是管理员客户端生成的 Ed25519 **公钥**。绝对不要把无 `.pub` 后缀的私钥传到 Linux、聊天窗口或 GitHub。

先预览（不改系统）：

```bash
python3 bootstrap_workstation.py --admin-public-key-file codex-admin.pub
```

确认后一次执行：

```bash
sudo python3 bootstrap_workstation.py --apply --admin-public-key-file codex-admin.pub
```

执行到 Tailscale 时，终端会显示登录 URL。用浏览器登录同一个 Tailnet，等待脚本继续。成功后终端会输出：

```text
CONNETLINUX_ACCESS_BEGIN
host=100.x.y.z
user=codex-admin
key_fingerprint=SHA256:...
host_key=...
backup=/var/backups/connetlinux/...
CONNETLINUX_ACCESS_END
```

只需把这一段私下发给维护人员。它不含密钥，但包含私网地址和主机指纹，不要贴到公开 Issue。

安装器会：

- 创建没有可用密码哈希的 `codex-admin` 独立账号并安装公钥
- 授予该账号免密码 `sudo`，使 Codex 可以完整管理服务器
- 使用传统 OpenSSH，通过 `AllowUsers` 只接受 Tailscale 地址来源
- 禁止 root、密码和键盘交互登录
- UFW 已启用时只新增 `tailscale0:22` 规则；UFW 未启用时不会擅自启用
- 屏蔽休眠/挂起，避免长计算被桌面电源策略中断
- 修改前保存状态和相关配置到 `/var/backups/connetlinux/`

它不会修改 Docker 工作负载、NVIDIA/CUDA、项目文件、磁盘数据或路由器端口映射。详细步骤见 [docs/BOOTSTRAP.md](docs/BOOTSTRAP.md)。

## 后续多人计算

多人计算工作站至少需要先确定以下决策：

- 使用 Ubuntu/Debian、RHEL 系还是其他发行版
- 是否已经存在 SSH、Tailscale、Docker、CUDA 和 NVIDIA 驱动
- 每个使用者是否独立 Linux 账号，谁拥有 sudo
- 是否需要磁盘配额、CPU/内存限制、GPU 分配和任务队列
- 机器是否会自动休眠，断电后是否自动开机
- 仅通过 Tailscale 访问，还是必须开放公网入口

管理通道验证后，再通过 SSH 处理 GPU 驱动、磁盘空间和多人账号。目标结构为：

```text
用户电脑
    -> Tailscale 私网
    -> OpenSSH
    -> 独立 Linux 用户目录
    -> tmux/systemd-run（轻量）或 Slurm（多人排队）
    -> 共享只读软件环境 + 独立任务与结果目录
```

## 本地开发验证

项目不依赖第三方 Python 包：

```bash
python3 -m unittest discover -s tests -v
python3 collect_linux_info.py --help
python3 bootstrap_workstation.py --help
```

更多字段说明见 [docs/REPORT.md](docs/REPORT.md)，安装流程见 [docs/BOOTSTRAP.md](docs/BOOTSTRAP.md)，安全边界见 [SECURITY.md](SECURITY.md)。
