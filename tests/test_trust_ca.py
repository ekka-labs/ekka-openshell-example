"""The browser's half of a person's setup: the one command that trusts the sign-in page .

`stage_people` writes `trust-ca.sh` into the people bundle. It PRINTS, and never runs, the command for the computer it
runs on, with the CA's SHA-256 fingerprint. The command trusts the CA for websites under the server's name only, for
that person only, with no sudo: the CA's name constraint covers DNS names and IP addresses alone, so a CA trusted for
every purpose would vouch for code signing or mail from anyone holding the server's CA key.

The real stage_people runs here with a real CA, made the way certs.sh makes it. `uname` is stubbed to choose the
system; `security` and `certutil` are stubbed to prove nothing calls them.
"""
import os
import re
import shlex
import subprocess
import tempfile
import unittest

from test_people_bundle import CONTENT_ID, ROOT, shell_function

NAME = "desks.example.com"


def make_ca(folder):
    key, crt = os.path.join(folder, "ca.key"), os.path.join(folder, "ca.crt")
    subprocess.run(["openssl", "ecparam", "-name", "prime256v1", "-genkey", "-noout", "-out", key], check=True)
    subprocess.run(["openssl", "req", "-x509", "-new", "-key", key, "-subj", f"/CN=EKKA desks CA ({NAME})", "-days", "30",
                    "-addext", "basicConstraints=critical,CA:TRUE,pathlen:0",
                    "-addext", f"nameConstraints=critical,permitted;DNS:{NAME}", "-out", crt],
                   check=True, capture_output=True)
    return crt


def fingerprint(crt):
    out = subprocess.run(["openssl", "x509", "-in", crt, "-noout", "-fingerprint", "-sha256"],
                         check=True, capture_output=True, text=True).stdout
    return out.strip().split("=", 1)[1]


class Bundle:
    """A people bundle made by the real stage_people, and a PATH where `uname` says `system`."""

    def __init__(self, system="Darwin"):
        self.state, self.t, self.bin = tempfile.mkdtemp(), tempfile.mkdtemp(), tempfile.mkdtemp()
        with open(os.path.join(self.state, "desks.env"), "w") as f:
            f.write(f"DESKS_NAME={NAME}\n")
        self.ca = make_ca(self.t)
        script = "\n".join([
            "set -euo pipefail", f"DESKS_STATE={self.state}; T={self.t}; DESKS_NAME={NAME}",
            'say() { :; }', 'die() { echo "die: $*" >&2; exit 1; }',
            "stage_enclave_subject() { echo 9a8b7c6d-0000-4000-8000-000000000001; }",
            f'docker() {{ [[ "$1 $2" == "image inspect" ]] && echo {CONTENT_ID}; }}',
            shell_function("deploy/setup.sh", "desk_backend_json"),
            shell_function("deploy/setup.sh", "stage_people"), "stage_people"])
        r = subprocess.run(["/bin/bash", "-c", script], cwd=os.path.join(ROOT, "deploy"), capture_output=True,
                           text=True, timeout=30)
        assert r.returncode == 0, r.stderr
        self.people = os.path.join(self.state, "people")
        self.ran = os.path.join(self.bin, "ran")
        self.stub("uname", f'echo {system}')
        for tool in ("security", "certutil", "sudo", "update-ca-certificates"):
            self.stub(tool, f'echo {tool} >> "{self.ran}"')

    def stub(self, name, body):
        path = os.path.join(self.bin, name)
        with open(path, "w") as f:
            f.write(f"#!/bin/sh\n{body}\n")
        os.chmod(path, 0o755)

    def env(self, home):
        return dict(os.environ, HOME=home, PATH=self.bin + os.pathsep + os.environ["PATH"])

    def trust(self, *args, home=None):
        home = home or tempfile.mkdtemp()
        return subprocess.run(["/bin/bash", os.path.join(self.people, "trust-ca.sh"), *args], env=self.env(home),
                              capture_output=True, text=True, timeout=30)

    def nothing_ran(self):
        return not os.path.exists(self.ran)


def command_line(out, starts):
    line = next(l for l in out.splitlines() if l.strip().startswith(starts) or f": {starts}" in l or f":  {starts}" in l)
    return shlex.split(line[line.index(starts):].split("   (")[0])


