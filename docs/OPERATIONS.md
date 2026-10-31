# Running a server

[Back to the start](../README.md)

Everything runs on one machine: EKKA's Enclave, NVIDIA's OpenShell gateway, and Keycloak for sign-in. Every
system of record has a backup and a tested restore. Desk work folders are working state and are not backed up:
your code lives in your repositories.

**Why this page:** a few routine checks keep your agents working and your server safe. Run every command below
from the `deploy/` folder with `sudo`, through `./setup.sh compose ...` rather than a bare `docker compose`: the
setup's own settings are only loaded that way.

## What runs

| Service | Image | Runs as | Data |
|---|---|---|---|
| `keycloak-db` | Postgres 17, by digest | postgres | volume `keycloak-db` |
| `keycloak` | Keycloak 26.7.4, started with `kc.sh start`, by digest | keycloak | in `keycloak-db` |
| `edge` | nginx-unprivileged 1.30.4, by digest | 101 | none |
| `ekka-enclave` | built here from the signed EKKA release | 2000:1000 | volume `ekka-home` (vault, desk records, journal) |
| `gateway` | NVIDIA's OpenShell 0.1.4, by digest | 1000:1000 + the Docker socket's group | volume `gateway-state` |
| `gateway-init` | one-shot | root, no network | gives `gateway-state` to uid 1000 |

**Before moving to a new NVIDIA OpenShell version,** run the walk with a person signed in
(`sudo WALK_PERSON_HOME=~/.ekka-walk-person ./up.sh walk`). Its person legs call the gateway directly with
that person's own sign-in: starting a desk, running a command in it, and opening an SSH session into it.
All three must stay refused. A new OpenShell version can change what a method requires.

## Server loopback and restart recovery

The server gateway shares the Enclave container's network namespace. The executor connects to
`https://127.0.0.1:8080` and still verifies the gateway certificate against the configured public DNS name.
The published gateway port and `host.openshell.internal` alias belong to the Enclave service's network
namespace; the Docker socket and gateway credentials remain mounted only into the gateway container.
The Enclave's control plane remains a Unix socket.

Run the original `sudo ./up.sh server <name>` command after an upgrade or a stale namespace. It migrates
only the kit's old executor address inside the enrolled home, preserving enrollment and TLS identity.
Every successful full rerun also regenerates and installs the Enclave CLI's public backend defaults with
the loopback URL before reporting the server ready; a failed rerun must be completed.
For the first migration or a stale namespace, it stops and removes the gateway before recreating the
namespace owner, avoiding an occupied host port, then brings the whole stack up. It checks loopback connectivity before saying
that the gateway is running. When the existing gateway names the current Enclave namespace and loopback
is reachable, Compose reconciles only changed configuration; an unchanged rerun keeps both containers.

The Linux server install enables `ekka-desks-loopback-repair.service` on Docker startup/restart and
`ekka-desks-loopback-repair.timer` every 60 seconds after the previous check completes. Both invoke the
same bounded oneshot and exclusive lock. Healthy checks are read-only and emit no application log.
The installed code/configuration is a root-owned snapshot, never a mutable checkout or env file.

Repair requires an existing, running pair with **different actual Linux network namespace inodes**.
A TCP failure alone never authorizes replacement: an unavailable gateway in the same namespace fails
without restarting it. A stopped or absent service remains stopped, including the all-desks kill switch.
Only the gateway is recreated; the Enclave, keys, grants, attempts and desk locks remain untouched.
The unit never starts Docker when Docker was deliberately stopped. No persistent daemon is added.

After three failed checks/repairs within ten minutes, repair latches failed and stops the timer.
The root-only failure ledger survives reboot; a killed or timed-out replacement counts because its
reservation is synced before mutation. Success removes only that attempt's reservation. The latch
requires an operator reset and never expires into another automatic restart loop.
`sudo ./up.sh status` reports **gateway repair failed** while the unit is failed. Inspect:

```sh
sudo journalctl -u ekka-desks-loopback-repair.service
```

After investigating and correcting the cause, explicitly reset the latch and start the installed check.
No full installer is needed to clear the latch:

