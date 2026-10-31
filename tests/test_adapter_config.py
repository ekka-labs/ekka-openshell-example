"""The backend adapter's configuration file, as up.sh writes it .

Checked against the vendored @ekka-ai/contracts backend/adapter-config.v1.schema.json itself: its required fields,
its closed objects and its patterns are READ from the schema, never restated here. Govern's well-known answer is
faked; everything else is the real tool.
"""
import io
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, os.path.join(ROOT, "tools"))
import adapter_config as ac  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import schema_check  # noqa: E402

with open(os.path.join(ROOT, "vendor", "ekka-contracts", "backend", "adapter-config.v1.schema.json")) as f:
    SCHEMA = json.load(f)

TENANT = "7c1e2a3b-4d5e-4f60-8a71-92b3c4d5e6f7"
NAME = "desks.acme.example"
NODE = "8e1d5a6a-6df2-429a-8c3d-cc75c7682671"
GATE_KEY = "AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8="
SECRET = "EKKA_NODE_SECRET=do-not-leak-this-value"
ENV = f"EKKA_TENANT_ID={TENANT}\nEKKA_NODE_ID={NODE}\nEKKA_GOVERN_URL=https://api.ekka.ai/govern\n"
WELL_KNOWN = {"verify_keys": [
    {"kid": "govern-dispatch-v1", "purpose": "dispatch", "public_key_b64": "BBECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8="},
    {"kid": "govern-gate-token-v1", "purpose": "gate_token", "public_key_b64": GATE_KEY},
]}


def schema_errors(value, schema=SCHEMA, path=""):
    return schema_check.schema_errors(value, schema)

class Opener:
    def __init__(self, body, status=200):
        self.body, self.status, self.urls = body, status, []

    def __call__(self, req, timeout=None):
        self.urls.append(req.full_url)
        me = self

        class R(io.BytesIO):
            status = me.status

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False
        return R(json.dumps(self.body).encode())


class TheFile(unittest.TestCase):
    def test_the_schema_check_is_real(self):
        # A checker that accepts anything would pass every case below.
        with open(os.path.join(ROOT, "vendor", "ekka-contracts", "backend", "samples", "adapter-config.json")) as f:
            sample = json.load(f)
        self.assertEqual(schema_errors(sample), [])
        self.assertNotEqual(schema_errors({**sample, "client_secret": "x"}), [])
        self.assertNotEqual(schema_errors({**sample, "api_gate_instance": TENANT}), [])

    def test_the_machines_values_make_a_file_the_contract_accepts(self):
        config = ac.build(ac.enclave_values(ENV), WELL_KNOWN, NAME)
        self.assertEqual(schema_errors(config), [])
        self.assertEqual(config["organization_id"], TENANT)
        self.assertEqual(config["api_gate_instance"], "enclave8e1d5a6aApi", "the runner's name for this Enclave's API gate")
        self.assertEqual(config["govern_keys"], [{"key_id": "govern-gate-token-v1", "public_key_b64": GATE_KEY}],
                         "only the gate-token key, never the dispatch key")
        self.assertEqual(config["backend_gateway"]["client_id"], "ekka-desk-admin")
        # Exactly what the gateway validates `iss` against: the public name, the realm, no trailing slash.
        self.assertEqual(config["backend_gateway"]["issuer"], "https://desks.acme.example/realms/ekka-desks")

    def test_planted_a_public_name_that_is_not_a_host_is_refused(self):
        for bad in ("", "desks.acme.example/x", "../etc", "desks..example", "https://x"):
            with self.assertRaisesRegex(ac.Refused, "not a host name"):
                ac.build(ac.enclave_values(ENV), WELL_KNOWN, bad)

    def test_planted_an_issuer_with_a_trailing_slash_breaks_the_contract(self):
        config = ac.build(ac.enclave_values(ENV), WELL_KNOWN, NAME)
        config["backend_gateway"]["issuer"] += "/"
        self.assertNotEqual(schema_errors(config), [], "the vendored schema refuses it, so a regression here is caught")

    def test_planted_only_the_three_lines_are_read_and_a_secret_line_never_reaches_the_file(self):
        values = ac.enclave_values(SECRET + "\n" + ENV)
        self.assertEqual(sorted(values), sorted(ac.WANTED))
        self.assertNotIn("do-not-leak", json.dumps(ac.build(values, WELL_KNOWN, NAME)))

    def test_planted_a_missing_or_malformed_id_is_refused(self):
        with self.assertRaisesRegex(ac.Refused, "EKKA_NODE_ID"):
            ac.enclave_values(f"EKKA_TENANT_ID={TENANT}\nEKKA_GOVERN_URL=https://api.ekka.ai/govern\n")
        with self.assertRaisesRegex(ac.Refused, "lowercase UUID"):
            ac.enclave_values(ENV.replace(NODE, NODE.upper()))
        with self.assertRaisesRegex(ac.Refused, "https"):
            ac.enclave_values(ENV.replace("https://api.ekka.ai/govern", "http://api.ekka.ai/govern"))

    def test_planted_no_gate_key_three_of_them_or_a_bad_one_is_refused(self):
        dispatch_only = {"verify_keys": [WELL_KNOWN["verify_keys"][0]]}
        three = {"verify_keys": [dict(WELL_KNOWN["verify_keys"][1], kid=f"k{i}") for i in range(3)]}
        short = {"verify_keys": [dict(WELL_KNOWN["verify_keys"][1], public_key_b64="AAEC")]}
        for wk, says in ((dispatch_only, "published 0"), (three, "published 3"), (short, "32-byte")):
            with self.assertRaisesRegex(ac.Refused, says):
                ac.gate_token_keys(wk)

    def test_two_gate_keys_while_one_rotates(self):
        two = {"verify_keys": [WELL_KNOWN["verify_keys"][1], dict(WELL_KNOWN["verify_keys"][1], kid="govern-gate-token-v2")]}
        self.assertEqual(len(ac.gate_token_keys(two)), 2)


