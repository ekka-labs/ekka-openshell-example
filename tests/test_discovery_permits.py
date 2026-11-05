"""Discovery's auto approval mode, on this machine: the permits folder, and what the Enclave is told about it.

The backend adapter writes a permit for one discovery desk (contracts backend/discovery-permit.v1) into
<state_dir>/permits, and the native Enclave's gateway interceptor reads the same files to let that desk's proposals
through. So the install makes the host folder (the Enclave's user's, 0750), and writes two lines into the Enclave's
enclave.env: who the adapter is (the `ekka-desk-admin` client's service-account subject, found the way the Enclave's own
`ekka-enclave` subject is) and where the permits are. The real functions run here, from up.sh and deploy/setup.sh,
with Keycloak, the Enclave's home, install, stat and systemctl stubbed.
"""
import os
import re
import subprocess
import tempfile
import unittest

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
ENCLAVE_SUB = "9a8b7c6d-0000-4000-8000-000000000001"
ADMIN_SUB = "5e4d3c2b-1111-4222-8333-000000000002"


def shell_function(path, name):
    with open(os.path.join(ROOT, path), encoding="utf-8") as f:
        text = f.read()
    one = re.search(rf"^{name}\(\) \{{[^\n]*\}}\n", text, re.M)
    return one.group(0) if one else re.search(rf"^{name}\(\) \{{.*?^\}}\n", text, re.S | re.M).group(0)


def read_lines(path):
    if not os.path.exists(path):
        return []
    with open(path) as f:
        return f.read().splitlines()


def run(script, cwd=ROOT, env=None, path_dir=None):
    e = dict(os.environ, **(env or {}))
    if path_dir:
        e["PATH"] = path_dir + os.pathsep + e["PATH"]
    return subprocess.run(["/bin/bash", "-c", script], cwd=cwd, capture_output=True, text=True, timeout=30, env=e,
                          stdin=subprocess.DEVNULL)


# Keycloak, stubbed by client: each client's service-account subject, or nothing when the client does not exist.
CLIENTS = f'''client_subject() {{
  case $1 in
    ekka-enclave) echo {ENCLAVE_SUB} ;;
    ekka-desk-admin) [[ -n ${{ADMIN_SUB:-}} ]] || {{ echo "setup: no such client" >&2; return 1; }}; echo "$ADMIN_SUB" ;;
    *) echo "setup: asked for $1" >&2; return 1 ;;
  esac
}}'''


