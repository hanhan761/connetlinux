# Windows Server 目标端

Windows Server 2019+ 可作为 yun 的 `server` 目标。yun 使用已固定 host key 的
OpenSSH 公钥认证，并将每个远程脚本显式封装为 PowerShell，因此不依赖 Windows
OpenSSH 默认的 `cmd.exe` shell。

## 目标端一次性准备

由 Windows Server 管理员在提升权限的 PowerShell 中安装并启动 OpenSSH Server：

```powershell
Add-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0
Start-Service sshd
Set-Service sshd -StartupType Automatic
```

确认 `OpenSSH-Server-In-TCP` 防火墙规则存在且仅对获授权网络开放。安装对应客户
端公钥到目标 SSH 用户的 `authorized_keys`，并通过可信渠道核对该服务器的
ED25519 host key。不要关闭 host-key 检查，也不要改用密码认证。

## 登记和验证

```powershell
python scripts/yunctl.py register windows-server `
  --platform windows `
  --host VERIFIED_HOST `
  --user VERIFIED_USER `
  --pem C:\Users\YOU\.ssh\yun_windows-server.pem `
  --known-hosts C:\Users\YOU\.ssh\yun_windows-server.known_hosts `
  --host-fingerprint SHA256:VERIFIED_FINGERPRINT `
  --role server

python scripts/yunctl.py probe windows-server
python scripts/yunctl.py exec windows-server --read-only -- Get-Service sshd
```

`upload` 和 `download` 使用 SCP；传入 Windows 路径时使用目标 OpenSSH Server
接受的路径格式，例如 `C:/Users/VERIFIED_USER/Downloads/result.zip`。

## 当前边界

Windows 目标支持探测、显式只读/写命令和有界文件传输。`compute` 角色、`submit`
和任务生命周期仍是 Linux 专用功能，因为它们依赖 Bash、tmux 和 setsid；不要为
Windows 目标登记 `--role compute`。
