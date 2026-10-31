"""Offline tests for tools/gateway_preflight.py: the gateway never starts able to hand desk credentials out,
and never with a supervisor that is not the pinned one."""
import copy
import hashlib
import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import gateway_preflight as gp  # noqa: E402

TEMPLATE = os.path.join(ROOT, "deploy", "gateway", "gateway.toml.template")


class PreflightTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        d = self.dir.name
        with open(TEMPLATE) as f:
            self.toml_text = f.read().replace("@@PUBLIC_NAME@@", "desks.test")
        self.toml = os.path.join(d, "gateway.toml")
        self.write_toml(self.toml_text)
        self.supervisor = os.path.join(d, "openshell-supervisor")
        with open(self.supervisor, "wb") as f:
            f.write(b"the pinned supervisor")
        with open(gp.PINS) as f:
            self.pins = json.load(f)["openshell"]
        self.pins["supervisor"]["binary_sha256"] = hashlib.sha256(b"the pinned supervisor").hexdigest()
        # The shape `docker compose config --format json` prints for compose.yml's gateway (measured, v5.5.1).
        self.compose = {"services": {"gateway": {
            "command": [],
            "entrypoint": None,
            "environment": {
                "OPENSHELL_GATEWAY_CONFIG": "/etc/openshell/gateway.toml",
                "OPENSHELL_DOCKER_SUPERVISOR_BIN": "/srv/desks/supervisor/openshell-supervisor",
                "OPENSHELL_TELEMETRY_ENABLED": "false",
            },
            "volumes": [
                {"type": "bind", "source": "/var/run/docker.sock", "target": "/var/run/docker.sock"},
                {"type": "volume", "source": "gateway-state", "target": "/var/lib/openshell/state"},
                {"type": "bind", "source": self.toml, "target": "/etc/openshell/gateway.toml"},
                {"type": "bind", "source": self.supervisor, "target": "/srv/desks/supervisor/openshell-supervisor"},
            ],
        }}}

    def tearDown(self):
        self.dir.cleanup()

    def write_toml(self, text):
        with open(self.toml, "w") as f:
            f.write(text)

    def gateway(self):
        return self.compose["services"]["gateway"]

    def test_this_setups_own_gateway_starts(self):
        self.assertEqual(gp.reasons(self.compose, self.pins), [])

    def test_the_template_says_operator_export_is_off(self):
        self.assertIn("operator_enabled = false", self.toml_text)

    def test_operator_export_in_gateway_toml_is_refused_by_name(self):
        self.write_toml(self.toml_text.replace("operator_enabled = false", "operator_enabled = true"))
        found = gp.reasons(self.compose, self.pins)
        self.assertEqual(len(found), 1, found)
        self.assertIn("[openshell.gateway.mtls_auth] operator_enabled = true", found[0])

    def test_mtls_user_sign_in_is_refused_by_name(self):
        self.write_toml(self.toml_text.replace("enabled = false   ", "enabled = true    ", 1))
        self.assertIn("[openshell.gateway.mtls_auth] enabled = true", " ".join(gp.reasons(self.compose, self.pins)))

    def test_a_client_ca_in_gateway_toml_is_refused_by_name(self):
        self.write_toml(self.toml_text.replace('key_path = "/tls/gateway/tls.key"',
                                               'key_path = "/tls/gateway/tls.key"\nclient_ca_path = "/tls/ca.crt"'))
        found = gp.reasons(self.compose, self.pins)
        self.assertEqual(len(found), 1, found)
        self.assertIn("client_ca_path", found[0])

    def test_each_environment_door_is_refused_by_name_whatever_its_value(self):
        for name in gp.REFUSED_ENV:
            c = copy.deepcopy(self.compose)
            c["services"]["gateway"]["environment"][name] = "false"
            found = gp.reasons(c, self.pins)
            self.assertEqual(len(found), 1, (name, found))
            self.assertIn(name, found[0])

    def test_any_command_line_flag_is_refused(self):
        for flags in (["--tls-client-ca", "/tls/ca.crt"], ["--enable-operator-auth=true"], ["--port", "8080"]):
            self.gateway()["command"] = flags
            self.assertIn("the gateway is given a command", " ".join(gp.reasons(self.compose, self.pins)))

    def test_an_entrypoint_is_refused(self):
        self.gateway()["entrypoint"] = ["/bin/sh", "-c", "openshell-gateway --enable-operator-auth"]
        self.assertIn("entrypoint", " ".join(gp.reasons(self.compose, self.pins)))

    def test_a_config_path_this_check_does_not_read_is_refused(self):
        self.gateway()["environment"]["OPENSHELL_GATEWAY_CONFIG"] = "/tmp/other.toml"
        self.assertIn("OPENSHELL_GATEWAY_CONFIG", " ".join(gp.reasons(self.compose, self.pins)))

    def test_the_mounted_file_is_the_one_read_not_a_fixed_path(self):
        other = os.path.join(self.dir.name, "other.toml")
        with open(other, "w") as f:
            f.write(self.toml_text.replace("operator_enabled = false", "operator_enabled = true"))
        self.gateway()["volumes"][2]["source"] = other
        self.assertIn("operator_enabled = true", " ".join(gp.reasons(self.compose, self.pins)))

    def test_an_unreadable_or_missing_gateway_toml_is_refused(self):
        self.write_toml("[openshell\n")
        self.assertIn("cannot be read as TOML", " ".join(gp.reasons(self.compose, self.pins)))
        del self.gateway()["volumes"][2]
        self.assertIn("mounts no gateway.toml", " ".join(gp.reasons(self.compose, self.pins)))

    def test_a_supervisor_that_is_not_the_pinned_one_is_refused_with_the_fix(self):
        with open(self.supervisor, "wb") as f:
            f.write(b"last release's supervisor")
        found = gp.reasons(self.compose, self.pins)
        self.assertEqual(len(found), 1, found)
        self.assertIn("sudo ./deploy/setup.sh supervisor", found[0])
        os.remove(self.supervisor)
        self.assertIn("unreadable", " ".join(gp.reasons(self.compose, self.pins)))

    def test_no_gateway_service_is_refused(self):
        self.assertEqual(gp.reasons({"services": {}}, self.pins), ["compose has no gateway service"])


