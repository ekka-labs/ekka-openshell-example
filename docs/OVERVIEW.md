# Documentation

[Back to the example](../README.md)

Use this example to connect EKKA governed execution to NVIDIA OpenShell, prepare a repository, and ask Claude Code a question about it.

## Start here

1. [Install the server](SERVER.md). Set up the services, model connection, and first desk.
2. [Set up repositories](SET-UP-YOUR-DESK.md). Add public repositories or connect private repository access.
3. [Ask your code](ASK-YOUR-CODE.md). Run a question, check its citations, and inspect the execution record.

Run the example workflow on the server. Team members can sign in from their computers to read desk information. The [server guide](SERVER.md#6-each-person-connects-to-the-server) explains the separate sign-ins.

## Understand the integration

| Guide | Purpose |
|---|---|
| [How it works](HOW-IT-WORKS.md) | Components, authorization checks, and the execution flow. |
| [Security and data](SECURITY.md) | Credential handling, permissions, and trust boundaries. |
| [Provider profiles](../providers/README.md) | Credential references and authorized destinations. |

## Run and maintain it

| Guide | Purpose |
|---|---|
| [Commands](COMMANDS.md) | Sign-in, desk administration, and credential commands. |
| [Operations](OPERATIONS.md) | Updates, backups, certificates, recovery, and troubleshooting. |

## Where data goes

Repository copies stay on your server. The model provider receives the code context sent by the agent. EKKA receives workflow inputs, including questions, and execution records. See [security and data](SECURITY.md) before connecting private repositories.

## Source and licenses

| Component | Source | License |
|---|---|---|
| This repository | Installation, configuration and examples | [Apache-2.0](../LICENSE) |
| EKKA Enclave | EKKA's signed release; checksum and signature verified before building its local image | EKKA terms |
| NVIDIA OpenShell gateway and supervisor | NVIDIA's v0.1.4 images, pulled by digest | [Apache-2.0 and notices](https://github.com/NVIDIA/OpenShell/blob/v0.1.4/THIRD-PARTY-NOTICES) |
| Desk image | [Separate EKKA image source](https://github.com/ekka-labs/ekka-agent-desks/tree/main/desk/image): Node, git and Claude Code | Per component |
| Keycloak, Postgres and nginx | Official images, pulled by digest | Apache-2.0, PostgreSQL and BSD-2 |

NVIDIA's provider files retain their attribution and license. See [third-party notices](../THIRD-PARTY-NOTICES.md). OpenShell usage reporting is off unless you set `OPENSHELL_TELEMETRY=on`.

## Help and feedback

Start with [troubleshooting](OPERATIONS.md#when-something-fails). When reporting a problem, include the command, expected result, actual result, and run ID. Remove API keys, passwords, and tokens from shared output.

---

NVIDIA and OpenShell are trademarks of NVIDIA Corporation in the U.S. and other countries. Claude and Claude Code are trademarks of Anthropic, PBC. EKKA is not affiliated with, sponsored or endorsed by NVIDIA or Anthropic.
