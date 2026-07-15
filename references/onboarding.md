# 新服务器接入与密钥

## 1. Establish authority

Obtain a unique target name, hostname/IP, SSH user, network scope, target roles,
and an authorized bootstrap channel such as a provider console, serial/KVM,
cloud-init, or an existing administrator account. Mark production or shared
infrastructure as protected. Do not onboard a host discovered by scanning.

Initialize the user-local registry once:

```text
python scripts/yunctl.py init
```

## 2. Generate one client key per trust boundary

Generate the pair on the control computer, not on the server:

```text
python scripts/yunctl.py keygen TARGET_NAME
python scripts/yunctl.py keygen TARGET_NAME --automation-key --confirm-unencrypted
python scripts/yunctl.py keygen TARGET_NAME --format rsa-pem
```

Prefer interactive Ed25519. Use the explicitly confirmed unencrypted form only
for non-interactive agents with a unique key, restrictive local permissions,
strict host checking, and a constrained server account/network. Use RSA-4096 PEM
only when a provider or legacy client requires it.

The private file stays on the controller; install only the `.pub` line on the
server. A `.pem` suffix is an encoding/filename convention, not authorization.
The command prints file paths and the public fingerprint, never key contents.

## 3. Install only the public half

Through the authorized bootstrap channel, append the `.pub` line to the target
account's `~/.ssh/authorized_keys`, then enforce:

```text
~/.ssh                 0700
~/.ssh/authorized_keys 0600
```

Prefer a dedicated administration account. Disable password/root login where
operationally safe and constrain exposure with Tailscale/VPN, firewall rules, or
cloud security groups. Never upload the private key.

## 4. Verify the server identity out of band

Obtain the ED25519 host fingerprint from the trusted server console:

```text
sudo ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub -E sha256
```

Compare it through a separate trusted channel. Only after an exact match, add
the corresponding host public key to a dedicated local known-hosts file. Do not
trust `ssh-keyscan` by itself over the same network being authenticated.

## 5. Create a strict SSH alias

Add a named block to `~/.ssh/config`:

```text
Host YUN_ALIAS
  HostName VERIFIED_HOST
  User VERIFIED_USER
  IdentityFile ABSOLUTE_PRIVATE_KEY_PATH
  IdentitiesOnly yes
  UserKnownHostsFile DEDICATED_KNOWN_HOSTS_PATH
  StrictHostKeyChecking yes
  ConnectTimeout 10
  ServerAliveInterval 30
  ServerAliveCountMax 3
```

Test `ssh -G YUN_ALIAS` and `ssh -o BatchMode=yes YUN_ALIAS true`. Do not proceed
through a host-key prompt or password fallback.

## 6. Register and accept

Register only secret-free metadata, using the exact trusted fingerprint:

```text
python scripts/yunctl.py register TARGET_NAME \
  --ssh-alias YUN_ALIAS \
  --hostname VERIFIED_HOST \
  --user VERIFIED_USER \
  --host-fingerprint SHA256:VERIFIED_FINGERPRINT \
  --role server \
  --description "AUTHORIZED PURPOSE"
```

Add `--role compute` only when detached tmux jobs are authorized, and
`--protected` for production/shared targets. `register` refuses to save until
the effective SSH alias and pinned known-hosts fingerprint match.

Finish with:

```text
python scripts/yunctl.py probe TARGET_NAME
```

For compute targets, also run one bounded success/fetch job and one disposable
cancellation job before describing the target as controllable through `/yun`.
