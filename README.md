<p><a href="https://ekka.ai"><img src="docs/assets/ekka-wordmark.png" alt="EKKA" width="180"></a></p>

# EKKA + NVIDIA OpenShell

**Run a coding agent on your Linux server, with EKKA permissions and execution receipts.**

Set up a development desk with [NVIDIA OpenShell](https://github.com/NVIDIA/OpenShell), then use a small EKKA plan to ask the agent about a repository.

## What each part does

| Part | Responsibility |
|---|---|
| **NVIDIA OpenShell** | Isolates the agent workspace, enforces network policy, and supplies credentials to authorized requests. |
| **EKKA** | Checks permission for the workflow and desk actions, runs the approved work through its Enclave, and records execution receipts. |
| **This example** | A short walkthrough and one sample plan. The setup itself lives in [ekka-agent-desks](https://github.com/ekka-labs/ekka-agent-desks). |

A **desk** is the agent's isolated workspace. A **plan** is the workflow it runs. A **grant** gives an agent permission to use a resource.

## Get started

### 1. Set up the server

You need:

- An Ubuntu 24.04 server (x86_64 or aarch64) with SSH and sudo access. Start with 8 GB RAM and 50 GB disk; storage needs depend on your repositories.
- An EKKA organization you own or administer.
- A workspace-scoped Anthropic API key. An organization-scoped key is not supported.

On the server:

```sh
git clone https://github.com/ekka-labs/ekka-agent-desks.git
cd ekka-agent-desks
```

Follow the [server installation guide](https://github.com/ekka-labs/ekka-agent-desks/blob/main/docs/SERVER.md) to configure access, install the services, connect the model, and create `code-desk`.

### 2. Try the two plans the setup created

Run these commands **on the server, as root** (`sudo -i` first). They work from any folder: `ekka` there is this server's Enclave.

Prepare a public repository:

```sh
ekka plan run ekka.example.desk.setup.codeDesk.v1 \
  --input 'repos=["https://github.com/octocat/Hello-World"]'
```

No GitHub token is needed for this public repository. Ask about its code:

```sh
ekka plan run ekka.example.desk.ask.codeDesk.v1 \
  --input 'question=What does the README say? Cite its file and line.'
```

The answer should cite `Hello-World/README`, line 1, containing `Hello World!`. Check the citation against the file.

### 3. Create your own plan

On the server, as root, clone this repository. Then create the plan, run it, and verify the execution record:

```sh
git clone https://github.com/ekka-labs/ekka-openshell-example.git
cd ekka-openshell-example
ekka plan create plans/explain-repo.json
ekka plan run ekka.example.openshell.explain.codeDesk.v1 \
  --input 'question=What does the README say? Cite its file and line.'
ekka receipts verify
```

The [sample plan](plans/explain-repo.json) uses the setup's `desks` agent:

- **Resource:** `desks/code-desk` selects the desk that runs the job.
- **Prompt:** the platform slug and version select the instructions; no prompt text is copied here.
- **Question:** `@input.question` supplies the question you pass when running the plan.
- **Outputs:** `.ekka/answer.md` holds the answer; `.ekka/ask-record.json` holds the work record.
- **Facts:** `facts_from` reads the work record; the output contract names the verdict, answer digest, reset count, discarded-file count, and whether the working tree changed.

To use another desk, change two fields: `resource` to `desks/<desk>` and the `codeDesk` segment of `plan.code` to the desk name in camelCase. Update the name and descriptive text to match.

Receipt verification checks the execution record. It does not establish that the model's answer is correct. See [answers and records](https://github.com/ekka-labs/ekka-agent-desks/blob/main/docs/ASK-YOUR-CODE.md) to inspect a run.

## What you just saw

- Each desk runs as an NVIDIA OpenShell sandbox.
- NVIDIA OpenShell provides isolation and network policy.
- EKKA checks permission, runs the work, and records signed receipts.
- The plan is a small JSON file.
- Nothing in the plan carries a secret.

## Where things live

| Place | What it holds |
|---|---|
| This repository | This walkthrough and one sample plan. |
| [ekka-agent-desks](https://github.com/ekka-labs/ekka-agent-desks) | The setup and desk base image. |
| [ekka-backend](https://github.com/ekka-labs/ekka-backend) | The adapter between EKKA and OpenShell. |
| [docs.ekka.ai](https://docs.ekka.ai) | EKKA documentation. |

## Scope and data handling

The included workflow covers repository setup and code questions. The default GitHub policy allows clone and fetch, with push blocked. A complete issue-to-PR workflow is outside this quick start.

Repository copies live on your server. The model provider receives the code context sent by the agent. EKKA receives run inputs, including your question, and execution records. Read [security and data handling](https://github.com/ekka-labs/ekka-agent-desks/blob/main/docs/SECURITY.md) before using private code.

This example is licensed under [Apache-2.0](LICENSE). EKKA services and release binaries have separate terms.

NVIDIA and OpenShell are trademarks of NVIDIA Corporation in the U.S. and other countries. Claude and Claude Code are trademarks of Anthropic, PBC. EKKA is not affiliated with, sponsored or endorsed by NVIDIA or Anthropic.
