"""sudo ekka desk connect <provider> (the installer's connect verb): a provider's one key, from stdin only, set on this machine (ADK-E8, runner#1042).

up.sh's two functions run here in bash with the gateway stubbed: a fake NVIDIA CLI records its arguments and only a
digest of the key it received in its environment. So each case proves where the key went without writing it down.
"""
import hashlib
import json
import os
import pty
import re
import subprocess
import tempfile
import unittest

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
KEY = "ghp_" + "Zx9" * 12

STUB = r"""#!/usr/bin/env python3
import hashlib, json, os, sys
args = sys.argv[1:]
with open(os.environ["CALLS"], "a") as f:
    f.write(json.dumps({"argv": args, "env_digest": {k: hashlib.sha256(os.environ[k].encode()).hexdigest()
                        for k in ("GITHUB_TOKEN", "ANTHROPIC_API_KEY") if k in os.environ}}) + "\n")
if args[2:4] == ["provider", "get"]:
    sys.exit(0 if os.environ.get("EXISTS") == "1" else 1)
"""


def functions(text=None):
    if text is None:
        with open(os.path.join(ROOT, "up.sh"), encoding="utf-8") as f:
            text = f.read()
    out = []
    for name in ("connect_cmd", "connect_provider"):
        m = re.search(rf"^{name}\(\) \{{.*?^\}}\n", text, re.S | re.M)
        assert m, f"{name} is not in up.sh"
        out.append(m.group(0))
    return "\n".join(out)


def run(args, stdin_bytes=None, exists=False, tty=False, up=None, refresh_fails=False, name=None):
    d = tempfile.mkdtemp()
    stub = os.path.join(d, "openshell")
    with open(stub, "w") as f:
        f.write(STUB)
    os.chmod(stub, 0o755)
    calls = os.path.join(d, "calls.jsonl")
    script = f"""
set -euo pipefail
DESKS_STATE={d}; OS={stub}; NAME=${{NAME:-desks.example.com}}
ok() {{ echo "ok: $*"; }}
die() {{ echo "die: $*" >&2; exit 1; }}
profile_names() {{ printf '%s\\n' claude-code codex github; }}
profile_source() {{ echo "providers/$1.yaml"; }}
as_gateway_admin() {{ ( "$@" ); }}
# The desks grant's refresh, stubbed: tests/test_desk_grant.py runs the real one. It records what it can see.
step_desk_grant() {{ printf '{{"after_calls": %s, "secret_seen": "%s"}}\n' "$(wc -l < "$CALLS" | tr -d ' ')" "${{secret:-}}" >> "$CALLS.refresh"
  [[ -z ${{REFRESH_FAILS:-}} ]] || die "the desks grant was refused; the one in place, if any, is kept"; }}
{functions(up)}
connect_cmd {' '.join(args)}
"""
    env = dict(os.environ, CALLS=calls, EXISTS="1" if exists else "0", **({"REFRESH_FAILS": "1"} if refresh_fails else {}),
               **({"NAME": name} if name else {}))
    env.pop("GITHUB_TOKEN", None)
    if tty:
        master, slave = pty.openpty()
        r = subprocess.run(["bash", "-c", script], cwd=ROOT, stdin=slave, capture_output=True, text=True, env=env,
                           timeout=30)
        os.close(master)
        os.close(slave)
    else:
        r = subprocess.run(["bash", "-c", script], cwd=ROOT, input=stdin_bytes, capture_output=True, env=env,
                           timeout=30)
        r = subprocess.CompletedProcess(r.args, r.returncode, r.stdout.decode(), r.stderr.decode())
    seen = []
    if os.path.exists(calls):
        with open(calls) as f:
            seen = [json.loads(x) for x in f]
    r.refreshes = []   # each run of step_desk_grant: how many gateway calls came before it, and the key it saw
    if os.path.exists(calls + ".refresh"):
        with open(calls + ".refresh") as f:
            r.refreshes = [json.loads(x) for x in f]
    log = os.path.join(d, "openshell-admin.log")
    log_text = ""
    if os.path.exists(log):
        with open(log) as f:
            log_text = f.read()
    return r, seen, log_text