class PreflightOnPython39Test(PreflightTest):
    """Every check above again, read the way Python 3.9 and 3.10 read gateway.toml: without tomllib. Amazon Linux
    2023's own python3 is 3.9, and before this the preflight could not even import there."""
    def setUp(self):
        super().setUp()
        self.real_tomllib, gp.tomllib = gp.tomllib, None

    def tearDown(self):
        gp.tomllib = self.real_tomllib
        super().tearDown()


# gateway.toml text that tomllib reads as trusting a client CA or turning operator export on, written outside the
# plain subset. Without tomllib each must be refused, never read as "not set".
OUTSIDE_THE_SUBSET = {
    "a dotted key": '[openshell.gateway]\ntls.client_ca_path = "/ca.pem"\n',
    "an inline table": '[openshell.gateway]\ntls = { client_ca_path = "/ca.pem" }\n',
    "a quoted key": '[openshell.gateway.tls]\n"client_ca_path" = "/ca.pem"\n',
    "a quoted table name": '[openshell.gateway."tls"]\nclient_ca_path = "/ca.pem"\n',
    "a spaced table name": '[ openshell.gateway.tls ]\nclient_ca_path = "/ca.pem"\n',
    "a literal string": "[openshell.gateway.tls]\nclient_ca_path = '/ca.pem'\n",
    "an escaped string": '[openshell.gateway.tls]\nclient_ca_path = "\\u002fca.pem"\n',
    "a multi-line string": '[openshell.gateway.tls]\nclient_ca_path = """\n/ca.pem"""\n',
    "a float": "[openshell.gateway.mtls_auth]\noperator_enabled = 1.0\n",
    "a table given twice": "[openshell.gateway.mtls_auth]\noperator_enabled = true\n[openshell.gateway.mtls_auth]\n",
    "a key given twice": "[openshell.gateway.mtls_auth]\noperator_enabled = false\noperator_enabled = true\n",
    "a nested array": '[openshell.gateway.tls]\nclient_ca_path = [["/ca.pem"]]\n',
}

# Plain-subset text, some of it unusual, that both readers must read the same.
INSIDE_THE_SUBSET = [
    "",
    "# only a comment\n",
    '[a.b]\nx = 1\n[a]\ny = "two"\n',
    "  [a]   # indented, with a comment\r\n  x = +0\r\n  y = -5 # done\r\n",
    'a = []\nb = [ "x" , 1, true, ]\nc = ["#not a comment"]\n',
    "[[t]]\nn = 1\n[t.s]\nk = true\n[[t]]\nn = 2\n[t.s]\nk = false\n[[t.u]]\nv = 3\n",
    'k = "tab\there, and é"\n',
]


class PlainTomlTest(unittest.TestCase):
    def test_this_setups_own_file_reads_the_same_without_tomllib(self):
        with open(TEMPLATE) as f:
            text = f.read()
        plain = gp.plain_toml(text)
        self.assertEqual([len(i["bindings"]) for i in plain["openshell"]["gateway"]["interceptors"]], [14, 9])
        self.assertIs(plain["openshell"]["gateway"]["mtls_auth"]["operator_enabled"], False)
        if gp.tomllib:
            self.assertEqual(plain, gp.tomllib.loads(text))

    @unittest.skipUnless(gp.tomllib, "needs tomllib (Python 3.11+) to compare against; CI runs it")
    def test_what_the_plain_reader_returns_is_what_tomllib_returns(self):
        for text in INSIDE_THE_SUBSET:
            with self.subTest(text=text):
                self.assertEqual(gp.plain_toml(text), gp.tomllib.loads(text))
        for name, text in OUTSIDE_THE_SUBSET.items():
            with self.subTest(name=name):
                try:
                    cfg = gp.tomllib.loads(text)
                except gp.tomllib.TOMLDecodeError:
                    continue   # tomllib refuses it too
                self.assertNotEqual(gp.toml_reasons(cfg), [], f"{name}: tomllib reads it as safe; the case is wrong")

    def test_planted_text_outside_the_subset_is_refused_by_line_never_read_as_unset(self):
        for name, text in OUTSIDE_THE_SUBSET.items():
            with self.subTest(name=name):
                with self.assertRaisesRegex(gp.PlainTOMLError, r"^line \d+: "):
                    gp.plain_toml(text)

    def test_planted_a_table_array_where_a_table_belongs_is_refused_by_name(self):
        # [[openshell.gateway.tls]] is valid TOML, and OpenShell would reject it; this check never reads past it.
        cfg = gp.plain_toml('[[openshell.gateway.tls]]\nclient_ca_path = "/ca.pem"\n')
        self.assertEqual(gp.toml_reasons(cfg), ["gateway.toml's [openshell.gateway.tls] is not a table, so this "
                                                "check cannot read it. Write it as [openshell.gateway.tls]"])
        self.assertIn("[openshell.gateway] is not a table", " ".join(gp.toml_reasons({"openshell": {"gateway": 1}})))


if __name__ == "__main__":
    unittest.main()
