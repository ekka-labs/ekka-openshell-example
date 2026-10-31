"""The Enclave runs natively under systemd, as the `ekka` user; nothing in deploy/ runs it in a container .

What this setup installs: the signed EKKA release through EKKA's own installer, the account and the folders from
deploy/host, the unit `ekka enclave install-service` generates plus this setup's drop-in, the `ekka` wrapper at
/usr/local/sbin/ekka (sudo -i, then `ekka <command>`), and ekka-desks.service running compose in the foreground, bound
to the Enclave. Each test below reads the files as they ship; the live proofs are the walk on a server.
"""
import os
import pwd
import re
import shutil
import stat
import subprocess
import tempfile
import unittest

try:
    import yaml
except ImportError:
    yaml = None

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


def up_function(name, text=None):
    text = text or read("up.sh")
    m = re.search(rf"^{name}\(\) \{{.*?^\}}\n", text, re.S | re.M)
    assert m, f"{name} is not in up.sh"
    return m.group(0)


class NoContainerEnclave(unittest.TestCase):
    def test_no_script_reaches_an_enclave_container(self):
        for rel in ("up.sh", "deploy/setup.sh", "deploy/backup.sh", "tools/walk.sh", "tools/desks_settle.py"):
            text = "\n".join(l.split("#", 1)[0] for l in read(*rel.split("/")).splitlines())
            # "ekka-enclave" is also the Enclave's Keycloak client id, which stays; the container service and its name do not
            self.assertNotRegex(text, r"ekka-desks-ekka-enclave|compose[^\n]* ekka-enclave\b", rel)
            self.assertNotRegex(text, r"compose (exec|run)[^\n]*ekka ", rel)
            self.assertNotRegex(text, r"docker (exec|cp) [^\n]*ekka-desks-ekka", rel)

    @unittest.skipIf(yaml is None, "needs PyYAML")
    def test_compose_has_no_enclave_service_and_the_gateway_owns_its_namespace(self):
        c = yaml.safe_load(read("deploy", "compose.yml"))
        self.assertNotIn("ekka-enclave", c["services"])
        for name in ("ekka-home", "ekka-run", "ekka-backend-sock"):
            self.assertNotIn(name, c.get("volumes") or {}, f"{name} was a volume shared with the container Enclave")
        gw = c["services"]["gateway"]
        self.assertNotIn("network_mode", gw)
        self.assertIn("host.openshell.internal", gw["networks"]["backend"]["aliases"])
        self.assertIn("/run/ekka:/run/ekka", gw["volumes"], "the interceptor socket, from the host folder tmpfiles makes")
        self.assertTrue(any("DESKS_OPENSHELL_GID" in g for g in gw["group_add"]), "the host openshell group, for the socket")
        self.assertTrue(any(v.startswith("/run/ekka-backend:") for v in c["services"]["ekka-backend"]["volumes"]))
        for name, svc in c["services"].items():
            self.assertEqual(str(svc.get("restart")), "no", f"{name}: systemd supervises, compose does not restart")

    def test_the_stack_unit_runs_compose_in_the_foreground_bound_to_the_enclave(self):
        u = read("deploy", "host", "ekka-desks.service.template")
        self.assertIn("BindsTo=ekka.service", u)
        self.assertIn("After=ekka.service docker.service", u)
        self.assertRegex(u, r"ExecStartPre=/usr/local/libexec/ekka-desks/wait-interceptor /run/ekka/openshell-interceptor.sock")
        self.assertRegex(u, r"ExecStartPre=.*compose .*up -d --wait keycloak-db keycloak edge",
                         "the issuer is up before the gateway asks it for OIDC discovery (measured: 503 during Keycloak's boot)")
        self.assertLess(u.index("up -d --wait keycloak-db"), u.index("ExecStart=/usr/bin/bash"))
        self.assertRegex(u, re.compile(r"ExecStart=.*compose .*up --no-deps --abort-on-container-exit --no-log-prefix \$DESKS_STACK_SERVICES$", re.M),
                         "the service list comes from up.env, so the adapter joins only once configured (Codex #4 finding 3)")
        self.assertIn("EnvironmentFile=@@DESKS_STATE@@/up.env", u)
        self.assertNotRegex(u, r"ExecStart=.*ekka-backend", "never hard-coded: a fresh install has no adapter configuration yet")
        self.assertIn("WorkingDirectory=@@SNAPSHOT@@/deploy", u, "a root-owned snapshot, never a person's checkout")
        self.assertIn("Restart=always", u, "a clean container exit is still a stack that stopped serving (Codex #4 finding 8)")
        self.assertIn("PartOf=docker.service", u, "a Docker restart restarts the stack (measured: Requires alone left it down)")
        self.assertIn("SuccessExitStatus=130 143", u, "a deliberate stop is not a failed unit (measured: compose exits 130)")
        self.assertRegex(u, re.compile(r"^ExecStopPost=-/usr/bin/bash setup\.sh compose .*stop -t 30 \$DESKS_STACK_SERVICES$", re.M),
                         "a stop between the detached pre-start and the foreground start leaves no container behind (Codex #4, third pass, finding 6)")

    def test_the_enclave_drop_in_adds_the_group_and_resets_the_writable_paths(self):
        d = read("deploy", "host", "ekka.service.d", "desks.conf.template")
        self.assertIn("Environment=EKKA_HOME=@@ENCLAVE_HOME@@", d, "the BASE home: the generator writes the environment's folder (Codex #4 finding 5)")
        self.assertIn("SupplementaryGroups=openshell", d)
        self.assertIn("RuntimeDirectory=\n", d, "cleared: tmpfiles own /run/ekka")
        self.assertRegex(d, r"ReadWritePaths=\nReadWritePaths=/var/lib/ekka/\.ekka /run/ekka /run/ekka-backend")
        self.assertIn("Wants=ekka-desks.service", d)
        self.assertNotIn("EKKA_FETCH", d)

    def test_accounts_and_folders_come_from_systemd_files(self):
        su = read("deploy", "host", "sysusers.d", "ekka-desks.conf")
        self.assertRegex(su, re.compile(r"^g ekka -\n", re.M), "the group first: `u NAME UID:GROUP` names an EXISTING group (measured on a fresh server)")
        self.assertRegex(su, re.compile(r"^u ekka 2000:ekka ", re.M), "uid 2000, its own group")
        self.assertLess(su.index("g ekka -"), su.index("u ekka 2000:ekka"))
        self.assertRegex(su, re.compile(r"^g openshell -", re.M), "a group with a fresh gid")
        self.assertNotRegex(su, re.compile(r"^m ", re.M), "no membership lines: the unit's SupplementaryGroups= joins the Enclave")
        tf = read("deploy", "host", "tmpfiles.d", "ekka-desks.conf")
        self.assertRegex(tf, re.compile(r"^d /var/lib/ekka/\.ekka\s+0700 ekka ekka", re.M), "the state root, the Enclave's alone")
        self.assertRegex(tf, re.compile(r"^d /run/ekka\s+0750 ekka openshell", re.M), "the gateway traverses, the socket's mode decides")
        self.assertRegex(tf, re.compile(r"^d /run/ekka-backend\s+0700 ekka ekka", re.M))


