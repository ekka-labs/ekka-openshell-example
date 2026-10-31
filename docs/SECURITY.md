# Security and data

[Back to the start](../README.md)

EKKA governs the work; NVIDIA OpenShell enforces the desk's execution policy. The server administrator operates the host and its credentials. This page describes the supplied server configuration.

## Permissions and desk changes

- **Job permission:** EKKA checks each plan step against the executing agent's grant before it runs.
- **Administration:** desk changes run as governed plans through the Enclave and backend adapter. Govern signs authorization for the request; the adapter checks its limits, including allowed image, policy and provider digests.
- **Separate identities:** the Enclave's job identity runs approved work. The adapter uses a separate credential for authorized desk changes. Team gateway sign-ins are read-only.
- **Requester evidence:** receipts verify the identity in Govern's signed dispatch. Server commands use the configured EKKA sign-in; that identity is not necessarily the person currently typing at the server terminal.
- **Desk identity:** jobs resolve the pinned backend ID and check the desk before commands. OpenShell 0.1.4 executes by name, so a replacement between that check and command remains a race. [Operations](OPERATIONS.md#upgrades) documents the release behavior.

The shipped desk grant is bounded. Root and people with sudo on the server remain trusted administrators. Follow [server hardening](SERVER.md#3-harden-it) and [change a desk](OPERATIONS.md#change-a-desk).

## Credentials and network access

The gateway stores connected credentials outside the desk. For the shipped Anthropic profile, Claude Code sees a placeholder; the gateway supplies the key to its authorized request to `api.anthropic.com`. Provider profiles name the destinations and programs that may use each credential.

Public GitHub repositories need no token. The shipped policy lets `git` clone and fetch over HTTPS and blocks Git push. Private repositories use the connected read-only GitHub token. The GitHub rule permits repository reads across GitHub; it is not a repository-specific allowlist.

Read the profiles in [Provider profiles](../providers/README.md), and the policy behavior in [How it works](HOW-IT-WORKS.md#what-a-desk-can-reach).

## Files and retained state

Desk programs run as a normal user. The shipped policy keeps system folders read-only and allows writes to the workspace and `/tmp`.

A server question asks the agent to refresh the repository copies before answering. This is a prompt instruction, not a whole-desk wipe. Agent home state and files outside those copies can persist. Choose which agents and workloads share a desk accordingly. [Answer storage and refresh](ASK-YOUR-CODE.md#what-was-kept-and-where) describes what remains.

## Where data goes

| Data | Where it is handled |
|---|---|
| Repository files | Your server; relevant code context goes to your selected model provider for inference |
| Connected API keys | OpenShell gateway on your server; supplied to authorized provider requests |
| Question and other run inputs | EKKA Govern, as part of the workflow |
| Answer file | Desk and Enclave's local run store |
| Run evidence | Signed instructions, requester identity, outcome and output digests in the run's records |
| Optional requester name/email snapshot | Retained in local receipts and exports; it grants no authority |

`sudo ./up.sh ekka receipts verify` checks the signed record chain. The answer displayed by `run show` is the Enclave's local copy; its display does not independently compare its bytes with the signed output digest.

## Server and supply chain

The server guide restricts inbound access to your selected network addresses, configures a private certificate authority and uses separate team sign-ins. Gateway startup checks require the Enclave and the pinned supervisor configuration.

Infrastructure images are pinned by digest. The backend adapter image is signature-verified. The Enclave image is built locally from a checksum- and signature-verified EKKA release. The agent image is built from the committed Dockerfile. See [source and licenses](OVERVIEW.md#source-and-licenses).

These controls and signed records support your security and audit processes. Compliance depends on your organization's configuration, operations and obligations.

The server executor and gateway share a Docker network namespace, so the executor uses literal loopback
without weakening its local-only check. TLS still checks the configured public gateway name. Port 8080
is published by that namespace's Enclave service, while only the gateway container mounts Docker control
and gateway secrets. Shared networking does not share process, mount or user namespaces. See
[restart limitations and recovery](OPERATIONS.md#server-loopback-and-restart-recovery).

The Linux server's Docker-start and 60-second timer repair oneshot runs as root within the existing Docker administration
trust. Its installed code, rendered gateway configuration and preflight pins are root-owned and reject
symlinks or paths writable by other users. Existing read-only gateway TLS/JWT data keeps its
kit-assigned uid1000 ownership under a root-protected parent; it is never executed as repair code.
The service never loads mutable checkout/env files at boot. Repair
recreates only an already-running gateway after validating its mounts and existing credential preflight;
it neither restarts the Enclave nor undoes a deliberate service stop. It adds no listener or Docker
socket mount. The service is bounded by a timeout and exclusive lock, with journal errors on failure.

The timer uses the same exclusive lock and only repairs differing Linux network namespace inodes.
Same-namespace outages never trigger a gateway restart. Three failures within ten minutes latch a
root-only, synced ledger and stop the timer until explicit operator reset; healthy checks write nothing.
