"""The Enclave runs natively under systemd, as the `ekka` user; nothing in deploy/ runs it in a container .

What this setup installs: the signed EKKA release through EKKA's own installer, the account and the folders from
deploy/host, the unit `ekka enclave install-service` generates plus this setup's drop-in, the `ekka` wrapper at
/usr/local/sbin/ekka (sudo -i, then `ekka <command>`), and ekka-desks.service running compose in the foreground, bound
to the Enclave. Each test below reads the files as they ship; the live proofs are the walk on a server.
"""
import os
import re
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
        self.assertRegex(u, r"ExecStart=.*compose up --no-deps --abort-on-container-exit")
        self.assertIn("WorkingDirectory=@@SNAPSHOT@@/deploy", u, "a root-owned snapshot, never a person's checkout")
        self.assertIn("Restart=on-failure", u)

    def test_the_enclave_drop_in_adds_the_group_and_resets_the_writable_paths(self):
        d = read("deploy", "host", "ekka.service.d", "desks.conf.template")
        self.assertIn("SupplementaryGroups=openshell", d)
        self.assertIn("RuntimeDirectory=\n", d, "cleared: tmpfiles own /run/ekka")
        self.assertRegex(d, r"ReadWritePaths=\nReadWritePaths=/var/lib/ekka/\.ekka /run/ekka /run/ekka-backend")
        self.assertIn("Wants=ekka-desks.service", d)
        self.assertNotIn("EKKA_FETCH", d)

    def test_accounts_and_folders_come_from_systemd_files(self):
        su = read("deploy", "host", "sysusers.d", "ekka-desks.conf")
        self.assertRegex(su, re.compile(r"^u ekka 2000:ekka ", re.M), "uid 2000, its own group")
        self.assertRegex(su, re.compile(r"^g openshell -", re.M), "a group with a fresh gid")
        self.assertNotRegex(su, re.compile(r"^m ", re.M), "no membership lines: the unit's SupplementaryGroups= joins the Enclave")
        tf = read("deploy", "host", "tmpfiles.d", "ekka-desks.conf")
        self.assertRegex(tf, re.compile(r"^d /var/lib/ekka/\.ekka\s+0700 ekka ekka", re.M), "the state root, the Enclave's alone")
        self.assertRegex(tf, re.compile(r"^d /run/ekka\s+0750 ekka openshell", re.M), "the gateway traverses, the socket's mode decides")
        self.assertRegex(tf, re.compile(r"^d /run/ekka-backend\s+0700 ekka ekka", re.M))


class TheWrapper(unittest.TestCase):
    PATH = os.path.join(ROOT, "deploy", "host", "sbin", "ekka")

    def test_it_is_executable_and_refuses_without_root(self):
        self.assertTrue(os.stat(self.PATH).st_mode & stat.S_IXUSR)
        r = subprocess.run(["bash", self.PATH, "desk", "list"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 77)
        self.assertIn("sudo -i, then: ekka desk list", r.stderr)

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

    def test_the_main_sequence_installs_accounts_then_the_release_before_init(self):
        text = read("up.sh")
        self.assertIn("step_preflight; step_host_accounts; step_settings; step_images; step_backend_image; step_enclave_install; step_init;", text)
        self.assertNotIn("step_loopback_repair", text)


if __name__ == "__main__":
    unittest.main()