class TheWrapper(unittest.TestCase):
    PATH = os.path.join(ROOT, "deploy", "host", "sbin", "ekka")

    @unittest.skipIf(os.geteuid() == 0, "this case is about a non-root caller")
    def test_it_is_executable_and_refuses_without_root(self):
        self.assertTrue(os.stat(self.PATH).st_mode & stat.S_IXUSR)
        r = subprocess.run(["bash", self.PATH, "desk", "list"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 77)
        self.assertIn("sudo -i, then: ekka desk list", r.stderr)

    def test_it_refuses_an_environment_selector_flag(self):
        """`ekka --dev whoami` would pick another EKKA environment; this server has one (Codex #4, answer 5)."""
        for flag in ("--dev", "--staging", "--env=staging", "--local", "--prod"):
            r = subprocess.run(["bash", self.PATH, flag, "whoami"], capture_output=True, text=True)
            self.assertEqual(r.returncode, 78, flag)
            self.assertIn("one environment", r.stderr)
        # The runner reads the selector anywhere in the arguments, so the wrapper must too (Codex #4, third pass, finding 5).
        for args in (["whoami", "--env", "staging"], ["whoami", "--env=dev"], ["enclave", "status", "--prod"], ["desk", "list", "--local"]):
            r = subprocess.run(["bash", self.PATH, *args], capture_output=True, text=True)
            self.assertEqual(r.returncode, 78, args)
        r = subprocess.run(["bash", self.PATH, "--version"], capture_output=True, text=True)
        self.assertNotEqual(r.returncode, 78, "--version passes the flag check")

    def test_as_root_it_drops_to_the_enclave_user_with_a_clean_environment(self):
        """`id` and `runuser` are stubbed: the wrapper must call runuser -u ekka with env -i and the configured home."""
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "id"), "w") as f:
                f.write("#!/bin/bash\necho 0\n")
            with open(os.path.join(d, "runuser"), "w") as f:
                f.write('#!/bin/bash\nprintf "%s\\n" "$@" > "$LOG"\n')
            os.chmod(os.path.join(d, "id"), 0o755)
            os.chmod(os.path.join(d, "runuser"), 0o755)
            log = os.path.join(d, "log")
            env = dict(os.environ, PATH=d + ":" + os.environ["PATH"], LOG=log, EKKA_HOME_OVERRIDE="/tmp/evil",
                       EKKA_ENV="staging", TERM="xterm")
            r = subprocess.run(["bash", self.PATH, "desk", "list"], capture_output=True, text=True, env=env)
            self.assertEqual(r.returncode, 0, r.stderr)
            args = open(log).read().splitlines()
            self.assertEqual(args[:4], ["-u", "ekka", "--", "/usr/bin/env"])
            self.assertIn("-i", args, "a clean environment")
            self.assertIn("EKKA_HOME=/var/lib/ekka/.ekka", args)
            self.assertIn("EKKA_ENV=prod", args, "from /etc/ekka/desks.env (absent here), never the caller's shell")
            self.assertNotIn("EKKA_HOME_OVERRIDE=/tmp/evil", " ".join(args))
            self.assertEqual(args[-3:], ["/usr/local/bin/ekka", "desk", "list"])


