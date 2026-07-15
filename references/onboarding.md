# 新服务器接入：一台服务器一个 PEM

## 1. Establish authority and dependencies

Obtain a unique lowercase target name, host/IP, SSH port, SSH user, network
reachability, roles, and an authorized bootstrap channel such as provider
console, KVM, cloud-init, or an existing administrator account. Do not scan for
hosts.

Ordinary control needs only Python 3 and OpenSSH on the controller and reachable
sshd on Linux. Do not require Tailscale, a cloud SDK, MCP, or `~/.ssh/config`.

Initialize the external registry:

```text
python scripts/yunctl.py init
```

## 2. Generate the target's only client identity

```text
python scripts/yunctl.py keygen TARGET_NAME
```

This creates exactly:

```text
~/.ssh/yun_TARGET_NAME.pem      # RSA-4096 private key in PEM encoding
~/.ssh/yun_TARGET_NAME.pem.pub  # public half installed on the server
```

The PEM is intentionally unencrypted so an authorized agent can use it without
a password prompt, SSH agent, or secret-manager dependency. The tool applies
restrictive local permissions. Never overwrite or reuse a target's PEM, protect
the controller account, keep the private `.pem` only on that controller, and
install only `.pem.pub`. The tool prints paths and the public fingerprint,
never private contents.

## 3. Install only the public half

Through the authorized bootstrap channel, append the single `.pem.pub` line to
the target account's `~/.ssh/authorized_keys`, then enforce:

```text
~/.ssh                 0700
~/.ssh/authorized_keys 0600
```

Prefer a dedicated account. Disable password/root login where operationally
safe and constrain public exposure with the user's chosen network controls.

## 4. Pin the server identity out of band

On the trusted server console, obtain the ED25519 host public key and
fingerprint:

```text
sudo cat /etc/ssh/ssh_host_ed25519_key.pub
sudo ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub -E sha256
```

Verify the fingerprint through an independent trusted channel. Create a
dedicated local file `~/.ssh/yun_TARGET_NAME.known_hosts` containing the verified
host public key prefixed by `HOST` for port 22 or `[HOST]:PORT` otherwise. Do not
trust `ssh-keyscan` alone over the same network being authenticated.

## 5. Register the direct connection

```text
python scripts/yunctl.py register TARGET_NAME \
  --host VERIFIED_HOST \
  --port 22 \
  --user VERIFIED_USER \
  --pem ABSOLUTE_PATH/yun_TARGET_NAME.pem \
  --known-hosts ABSOLUTE_PATH/yun_TARGET_NAME.known_hosts \
  --host-fingerprint SHA256:VERIFIED_FINGERPRINT \
  --role server \
  --description "AUTHORIZED PURPOSE"
```

Add `--role compute` only when remote Bash, tmux, and setsid jobs are authorized;
add `--protected` for production/shared targets. `register` refuses a missing or
non-PEM identity, missing known-hosts file, or fingerprint mismatch.

## 6. Accept

```text
python scripts/yunctl.py probe TARGET_NAME
```

The probe uses direct OpenSSH flags with `-F none`, the exact PEM, no SSH agent,
strict checking, and only the dedicated known-hosts file. For compute targets,
also complete one bounded success/fetch job and one disposable cancellation job.
