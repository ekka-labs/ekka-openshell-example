# Network packs

A pack is a reusable set of named network rules, each with the reason it exists. A desk takes one policy; the packs
are combined with `desk/policy.json` into that one policy by `sudo ekka desk grant discovery --image <ref> --packs a,b`,
which checks the combination against OpenShell's rules before any desk exists.

| Pack | Opens | For |
|---|---|---|
| `github` | `github.com` (clone and fetch, never push), `codeload.github.com`, `*.githubusercontent.com`, `api.github.com` (read only) | every project |
| `rust` | `index.crates.io`, `static.crates.io`, `crates.io` (read only), `static.rust-lang.org` | Rust projects |
| `node` | `registry.npmjs.org` (read only) | Node projects |

Which packs the EKKA repositories need, read from their lock files (2026-10-31):

| Repository | Packs |
|---|---|
| ekka-runner, ekka-backend | github, rust |
| ekka-govern | github, node, and the `github-packages` key profile |
| ekka-contracts, ekka-dashboard | github, node |

Rules every pack keeps, checked by `tools/desk_grant.py` and its tests:

- **Names, never addresses.** NVIDIA OpenShell answers DNS only for names a rule names, so a rule over address ranges
  opens nothing for a program that connects normally (measured on a desks server, 2026-10-31).
- **Registries are download only** (`read-only`): a publish is refused by the desk itself.
- **Programs by path**, and a pattern only under read-only system folders (`/usr/`, `/bin/`, `/sbin/`, `/lib/`), so the
  agent cannot plant a program that a rule trusts.
- **Ports 443 and 80, enforcement on**, a wildcard host only with three labels (`*.example.com`).
- **Agreement**: two rules that can reach the same host and port must agree on protocol, TLS, address list, enforcement
  and encoded slashes, or OpenShell refuses the desk. The check refuses such a pair by name.

A project's own pack (a private registry, a test service it calls) goes in `/var/lib/ekka-agent-desks/packs.local/`,
root-owned, in the same format, and is named the same way.

Left out on purpose: Claude Code's telemetry (`*.datadoghq.com`, `statsig.anthropic.com`). The agent works without it;
it shows in the log as denied.

## A private registry needs a key, so it is a key profile, not a pack

A pack opens hosts and holds no key. A private registry answers nothing without one, so its host comes with its key, in
a key profile (an OpenShell provider profile). `desk/profiles/github-packages.yaml` is one for private npm packages on
GitHub Packages: the key is sent only by `node`, only to `npm.pkg.github.com`, and only for downloads. Opening the same
host in a pack as well would declare it twice, and OpenShell refuses a desk where the two copies disagree.

On the server, once:

```sh
sudo install -o root -g root -m 0644 desk/profiles/github-packages.yaml /var/lib/ekka-agent-desks/providers.local/
sudo ./up.sh profiles
<a GitHub token with read:packages> | sudo ekka desk connect github-packages
```

GitHub Packages' npm registry takes a classic token, not a fine-grained one, so give it `read:packages` and nothing else.
Then name it on the desk: `--provider claude-code --provider github-packages`. The desk sees only a stand-in for the key
in `NODE_AUTH_TOKEN`; a project's `.npmrc` reads it as `//npm.pkg.github.com/:_authToken=${NODE_AUTH_TOKEN}`, and
the gateway puts the real key into the request on its way out.
