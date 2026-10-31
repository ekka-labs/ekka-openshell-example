# Commands

[Back to the start](../README.md)

Everything you do with desks is an `ekka` command, and EKKA checks each one ([How it works](HOW-IT-WORKS.md)). Add `--help` to any command for every option.

## Where to run commands

| Task | Run it here |
|---|---|
| Read desk lists, logs, policies, or connections | Any computer signed in with `ekka desk login`. |
| Read pins with `show`, check live status, or change a desk | The installed server, as its administrator, using `sudo ./up.sh ekka desk <command>`. |
| Run the example setup or question plan | The installed server, using its configured EKKA sign-in. |

[Desk administration](OPERATIONS.md#change-a-desk) explains the permissions and sign-in used for changes.

## Sign in

| Command | What it does |
|---|---|
| `ekka desk login` | Sign in to the desks server. Opens your browser. |
| `ekka desk logout` | Remove your sign-in from this computer. |
| `ekka desk backend show` | Which desks server this computer uses, and how you sign in there. |
| `ekka desk backend set <file.json>` | Point this computer at a desks server. The people bundle's `install.sh` does this for you. |

```sh
./install.sh desks        # once, from the bundle your server's administrator gives you
ekka desk login
```

## Keys

A *provider profile* says which programs on a desk may use a key, and which hosts the key may be sent to. Keys belong to the company: one person connects each key once, and every desk on the server can be given it. One key per command, read from standard input, never typed as an argument.

| Command | What it does |
|---|---|
| `ekka desk profile list` | The kinds of connection the server knows (`claude-code`, `github`, ...). |
| `ekka desk profile show <profile>` | One profile in plain words: the key it takes, which programs may use it, which hosts it reaches. |
| `ekka desk connect` | The kinds of connection this server knows. Naming one (`ekka desk connect <profile>`) is refused: a key is set on the server, `<key> \| sudo ./up.sh connect <profile>` ([Operations](OPERATIONS.md#connect-a-providers-key)). |
| `ekka desk connections` | The keys your desks may use (names only, never values). |
| `sudo ./up.sh ekka desk disconnect <name>` | On the installed machine: detach a key from every desk that uses it. |
| `ekka desk profile add <file.json>` | Refused. A kind of connection is added on the server: its `.yaml` in `/var/lib/ekka-agent-desks/providers.local/`, then `sudo ./up.sh profiles`. |

```sh
ekka desk profile show claude-code
ekka desk connect                   # the kinds of connection this server knows
```

What `ekka desk profile show claude-code` says about the key, before anyone sets it:

```text
claude-code: Claude Code
  api_key: Anthropic API key used by Claude Code, sent in header x-api-key
    may reach api.anthropic.com
    used only by /usr/bin/claude, /usr/local/bin/claude
```

The key itself is set on the server, from a pipe: `sudo cat /root/anthropic.key | sudo ./up.sh connect claude-code`. It is never an argument, so it never shows up in your shell history or the process list. `up.sh connect` takes a profile with one key; one with several, such as `codex`, is refused there.

## Desks

| Command | What it does |
|---|---|
| `sudo ./up.sh ekka desk create <name> --provider <profile> ...` | On the installed machine: make a desk with the keys it may use. It gets the setup's desk image and policy; `--policy <file>` sets another policy. |
| `ekka desk list` | The desks on the server. |
| `sudo ./up.sh ekka desk show <name>` | On the installed machine: one desk's folders, pins and required grant. |
| `sudo ./up.sh ekka desk stop <name>` | On the installed machine: stop a desk. Its files stay. |
| `sudo ./up.sh ekka desk start <name>` | On the installed machine: start it again, and wait until it is ready. |
| `sudo ./up.sh ekka desk status <name>` | On the installed machine: the desk's live state, read through EKKA. While a change is still running it says so. |
| `ekka desk logs <name> [-f]` | A desk's recent log lines; `-f` follows them. |
| `sudo ./up.sh ekka desk delete <name>` | On the installed machine: delete a desk and its work folder. Deletion retires its old pin; a new desk of that name is registered and pinned anew. An unexpected replacement of a still-pinned desk needs approval (`repin`, below) before work runs. |

```sh
sudo ./up.sh ekka desk create code-desk --provider claude-code   # on the installed machine
sudo ./up.sh ekka desk show code-desk                             # the Enclave's pins and details
```

Connect the keys **before** you make the desk: a desk names the keys it may use when it is made.

## Files on a desk

Nothing is copied into or out of a desk, and nothing runs in one, outside EKKA. The first two are refused, and the
third is no longer a command:

| Command | What it does |
|---|---|
| `ekka desk upload <name> <local> [<folder>]` | Refused. Put the files in the repository the desk clones. |
| `ekka desk download <name> <path> [<folder>]` | Refused. |
| `ekka desk shell <name> [-- <command>]` | Not a command since ekka 0.1.119: `ekka` answers "unrecognized subcommand". To work with a desk's agent, run a plan whose job step names the desk: `ekka plan run <plan>`. |

## What a desk may reach

| Command | What it does |
|---|---|
| `ekka desk policy show <name>` | The desk's policy in plain words. |
| `ekka desk policy check <file>` | Check a policy file exactly as `ekka desk create` reads it. |
| `sudo ./up.sh ekka desk policy set <name> <file>` | On the installed machine: replace a live desk's policy. |
| `sudo ./up.sh ekka desk allow <name> --program <path> --host <host>` | On the installed machine: let one program reach one host. Add `--read-only`, `--port`, `--path` to narrow it. |
| `sudo ./up.sh ekka desk deny <name> --program <path> --host <host>` | On the installed machine: stop one program reaching one host. |

```sh
sudo ./up.sh ekka desk allow code-desk --program /usr/bin/curl --host registry.npmjs.org --read-only
ekka desk policy show code-desk
```

A policy change runs on the installed machine like every desk change, and never while a job is running on that desk. It first reads the desk's current policy, so the Enclave needs its own read-only desk sign-in, once ([Change a desk](OPERATIONS.md#change-a-desk)). A change is stored at once and takes effect when the desk loads it, a few seconds later (`policy show` says "pending" until then). **Work on that desk then waits** until a person approves the new policy. The Enclave notices the change within about a minute; then approve it on the installed machine: `sudo ./up.sh ekka desk repin <name>`.

Policy files in a server command are read **inside the Enclave**, not from your host's working directory. The installer sets its default policy there. To use a policy file on the host, pass it on standard input:

```sh
sudo cat policy.json | sudo ./up.sh ekka desk policy set code-desk /dev/stdin --yes
```

Read the file before running this command; `--yes` confirms the change without an interactive prompt. The same input path works with `desk create --policy /dev/stdin`.

## Who may use the desks

Give a person a sign-in account and a place in the desk workspace, on the server:

```sh
sudo ./up.sh invite member member@example.com
```

| Command | What it does |
|---|---|
| `ekka desk access list` | The workspace's members and their roles. |
| `ekka desk access add <person>` | Refused. A person is added on the server: `sudo ./up.sh invite <username> <email>`. |
| `ekka desk access remove <person>` | Refused. A person is removed on the server: `sudo ./up.sh revoke <username>`. |

## Plans, grants and records

Work on a desk is a *plan*: steps EKKA checks, runs on your Enclave, and signs.

| Command | What it does |
|---|---|
| `ekka plan list` | The plans you can run. |
| `ekka plan run <plan> --input <key>=<value>` | Run a plan and wait for it. |
| `ekka plan validate <file>` | Check a plan file; prints any grant it still needs. |
| `ekka plan create <file>` | Create a plan from a file. |
| `ekka plan schedule <plan> --every 1h` | Run it on a schedule. |
| `ekka plan schedules` | Every schedule, with its next and last run. |
| `ekka plan unschedule <id>` | Stop a schedule. |
| `ekka gate grant list` | The grants your organization has issued. |
| `ekka gate grant revoke <id>` | Take one back. |
| `ekka decide list` | The decisions waiting on you. |
| `ekka decide answer <id>` | Answer one. `ekka decide show <id>` first shows everything about it. |
| `ekka run list` | What your organization has run, newest first. |
| `ekka run show <id>` | EKKA's recorded decisions for one run. |
| `ekka receipts list` | Recent runs with a signed record. |
| `ekka receipts show <run>` | What EKKA recorded for one run, in plain English. |
| `ekka receipts verify` | Check that nothing in the signed record was changed after the fact. |

**Where to run them.** Running a plan goes through your Enclave, on the machine you installed on: use `sudo ./up.sh ekka plan run ...` there. The other commands talk to EKKA directly, so they work from any computer signed in with `ekka login`.

```sh
sudo ./up.sh ekka plan run ekka.example.desk.ask.codeDesk.v1 --input question="Where do we retry failed payments?"
sudo ./up.sh desk-plans business-desk     # the setup and ask plans for one more desk
sudo ./up.sh ekka receipts list
```

## On the installed machine

Rarely needed: desks register themselves. Run these on the machine you installed on, through `up.sh`.

| Command | What it does |
|---|---|
| `sudo ./up.sh ekka desk repin <name>` | Approve the desk now holding a name, after it was replaced. Until then, no work runs on it. |
| `sudo ./up.sh ekka desk add <file.json>` | Register an OpenShell sandbox that was not made with `ekka desk create`. |

## Related workflow commands

- On the server, as root (`sudo -i`): `ekka job answer <run-id>` shows a run's answer, `ekka receipts show <run-id>` its record. This setup serves no downloads to laptops.
- `sudo ./up.sh ekka agent deny desks --everyone` removes shared permission to run the desks agent's plans. Use it only when you intend to restrict team access.
