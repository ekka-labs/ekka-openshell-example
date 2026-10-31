# A server for your team

[Back to the start](../README.md)

Install once on a Linux server, connect an Anthropic API key, and run your first repository question. The gateway and sign-in service accept connections only from the network addresses you allow.

**You need a Linux server.** A Mac or Windows computer cannot be the server. Use an x86_64 Ubuntu 24.04 server with at least 8 GB of memory for the first example ([step 1](#1-choose-the-machine)).

**Who follows this guide:** an administrator with SSH and sudo access to the server, an EKKA organization they own or administer, and an Anthropic API key. Use a separate terminal on your laptop for commands marked **on your computer**.

**The path:** prepare the server (1 to 3), install (4), set up sign-in (5 and 6), connect the key (7), then create a desk and ask a question (8). Adding teammates in step 5 is optional for your first answer.

If you do not have an EKKA organization, follow the installer's sign-in instructions and use `ekka org create <name>` to create one. Replace `<name>` with your organization name.

The gateway has its own browser sign-in for reading desks. EKKA sign-in authorizes governed work. They are separate accounts; the installer prints the EKKA sign-in command when it needs it.

## 1. Choose the machine

**Why:** each working agent needs memory, and the machine holds your company's keys, so it should do nothing else.

Memory is what limits how many agents can work at once. Each working desk needs about 1 to 2 GB, plus about 3 GB for the gateway, sign-in and the EKKA Enclave. These are estimates; measure your own runs.

Choose x86_64, Ubuntu 24.04 and enough memory for the agents you will run concurrently. Start with 8 GB of memory for one desk. Leave room for repository storage, container images, answer files and backups. You can increase capacity after measuring your workload.

What matters:

- **Linux 6.2 or newer.** NVIDIA OpenShell isolates desks with Landlock version 3. Ubuntu 24.04 has it (6.8). Ubuntu 22.04's standard kernel (5.15) does not.
- **Persistent storage and backups.** Use a disk mirror where available; keep a separate backup so you can recover the server and its credentials.
- **Its own machine.** The gateway holds your company's keys and supplies them to authorized requests from desk programs. Do not share the machine with your VPN server or with production systems: a problem on one should not reach the other.
- Measure resource use before increasing capacity.

## 2. Install Ubuntu 24.04

**Why:** OpenShell needs Linux 6.2 or newer to isolate desks, and this whole guide is tested on Ubuntu 24.04.

Install Ubuntu 24.04 (Server) from your provider's control panel, with your SSH public key. Do not set a password login.

## 3. Harden it

**Why:** this machine will hold your company's keys and run AI agents. Before anything else, close it to everyone except your own people, and keep it patched.

Decide which addresses may connect, and allow nothing else:

- **With a VPN:** its exit address. Check it from a computer on the VPN with `curl https://api.ipify.org`.
- **Without one:** your office's fixed address, or each person's address. Separate several with spaces: `ALLOW_FROM="203.0.113.7 198.51.100.20"`.

On the **server**, clone the integration example:

```sh
git clone --recurse-submodules https://github.com/ekka-labs/ekka-openshell-example.git
cd ekka-openshell-example
sudo ALLOW_FROM=<your-vpn-or-office-ip> ./deploy/harden.sh
sudo curl -fsSLo /usr/local/bin/cosign https://github.com/sigstore/cosign/releases/latest/download/cosign-linux-amd64 && sudo chmod +x /usr/local/bin/cosign
```

The last line installs cosign: the installer checks EKKA's release signature with it and refuses without it.

Replace every `<...>` value before running a command. Keep this terminal in the repository folder for the rest of the guide.

It does this:

| What | Why |
|---|---|
| Security updates install daily, no automatic reboot | fixes arrive without stopping an agent mid-run; reboot in a quiet hour when `/var/run/reboot-required` exists |
| SSH: one account, key only, no root, no passwords | the only way in is your key |
| Firewall: SSH, sign-in (443) and the gateway (8080) only from `ALLOW_FROM`, IPv4 and IPv6 | nobody outside your VPN can even see the sign-in page |
| A rule in Docker's own chain (`DOCKER-USER`) | Docker opens published ports ahead of the firewall; without this rule they are open to the internet whatever the firewall says |
| Kernel settings: kernel pointers hidden, unprivileged eBPF off, no network redirects | removes common ways for code on the machine to learn about or attack the kernel |
| The journal kept across reboots (2 GB), audit on SSH settings, sudo, Docker and the desks secrets | a record of who changed what |
| Docker from Docker's own packages, live restore enabled, logs capped | logs cannot fill the disk; plan Engine updates using [Docker Engine maintenance](OPERATIONS.md#docker-engine-maintenance) |

**Confirm access from a second SSH session.** The host firewall change has a 5-minute rollback unless you confirm:

```sh
sudo touch /var/lib/desks-harden/confirmed
```

If the new session does not connect, keep your existing session open and allow the host firewall rollback to run. Check the SSH settings and your provider's network rules as well; the host firewall rollback does not change those. Correct `ALLOW_FROM` before trying again.

Check it from the new session:

```sh
sudo ufw status                 # only your address on 22, 443, 8080
sudo iptables -S DOCKER-USER    # ends with -j DROP
sudo sshd -T | grep -E '^(permitrootlogin|passwordauthentication) '   # both "no"
```

Then, in your provider's console, add the same rule in its network firewall: allow TCP 22, 443 and 8080 from your VPN address, deny the rest. On AWS that is the instance's security group; on OVH, the Network Firewall. Two layers: if one is ever switched off by mistake, the other still holds.

## 4. Install desks

**Why:** this puts three things on the server, wired together: NVIDIA's OpenShell gateway (it runs desks and keeps your keys), EKKA's Enclave (it checks and runs work, and keeps your data on your server), and a sign-in service (so each person signs in to the gateway with their own account).

Pick the name people will use. A VPN-only server needs no public DNS: use a name under `.internal`, which is reserved for private networks (for example `desks.internal`). Each person's install adds it to their hosts file.

```sh
sudo EKKA_ORG=<your-org> ADMIN_EMAIL=<you@example.com> DESKS_ADDRESS=<server-ip-your-team-reaches> ./up.sh server desks.internal
```

| Setting | What it is |
|---|---|
| `EKKA_ORG` | Your EKKA organization's short name. **Required:** if you belong to several, the install never guesses. Leave it out once to see a list of yours. |
| `ADMIN_EMAIL` | The server's first administrator, who signs in as `desks-admin`. Their one-time password is in `/var/lib/ekka-agent-desks/secrets/first-admin-password`. |
| `DESKS_AGENT` | Optional. The name of the agent that does desk work (default `desks`). |
| `DESKS_ADDRESS` | The address people's computers reach this server at. The default is the machine's own address, which is right when the public address is on the machine itself (OVH). **On a cloud machine behind NAT (AWS, Google Cloud, Azure), set it** to the address your VPN reaches, for example the Elastic IP on AWS. |

It stops for you, and prints what to do each time: **sign in to EKKA** as an owner or administrator of that organization (EKKA emails you a link; click it). If you are in no EKKA organization yet, it stops once more for you to create one, and it stops if `EKKA_ORG` is not set. After each stop, run the same install line again. It carries on from where it stopped.

What it sets up, in order:

1. NVIDIA OpenShell's gateway (NVIDIA's image, pinned by digest), the team sign-in service (Keycloak) and the HTTPS front.
2. A private certificate authority that may only vouch for this server's own names (checked: a certificate it signs for any other site is rejected).
3. The EKKA Enclave, as a systemd service (`ekka.service`) running as its own user, enrolled into `EKKA_ORG`, with desks registered automatically, and the `ekka` command for administrators: `sudo -i`, then `ekka <command>`.
4. NVIDIA's provider profiles from `providers/`.
5. People's sign-ins to the desks, set to read only.
6. Desk administration through EKKA: the backend adapter (its image pulled by digest and its signature checked), its configuration, and its credential in the Enclave's vault.
7. A desks agent, the two example plans for `code-desk` and the seven desk administration plans (`ekka.desk.<op>.v1`).
8. Two grants for the desks agent: it may run jobs on every desk on this server, and it may administer desks within limits (the desk image, the desk policy and the connected keys). Take the first back with the command it prints.

The Developer and Enterprise editions include desk jobs. If setup refuses a job permission, keep the exact error and check your active organization and edition with `ekka org edition` before contacting EKKA.

Check it:

```sh
sudo ./up.sh status                    # done: lines, then the two services and the interceptor socket
systemctl status ekka ekka-desks       # the Enclave, and the stack bound to it
sudo docker ps                         # gateway, ekka-backend, edge, keycloak, keycloak-db: all up
```

After an update of this repository, put the provider profiles into the gateway again:

```sh
sudo ./up.sh profiles
```

## 5. Add your people

**Why:** the people who use your agents (read desks and their records, answer decisions) each sign in with their own account, so you can remove one person without touching anyone else. In this release a person's own sign-in only reads desks. Changing a desk is done on this server ([step 8](#8-make-a-desk-for-each-agent)).

For your first answer, you can use the existing `desks-admin` account and continue to step 6. To add teammates, on the **server**, for each person:

```sh
sudo ./up.sh invite member member@example.com
```

```text
   ok: member (member@example.com) may use desks
   Give them: the people bundle (/var/lib/ekka-agent-desks/people/) and their one-time password, changed at first sign-in:
     sudo cat /var/lib/ekka-agent-desks/secrets/people/member-password
```

Send them the bundle (three small files) and, separately, their one-time password. You are already in: you sign in as `desks-admin`.

To take a person off later, on the server: `sudo ./up.sh revoke member`. Their sign-in is disabled, every session they have ends, and they leave the desk workspace. Running it twice changes nothing. It never removes an administrator; that is done in the Keycloak admin console, once another administrator exists. `sudo ./up.sh invite member member@example.com` brings them back, with a new one-time password.

## 6. Each person connects to the server

**Why:** each person's `ekka` needs the server's address, and the certificate that proves it is your server, before they can sign in.

On **your computer**, connected to an allowed network, set up the server's first administrator too. For that account, use username `desks-admin`; read its one-time password on the server with `sudo cat /var/lib/ekka-agent-desks/secrets/first-admin-password`. Keep the password out of messages and screenshots.

Copy the three files from `/var/lib/ekka-agent-desks/people/` on the server to a folder on your computer using your normal secure file-transfer method. Teammates use the same bundle and their own username and password from step 5.

First install the `ekka` command line on your computer, if needed. Run only the cosign line for your operating system:

```sh
# the installer checks EKKA's release signature with cosign; install it first
brew install cosign          # macOS
sudo curl -fsSLo /usr/local/bin/cosign https://github.com/sigstore/cosign/releases/latest/download/cosign-linux-amd64 && sudo chmod +x /usr/local/bin/cosign   # Linux x86_64
curl -fsSL https://get.ekka.ai/install.sh | sh
```

Then, from the folder with the bundle:

```sh
./install.sh desks          # adds the server's name to /etc/hosts and points ekka at the server
```

Their browser trusts the sign-in page's certificate, once, for websites under your server's name only (`install.sh` prints the command):

| System | Trust it, as the person, no sudo | Take the trust away |
|---|---|---|
| macOS | `security add-trusted-cert -r trustRoot -p ssl -s <server name> -k ~/Library/Keychains/login.keychain-db desks-ca.pem` | `security remove-trusted-cert desks-ca.pem` |
| Linux, Chrome and Chromium | `certutil -d sql:$HOME/.pki/nssdb -A -t C,, -n "EKKA desks CA (<server name>)" -i desks-ca.pem` (`certutil` is in `libnss3-tools`) | `certutil -d sql:$HOME/.pki/nssdb -D -n "EKKA desks CA (<server name>)"` |
| Windows (administrator) | `certutil -addstore -f Root desks-ca.pem` | `certutil -delstore Root "EKKA desks CA (<server name>)"` |

`install.sh` prints the right one for the computer it runs on, with the CA's SHA-256 fingerprint, and runs
nothing; `./trust-ca.sh --all` in the bundle prints both. Read people the fingerprint `up.sh invite` shows, so they
can check the CA they received before their browser trusts it.

The trust is as narrow as each system allows: websites only, the server's name only, that person only. The CA's own
constraint names DNS names and IP addresses alone, so a CA trusted for every purpose (the old
`sudo security add-trusted-cert -d ... System.keychain`) would also let anyone holding the server's CA key sign
software or mail that computer trusts. On Linux, Chrome and Firefox do not read the system store, so
`update-ca-certificates` needed root for the whole machine and still left the warning. The Windows command still
trusts for every purpose; narrowing it is its own change.

Then they sign in:

```sh
ekka desk login             # their username and one-time password; they choose a new one
```

## 7. Connect the company's keys, once

**Why:** the keys belong to the company, not to a person. Connected once, they live in one place on the server: every desk can be given them, nobody needs a copy on their laptop, and rotating a key is one command.

The server's administrator does this, once, on the server. The key comes from a pipe, never as an argument, so it is not on screen, in `ps` or in shell history. A person's own `ekka desk connect <profile>` is refused and points here: a person's sign-in only reads.

```sh
sudo -v
python3 -c "import getpass; print(getpass.getpass('Anthropic API key: '))" | sudo ./up.sh connect claude-code
```

Paste the API key only at that hidden prompt. The profile is called `claude-code` because it runs Claude Code; this flow uses an **Anthropic API key**, not a Claude subscription login.

Each connection also updates the desks grant, so a desk may be given that key right away ([Operations](OPERATIONS.md#connect-a-providers-key)).

Public GitHub repositories need no token. For private repositories, run `sudo ./up.sh connect github` with a read-only Contents token and add `--provider github` when creating the desk.

## 8. Make a desk for each agent

**Why:** each agent gets its own workspace for one job, with only the keys that job needs. One agent's desk never sees another's work.

On the server, sign this server's Enclave in to the desks once. It prints an address: open it in a browser on a computer set up as in step 6, and sign in as `desks-admin`. That sign-in has read permissions only. Then make the desk:

```sh
sudo -i
ekka desk login
ekka desk create code-desk --provider claude-code
```

**Who can change a desk.** In this release every desk change (create, delete, stop, start, policy, allow, deny, disconnect) runs here, through the Enclave, as `ekka desk <command>`. It runs under the EKKA sign-in of the person who installed the server, whoever types it, so only people with sudo on this server can change a desk. A person's `ekka` on their own computer reads desks and is refused for changes ([Change a desk](OPERATIONS.md#change-a-desk)).

Check registration:

```sh
ekka desk show code-desk
```

Wait for it to say **Pinned**. Poll that read if necessary; do not repeat creation. `code-desk` already has its example plans from installation. For another desk, create its plans once with `sudo ./up.sh desk-plans <desk>`.

Now follow [Your first answer](../README.md#try-the-example), using the public sample, then [set up your own repositories](SET-UP-YOUR-DESK.md).

## 9. Keep it running

**Why:** a few checks keep your team's agents running and your server patched.

After your first answer, `sudo ./up.sh walk` exercises the automated checks against the server, reports passes, failures and checks requiring a manual test, and cleans up its test resources. It runs a small real model job using your connected API key.

Some walk checks read the desks as you, with your own sign-in on the server. Set that up once, as in [Administer desks from the server itself](OPERATIONS.md#administer-desks-from-the-server-itself). Until then, those checks SKIP with one line that names this step, never FAIL.

| What | How |
|---|---|
| Is everything up? | `sudo ./up.sh status`, `systemctl status ekka ekka-desks`, and `cd deploy && sudo ./setup.sh compose ps` |
| After updating this repository | `sudo ./up.sh profiles` puts the provider profiles into the gateway again |
| Certificates, upgrades, backup and restore | [Running a server](OPERATIONS.md) |
| Security updates | install daily by themselves; reboot in a quiet hour when `/var/run/reboot-required` exists |