```sh
sudo systemctl stop ekka-desks-loopback-repair.timer ekka-desks-loopback-repair.service
sudo flock -n /run/ekka-desks-loopback-repair.lock rm -f /var/lib/ekka-desks-loopback/failures.json
sudo systemctl reset-failed ekka-desks-loopback-repair.service
sudo systemctl start ekka-desks-loopback-repair.service ekka-desks-loopback-repair.timer
```

Failures produce a `GATEWAY_REPAIR_FAILED` journal error and a failed unit. This is local failure
visibility; no external notification delivery is claimed. The timer also covers an Enclave container
restart while Docker stays up. If automation has latched or cannot repair, use the original full
`up.sh server` command after the explicit reset; a bare `docker compose up -d` is not the repair
procedure. Do not use targeted Enclave recreation as an upgrade procedure.

Before manual maintenance or the all-desks gateway kill switch, stop the timer and service first:

```sh
sudo systemctl stop ekka-desks-loopback-repair.timer ekka-desks-loopback-repair.service
```

Keep them stopped during maintenance. After restoring the intended running state, resume checks with
`sudo systemctl start ekka-desks-loopback-repair.service ekka-desks-loopback-repair.timer`.
The installer shares the same lock while updating its snapshot and refuses a busy repair after five
seconds; it never lets the timer read a partially updated snapshot.

Maintenance can interrupt a conversation. Its existing uncertain outcome and durable desk hold must
be resolved through the normal desk recovery procedure. This repair never deletes the Enclave home,
its attempt records or desk locks and does not resubmit a plan or conversation.

This loopback lifecycle is for the Linux server kit.
A network fixture passing is not production acceptance: verify the real gateway, TLS/OIDC, governed
interactive session, actual Linux systemd namespace recovery and the separate desk lifecycle after Engine restart
and reboot on the release candidate before deployment. A Docker fixture does not prove systemd or boot behavior.

### Docker Engine maintenance

The repair above restores the gateway's network namespace. It does not recover a desk that
OpenShell has marked as a fatal error. On OpenShell 0.1.4, restarting Docker with live restore
can break the supervisor monitoring connection and leave a desk in `ControlSupervisorExited`.
The normal desk start and stop commands then refuse that desk. This was observed on the test
server; a host reboot did not clear it.

Plan Docker Engine maintenance separately from kit upgrades. Do not treat live restore or this
repair timer as a guarantee that desk work survives an Engine restart. Before maintenance,
finish active work, record each desk's settings (`sudo ./up.sh ekka desk show <desk>`) and keep a
backup of retained data. Do not edit the gateway database or start the desk container directly.

`deploy/harden.sh` restarts Docker only when its Docker settings change (the desk installation checks),
and refuses even then while a desk exists, unless you run it with `ALLOW_DOCKER_RESTART=yes`.
Running it again with unchanged settings does not restart Docker. It also keeps automatic updates from upgrading or
restarting Docker, containerd or runc, so update those by hand, in a planned window.

**If a desk is stuck in this error, you, the server's administrator, recover it** on the installed
machine, through EKKA (measured on the test server):

1. `sudo ./up.sh ekka desk delete <desk>`. Deletion works on a desk in this error, and retires its
   old pin.
2. `sudo ./up.sh ekka desk create <desk> --provider <profile> ...`, with the same settings it had.
   It is registered and pinned as a new desk.
3. Run its setup plan again, as in [Set up your desk](SET-UP-YOUR-DESK.md).

The desk's files are lost: server desks keep their repositories, `.ekka/desk-map.json` and answer
files inside the desk, and deletion removes them. Answers already collected stay on the Enclave.
Approve with `sudo ./up.sh ekka desk repin <desk>` only if `desk show` still lists an old record
for that name.

## What is exposed, and to whom

| Port | Bound to | Who uses it |
|---|---|---|
| 443 | `DESKS_EDGE_BIND` (default `0.0.0.0`) | people's browsers and `ekka`, to sign in. Only the desks realm's paths are forwarded; `/admin`, `/realms/master`, `/metrics`, `/health` and everything else answer 404 |
| 8080 | `DESKS_GATEWAY_BIND` (default `0.0.0.0`) | people's `ekka desk` commands from their own computers, and the desks on this machine (over `127.0.0.1`) |
| 8443 | `127.0.0.1` only | the Keycloak admin console, from this machine |

