"""up.sh openshell: an allow-list, each allowed call logged, every other command refused by name (ADK-E8, runner#1042).

The command words are NVIDIA OpenShell 0.1.4's own (crates/openshell-cli/src/main.rs), written here from that source,
not read from the code under test.
"""
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
TOOL = os.path.join(ROOT, "tools", "openshell_allow.py")
EKKA = "desk changes run through EKKA: ekka desk <op>"


def run(*args, user="alice"):
    log = os.path.join(tempfile.mkdtemp(), "openshell-admin.log")
    env = dict(os.environ, SUDO_USER=user)
    r = subprocess.run([sys.executable, TOOL, log, *args], capture_output=True, text=True, env=env,
                       stdin=subprocess.DEVNULL, timeout=30)
    lines = []
    if os.path.exists(log):
        with open(log) as f:
            lines = [json.loads(x) for x in f]
    return r, lines, log


class Allowed(unittest.TestCase):
    def assertAllowedAndLogged(self, *args):
        r, lines, log = run(*args)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0]["command"], ["openshell", *args])
        self.assertEqual(lines[0]["user"], "alice", "the person who ran sudo")
        self.assertRegex(lines[0]["at"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
        self.assertEqual(os.stat(log).st_mode & 0o777, 0o600)

    def test_a_read(self):
        self.assertAllowedAndLogged("provider", "list")

    def test_the_kill_switch_stops_one_named_desk(self):
        self.assertAllowedAndLogged("sandbox", "stop", "code-desk")

    def test_a_providers_refresh_is_set_on_the_box(self):
        self.assertAllowedAndLogged("provider", "refresh", "configure", "aws", "--credential-key", "AWS_ACCESS_KEY_ID",
                                    "--strategy", "aws-sts-assume-role", "--secret-material-env", "role_arn=ROLE_ARN",
                                    "--secret-material-env", "aws_secret_access_key=SOURCE_SECRET")

    def test_a_workspace_on_this_gateway_may_be_named(self):
        self.assertAllowedAndLogged("provider", "list", "--workspace", "desks")

    def test_a_provider_key_named_from_the_environment(self):
        self.assertAllowedAndLogged("provider", "create", "--name", "github", "--type", "github", "--credential", "GITHUB_TOKEN")

    def test_the_log_keeps_every_call(self):
        r1, _, log = run("provider", "list")
        subprocess.run([sys.executable, TOOL, log, "status"], check=True, capture_output=True, stdin=subprocess.DEVNULL)
        with open(log) as f:
            self.assertEqual([json.loads(x)["command"][1] for x in f], ["provider", "status"])


class Refused(unittest.TestCase):
    def assertRefused(self, args, says):
        r, lines, _ = run(*args)
        self.assertEqual(r.returncode, 1, args)
        self.assertIn(says, r.stderr)
        self.assertEqual(lines, [], "a refused call is never logged as run")

    def test_planted_sandbox_create_is_refused_by_name(self):
        self.assertRefused(["sandbox", "create", "--name", "x"], f"REFUSED: openshell sandbox create: {EKKA}")

    def test_planted_every_other_desk_write_is_refused_by_name(self):
        for args in (["sandbox", "delete", "code-desk"], ["sandbox", "start", "code-desk"],
                     ["sandbox", "exec", "code-desk", "--", "sh"], ["sandbox", "connect", "code-desk"],
                     ["policy", "set", "code-desk"], ["settings", "set"], ["workspace", "member", "add"],
                     ["provider", "profile", "import"], ["sandbox", "provider", "attach"]):
            self.assertRefused(args, EKKA)

    def test_planted_an_alias_or_a_flag_before_the_command_is_refused(self):
        self.assertRefused(["sb", "list"], EKKA)
        self.assertRefused(["-g", "other", "provider", "list"], f"openshell (no command): {EKKA}")
        self.assertRefused([], EKKA)

    def test_planted_a_stop_must_name_one_desk(self):
        for args in (["sandbox", "stop"], ["sandbox", "stop", "a", "b"], ["sandbox", "stop", "--all"],
                     ["sandbox", "stop", "../x"]):
            self.assertRefused(args, "name exactly one desk")

    def test_planted_a_secret_as_an_argument_is_refused(self):
        self.assertRefused(["provider", "create", "--name", "g", "--type", "github", "--credential", "TOKEN=abc"],
                           "a secret is never an argument")
        self.assertRefused(["provider", "update", "g", "--credential=TOKEN=abc"], "a secret is never an argument")
        self.assertRefused(["provider", "refresh", "configure", "aws", "--secret-material-key", "k"],
                           "refresh material is never an argument")

    def test_planted_refresh_material_as_an_argument_is_refused(self):
        # regression review: `--material refresh_token=abc` passed, and NVIDIA stores it as ordinary material.
        for args in (["--material", "refresh_token=abc"], ["--material=refresh_token=abc"],
                     ["--material", "role_arn=arn:aws:iam::1:role/r"]):
            self.assertRefused(["provider", "refresh", "configure", "aws", "--credential-key", "K",
                                "--strategy", "aws-sts-assume-role", *args],
                               "openshell --material: refresh material is never an argument")

    def test_planted_a_gateway_flag_after_the_command_is_refused(self):
        # Global in NVIDIA's CLI, so one after the command would override up.sh's `-g ekka-agent-desks`.
        for args, flag in ((["--gateway", "other"], "--gateway"), (["--gateway=other"], "--gateway"),
                           (["--gateway-endpoint", "https://gw.example.com"], "--gateway-endpoint"),
                           (["--gateway-endpoint=https://gw.example.com"], "--gateway-endpoint"),
                           (["--gateway-insecure"], "--gateway-insecure"),
                           (["--gateway-insecure=true"], "--gateway-insecure"),
                           (["-g", "other"], "-g"), (["-gother"], "-g"), (["-g=other"], "-g"), (["-vg", "other"], "-g")):
            self.assertRefused(["provider", "list", *args], f"openshell {flag}: the call always goes to this machine's gateway")
        self.assertRefused(["sandbox", "stop", "code-desk", "-g", "other"], "name exactly one desk")

    def test_planted_the_administrators_own_sign_in_never_becomes_a_key(self):
        self.assertRefused(["provider", "update", "g", "--from-oidc-token"], "never becomes a desk's key")

    def test_planted_a_log_that_is_a_link_is_never_followed(self):
        d = tempfile.mkdtemp()
        target, log = os.path.join(d, "elsewhere"), os.path.join(d, "openshell-admin.log")
        os.symlink(target, log)
        r = subprocess.run([sys.executable, TOOL, log, "provider", "list"], capture_output=True, text=True,
                           stdin=subprocess.DEVNULL, timeout=30)
        self.assertEqual(r.returncode, 1)
        self.assertIn("could not be logged", r.stderr)
        self.assertFalse(os.path.exists(target))


class UpSh(unittest.TestCase):
    def test_up_sh_checks_and_logs_before_nvidias_cli_runs(self):
        with open(os.path.join(ROOT, "up.sh"), encoding="utf-8") as f:
            text = f.read()
        m = re.search(r"^  openshell\)(.*?);;", text, re.S | re.M)
        self.assertTrue(m)
        body = m.group(1)
        guard = body.find('python3 tools/openshell_allow.py "$DESKS_STATE/openshell-admin.log" "$@" || exit 1')
        self.assertGreater(guard, 0, "up.sh openshell does not run the allow-list")
        self.assertLess(guard, body.find("as_gateway_admin"), "the allow-list must run before NVIDIA's CLI")

    def test_planted_gateway_environment_never_reaches_nvidias_cli(self):
        with open(os.path.join(ROOT, "up.sh"), encoding="utf-8") as f:
            text = f.read()
        body = re.search(r"^as_gateway_admin\(\) \{.*?^\}\n", text, re.S | re.M).group(0)
        unset = re.search(r"unset ([A-Z_ ]+)\n", body)
        self.assertTrue(unset, "as_gateway_admin clears NVIDIA's gateway environment")
        self.assertEqual(set(unset.group(1).split()),
                         {"OPENSHELL_GATEWAY", "OPENSHELL_GATEWAY_ENDPOINT", "OPENSHELL_GATEWAY_INSECURE"})
        self.assertLess(unset.start(), body.find('\n    "$@"\n'), "cleared before the call runs")


if __name__ == "__main__":
    unittest.main()