class TheEnclaveIsToldWhoTheAdapterIsAndWhereThePermitsAre(unittest.TestCase):
    BEFORE = ("EKKA_TENANT_ID=t-1\nEKKA_NODE_ID=n-1\nEKKA_OPENSHELL_EXECUTOR_SUBJECT=user:oidc:" + ENCLAVE_SUB +
              "\nEKKA_DESKS_AUTO=true\n")

    def setUp(self):
        self.state, self.home = tempfile.mkdtemp(), tempfile.mkdtemp()
        with open(os.path.join(self.state, "desks.env"), "w") as f:
            f.write("DESKS_NAME=desks.example.com\n")

    def env_file(self, env="prod"):
        return os.path.join(self.home, "enclave.env" if env == "prod" else os.path.join(env, "enclave.env"))

    def write_env(self, text, env="prod"):
        os.makedirs(os.path.dirname(self.env_file(env)), exist_ok=True)
        with open(self.env_file(env), "w") as f:
            f.write(text)

    def read_env(self, env="prod"):
        with open(self.env_file(env)) as f:
            return f.read()

    def stage(self, call, admin_sub=ADMIN_SUB):
        script = "\n".join([
            "set -euo pipefail", f"DESKS_STATE={self.state}; T={self.state}/tls; HOME_DIR={self.home}",
            'say() { echo "say: $*"; }', 'die() { echo "die: $*" >&2; exit 1; }', "kc_env() { :; }",
            'wait_keycloak() { echo "   waiting for the sign-in service ... ok (5 s)"; }',
            "openssl() { printf 'k'; }",
            'enclave_home_read() { cat "$HOME_DIR/$1"; }',
            'enclave_home_file() { mkdir -p "$(dirname "$HOME_DIR/$1")"; cat > "$HOME_DIR/$1"; chmod "$2" "$HOME_DIR/$1"; }',
            CLIENTS,
            *(shell_function("deploy/setup.sh", n) for n in (
                "stage_enclave_subject", "stage_backend_subject", "enclave_backend_lines", "stage_enclave_backend",
                "stage_enclave_config")),
            call])
        return run(script, cwd=os.path.join(ROOT, "deploy"), env={"ADMIN_SUB": admin_sub})

    def lines(self):
        return [f"EKKA_OPENSHELL_BACKEND_SUBJECT=user:oidc:{ADMIN_SUB}",
                f"EKKA_OPENSHELL_DISCOVERY_PERMITS_DIR={self.state}/backend/permits"]

    def test_the_two_lines_are_added_and_every_other_line_is_kept(self):
        self.write_env(self.BEFORE)
        r = self.stage("stage_enclave_backend prod")
        self.assertEqual((r.returncode, r.stdout), (0, "changed\n"), r.stderr)
        self.assertEqual(self.read_env(), self.BEFORE + "\n".join(self.lines()) + "\n")
        self.assertEqual(oct(os.stat(self.env_file()).st_mode & 0o777), "0o600")

    def test_the_subject_is_the_desk_admin_clients_never_the_enclaves(self):
        self.write_env(self.BEFORE)
        self.stage("stage_enclave_backend prod")
        got = [x for x in self.read_env().splitlines() if x.startswith("EKKA_OPENSHELL_BACKEND_SUBJECT=")]
        self.assertEqual(got, [f"EKKA_OPENSHELL_BACKEND_SUBJECT=user:oidc:{ADMIN_SUB}"])
        self.assertNotIn(ENCLAVE_SUB, got[0])

    def test_a_second_run_changes_nothing_and_says_so(self):
        self.write_env(self.BEFORE)
        self.stage("stage_enclave_backend prod")
        first = self.read_env()
        r = self.stage("stage_enclave_backend prod")
        self.assertEqual((r.returncode, r.stdout), (0, "kept\n"), r.stderr)
        self.assertEqual(self.read_env(), first)

    def test_a_new_subject_replaces_the_old_line_never_a_second_one(self):
        self.write_env(self.BEFORE + "EKKA_OPENSHELL_BACKEND_SUBJECT=user:oidc:old\nEKKA_OPENSHELL_DISCOVERY_PERMITS_DIR=/elsewhere\n")
        r = self.stage("stage_enclave_backend prod")
        self.assertEqual(r.stdout, "changed\n", r.stderr)
        self.assertEqual(self.read_env(), self.BEFORE + "\n".join(self.lines()) + "\n")

    def test_another_environment_keeps_its_file_in_its_own_folder(self):
        self.write_env(self.BEFORE, env="staging")
        r = self.stage("stage_enclave_backend staging")
        self.assertEqual(r.stdout, "changed\n", r.stderr)
        self.assertTrue(self.read_env("staging").endswith("\n".join(self.lines()) + "\n"))
        self.assertFalse(os.path.exists(self.env_file()))

    def test_planted_no_desk_admin_client_writes_nothing(self):
        self.write_env(self.BEFORE)
        r = self.stage("stage_enclave_backend prod", admin_sub="")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("did not name the ekka-desk-admin client's subject; nothing written", r.stderr)
        self.assertEqual(self.read_env(), self.BEFORE)

    def test_planted_a_subject_that_is_not_one_writes_nothing(self):
        self.write_env(self.BEFORE)
        r = self.stage("stage_enclave_backend prod", admin_sub="x\nEKKA_OPENSHELL_EXECUTOR_SUBJECT=user:oidc:evil")
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(self.read_env(), self.BEFORE)

    def test_the_first_configuration_has_the_lines_once_the_client_exists_and_omits_them_before(self):
        self.write_env("EKKA_TENANT_ID=t-1\n")
        r = self.stage("stage_enclave_config prod desks", admin_sub="")
        self.assertEqual(r.returncode, 0, r.stderr)
        text = self.read_env()
        self.assertIn(f"EKKA_OPENSHELL_EXECUTOR_SUBJECT=user:oidc:{ENCLAVE_SUB}\n", text)
        self.assertNotIn("EKKA_OPENSHELL_BACKEND_SUBJECT", text, "a fresh install: the client is made later")
        self.assertNotIn("EKKA_OPENSHELL_DISCOVERY_PERMITS_DIR", text)
        # Run again once the client exists (or by hand later): the lines are there, and enclave-backend keeps them.
        r = self.stage("stage_enclave_config prod desks")
        self.assertEqual(r.returncode, 0, r.stderr)
        for line in self.lines():
            self.assertEqual(self.read_env().splitlines().count(line), 1, line)
        self.assertEqual(self.stage("stage_enclave_backend prod").stdout, "kept\n")


INSTALL = """#!/bin/bash
echo "$*" >> "$CALLS"
last=${@: -1}; mkdir -p "$last"
"""
STAT = """#!/bin/bash
echo "$STAT_ANSWER"
"""


