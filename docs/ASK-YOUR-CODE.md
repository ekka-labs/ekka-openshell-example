# Ask your code a question

[Back to the start](../README.md)

The agent reads the repositories on its desk and answers a question about them. Each run writes its answer once, and the Enclave collects it with that run, so two people asking get two answers. It displays the answer on the machine where you installed.

## Before you start

The administrator has completed [server installation](SERVER.md) and [set up the desk's repositories](SET-UP-YOUR-DESK.md). Run the commands below on the **server**, from this repository's folder, using the configured EKKA sign-in.

Every desk has its own ask plan. `code-desk`'s came with the install. For another desk, make its plans once on the machine where you installed (the desk name is written in camelCase in the plan name):

```sh
sudo ekka desk plans business-desk     # ekka.example.desk.ask.businessDesk.v1 and its setup plan
```

## Ask

**Why:** the answer comes from the code on the desk, at a known commit, not from what a model remembers. Repository files remain on the desk. The selected model provider receives the code context the agent sends for inference; EKKA's control plane does not receive your repository.

On the machine where you installed, as root (`sudo -i`):

```sh
ekka plan run ekka.example.desk.ask.codeDesk.v1 \
  --input question="How does a user reset their password, from the request to the email? Cite the files and lines."
```

```text
✓ Plan completed (run 99908c81-6f2b-41da-ad1f-ffe31a7eac86).
```

The plan name without a version runs its newest version, so an upgrade of this setup reaches you with no change to the command.

## Read the answer

**Why:** your answer is collected on your own Enclave. Only its fingerprint goes into EKKA's record.

The completed plan displays the answer on the installed machine. Read it again with the run id it printed:

```sh
ekka run show 99908c81-6f2b-41da-ad1f-ffe31a7eac86
```

The run succeeds only when the Enclave collected an answer file. A run that wrote none fails.

The answer names the repository and commit it read, and points to files and lines. For a first check with the public `octocat/Hello-World` repository, ask what its README says: the answer should cite `Hello-World/README`, line 1, containing `Hello World!`. For your own code, check a cited file and line against the repository before relying on the answer.

Long answers show a preview and the path to the complete file on the Enclave machine.

It is read on the Enclave's machine, where the run's store is (the Enclave must be release 0.1.112 or later).
The answer ends with a line saying it is this machine's local copy and is not checked against the run's signed
receipt; `ekka receipts verify` checks the record itself. Characters that could drive a terminal are shown
escaped, never acted on.

## What was kept, and where

People authorized to use this desk share its workspace. Use separate desks for confidential work.

A desk is a shared workspace. We do not guarantee privacy between people using the same desk. An agent may retain or copy information anywhere its permissions allow. Customers who need separation can use a desk per user, with access restricted accordingly.

| | Where it is |
|---|---|
| Repository files | stored on the desk; context is sent to the selected model provider for inference |
| The answer | written once on the desk as `.ekka/answer.md` for EKKA to collect (from EKKA 0.1.120, removed once collected), and a copy on your Enclave with the run |
| Repository preparation and relevant working-tree changes | `.ekka/ask-record.json` on the desk, kept on your Enclave with the run |
| Your question | with the run in EKKA, like every run input |
| What ran, on which desk, with which prompt, and the answer's digest | EKKA's signed record: `ekka receipts show <run>` |

## The desk's repository copies

Before answering, the ask prompt tells the agent to remove
and re-clone the repository copies listed in the desk map. Changes inside those copies,
including ignored files and Git hooks, are discarded. Keep work you want to retain elsewhere.
If preparation fails, the prompt tells the agent to stop without answering.

This refresh is performed by the agent following the prompt. It is not a whole-desk wipe
or an isolation boundary: files outside the repository copies and agent home state can
remain. OpenShell's network policy separately blocks Git push.

After upgrading, make each desk's plans again with `sudo ekka desk plans <desk>`. Never point
a desk's disposable repository plan at a checkout whose changes you need to keep.

## Ask something else

Change the question and run it again. Good questions name a behavior, not a file:

- "How does a user reset their password, end to end?"
- "Which services call the billing API, and with what retries?"
- "What would break if we removed the `legacy_id` column?"