class Writing(unittest.TestCase):
    def test_main_reads_govern_writes_the_file_whole_and_readable_by_the_adapter(self):
        out = os.path.join(tempfile.mkdtemp(), "backend", "config.json")
        opener = Opener(WELL_KNOWN)
        rc = ac.main(["write", out, NAME], stdin=io.StringIO(SECRET + "\n" + ENV), opener=opener)
        self.assertEqual(rc, 0)
        self.assertEqual(opener.urls, ["https://api.ekka.ai/govern/.well-known/ekka-configuration"])
        with open(out) as f:
            written = json.load(f)
        self.assertEqual(schema_errors(written), [])
        self.assertEqual(stat.S_IMODE(os.stat(out).st_mode), 0o644, "no secret in it; the adapter reads it as another user")
        self.assertEqual([n for n in os.listdir(os.path.dirname(out)) if n != "config.json"], [], "no half-written file left")

    def test_planted_a_govern_that_does_not_answer_writes_nothing(self):
        out = os.path.join(tempfile.mkdtemp(), "config.json")

        def broken(req, timeout=None):
            raise OSError("unreachable")
        self.assertEqual(ac.main(["write", out, NAME], stdin=io.StringIO(ENV), opener=broken), 1)
        self.assertFalse(os.path.exists(out))


class UpSh(unittest.TestCase):
    def setUp(self):
        with open(os.path.join(ROOT, "up.sh"), encoding="utf-8") as f:
            self.text = f.read()

    def test_only_the_three_lines_leave_the_enclaves_container(self):
        m = re.search(r"^write_adapter_config\(\) \{.*?^\}\n", self.text, re.S | re.M)
        self.assertIn('grep -E "^EKKA_(TENANT_ID|NODE_ID|GOVERN_URL)=" "$EKKA_HOME/enclave.env"', m.group(0))
        self.assertNotRegex(m.group(0), r"\bcat\b", "never the whole file: it holds the node's secret")

    def test_the_step_waits_for_the_adapter_and_runs_after_the_gateway_setup(self):
        m = re.search(r"^step_desk_admin\(\) \{.*?^\}\n", self.text, re.S | re.M)
        script = f"""
set -euo pipefail
DESKS_STATE=$1
say() {{ echo "say $*"; }}
connect_desk_admin() {{ echo CONNECT; }}
write_adapter_config() {{ echo WRITE; }}
{m.group(0)}
step_desk_admin
"""
        state = tempfile.mkdtemp()
        with open(os.path.join(state, "up.env"), "w") as f:
            f.write("DESKS_NAME=x\n")
        r = subprocess.run(["bash", "-c", script, "_", state], capture_output=True, text=True, timeout=30,
                           stdin=subprocess.DEVNULL)
        self.assertEqual((r.returncode, r.stdout), (0, ""), "no adapter image: nothing happens")
        with open(os.path.join(state, "up.env"), "a") as f:
            f.write("EKKA_BACKEND_IMAGE=ghcr.io/ekka-ai/ekka-backend@sha256:" + "a" * 64 + "\n")
        r = subprocess.run(["bash", "-c", script, "_", state], capture_output=True, text=True, timeout=30,
                           stdin=subprocess.DEVNULL)
        self.assertEqual(r.stdout.split("\n")[1:3], ["CONNECT", "WRITE"], "the credential first, then the file")
        self.assertIn("step_admin; step_desk_admin; step_grant;", self.text)


if __name__ == "__main__":
    unittest.main()