class Connects(unittest.TestCase):
    def test_a_new_provider_is_created_with_its_key_from_stdin_only(self):
        r, calls, log = run(["github"], (KEY + "\n").encode())
        self.assertEqual(r.returncode, 0, r.stderr)
        create = calls[-1]
        self.assertEqual(create["argv"], ["-g", "ekka-agent-desks", "provider", "create", "--name", "github", "--type",
                                          "github", "--global-profile", "--credential", "GITHUB_TOKEN"])
        self.assertEqual(create["env_digest"], {"GITHUB_TOKEN": hashlib.sha256(KEY.encode()).hexdigest()},
                         "NVIDIA's CLI got the key, in the variable the profile names")
        self.assertNotIn("GITHUB_TOKEN", calls[0]["env_digest"], "only the create sees the key, not the lookup")
        for c in calls:
            self.assertNotIn(KEY, " ".join(c["argv"]), "the key is never an argument")
        self.assertNotIn(KEY, r.stdout + r.stderr, "the key is never printed")
        self.assertIn('"command":["openshell","provider","create","--name","github"', log)
        self.assertNotIn(KEY, log, "the key is never logged")
        self.assertIn("ok: github is connected", r.stdout)

    def test_a_provider_that_exists_gets_its_key_replaced(self):
        r, calls, _ = run(["github"], KEY.encode(), exists=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(calls[-1]["argv"][2:], ["provider", "update", "github", "--credential", "GITHUB_TOKEN"])

    def test_each_provider_gets_its_own_key_name(self):
        r, calls, _ = run(["claude-code"], b"sk-ant-abc123")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(calls[-1]["argv"][-1], "ANTHROPIC_API_KEY")


class RefreshesTheDesksGrant(unittest.TestCase):
    # The clean-box walk (regression coverage, 2026-10-19): a key was connected, and the desks grant still
    # listed no provider (providers: []), so a desk naming it would be refused CONSTRAINT_PROVIDERS.
    def assertRefreshedOnceAfterTheKey(self, r, calls):
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(len(r.refreshes), 1, "the desks grant is refreshed once")
        self.assertEqual(r.refreshes[0]["after_calls"], len(calls), "after the key is in the gateway")
        self.assertIn(calls[-1]["argv"][2:4], (["provider", "create"], ["provider", "update"]))
        self.assertEqual(r.refreshes[0]["secret_seen"], "", "the key is gone before the refresh runs")

    def test_a_new_key_refreshes_the_desks_grant(self):
        r, calls, _ = run(["github"], KEY.encode())
        self.assertRefreshedOnceAfterTheKey(r, calls)

    def test_a_replaced_key_refreshes_the_desks_grant(self):
        r, calls, _ = run(["claude-code"], b"sk-ant-abc123", exists=True)
        self.assertRefreshedOnceAfterTheKey(r, calls)

    def test_planted_a_refused_refresh_says_the_key_is_set_and_how_to_finish(self):
        r, calls, _ = run(["github"], KEY.encode(), refresh_fails=True)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("ok: github is connected", r.stdout, "the key was set first")
        self.assertIn("die: the key for github IS set; the desks grant was not refreshed", r.stderr)
        self.assertIn("sudo ./up.sh server desks.example.com", r.stderr, "the exact rerun, with this server's name")
        self.assertNotIn(KEY, r.stdout + r.stderr)

    def test_planted_a_refused_key_refreshes_no_grant(self):
        for given in (b"", b"two words", (KEY + "\nsecond\n").encode()):
            r, _, _ = run(["github"], given)
            self.assertNotEqual(r.returncode, 0)
            self.assertEqual(r.refreshes, [])

    def test_planted_without_the_call_no_grant_is_refreshed(self):
        with open(os.path.join(ROOT, "up.sh"), encoding="utf-8") as f:
            up = f.read()
        body = re.search(r"^connect_cmd\(\) \{.*?^\}\n", up, re.S | re.M).group(0)
        call = re.search(r"^  \( step_desk_grant \) \|\| die .*\n", body, re.M)
        self.assertIsNotNone(call, "connect_cmd calls it once")
        r, calls, _ = run(["github"], KEY.encode(), up=up.replace(body, body.replace(call.group(0), "")))
        with self.assertRaises(AssertionError):
            self.assertRefreshedOnceAfterTheKey(r, calls)


class NoPageSendsAPersonToConnect(unittest.TestCase):
    """Runner 0.1.111 refuses `ekka desk connect <profile>` from a person's computer: a person's sign-in only reads, and
    the key is set on the server, as root: `sudo ekka desk connect <profile>` (the installed wrapper's verb). The walk
    found every page still telling a person to run the bare form."""
    # Running it bare names a profile or a variable. The server's form carries `sudo`; naming the refusal itself,
    # `ekka desk connect <profile>`, is allowed.
    RUN = re.compile(r"(?<!sudo )ekka desk connect [a-z$]")

    def pages(self):
        files = ["README.md", "up.sh", os.path.join("providers", "README.md")]
        files += [os.path.join("docs", f) for f in sorted(os.listdir(os.path.join(ROOT, "docs"))) if f.endswith(".md")]
        for rel in files:
            with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
                yield rel, f.read()

    def test_no_page_and_no_up_sh_message_runs_it(self):
        found = [f"{rel}: {line.strip()}" for rel, text in self.pages() for line in text.splitlines() if self.RUN.search(line)]
        self.assertEqual(found, [])

    def test_planted_the_old_lines_are_caught_and_the_refusal_is_not(self):
        self.assertTrue(self.RUN.search("ekka desk connect claude-code       # asks for the company's Anthropic API key"))
        self.assertTrue(self.RUN.search('  local keys="       ekka desk connect $AGENT'))
        self.assertTrue(self.RUN.search("A profile with several keys: `ekka desk connect codex access_token`."))
        self.assertFalse(self.RUN.search("Naming one (`ekka desk connect <profile>`) is refused"))
        self.assertFalse(self.RUN.search("ekka desk connect                   # the kinds of connection this server knows"))
        self.assertFalse(self.RUN.search("<your claude-code key> | sudo ekka desk connect claude-code"), "the server's own form, as root")


class Refuses(unittest.TestCase):
    def assertNothingSet(self, r, calls, log, says):
        self.assertNotEqual(r.returncode, 0)
        self.assertIn(says, r.stderr)
        self.assertFalse(any("create" in c["argv"] or "update" in c["argv"] for c in calls), "a key was set")
        self.assertNotIn(KEY, r.stdout + r.stderr + log)

    def test_planted_a_key_typed_at_a_terminal_is_refused(self):
        r, calls, log = run(["github"], tty=True)
        self.assertNothingSet(r, calls, log, "never typed at a terminal")

    def test_planted_a_key_as_an_argument_is_refused_and_never_echoed(self):
        r, calls, log = run(["github", KEY], b"")
        self.assertNothingSet(r, calls, log, "never as an argument")
        r, calls, log = run([KEY], KEY.encode())
        self.assertNothingSet(r, calls, log, "that is not a provider this install knows")

    def test_planted_two_lines_or_spaces_or_nothing_on_stdin_are_refused(self):
        for given in ((KEY + "\nsecond\n").encode(), b"two words", b"", b"\n"):
            r, calls, log = run(["github"], given)
            self.assertNothingSet(r, calls, log, "exactly one key")

    def test_planted_a_provider_that_needs_several_keys_is_refused(self):
        r, calls, log = run(["codex"], KEY.encode())
        self.assertNothingSet(r, calls, log, "needs 3 keys")

    def test_planted_a_profile_naming_a_system_variable_is_refused(self):
        # regression review: the name was checked for shape only, so PATH or LD_PRELOAD passed.
        for env in ("PATH", "LD_PRELOAD", "DYLD_INSERT_LIBRARIES", "OPENSHELL_GATEWAY_ENDPOINT", "SSL_CERT_FILE",
                    "HTTPS_PROXY", "BASH_ENV", "PYTHONPATH"):
            profile = os.path.join(tempfile.mkdtemp(), "p.yaml")
            with open(profile, "w") as f:
                f.write(f"id: p\ncredentials:\n  - name: token\n    env_vars: [{env}]\n    required: true\n")
            r = subprocess.run(["python3", os.path.join(ROOT, "tools", "provider_connect.py"), "key", profile],
                               capture_output=True, text=True, timeout=30)
            self.assertEqual(r.returncode, 1, env)
            self.assertIn(f"names {env}, which the system itself reads", r.stderr)
            self.assertEqual(r.stdout, "", "no name is printed for up.sh to use")


class UpSh(unittest.TestCase):
    def test_up_sh_runs_it_and_lists_it(self):
        with open(os.path.join(ROOT, "up.sh"), encoding="utf-8") as f:
            text = f.read()
        self.assertIn("#   <key> | sudo ekka desk connect <provider>", text)
        self.assertRegex(text, r'  connect\)[^\n]*\n(?:[^\n]*\n){0,3}\s*connect_cmd "\$\{@:2\}"; exit \$\? ;;')


if __name__ == "__main__":
    unittest.main()
