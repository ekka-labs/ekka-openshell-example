"""A second run under another name is refused before it changes anything (the cg-desks.internal → desks.acme.com box).

The first run binds the public name: deploy/certs.sh makes a CA name-constrained to it, and Keycloak's issuer and
gateway.toml carry it. A rerun skipped init (its done marker) and pointed up.env, /etc/hosts, the gateway's registration
and the adapter at the new name, so the server half-answered to each. These cases run up.sh's own refuse_if_renamed in
bash against a scratch state folder, and one runs the real CA commands to show why a rename cannot be patched over.
"""
import os
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")


def up_text():
    with open(os.path.join(ROOT, "up.sh"), encoding="utf-8") as f:
        return f.read()


def guard():
    m = re.search(r"^refuse_if_renamed\(\) \{.*?^\}\n", up_text(), re.S | re.M)
    assert m, "refuse_if_renamed is not in up.sh"
    return m.group(0)


def run(name, desks_env=None, ca=False):
    d = tempfile.mkdtemp()
    if desks_env is not None:
        with open(os.path.join(d, "desks.env"), "w") as f:
            f.write(desks_env)
    if ca:
        os.makedirs(os.path.join(d, "tls"))
        open(os.path.join(d, "tls", "ca.crt"), "w").close()
    script = f"""
set -euo pipefail
DESKS_STATE={d}; NAME={name}
die() {{ echo "die: $*" >&2; exit 1; }}
{guard()}
refuse_if_renamed
echo passed
"""
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True)


class ARerunUnderAnotherName(unittest.TestCase):
    def test_a_new_name_after_init_is_refused_and_names_both_ways_out(self):
        r = run("desks.acme.com", "DESKS_NAME=cg-desks.internal\nDESKS_DOCKER_GID=999\n", ca=True)
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertNotIn("passed", r.stdout)
        self.assertIn("set up as cg-desks.internal, and you asked for desks.acme.com; nothing was changed", r.stderr)
        self.assertIn("./up.sh server cg-desks.internal", r.stderr)
        self.assertIn("Remove the server's desks", r.stderr)

    def test_the_same_name_again_goes_on(self):
        r = run("desks.acme.com", "DESKS_NAME=desks.acme.com\n", ca=True)
        self.assertEqual((r.returncode, r.stdout.strip()), (0, "passed"), r.stderr)

    def test_a_first_run_goes_on(self):
        r = run("desks.acme.com")
        self.assertEqual((r.returncode, r.stdout.strip()), (0, "passed"), r.stderr)

    def test_a_name_never_bound_by_a_ca_may_still_change(self):
        # init wrote desks.env and stopped before the CA: it runs again under the new name and binds that one.
        r = run("desks.acme.com", "DESKS_NAME=cg-desks.internal\n", ca=False)
        self.assertEqual((r.returncode, r.stdout.strip()), (0, "passed"), r.stderr)

    def test_the_guard_runs_in_preflight_before_anything_is_written(self):
        text = up_text()
        pre = re.search(r"^step_preflight\(\) \{.*?^\}\n", text, re.S | re.M).group(0)
        self.assertIn("refuse_if_renamed", pre, "step_preflight no longer calls refuse_if_renamed")
        steps = re.search(r"^step_preflight; .*$", text, re.M).group(0).split("; ")
        self.assertEqual(steps[0], "step_preflight", "a step now runs before step_preflight, and could write first")


@unittest.skipUnless(shutil.which("openssl"), "no openssl")  # Amazon Linux 2023 has no `which`
class WhyARenameCannotBePatchedOver(unittest.TestCase):
    def test_the_first_names_ca_cannot_vouch_for_a_second_name(self):
        d = tempfile.mkdtemp()
        script = r"""
set -euo pipefail
cd "$1"
openssl ecparam -name prime256v1 -genkey -noout -out ca.key
openssl req -x509 -new -key ca.key -subj "/CN=EKKA desks CA (cg-desks.internal)" -days 2 -sha256 \
  -addext "basicConstraints=critical,CA:TRUE,pathlen:0" -addext "keyUsage=critical,keyCertSign,cRLSign" \
  -addext "nameConstraints=critical,permitted;DNS:cg-desks.internal,permitted;DNS:localhost,permitted;IP:127.0.0.1/255.255.255.255" \
  -out ca.crt 2>/dev/null
printf 'basicConstraints=critical,CA:FALSE\nsubjectAltName=DNS:desks.acme.com,DNS:localhost\n' > ext
openssl ecparam -name prime256v1 -genkey -noout -out s.key
openssl req -new -key s.key -subj "/CN=desks.acme.com" -out s.csr 2>/dev/null
openssl x509 -req -in s.csr -CA ca.crt -CAkey ca.key -CAcreateserial -days 1 -out s.crt -extfile ext 2>/dev/null
openssl verify -CAfile ca.crt s.crt
"""
        r = subprocess.run(["bash", "-c", script, "x", d], capture_output=True, text=True)
        self.assertNotEqual(r.returncode, 0, "the CA vouched for a name outside its constraint")
        self.assertIn("permitted subtree violation", r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
