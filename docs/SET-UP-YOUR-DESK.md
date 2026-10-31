# Set up a desk

[Back to the start](../README.md)

Add the repositories you want the agent to read. Run these commands on the **server**, from this repository's folder, after completing [server installation](SERVER.md).

The agent does the cloning inside the desk. Public GitHub repositories need no token: the shipped policy lets `git` clone and fetch over HTTPS, with push blocked. Private repositories also need the GitHub connection; the gateway adds its read-only Contents token on the way to GitHub, so the agent never sees it.

## Before you start

The server is installed, the Enclave has completed its browser sign-in, and the model API key is connected. A signed-in laptop client can inspect the connections:

```sh
ekka desk connections               # lists your connected model and optional GitHub connection
```

If you followed the install guide, `code-desk` exists already, with its plans: skip to step 2.

## 1. Make a desk for the agent

**Why:** the desk is the agent's own workspace for this job, cut off from your machine. Its shipped policy allows GitHub clone and fetch; attached connections add the services the agent may use.

On the machine you installed on, as root (`sudo -i`), where every desk change runs ([why](OPERATIONS.md#change-a-desk)):

```sh
ekka desk create code-desk --provider claude-code
```

Every desk gets the setup's policy ([`desk/policy.json`](../desk/policy.json)), which the server installer sets as the Enclave's default. It keeps the system folders read-only, lets programs write only to `/tmp` and the work folder, and runs them as a normal user. The shipped GitHub rule lets only `git` clone and fetch. `claude-code` adds the Anthropic connection. For private repositories, connect a read-only GitHub Contents token with `sudo ekka desk connect github` and include `--provider github` in the create command. Other network traffic is blocked.

The desk registers itself with EKKA shortly after it starts. Check it:

```sh
ekka desk show code-desk
```

Each desk is set up and asked through its own plans. `code-desk`'s came with the install. For any other desk, make them once, on the machine where you installed:

```sh
sudo ekka desk plans business-desk     # ekka.example.desk.setup.businessDesk.v1 and its ask plan
```

A desk's plans are offered to your organization and accepted, and the install lets every member run the `desks`
agent's plans (`ekka agent allow desks --everyone --as use`), so every member can run them, not only the
administrator. Only run: writing a new plan under that agent still needs an owner to say so (`--as author`), and
the desk administration plans stay yours. The desk's grant still decides what the work may touch. To keep a desk's
plans to yourself, add `--private`: `sudo ekka desk plans business-desk --private`.

## 2. Clone your repositories

**Why:** the agent does the cloning, inside the desk. Your GitHub token stays in the gateway, and EKKA's record says exactly which commits arrived.

On the machine where you installed:

```sh
ekka plan run ekka.example.desk.setup.codeDesk.v1 \
  --input 'repos=["https://github.com/your-org/api", "https://github.com/your-org/web"]'
```

```text
✓ Plan completed (run 3f2a9c1e-...).
```

For each repository, the agent:

- clones it into `/sandbox/ekka-workspace/<name>` if it is absent;
- reuses an existing checkout, preserving its branch, commit and uncommitted changes, and reports it as reused;
- leaves a folder that is not that repository alone, and reports it as skipped;
- deletes or overwrites nothing else, and never stores a credential.

The plan fails unless every repository is ready, so a skipped or failed repository is never reported as success. The counts are part of the signed record:

```text
cloned=2 reused=0 updated=0 reset=0 skipped=0 failed=0 result=ready
```

## 3. See what is on the desk

**Why:** you know exactly which commit of each repository the agent will read.

The agent writes a map of every repository, its branch and commit. Ask it to read that map and report what is ready:

```sh
ekka plan run ekka.example.desk.ask.codeDesk.v1 \
  --input question="Which repositories are on this desk, and what branch and commit is each on?"
```

The answer is displayed on the installed machine and collected with the run.

People authorized to use this desk share its workspace. Use separate desks for confidential work. A desk is a shared workspace. We do not guarantee privacy between people using the same desk. An agent may retain or copy information anywhere its permissions allow. Customers who need separation can use a desk per user, with access restricted accordingly.

## Run setup again

**Why:** add missing repositories without disturbing work already on the desk.

Run the same setup command again. Existing matching checkouts report `reused`; setup itself
does not update or discard them. The ask prompt then recreates the repository copies before
answering. See [the desk's repository copies](ASK-YOUR-CODE.md#the-desks-repository-copies).

## What made this safe

- The plan names a prompt, `desk-setup` version 3, that EKKA keeps and never changes. EKKA sends its text and SHA-256 in the signed request; your Enclave refuses the run if the text differs.
- Your desks agent may run jobs on this workspace's desks because of one grant, made at install. `ekka gate grant list` shows it, with the command to take it back.
- The desk could reach only GitHub and Anthropic. Only `claude` could use the Anthropic key, and only `git` and `gh` the GitHub token.

Next: [Ask your code a question](ASK-YOUR-CODE.md).