class TheBrowsersTrustIsNarrow(unittest.TestCase):
    def test_on_a_mac_websites_under_the_servers_name_for_this_person(self):
        b = Bundle("Darwin")
        home = tempfile.mkdtemp(prefix="a home with spaces ")
        ca = os.path.join(home, "Ekka Home", "desks-ca.pem")
        os.makedirs(os.path.dirname(ca))
        with open(b.ca) as src, open(ca, "w") as dst:
            dst.write(src.read())
        r = b.trust(ca, home=home)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(f"fingerprint is {fingerprint(b.ca)}.", r.stdout)
        argv = command_line(r.stdout, "security ")
        self.assertEqual(argv, ["security", "add-trusted-cert", "-r", "trustRoot", "-p", "ssl", "-s", NAME, "-k",
                                os.path.join(home, "Library/Keychains/login.keychain-db"), ca],
                         "websites only, this name only, this person's keychain, the CA path whole")
        self.assertNotIn("sudo", r.stdout)
        self.assertNotIn("certutil", r.stdout, "one command, for this computer")
        self.assertTrue(b.nothing_ran(), "it prints the command; it never runs it")

    def test_on_linux_chromes_own_store_trusted_for_websites(self):
        b = Bundle("Linux")
        home = tempfile.mkdtemp()
        r = b.trust(home=home)
        self.assertEqual(r.returncode, 0, r.stderr)
        argv = command_line(r.stdout, "certutil ")
        self.assertEqual(argv, ["certutil", "-d", f"sql:{home}/.pki/nssdb", "-A", "-t", "C,,",
                                "-n", f"EKKA desks CA ({NAME})", "-i", os.path.join(b.people, "desks-ca.pem")])
        self.assertNotIn("update-ca-certificates", r.stdout)
        self.assertTrue(b.nothing_ran())

    def test_all_prints_both_for_the_administrator_to_pass_on(self):
        r = Bundle("Linux").trust("--all")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(f"security add-trusted-cert -r trustRoot -p ssl -s {NAME} -k ~/Library/Keychains/"
                      "login.keychain-db desks-ca.pem", r.stdout)
        self.assertIn("certutil -d sql:$HOME/.pki/nssdb -A -t C,, -n", r.stdout)

    def test_a_missing_ca_is_said_and_nothing_is_printed_to_run(self):
        r = Bundle("Darwin").trust("/nowhere/desks-ca.pem")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("no desks CA at /nowhere/desks-ca.pem", r.stderr)
        self.assertNotIn("add-trusted-cert", r.stdout)


class InstallShOffersIt(unittest.TestCase):
    def test_install_ends_with_the_command_for_the_ca_it_saved(self):
        b = Bundle("Darwin")
        home = tempfile.mkdtemp()
        b.stub("ekka", "exit 0")
        with open(os.path.join(b.people, "install.sh")) as f:
            text = f.read()
        r = subprocess.run(["/bin/bash", os.path.join(b.people, "install.sh"), "desks"],
                           env=dict(b.env(home), EKKA_HOME=os.path.join(home, "ekka home"), DESKS_ADDRESS=""),
                           capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('trust-ca.sh', text)
        argv = command_line(r.stdout, "security ")
        self.assertEqual(argv[-1], os.path.join(home, "ekka home", "desks-ca.pem"), "the copy install.sh saved")
        self.assertTrue(b.nothing_ran())


class TheWalkChecksWhatIsPrinted(unittest.TestCase):
    """tools/walk.sh's trust_printed, cut out and run against a real bundle."""

    def run_check(self, b, tls_ca):
        script = "\n".join([f"DESKS_STATE={b.state}", shell_function("tools/walk.sh", "trust_printed"),
                            f'trust_printed {NAME} {b.people}'])
        os.makedirs(os.path.join(b.state, "tls"), exist_ok=True)
        with open(tls_ca) as src, open(os.path.join(b.state, "tls", "ca.crt"), "w") as dst:
            dst.write(src.read())
        return subprocess.run(["/bin/bash", "-c", script], env=b.env(tempfile.mkdtemp()), capture_output=True,
                              text=True, timeout=30)

    def test_the_real_bundle_passes(self):
        b = Bundle("Linux")
        r = self.run_check(b, b.ca)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_planted_another_ca_fails_on_the_fingerprint(self):
        b = Bundle("Linux")
        r = self.run_check(b, make_ca(tempfile.mkdtemp()))
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("not tls/ca.crt's", r.stdout)

    def test_planted_a_broad_command_fails(self):
        for old, new, why in (("-p ssl -s $NAME ", "", "not limited to websites"),
                              ('mac="security', 'mac="sudo security', "broader"),
                              ("-t C,, ", "-t CT,C,C ", "Linux command is not limited")):
            b = Bundle("Linux")
            path = os.path.join(b.people, "trust-ca.sh")
            with open(path) as f:
                text = f.read()
            self.assertEqual(text.count(old), 1, f"the plant did not apply: {old}")
            with open(path, "w") as f:
                f.write(text.replace(old, new))
            r = self.run_check(b, b.ca)
            self.assertNotEqual(r.returncode, 0, f"planted {new or 'no -p ssl -s'} passed")
            self.assertIn(why, r.stdout)


class UpShPrintsIt(unittest.TestCase):
    def test_step_six_and_invite_carry_it(self):
        with open(os.path.join(ROOT, "up.sh"), encoding="utf-8") as f:
            up = f.read()
        step = re.search(r"^step_people\(\) \{.*?^\}\n", up, re.S | re.M).group(0)
        self.assertIn('trust-ca.sh" --all', step)
        self.assertIn("$trust", step)
        invite = up[up.index("  invite)"):up.index("  revoke)")]
        self.assertIn("-fingerprint -sha256", invite, "the administrator reads people the CA's fingerprint")


if __name__ == "__main__":
    unittest.main()
