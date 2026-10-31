#!/usr/bin/env bash
# Harden a fresh Ubuntu 24.04 machine before it becomes a desks server.
#
#   sudo ALLOW_FROM=203.0.113.7 ADMIN_USER=ubuntu ./deploy/harden.sh
#
# ALLOW_FROM   the ONLY addresses that may connect (space separated; e.g. your VPN's exit address).
#              SSH, sign-in (443) and the desk gateway (8080) are closed to everyone else, IPv4 and IPv6.
# ADMIN_USER   the one account that may SSH in (key only). Default: the user running sudo.
# ALLOW_DOCKER_RESTART=yes  apply changed Docker settings even though desks exist. A Docker restart leaves every
#              desk in Error until it is deleted, created and repinned (the desk installation checks).
#
# Safe to run again: Docker is restarted only when its settings change, and never while desks exist unless
# ALLOW_DOCKER_RESTART=yes. The firewall change rolls itself back after 5 minutes unless you confirm from a
# NEW SSH session:  sudo touch /var/lib/desks-harden/confirmed
# so a wrong ALLOW_FROM can never lock you out.
set -euo pipefail
ALLOW_FROM=${ALLOW_FROM:?set ALLOW_FROM to the addresses that may connect}
ADMIN_USER=${ADMIN_USER:-${SUDO_USER:-}}
STATE=/var/lib/desks-harden
say() { printf '\033[1m==\033[0m %s\n' "$*"; }
ok()  { printf '   ok: %s\n' "$*"; }
die() { printf 'harden: %s\n' "$*" >&2; exit 1; }