class ThePermitsFolder(unittest.TestCase):
    def permits(self, stat_answer="ekka:ekka 750", plant=None):
        state, bin_ = tempfile.mkdtemp(), tempfile.mkdtemp()
        for name, text in (("install", INSTALL), ("stat", STAT)):
            with open(os.path.join(bin_, name), "w") as f:
                f.write(text)
            os.chmod(os.path.join(bin_, name), 0o755)
        if plant == "link":
            os.makedirs(os.path.join(state, "backend"))
            os.symlink(tempfile.mkdtemp(), os.path.join(state, "backend", "permits"))
        calls = os.path.join(state, "calls")
        script = "\n".join(["set -euo pipefail", ". deploy/lib.sh", f"DESKS_STATE={state}",
                            'die() { echo "die: $*" >&2; exit 1; }', shell_function("up.sh", "backend_permits_dir"),
                            "backend_permits_dir"])
        r = run(script, env={"CALLS": calls, "STAT_ANSWER": stat_answer}, path_dir=bin_)
        return r, read_lines(calls), state

    def test_it_is_the_enclaves_user_0750_under_the_backend_state(self):
        r, made, state = self.permits()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(made, [f"-d -m 0755 {state}/backend", f"-d -m 0750 -o ekka -g ekka {state}/backend/permits"])

    def test_planted_a_folder_left_with_another_owner_or_mode_is_refused(self):
        for answer in ("root:root 755", "ekka:ekka 770", "ekka:ubuntu 750"):
            r, _, state = self.permits(stat_answer=answer)
            self.assertNotEqual(r.returncode, 0, answer)
            self.assertIn(f"{state}/backend/permits is not ekka:ekka 0750", r.stderr)

    def test_planted_a_link_in_its_place_is_refused_before_anything_is_made(self):
        r, made, _ = self.permits(plant="link")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("is a link", r.stderr)
        self.assertEqual(made, [], "root never follows the link")

    def test_it_is_made_before_anything_can_start_the_adapter(self):
        with open(os.path.join(ROOT, "up.sh"), encoding="utf-8") as f:
            up = f.read()
        start = shell_function("up.sh", "step_start")
        self.assertLess(start.index("backend_permits_dir"), start.index("setup.sh backend-preflight"))
        admin = shell_function("up.sh", "step_desk_admin")
        self.assertLess(admin.index("backend_permits_dir"), admin.index("systemctl restart ekka-desks"))
        self.assertIn("step_start;", up)


class TheEnclaveRestartsOnlyWhenItsSettingsChanged(unittest.TestCase):
    """step_desk_admin, run for real with its neighbours stubbed: the Enclave reads enclave.env at start, so a changed
    file restarts it (after the adapter is healthy), and an unchanged one never does."""

    def step(self, answer, fails=False):
        state = tempfile.mkdtemp()
        with open(os.path.join(state, "up.env"), "w") as f:
            f.write("DESKS_NAME=x\nEKKA_BACKEND_IMAGE=ghcr.io/ekka-labs/ekka-backend@sha256:" + "a" * 64 + "\n")
        calls = os.path.join(state, "calls")
        script = "\n".join([
            "set -euo pipefail", f"DESKS_STATE={state}; CALLS={calls}",
            'say() { :; }; ok() { echo "ok: $*"; }; die() { echo "die: $*" >&2; exit 1; }',
            'log() { echo "$*" >> "$CALLS"; }',
            "connect_desk_admin() { log connect; }; write_adapter_config() { log config; }; backend_permits_dir() { log permits; }",
            "sed() { :; }; wait_enclave() { log wait_enclave; }",
            'systemctl() { log "systemctl $*"; }',
            "docker() { echo healthy; }",
            'bash() { log "setup.sh $2"; if [[ $2 == enclave-backend ]]; then '
            + ('echo "setup: Keycloak did not answer" >&2; return 1; ' if fails else f'echo {answer}; ') + "fi; }",
            shell_function("up.sh", "step_desk_admin"), "step_desk_admin"])
        r = run(script)
        return r, read_lines(calls)

    def test_changed_settings_restart_the_enclave_after_the_adapter_is_healthy(self):
        r, calls = self.step("changed")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(calls, ["connect", "config", "permits", "setup.sh enclave-backend", "setup.sh backend-preflight",
                                 "systemctl restart ekka-desks", "systemctl restart ekka", "wait_enclave"])

    def test_unchanged_settings_restart_nothing_but_the_stack(self):
        r, calls = self.step("kept")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("systemctl restart ekka", calls)
        self.assertIn("systemctl restart ekka-desks", calls)

    def test_planted_settings_not_written_stop_before_the_adapter_starts(self):
        r, calls = self.step(None, fails=True)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("the Enclave's settings for the backend adapter were not written", r.stderr)
        self.assertFalse([c for c in calls if c.startswith("systemctl")], calls)


if __name__ == "__main__":
    unittest.main()