@unittest.skipUnless(os.geteuid() == 0 and shutil.which("runuser") and shutil.which("useradd"), "root with runuser: the python39 CI container")
class HomeWritesAsTheEnclaveUser(unittest.TestCase):
    """deploy/lib.sh writes into the Enclave's home AS the ekka user. Codex reproduced the previous root version following a
    planted `plans` symlink out of the home and chowning a root-owned folder (review of #4, finding 1)."""

    def test_a_planted_symlink_cannot_carry_roots_authority_out_of_the_home(self):
        if subprocess.run(["id", "-u", "ekka"], capture_output=True).returncode != 0:
            subprocess.run(["useradd", "--system", "--no-create-home", "--shell", "/usr/sbin/nologin", "ekka"], check=True)
        with tempfile.TemporaryDirectory() as d:
            os.chmod(d, 0o755)
            home = os.path.join(d, "home"); os.mkdir(home); shutil.chown(home, "ekka", "ekka"); os.chmod(home, 0o700)
            sentinel = os.path.join(d, "root-only"); os.mkdir(sentinel); os.chmod(sentinel, 0o700)   # root's, 0700
            os.symlink(sentinel, os.path.join(home, "plans"))                                           # planted by "ekka"
            ekka = pwd.getpwnam("ekka")
            os.chown(os.path.join(home, "plans"), ekka.pw_uid, ekka.pw_gid, follow_symlinks=False)
            # lib.sh sets ENCLAVE_HOME itself, so the fixture's home comes AFTER sourcing it (Codex #4, third pass, finding 3).
            script = f". {ROOT}/deploy/lib.sh\nENCLAVE_USER=ekka; ENCLAVE_HOME={home}\necho planted | enclave_home_file plans/p.json 0600"
            r = subprocess.run(["bash", "-c", script], capture_output=True, text=True)
            st = os.stat(sentinel)
            self.assertEqual((st.st_uid, stat.S_IMODE(st.st_mode)), (0, 0o700), "root's folder is untouched")
            self.assertEqual(os.listdir(sentinel), [], "nothing was written through the link")
            self.assertNotEqual(r.returncode, 0, "and the write was refused, by name")
            self.assertIn("is a link", r.stderr)
            r = subprocess.run(["bash", "-c", f". {ROOT}/deploy/lib.sh\nENCLAVE_USER=ekka; ENCLAVE_HOME={home}\necho ok | enclave_home_file notes/n.txt 0600"],
                               capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            st = os.stat(os.path.join(home, "notes", "n.txt"))
            self.assertEqual(st.st_uid, ekka.pw_uid)
            self.assertEqual(stat.S_IMODE(st.st_mode), 0o600)


class EnrollmentAndBackup(unittest.TestCase):
    def test_the_claim_runs_as_a_transient_unit_with_a_stop_deadline(self):
        fn = up_function("step_enroll")
        self.assertIn("systemd-run --unit=ekka-enroll --uid=ekka --gid=ekka", fn, "a managed lifecycle, not a backgrounded shell (Codex #4 finding 4)")
        self.assertNotIn("kill -INT", fn)
        self.assertNotIn("is-active --quiet ekka-enroll", fn, "no yes/no query whose failure reads as 'it exited'")
        self.assertNotIn("stop ekka-enroll.service >/dev/null 2>&1 || true", fn, "no ignored stop")
        gone = up_function("claim_gone")
        self.assertRegex(gone, r"systemctl stop ekka-enroll\.service \\\n\s+\|\| die", "the stop's own exit status is a refusal (Codex #4, third pass, finding 1)")
        self.assertIn("inactive || $state == failed", gone, "an explicit end state, read back")
        self.assertIn("could not report the claim", gone, "a query that fails is an error, never 'it stopped'")
        self.assertNotIn("SIGINT", fn + gone, "systemd's default stop signal is SIGTERM")

    def test_backup_refuses_to_touch_state_after_a_failed_stop(self):
        b = read("deploy", "backup.sh")
        self.assertIn("stop_writers() {", b)
        self.assertRegex(b, r"systemctl stop ekka-desks\.service ekka\.service 2>/dev/null \|\| die")
        self.assertIn("still running after stop; nothing was copied or changed", b)
        self.assertEqual(b.count("    stop_writers   #"), 2, "before the backup copy and before the restore replaces state")
        self.assertRegex(b, r"\[\[ -f /var/lib/ekka/\.ekka/\$envdir/enclave\.env \]\]", "the environment's own file, not a glob (Codex #4 finding 7)")


class HostAccounts(unittest.TestCase):
    def run_step(self, passwd_ekka="", uid2000="", groups_ekka="", openshell="", ubuntu_groups=""):
        script = f"""
set -euo pipefail
say() {{ :; }}; ok() {{ echo "ok: $*"; }}; die() {{ echo "die: $*" >&2; exit 1; }}
id() {{ case "$*" in "-u ekka") [[ -n "{passwd_ekka}" ]] && echo "{passwd_ekka}" || return 1 ;;
               "-nG ekka") echo "{groups_ekka}" ;; "-nG ubuntu") echo "{ubuntu_groups}" ;; *) echo 2000 ;; esac; }}
getent() {{ case "$*" in "passwd 2000") [[ -n "{uid2000}" ]] && echo "{uid2000}:x:2000:2000::/:/bin/false" || return 2 ;;
                   "group openshell") [[ -n "{openshell}" ]] && echo "openshell:x:{openshell}:" || return 2 ;; *) return 2 ;; esac; }}
install() {{ :; }}; systemd-sysusers() {{ :; }}; systemd-tmpfiles() {{ :; }}
stat() {{ case "$*" in *"/run/ekka") echo "ekka:openshell 750" ;; *) echo "ekka:ekka 700" ;; esac; }}
{up_function("step_host_accounts")}
step_host_accounts
"""
        return subprocess.run(["bash", "-c", script], cwd=ROOT, capture_output=True, text=True, timeout=30)

    def test_a_wrong_uid_for_ekka_is_refused(self):
        r = self.run_step(passwd_ekka="1500")
        self.assertEqual(r.returncode, 1)
        self.assertIn("uid 1500, not 2000", r.stderr)

    def test_uid_2000_held_by_another_name_is_refused(self):
        r = self.run_step(uid2000="deploy")
        self.assertIn("uid 2000 belongs to 'deploy'", r.stderr)

    def test_ekka_in_docker_or_sudo_is_refused(self):
        for g in ("docker", "sudo"):
            r = self.run_step(passwd_ekka="2000", groups_ekka=f"ekka {g}")
            self.assertIn(f"in group '{g}'", r.stderr, g)

    def test_an_openshell_group_at_gid_1000_is_refused(self):
        r = self.run_step(openshell="1000")
        self.assertIn("gid 1000", r.stderr)

    def test_ubuntu_in_openshell_is_refused(self):
        r = self.run_step(passwd_ekka="2000", openshell="990", ubuntu_groups="ubuntu openshell")
        self.assertIn("'ubuntu' user is in group 'openshell'", r.stderr)


class TheInstaller(unittest.TestCase):
    def test_the_signed_installer_runs_with_a_controlled_path_and_never_skips_the_signature(self):
        fn = up_function("step_enclave_install")
        self.assertIn("env -i PATH=/usr/local/bin:/usr/bin:/bin", fn)
        self.assertIn("EKKA_INSTALL_DIR=/usr/local/bin", fn)
        self.assertNotIn("--skip-signature-check", fn)
        self.assertIn("install.sh.sha256", fn, "the installer itself is checked against its published checksum")
        self.assertIn("/usr/local/sbin/ekka", fn, "the wrapper is installed beside it")

    def test_the_enclave_unit_comes_from_ekkas_own_generator(self):
        fn = up_function("enclave_units")
        self.assertIn("ekka enclave install-service", fn)
        self.assertIn("ekka.service.d/desks.conf", fn)
        self.assertIn("systemctl enable ekka ekka-desks", fn)
        self.assertIn("/usr/local/lib/systemd/system/ekka-desks.service", fn, "where `systemctl mask` can override it (Codex #4, third pass, finding 2)")
        self.assertNotIn("template /etc/systemd/system/ekka-desks.service", fn)
        self.assertIn("ekka-desks is masked", fn, "a masked stack is a stop somebody meant: refused, never un-masked by an install")

    def test_the_first_start_masks_the_stack_until_the_secret_is_in_the_vault(self):
        fn = up_function("step_start")
        fresh = fn[fn.index("; return; fi"):]
        self.assertLess(fresh.index('touch "$DESKS_STATE/stack.hold"'), fresh.index("enclave-config"), "before anything asks Keycloak")
        self.assertLess(fresh.index("enclave-secret"), fresh.index('rm -f "$DESKS_STATE/stack.hold"'))
        self.assertLess(fresh.index('rm -f "$DESKS_STATE/stack.hold"'), fresh.index("systemctl restart ekka-desks"))
        self.assertIn('rm -f "$DESKS_STATE/stack.hold"', fn[:fn.index("; return; fi")], "a rerun repairs a first install that died while held")
        u = read("deploy", "host", "ekka-desks.service.template")
        self.assertLess(u.index("ExecStartPre=/usr/bin/test ! -e @@DESKS_STATE@@/stack.hold"), u.index("wait-interceptor"), "the hold is the first check")

    def test_the_stack_service_list_in_up_env_is_quoted(self):
        """setup.sh sources up.env: an unquoted list with spaces ran `keycloak` as a command (measured on the walk box)."""
        text = read("up.sh")
        self.assertIn("echo 'DESKS_STACK_SERVICES=\"keycloak-db keycloak edge gateway\"'", text)
        self.assertIn("echo 'DESKS_STACK_SERVICES=\"keycloak-db keycloak edge gateway ekka-backend\"'", text)
        self.assertIn("DESKS_STACK_SERVICES=\"keycloak-db keycloak edge gateway ekka-backend\"/", up_function("step_desk_admin"))
        self.assertNotRegex(text, r"DESKS_STACK_SERVICES=keycloak", "never the unquoted form")

    def test_the_main_sequence_installs_accounts_then_the_release_before_init(self):
        text = read("up.sh")
        self.assertIn("step_preflight; step_host_accounts; step_settings; step_images; step_backend_image; step_enclave_install; step_init;", text)
        self.assertNotIn("step_loopback_repair", text)


if __name__ == "__main__":
    unittest.main()