**On a server, the gateway listens on your network, on purpose:** people use `ekka desk` from their own
computers. Every call is encrypted and needs a person's sign-in. **Allow 8080 and 443 only from your people's
network**: `deploy/harden.sh` does exactly that.

**Before sign-in, the gateway answers one thing to anyone who reaches 8080:** OpenShell 0.1.4's gRPC reflection
lists the API's service and method names. It reveals names only, never data or credentials, but it is one more
reason to keep 8080 on your private network.

**The gateway never trusts client certificates here.** OpenShell 0.1.4 can hand real credential values to an
"operator" (a client certificate with OU=operator under a client CA the gateway trusts). This setup signs everyone
in through OIDC, so the gateway does not start if anything turns that on: a `client_ca_path` or
`[openshell.gateway.mtls_auth]` `operator_enabled`/`enabled` in gateway.toml, `OPENSHELL_TLS_CLIENT_CA`,
`OPENSHELL_ENABLE_OPERATOR_AUTH` or `OPENSHELL_ENABLE_MTLS_AUTH` in its environment, or any command-line flag
(`tools/gateway_preflight.py`, run by `up.sh`, `setup.sh compose up|start|restart|create|run` and `backup.sh`).
Each reason is named, with its fix. The Enclave also checks the running gateway once a minute and refuses all
desk work (`DESK_GATEWAY_TRUSTS_CLIENT_CERTS`) while it asks for a client certificate, so a gateway started
some other way is caught too. There is no setting to allow either.

Postgres, Keycloak itself and the Enclave control plane are never published. The gateway listener is published
through their shared network namespace, owned by the Enclave service. The Enclave has no Docker socket.

**The gateway can control Docker on this machine: treat it as root here, and run this setup on a machine used
for nothing else.** A dedicated Docker daemon for the gateway is a further hardening step, not the
supported setup.

### No remote door

This setup serves nothing to a laptop from the Enclave's records: no download of an answer or a receipt, no
remote conversation. Every such read happens on the server, as the administrator: `sudo -i`, then
`ekka job answer <run>`, `ekka receipts show <run>`, `ekka receipts verify`, or `ekka run attach <run>` for a
running conversation. A laptop's download command against this server is refused by EKKA
(`FETCH_TICKET_ENCLAVE_NOT_REACHABLE`), because the Enclave reports no download address. The edge therefore
carries only the sign-in paths; `/ekka/runs/...` answers 404.

## Start order, and why it matters