# ⛔ A DOCKER RESTART STRANDS EVERY DESK (the desk installation checks). OpenShell 0.1.4 reads a broken Docker
# connection as its supervisor exiting (ControlSupervisorExited), keeps the desk in Error, and refuses to start or
# stop it; only delete, create and repin recover it, and the desk's files are lost. So harden restarts Docker only
# when its settings change, never while a desk container exists unless asked, and keeps updates from doing it.
DOCKER_PACKAGES="docker-ce docker-ce-cli containerd.io docker.io containerd runc"
# A fresh Ubuntu runs its own updater at boot and holds the package lock for minutes. Every apt call
# waits for that lock instead of failing (the desk installation checks); APT_LOCK_TIMEOUT is in seconds.
APT_LOCK_TIMEOUT=${APT_LOCK_TIMEOUT:-600}
apt_get()  { apt-get -o DPkg::Lock::Timeout="$APT_LOCK_TIMEOUT" "$@"; }
wait_for_apt() {  # until no process holds the dpkg or apt lists lock, or the timeout passes
  local waited=0
  while fuser /var/lib/dpkg/lock-frontend /var/lib/dpkg/lock /var/lib/apt/lists/lock >/dev/null 2>&1; do
    (( waited >= APT_LOCK_TIMEOUT )) && die "another package manager still holds the package lock after ${APT_LOCK_TIMEOUT}s (Ubuntu's own updater runs at first boot); wait for it and run harden again"
    (( waited == 0 )) && ok "waiting for Ubuntu's package updater to finish"
    sleep 5; waited=$((waited + 5))
  done
}
desk_containers() {  # how many OpenShell desk containers exist, running or not; fails when Docker cannot answer
  local ids; ids=$(docker ps -a -q --filter label=openshell.ai/managed-by=openshell) || return 1
  grep -c . <<<"$ids" || true
}
apply_docker_settings() {  # apply_docker_settings <daemon.json> <content>: write and restart only on a change
  local file=$1 want=$2 n
  if [[ -f $file && $(cat "$file") == "$want" ]]; then
    ok "Docker settings unchanged; Docker was not restarted"; return 0
  fi
  # Docker installed but not running: every desk is already down, so starting it strands nothing.
  if command -v docker >/dev/null && systemctl is-active --quiet docker; then
    n=$(desk_containers) || die "Docker settings changed, but Docker could not list its containers, so harden cannot tell whether desks exist; nothing was changed"
    if (( n > 0 )) && [[ ${ALLOW_DOCKER_RESTART:-} != yes ]]; then
      die "Docker settings changed and applying them restarts Docker, but $n desk container(s) exist. A Docker restart leaves every desk in Error (OpenShell ControlSupervisorExited, the desk installation checks); each then needs delete, create and repin, and loses its files. Nothing was changed. To restart anyway: sudo ALLOW_DOCKER_RESTART=yes ALLOW_FROM=... ./deploy/harden.sh"
    fi
  fi
  printf '%s\n' "$want" > "$file"
  systemctl restart docker
  ok "Docker settings changed; Docker restarted"
}
upgrade_holding_docker() {  # the security upgrade, with every installed Docker package held so it cannot restart Docker
  local pkg held=() holds
  # Read once: `apt-mark showhold | grep -q` can lose its writer to SIGPIPE under pipefail, misread an
  # operator's hold as absent, and then release a hold harden never made.
  wait_for_apt
  holds=$(apt-mark showhold)
  for pkg in $DOCKER_PACKAGES; do
    dpkg-query -W -f='${Status}' "$pkg" 2>/dev/null | grep -q 'install ok installed' || continue
    grep -qx -- "$pkg" <<<"$holds" && continue   # an operator's own hold stays theirs
    apt-mark hold "$pkg" >/dev/null || die "could not hold $pkg before the security upgrade"
    held+=("$pkg")
  done
  local rc=0
  apt_get -y -qq -o Dpkg::Options::=--force-confdef -o Dpkg::Options::=--force-confold upgrade >/dev/null || rc=$?
  (( ${#held[@]} == 0 )) || apt-mark unhold "${held[@]}" >/dev/null
  (( rc == 0 )) || die "the security upgrade failed (apt-get exit $rc)"
}

[[ $(id -u) == 0 ]] || die "run with sudo"
. /etc/os-release; [[ $ID == ubuntu && $VERSION_ID == 24.04 ]] || die "written for Ubuntu 24.04; this is $PRETTY_NAME"
[[ -n $ADMIN_USER && $ADMIN_USER != root ]] || die "set ADMIN_USER to the account you SSH in with"
[[ -s /home/$ADMIN_USER/.ssh/authorized_keys ]] || die "$ADMIN_USER has no SSH key; add one before passwords are turned off"
for a in $ALLOW_FROM; do [[ $a =~ ^[0-9a-fA-F:.]+(/[0-9]+)?$ ]] || die "not an address: $a"; done
install -d -m 0700 "$STATE"

say "this machine"
abi=$(python3 -c 'import ctypes; print(ctypes.CDLL(None, use_errno=True).syscall(444, None, 0, 1))')
(( abi >= 3 )) || die "Landlock ABI $abi; desks need 3 or newer"
ok "Ubuntu $VERSION_ID, kernel $(uname -r), Landlock ABI $abi"
if [[ -r /proc/mdstat ]] && grep -q '^md' /proc/mdstat; then
  grep -q '\[U*_U*\]' /proc/mdstat && die "a RAID mirror is degraded; see /proc/mdstat" || ok "disk mirror healthy"
fi

say "packages and security updates"
export DEBIAN_FRONTEND=noninteractive
wait_for_apt
apt_get update -qq
apt_get install -y -qq ufw unattended-upgrades needrestart chrony auditd ca-certificates curl >/dev/null
cat > /etc/apt/apt.conf.d/52desks-unattended <<'EOF'
// Security updates install daily. No automatic reboot: agents may be mid-run. `needrestart` and
// /var/run/reboot-required say when a reboot is due; reboot in a quiet hour.
Unattended-Upgrade::Allowed-Origins { "${distro_id}:${distro_codename}-security"; "${distro_id}ESMApps:${distro_codename}-apps-security"; "${distro_id}ESM:${distro_codename}-infra-security"; };
Unattended-Upgrade::Automatic-Reboot "false";
Unattended-Upgrade::Remove-Unused-Dependencies "true";
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
// Never Docker, containerd or runc: their upgrade restarts Docker, which strands every desk (ekka-agent-desks#148).
Unattended-Upgrade::Package-Blacklist { "docker-ce"; "docker-ce-cli"; "containerd.io"; "docker.io"; "containerd"; "runc"; };
EOF
install -d /etc/needrestart/conf.d
cat > /etc/needrestart/conf.d/90-ekka-desks.conf <<'EOF'
# EKKA desks: never restart Docker or containerd after an update; it strands every desk (ekka-agent-desks#148).
$nrconf{override_rc}{qr(^docker)} = 0;
$nrconf{override_rc}{qr(^containerd)} = 0;
EOF
upgrade_holding_docker
ok "security updates on (daily, no automatic reboot; Docker, containerd and runc are updated by hand)"

say "SSH: one account, key only"
cat > /etc/ssh/sshd_config.d/10-desks.conf <<EOF
PermitRootLogin no
PasswordAuthentication no
KbdInteractiveAuthentication no
PubkeyAuthentication yes
AllowUsers $ADMIN_USER
MaxAuthTries 3
LoginGraceTime 20
X11Forwarding no
AllowAgentForwarding no
EOF
# On a stock Ubuntu 24.04 server /run/sshd (sshd's privilege separation folder) can be missing, and `sshd -t`
# then refuses with "Missing privilege separation directory". Create it the way sshd expects; an existing
# one is left exactly as it is.
privsep_dir() { [[ -d $1 ]] || install -d -o root -g root -m 0755 "$1"; }
privsep_dir /run/sshd
sshd -t || die "the SSH settings do not parse; nothing was reloaded"
systemctl reload ssh
ok "root and passwords off; only $ADMIN_USER, by key"

say "kernel settings"
cat > /etc/sysctl.d/60-desks.conf <<'EOF'
kernel.kptr_restrict = 2
kernel.dmesg_restrict = 1
kernel.unprivileged_bpf_disabled = 1
net.core.bpf_jit_harden = 2
fs.protected_symlinks = 1
fs.protected_hardlinks = 1
fs.protected_fifos = 2
fs.protected_regular = 2
fs.suid_dumpable = 0
net.ipv4.conf.all.rp_filter = 1
net.ipv4.conf.default.rp_filter = 1
net.ipv4.conf.all.accept_redirects = 0
net.ipv4.conf.default.accept_redirects = 0
net.ipv6.conf.all.accept_redirects = 0
net.ipv4.conf.all.send_redirects = 0
net.ipv4.conf.all.accept_source_route = 0
net.ipv4.conf.all.log_martians = 1
net.ipv4.tcp_syncookies = 1
EOF
sysctl -q --system
ok "kernel pointers, eBPF and network redirects locked down"

say "logs"
install -d /etc/systemd/journald.conf.d
printf '[Journal]\nStorage=persistent\nSystemMaxUse=2G\n' > /etc/systemd/journald.conf.d/desks.conf
systemctl restart systemd-journald
install -d -m 0700 /var/lib/ekka-agent-desks/secrets   # audit cannot watch a folder that does not exist yet
cat > /etc/audit/rules.d/desks.rules <<'EOF'
-w /etc/ssh/sshd_config.d/ -p wa -k ssh
-w /etc/sudoers -p wa -k sudo
-w /etc/sudoers.d/ -p wa -k sudo
-w /var/lib/ekka-agent-desks/secrets/ -p rwa -k desks-secrets
-w /usr/bin/docker -p x -k docker
EOF
augenrules --load >/dev/null || die "the audit rules did not load"
ok "journal kept across reboots (2 GB); audit on SSH, sudo, docker and the desks secrets"

say "firewall: only $ALLOW_FROM"
ufw --force reset >/dev/null
ufw default deny incoming >/dev/null; ufw default allow outgoing >/dev/null
for a in $ALLOW_FROM; do for p in 22 443 8080; do ufw allow from "$a" to any port "$p" proto tcp >/dev/null; done; done
# Docker publishes ports through its own iptables chain, ahead of ufw. Without this rule a published
# port is open to the internet whatever ufw says. DOCKER-USER runs first for traffic to containers.
cat > /usr/local/sbin/desks-docker-user <<EOF
#!/bin/sh
# Only $ALLOW_FROM may reach a published container port from outside; replies and container-to-container pass.
iptables -N DOCKER-USER 2>/dev/null || true
iptables -F DOCKER-USER
iptables -A DOCKER-USER -m conntrack --ctstate RELATED,ESTABLISHED -j RETURN
iptables -A DOCKER-USER ! -i \$(ip -4 route show default | awk '{print \$5; exit}') -j RETURN
$(for a in $ALLOW_FROM; do [[ $a == *:* ]] || echo "iptables -A DOCKER-USER -s $a -j RETURN"; done)
iptables -A DOCKER-USER -j DROP
EOF
chmod 0755 /usr/local/sbin/desks-docker-user
cat > /etc/systemd/system/desks-docker-user.service <<'EOF'
[Unit]
Description=Limit published Docker ports to the allowed addresses
After=docker.service
PartOf=docker.service
[Service]
Type=oneshot
ExecStart=/usr/local/sbin/desks-docker-user
RemainAfterExit=yes
[Install]
WantedBy=docker.service multi-user.target
EOF
systemctl daemon-reload
rm -f "$STATE/confirmed"
# The rollback: unless confirmed from a new session within 5 minutes, the firewall turns itself off.
systemd-run --quiet --unit=desks-harden-rollback --on-active=300 /bin/sh -c \
  "[ -f $STATE/confirmed ] || { ufw --force disable; iptables -F DOCKER-USER 2>/dev/null; logger -t desks-harden firewall rolled back, not confirmed; }" || true
ufw --force enable >/dev/null
command -v docker >/dev/null && systemctl enable --now desks-docker-user.service >/dev/null 2>&1 || true
ok "on. From a NEW SSH session within 5 minutes: sudo touch $STATE/confirmed   (else it rolls back)"

say "Docker"
if ! command -v docker >/dev/null; then
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $VERSION_CODENAME stable" \
    > /etc/apt/sources.list.d/docker.list
  wait_for_apt
  apt_get update -qq
  apt_get install -y -qq docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin >/dev/null
fi
command -v docker >/dev/null || die "Docker is not installed after the install step; nothing after it ran"
install -d /etc/docker
apply_docker_settings /etc/docker/daemon.json '{ "live-restore": true,
  "log-driver": "json-file", "log-opts": { "max-size": "20m", "max-file": "3" } }'
systemctl enable --now desks-docker-user.service >/dev/null
ok "$(docker --version | cut -d, -f1); containers keep running through a Docker restart; logs capped"
ok "docker group is root-equivalent: only $ADMIN_USER belongs in it"

say "done"
echo "   Still to do outside this machine, in your provider's console: the same rule in its network firewall (on AWS a security group, on OVH the Network Firewall), and backup storage."
[[ -f /var/run/reboot-required ]] && echo "   A reboot is due for updates: sudo reboot (then reconnect and continue)."
exit 0
