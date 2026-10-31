<p><a href="https://ekka.ai"><img src="docs/assets/ekka-wordmark.png" alt="EKKA" width="180"></a></p>

# EKKA + NVIDIA OpenShell

**Run a coding agent on your Linux server, with EKKA permissions and execution receipts.**

This example connects EKKA governed execution to [NVIDIA OpenShell](https://github.com/NVIDIA/OpenShell). The included workflow prepares a repository, asks Claude Code a question, and records the result for verification.

[Get started](docs/SERVER.md) · [How it works](docs/HOW-IT-WORKS.md) · [Security](docs/SECURITY.md)

## What each part does

| Part | Responsibility |
|---|---|
| **NVIDIA OpenShell** | Isolates the agent workspace, enforces network policy, and supplies credentials to authorized requests. |
| **EKKA** | Checks permission for the workflow and desk actions, runs the approved work through its Enclave, and records execution receipts. |
| **This example** | Connects the components with installation scripts, provider profiles, policies, and example plans. |

A **desk** is the agent's isolated workspace. A **plan** is the workflow it runs. A **grant** gives an agent permission to use a resource.

The [desk-image repository](https://github.com/ekka-labs/ekka-agent-desks) is separate. This repository demonstrates the integration; it is not the image distribution repository.

## Get started

You need:

- An Ubuntu 24.04 x86_64 server with SSH and sudo access. Start with 8 GB RAM and 50 GB disk; storage needs depend on your repositories.
- An EKKA organization you own or administer.
- A workspace-scoped Anthropic API key. An organization-scoped key is not supported by this example.

On the server:

```sh
git clone --recurse-submodules https://github.com/ekka-labs/ekka-openshell-example.git
cd ekka-openshell-example
```

Follow the **[server installation guide](docs/SERVER.md)** to configure access, install the services, connect the model, and create `code-desk`. It explains which commands run on the server and which run on your computer.

## Try the example

After installation, run these commands **on the server, as root** (`sudo -i` first). They work from any folder: `ekka` there is this server's Enclave.

### 1. Prepare a public repository

```sh
ekka plan run ekka.example.desk.setup.codeDesk.v1 \
  --input 'repos=["https://github.com/octocat/Hello-World"]'
```

No GitHub token is needed for this public repository.

### 2. Ask about its code

```sh
ekka plan run ekka.example.desk.ask.codeDesk.v1 \
  --input 'question=What does the README say? Cite its file and line.'
```

The answer should cite `Hello-World/README`, line 1, containing `Hello World!`. Check the citation against the file. Keep the run ID printed by the command.

### 3. Verify the execution record

```sh
ekka receipts verify
```

Receipt verification checks the execution record. It does not establish that the model's answer is correct. See [answers and records](docs/ASK-YOUR-CODE.md) for how to inspect a run.

## Scope and data handling

The included workflow covers repository setup and code questions. The default GitHub policy allows clone and fetch, with push blocked. A complete issue-to-PR workflow is outside this example's quick start.

Repository copies live on your server. The model provider receives the code context sent by the agent. EKKA receives run inputs, including your question, and execution records. Read [security and data handling](docs/SECURITY.md) before using private code.

## Documentation

| Guide | What you will learn |
|---|---|
| [Install the server](docs/SERVER.md) | Prepare the machine, connect credentials, and create a desk. |
| [Set up repositories](docs/SET-UP-YOUR-DESK.md) | Use public or private repositories. |
| [Ask your code](docs/ASK-YOUR-CODE.md) | Submit questions, read answers, and inspect run records. |
| [How it works](docs/HOW-IT-WORKS.md) | Understand the components and execution flow. |
| [Command reference](docs/COMMANDS.md) | Find desk and credential commands. |
| [Operations](docs/OPERATIONS.md) | Upgrade, back up, and troubleshoot the installation. |
| [Documentation index](docs/OVERVIEW.md) | Find advanced configuration and component licenses. |

## Image source

The base image is maintained in [ekka-agent-desks](https://github.com/ekka-labs/ekka-agent-desks). This example references a reviewed revision through the `external/desk-base` Git submodule. The installer initializes it and builds the image locally; no duplicate Dockerfile is maintained here.

If you cloned without submodules, run `git submodule update --init --checkout` before running the offline checks.

## Development

The offline checks use Python's standard library:

```sh
python3 -m unittest discover -s tests
```

These checks validate this repository's configuration and helpers. They do not replace a live installation test.

## License and help

This example is licensed under [Apache-2.0](LICENSE). Included third-party files retain their [notices](THIRD-PARTY-NOTICES.md). EKKA services and release binaries have separate terms.

For installation problems, start with [troubleshooting](docs/OPERATIONS.md#when-something-fails). Include the command, expected result, actual result, and run ID. Remove credentials before sharing output.

[EKKA](https://ekka.ai) · [EKKA documentation](https://docs.ekka.ai)