`keycloak-db`, then `keycloak` (healthy), then `edge` and the Enclave, then the gateway after the Enclave is
healthy (its interceptor socket exists). The order is enforced by compose, and the gateway enforces it too:
**without the Enclave's socket, OpenShell's gateway does not start at all** (measured on 0.1.3) ("gateway interceptor
initialization failed ... could not connect"). With `restart: unless-stopped` it comes up by
itself once the Enclave is running. This matters: a gateway running without EKKA's checks would let the Enclave's machine identity create desks.
The order makes that impossible.

Every service has `restart: unless-stopped`, so Docker restarts its processes after a reboot. This does
not guarantee that the gateway rejoins the Enclave's current network namespace. Check after any restart:

```bash
./setup.sh compose ps
cd .. && sudo ./up.sh status
```

If loopback is unavailable, run the original `sudo ./up.sh server <name>` command from the checkout root.
A targeted Compose restart with `depends_on.restart: true` worked in the local network fixture; a direct
Docker restart did not. The service/timer repair path is tested separately with gateway-only recovery.
Use full-stack repair for an unavailable loopback; real Engine restart and reboot acceptance
remains a release gate.

## Everyday commands

```bash
./setup.sh compose ps                         # what runs, and its health
./setup.sh compose logs --tail 50 gateway     # one service's log
./setup.sh people                             # the bundle each new person runs once (desks-ca.pem, install.sh)
./setup.sh compose exec -T ekka-enclave ekka desk list   # the desks the Enclave runs, and whether each is pinned
```

For Enclave maintenance, run the original `sudo ./up.sh server <name>` command from the checkout root.
This reconciles changed configuration or repairs the namespace and may interrupt a conversation when
recreation is needed. An unchanged, healthy rerun keeps the running containers.

## Change a desk

A desk change (`create`, `delete`, `stop`, `start`, `policy set`, `allow`, `deny`, `disconnect`) is an EKKA plan run that only an organization admin may run, and so is `status`, which reads the desk's live state. A plan runs through an Enclave. In this release that means the Enclave on the machine you installed on, so a change runs there, through `up.sh`:

```sh
sudo ./up.sh ekka desk stop code-desk
```

- **Whose sign-in.** It runs under the Enclave's EKKA sign-in, the person who installed the server (an organization admin), whoever types the command. So who may change a desk comes down to who has sudo on this machine.
- **Not from your own computer.** A person's `ekka` on their own computer reads desks (`list`, `logs`, `policy show`, `connections`). A change from there is refused, because no Enclave runs there. Changing a desk from another computer is not in this release.
- **Policy changes read first.** `allow`, `deny` and `policy set` read the desk's current policy before they change it, so the Enclave needs its own desk sign-in, once: `sudo ./up.sh ekka desk login`. It prints an address. Open it in a browser on a computer set up as in [step 6](SERVER.md#6-each-person-connects-to-the-server), and sign in as `desks-admin`. That sign-in has read permissions only. OpenShell 0.1.4 checks those permissions even for its admin role. The change itself is made by the backend adapter.

## Administer desks from the server itself

**Why:** fewer computers that hold a desks sign-in. An administrator reads desks on the server (over SSH, or AWS Session Manager) instead of setting up their own computer. Desk changes run on the server in any case ([Change a desk](#change-a-desk)).

On the server, once. Point the server's name at the server itself: on a cloud machine its public address is not reachable from inside, because the firewall only lets your VPN in.

```sh
echo "127.0.0.1 desks.internal" | sudo tee -a /etc/hosts      # your server's name
sudo cp -r /var/lib/ekka-agent-desks/people ~/desks-people && sudo chown -R "$USER" ~/desks-people
cd ~/desks-people && ./install.sh desks
```

The sign-in still opens in a browser, so the computer whose browser you use needs the server's name in its hosts file and `desks-ca.pem` trusted, as in [step 6](SERVER.md#6-each-person-connects-to-the-server). Then, on the server:

```sh
ekka desk login             # as desks-admin; one-time password: sudo cat /var/lib/ekka-agent-desks/secrets/first-admin-password
```

Gateway reads such as `ekka desk logs` and `ekka desk connections` now work from the server. With no local desk records, `ekka desk list` also lists the gateway's desks. To see the Enclave's records and pins, use `sudo ./up.sh ekka desk list` and `sudo ./up.sh ekka desk show <desk>`. Changes and live status also go through the Enclave ([Change a desk](#change-a-desk)).

## The backend adapter's image

The backend adapter, which carries desk administration through EKKA, runs from EKKA's public image `ghcr.io/ekka-labs/ekka-backend`, built from the public repository `ekka-labs/ekka-backend` and pinned by digest in `deploy/pins.json`. No registry login is needed, and none is kept on this machine.

`up.sh` pulls the pinned digest and checks its signature with cosign before anything runs it. The signature must come from the release workflow of `ekka-labs/ekka-backend` on a version tag; any other image does not run.

## Connect a provider's key

A provider's key is set on this machine, at install or when it changes. People's own `ekka desk connect` points here. The key comes from a pipe, never as an argument and never typed at the terminal, so it is not on screen, in `ps` or in shell history:

```sh
gh auth token | sudo ./up.sh connect github
sudo cat /root/anthropic.key | sudo ./up.sh connect claude-code
```

It creates the provider with that key, or replaces the key of one that exists, then updates the desks grant to name that provider, the same way the install does (the new grant is added before the old one is revoked). The key is handed only to NVIDIA's CLI, in the variable the provider's profile names (`GITHUB_TOKEN`, `ANTHROPIC_API_KEY`). The call is logged like the commands below, with the key's name, never its value. A provider that needs more than one key, such as `codex`, is refused here.

## NVIDIA's own commands, as the gateway's administrator

**Why:** providers and their keys are set at install, on this machine, and some need the gateway's administrator, for example a provider whose keys the gateway mints itself (AWS through STS). `up.sh` runs NVIDIA's pinned `openshell` CLI as that administrator for the length of one command, then switches that identity off again.

It runs only these commands, and refuses every other one by name with "desk changes run through EKKA: ekka desk <op>":

| Kind | Commands |
|---|---|
| Reads | `status`, `sandbox list`, `sandbox get`, `provider list`, `provider get`, `provider list-profiles`, `provider refresh status` |
| The kill switch | `sandbox stop <desk>`, always with the desk's name ([When EKKA's cloud is unreachable](#when-ekkas-cloud-is-unreachable)) |
| Provider setup | `provider create`, `provider update`, `provider delete`, `provider refresh configure`, `provider refresh rotate`, `provider refresh delete` |

A secret is never an argument: name the key and pass its value in the environment (`--credential KEY`, `--secret-material-env`). `--credential KEY=VALUE`, `--secret-material-key` and `--from-oidc-token` are refused. Each allowed call is logged before it runs, with the command and the person who ran sudo, in `/var/lib/ekka-agent-desks/openshell-admin.log` (mode 0600).

```sh
sudo ./up.sh openshell provider list
```

Secret material goes through the environment, never an argument, as NVIDIA's `--secret-material-env` expects:

```sh
sudo --preserve-env=SOURCE_SECRET ./up.sh openshell provider refresh configure aws --credential-key AWS_ACCESS_KEY_ID --strategy aws-sts-assume-role --material role_arn=<role> --secret-material-env aws_secret_access_key=SOURCE_SECRET
```

## Certificates

`certs.sh` made a local CA (5 years) and the server certificates (1 year). Check them monthly:

```bash
sudo ./certs.sh check                              # exit 1 when anything expires within 30 days
sudo ./certs.sh renew <server name> && sudo ./setup.sh compose restart edge gateway
```

People's computers trust the desks CA: the browser through the operating system, and `ekka` through
`backend.json`'s `ca_pem_path`, which `install.sh` writes. With a certificate from a public CA, nothing is installed on people's computers.

The browser half, once per computer, with `desks-ca.pem` from the people bundle:

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

Firefox keeps its own store: import `desks-ca.pem` under Settings, Certificates, Authorities, or turn on
`security.enterprise_roots.enabled`.

## Rotating credentials

| What | How |
|---|---|
| The Enclave's client secret | regenerate it in the Keycloak admin console (client `ekka-enclave`, Credentials), then `./setup.sh enclave-secret <env>`. The Enclave reads it from its vault each time it makes a token: no restart |
| People's sessions | Keycloak admin console, the person's Sessions, sign out. They sign in again at their next command |
| A company key (Anthropic, GitHub, ...) | on the server, `<new key> \| sudo ./up.sh connect <profile>` ([above](#connect-a-providers-key)). It replaces the key for every desk at once |
| The gateway's signing key | it is pinned in the Enclave (`EKKA_OPENSHELL_GATEWAY_KEY_B64`, derived from `tls/jwt/public.pem`). Replace the key, run `./setup.sh enclave-config <env> <workspace>`, then run the original `sudo ./up.sh server <name>` command from the checkout root for full-stack maintenance. Every call is refused until both match |

## Upgrades

Upgrade the kit and the Enclave together, in one maintenance window. The installer refuses to install on an older Enclave than its minimum supported release. Take a backup first; upgrades can interrupt running jobs and conversations.

1. Record the current repository commit and configured EKKA version so you can return to them.
2. Run `git pull` in this repository on the server.
3. Rerun the original installation command, such as `sudo ./up.sh server <name>`. Replace `<name>` with the installed server name.
4. Check `sudo ./up.sh status`, then run `sudo ./up.sh walk` and a sample question before resuming work.

The installer initializes the pinned base-image submodule, builds the image locally, and reconciles the configured services. Existing desks keep their previous image until recreated. Save any work you need before deleting a desk.

Update one component at a time:

- **EKKA Enclave:** use the release configured by this example. An explicit override, such as `sudo EKKA_VERSION=0.1.124 ./up.sh server <name>`, must be compatible with the integration's minimum release.
- **Base image:** update the `external/desk-base` submodule revision in a reviewed integration change. Do not maintain another Dockerfile here.
- **OpenShell:** update the release and image pins together. Verify checksums and run the integration checks before installing the change.
- **Keycloak and Postgres:** back up their data before changing image pins. Startup may migrate the database; restoring an older image alone may not reverse a migration.

For rollback, restore the previous integration commit and compatible EKKA version, then rerun the installation command. If a database migration ran, follow the component's recovery instructions and restore its backup where required.

Receipts and exports may include a requester's name or email when supplied at dispatch. Treat these records according to your retention policy. Those identity fields grant no authority.

## When EKKA's cloud is unreachable

Every desk change normally goes through EKKA: `sudo ./up.sh ekka desk stop`, `sudo ./up.sh ekka desk start` and the rest. If EKKA's cloud cannot be reached and a desk must stop now, stop it on the server. Nothing below needs EKKA. First stop the gateway repair timer, so it does not undo a stop ([Server loopback and restart recovery](#server-loopback-and-restart-recovery)).

**One desk.** The sign-in service and the gateway on the server must be running.

```sh
sudo ./up.sh openshell sandbox stop <desk>    # always name the desk: with no name it stops the last one used
```

The desk stays stopped until someone starts it again.

**Every desk at once.** Stop the gateway. It stops each running desk before it exits, and it stays stopped, even after a reboot, until you start it.

```sh
sudo docker stop -t 120 ekka-desks-gateway-1                         # up to 2 minutes to stop every desk first
sudo docker ps --filter label=openshell.ai/managed-by=openshell       # should list nothing
```

If the second command still lists containers, the gateway ran out of time. Stop them directly:

```sh
sudo docker stop $(sudo docker ps -q --filter label=openshell.ai/managed-by=openshell)
```

To stop only one desk's containers this way, add `--filter label=openshell.ai/sandbox-name=<desk>`.

When EKKA is back, start the gateway: `sudo docker start ekka-desks-gateway-1`. It starts again every desk that was running when you stopped it. A desk you stopped with `sandbox stop` stays stopped.

## Remove the server's desks

This deletes your desks, the keys you connected, the sign-in accounts and the Enclave's vault. Back up first if you might want them ([Backup and restore](#backup-and-restore)).

```sh
sudo ./up.sh ekka enclave list                      # note this machine's Enclave id
cd deploy && sudo ./setup.sh compose down -v        # every container and volume
sudo rm -rf /var/lib/ekka-agent-desks
ekka enclave revoke <id>                            # removes it from your EKKA organization
```

## Backup and restore

```bash
./backup.sh backup [<dir>]       # one dated folder, mode 0700: Keycloak's dump, both volumes, $DESKS_STATE
./backup.sh restore <folder>     # refuses unless every file matches the folder's SHA256SUMS
```

The gateway and the Enclave are stopped while their volumes are copied (a minute), and only the services
that were running are started again. **A backup is a secret:** it holds the Enclave's vault and the key that unlocks it. Keep it where you keep
passwords.

Tested: back up, destroy every volume and a certificate, restore. The sign-in realm, the administrator, both
volumes with their owners and modes, and the certificate came back. A tampered backup is refused before anything
is touched.

## A desk change that never finished

A desk change (start, stop, create, delete and the others) can reach the backend adapter's deadline before it is done. The plan's step then fails, and the change is named by its request id, a lowercase UUID such as `3f1c2a9e-5b7d-4e2a-9c1b-7d6e5f4a3b2c`. `sudo ./up.sh ekka desk status <desk>` prints it, and the desk stays locked to it (`admin:3f1c2a9e-...`), so no job and no other change can use the desk meanwhile.

Usually the next `sudo ./up.sh ekka desk status <desk>` settles it on its own. When status says the change cannot be settled from what the backend reports, settle it on the machine:

```sh
sudo ./up.sh desks settle code-desk --operation 3f1c2a9e-5b7d-4e2a-9c1b-7d6e5f4a3b2c
```

It checks that the desk's lock names exactly this change and does nothing otherwise. Then it restarts the gateway, and watches the desk's containers until none is starting, restarting or being removed and nothing has changed for 40 seconds (NVIDIA OpenShell's 10-second stop timeout plus 30). Only then does it write one settle marker for this change, `settle/<request id>.json`, into the adapter's state. It gives up after 10 minutes and writes nothing. For a deleted desk, whose containers are gone, add `--backend-id <id>` from `ekka desk status`. For a create that never reached the desk, no container carries its labels: the procedure writes the marker with no backend id and no containers, and the adapter accepts that only for a create it cannot find either. It then answers `NOT_APPLIED`: nothing was created.

**The hold.** Before it restarts the gateway, the procedure writes a hold for the change, `settle/<request id>.hold` in the adapter's state. While the hold exists, the adapter never re-issues that change, so a `status` during the quiet window cannot send it again after the restart. The procedure removes the hold when it writes the marker. If the procedure stops without a marker (the desk kept changing, the gateway did not restart, or it was interrupted), the hold stays, and the message says so. Check the desk with `sudo ./up.sh ekka desk status <desk>`, then remove the hold:

```sh
sudo ./up.sh desks unhold <desk> --operation <request id>
```

After that the adapter may re-issue the change, or you settle it again.

Then run `sudo ./up.sh ekka desk status code-desk` again. The adapter reads the marker once, settles the change and frees the desk.

## When something fails

| Symptom | Cause | What to do |
|---|---|---|
| `gateway` restarting, log says "gateway interceptor initialization failed" | the Enclave is down or not enrolled | `./setup.sh compose logs ekka-enclave`; the gateway starts once the Enclave is healthy |
| `gateway` restarting, "unable to open database file" | `gateway-state` is not uid 1000's | `./setup.sh compose up gateway-init` |
| people's commands say the issuer's certificate is not trusted | an old `ekka`, or `install.sh` not run | upgrade `ekka`; run `install.sh` |
| every Enclave call refused after a gateway key change | the pinned key is stale | `./setup.sh enclave-config <env> <workspace>`, restart the Enclave |
| Keycloak down | runs refuse by name (`OPENSHELL_EXECUTOR_ISSUER_UNREACHABLE`), no gateway call is made | start Keycloak; the next run makes a token |
| a plan run is refused: no grant | the desks agent has no grant for that desk | `sudo ./up.sh ekka gate grant list`; the install made one for `desks/*` |
| `ekka desk create` refused: a key is missing | the desk names a key nobody connected yet | `ekka desk connections`; connect it first, then make the desk |
| the browser warns about the sign-in page's certificate | this computer does not trust the server's certificate authority yet | trust `desks-ca.pem` from the people bundle ([Certificates](#certificates)) |

### A long wait reports an expired EKKA session

A run can finish even when `plan run` reports `session expired` while waiting. Read the existing run rather than dispatching it again:

```sh
sudo ./up.sh ekka run list --limit 10 --json
sudo ./up.sh ekka run show <full-run-id>
```

Choose the full `id` belonging to your plan and start time in the JSON list. `run show` reads that run's result; it does not start another run. On the Enclave's machine it also shows the collected answer, labelled as the local copy. Refreshing the session during a long wait is a runner follow-up; this recovery path is available now.

## Known differences from NVIDIA's documentation (OpenShell 0.1.3)

- The supervisor binary in `ghcr.io/nvidia/openshell/supervisor:0.1.3` is `/openshell-supervisor`, not the
  `/openshell-sandbox` the container-deployment page names.
- `openshell-gateway generate-certs` has no lifetime option and issues certificates valid until the year
  4096. This setup makes its own (`certs.sh`).
- `openshell gateway add https://...` without `--local` is taken for a cloud gateway and waits two minutes
  for a browser sign-in.
- The gateway image runs as uid 1000 but owns no folder for it, so a fresh state volume cannot hold its
  database. NVIDIA's own compose runs the gateway as root; this setup uses `gateway-init` instead.
- Desk supervisors use the host network and reach the gateway on `127.0.0.1`, so the host port must equal
  the gateway's own port.
- Neither the gateway nor the supervisor image carries a signature or attestation. This setup pins both
  by digest, and checks each binary against NVIDIA's checksummed release (`tools/verify_pins.py`).
