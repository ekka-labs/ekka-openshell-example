# Provider profiles

[Back to the start](../README.md)

A provider profile tells the OpenShell gateway three things about a key: which programs on a desk may use it, which hosts it may be sent to, and how it is sent. A profile's key is set on the server, from a pipe: `<key> | sudo ./up.sh connect <profile>` ([Operations](../docs/OPERATIONS.md#connect-a-providers-key)).

These are NVIDIA's own profiles from [OpenShell v0.1.3](https://github.com/NVIDIA/OpenShell/tree/v0.1.3/providers), with the changes below. `PIN.json` records NVIDIA's commit, each file's SHA-256 and every change.

| Profile | For | The key | It may reach |
|---|---|---|---|
| `claude-code` | the Claude Code agent | an Anthropic API key | `api.anthropic.com` |
| `codex` | the Codex agent | four ChatGPT sign-in tokens, one per command | `api.openai.com`, `auth.openai.com`, `chatgpt.com`, `ab.chatgpt.com` |
| `github` | `git` and `gh` | a GitHub token | `github.com` (for `git`), `api.github.com` (read only) |

## What we changed, and why

**A key reaches only its own vendor.** The gateway gives a key to a request whenever the request goes to *any* host in the key's profile. NVIDIA's `claude-code` also lists `statsig.anthropic.com` and `sentry.io` for telemetry, so the Anthropic key would be usable on `sentry.io` too. We drop those two hosts, which the profile's own comment offers; Claude Code works without them. A test fails if any profile names a host outside its key's vendor.

**Not included:** NVIDIA's `copilot` and `cursor` profiles. Each has hosts that are logged but not enforced (audit only), and OpenShell then refuses or weakens desks that enforce the same host. To use either agent, copy NVIDIA's file and set `enforcement: enforce` on those hosts, or drop them.

## Two files for each profile

| File | Read by |
|---|---|
| `<profile>.yaml` | NVIDIA's `openshell` command line, and people |
| `<profile>.json` | The same profile in the gateway's own JSON form, for reference (a person's `ekka desk profile add` took it before runner 0.1.111) |

Each `.json` is generated from the `.yaml` beside it with OpenShell's own parser, so the two always agree. Never edit a `.json` by hand.

## Use your own agent

Choose the profile for the agent your desk image runs, connect its key on the server, and name it when you make the desk:

```sh
ekka desk profile show <profile>                     # what it takes, in plain words
sudo cat /root/agent.key | sudo ./up.sh connect <profile>   # on the server
sudo ./up.sh ekka desk create code-desk --provider <profile>   # on the server
```

`up.sh connect` sets a profile that takes one key. `codex` takes several, so it is refused there today.

To add a kind of connection the server does not have yet, on the server: put its `.yaml` in `/var/lib/ekka-agent-desks/providers.local/` (or here) and run `sudo ./up.sh profiles`. A person's `ekka desk profile add` is refused.

## This install's own profile

When your server signs an agent in some other way than the profile here, keep your own profile out of this repository, in `/var/lib/ekka-agent-desks/providers.local/<profile>.yaml`. A file there wins over the shipped one of the same name, and `sudo ./up.sh profiles` never replaces it. It says which file it used:

```text
   ok: profile claude-code updated from /var/lib/ekka-agent-desks/providers.local/claude-code.yaml (this install's own)
   ok: profile github updated from providers/
```

The file decides how the agent signs in and which hosts its key may reach, so it must be a real file in a real folder, owned by root and writable by nobody else; otherwise `up.sh profiles` refuses, naming the fix. This repository carries no such profile, and tests do not pin it: it is yours to review.
