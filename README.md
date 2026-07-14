# connetlinux

把一台现有 Linux 电脑整理成可通过 SSH 使用的多人计算工作站。

项目采用两阶段流程，避免一条未经检查的安装命令误改 SSH、防火墙或显卡环境：

1. **只读体检**：收集系统、硬件、SSH、Tailscale、防火墙、GPU、存储和休眠状态。
2. **生成安装方案**：根据真实报告生成可审计、可回滚的一键安装命令。

当前仓库完成的是第一阶段。采集器不会安装软件、修改配置、重启服务或开放端口。

## 在你创建的文件夹中执行

你自己创建一个文件夹并进入该文件夹。采集器不会替你创建工作目录。

进入文件夹后执行这一条命令：

```bash
curl -fsSL https://raw.githubusercontent.com/hanhan761/connetlinux/main/collect_linux_info.py -o collect_linux_info.py && python3 collect_linux_info.py --network-check --output workstation-report.json
```

该命令只会在当前文件夹生成：

```text
collect_linux_info.py
workstation-report.json
```

执行完成后，把下面这个文件**私下提供给维护人员**：

```text
./workstation-report.json
```

不要把真实报告提交到 GitHub。报告包含本机用户名、主机名和局域网/Tailscale 地址，但不包含密码、Token、环境变量和 SSH 私钥。

## 可选的只读 sudo 检查

普通用户无法读取某些发行版的有效 SSH 和防火墙配置。只有已经配置免密码 sudo，并且明确需要这部分信息时，才使用：

```bash
python3 collect_linux_info.py --allow-sudo --network-check --output workstation-report.json
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

## 为什么暂时不直接安装

多人计算工作站至少需要先确定以下决策：

- 使用 Ubuntu/Debian、RHEL 系还是其他发行版
- 是否已经存在 SSH、Tailscale、Docker、CUDA 和 NVIDIA 驱动
- 每个使用者是否独立 Linux 账号，谁拥有 sudo
- 是否需要磁盘配额、CPU/内存限制、GPU 分配和任务队列
- 机器是否会自动休眠，断电后是否自动开机
- 仅通过 Tailscale 访问，还是必须开放公网入口

报告确认后，第二阶段会提供一个可重复执行的一键安装器，目标结构为：

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
```

更多字段说明见 [docs/REPORT.md](docs/REPORT.md)，安全边界见 [SECURITY.md](SECURITY.md)。
