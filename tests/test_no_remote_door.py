"""This setup has no remote door: nothing serves a run's answer, receipt or conversation to a laptop .

Answers, receipts and conversations are read on the server with the `ekka` command, as the administrator. The
Enclave reports no download address, the edge carries only the sign-in paths, and no service in deploy/ speaks for
the Enclave's records to the network. Each test below reads deploy/ as it ships, so a door cannot appear
unnoticed. The gateway's own health listener on 8081 (deploy/gateway/gateway.toml.template) is unrelated.
"""
import os
import re
import unittest

try:   # the python3.9 CI job has no PyYAML; the main job has it and runs the compose tests too
    import yaml
except ImportError:
    yaml = None

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
UUID = "0d71efa2-91d3-418a-9c44-7cb734107dcf"


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


def nginx_conf_without_comments():
    return "\n".join(line.split("#", 1)[0] for line in read("deploy", "edge", "nginx.conf").splitlines())


@unittest.skipIf(yaml is None, "needs PyYAML to read compose.yml as written (the main unittest job has it)")
class TheComposeFile(unittest.TestCase):
    def setUp(self):
        self.c = yaml.safe_load(read("deploy", "compose.yml"))

    def test_no_helper_service_and_no_helper_network(self):
        self.assertNotIn("ekka-artifacts", self.c["services"])
        self.assertNotIn("artifacts", self.c["networks"])
        for name, svc in self.c["services"].items():
            nets = svc.get("networks") or {}
            self.assertNotIn("artifacts", nets, f"{name} still joins the removed helper network")
            cmd = svc.get("command") or []
            self.assertNotEqual(list(cmd)[:2], ["artifacts", "serve"], f"{name} runs the download helper")

    def test_no_service_carries_a_download_address(self):
        for name, svc in self.c["services"].items():
            for key in (svc.get("environment") or {}):
                self.assertFalse(str(key).startswith("EKKA_FETCH"), f"{name}: {key} would make the Enclave report a download address")
        # The Enclave itself is a systemd unit; its drop-in carries no download address either.
        self.assertNotIn("EKKA_FETCH", read("deploy", "host", "ekka.service.d", "desks.conf.template"))


class TheEdge(unittest.TestCase):
    def setUp(self):
        self.conf = nginx_conf_without_comments()

    def test_no_route_reaches_a_run(self):
        self.assertNotIn("/ekka/runs", self.conf)
        self.assertNotIn("ekka-artifacts", self.conf)
        self.assertNotIn("ekka_session", self.conf)
        self.assertNotRegex(self.conf.lower(), r"x[-_]ekka[-_]session")

    def test_the_public_server_still_ends_in_a_404(self):
        public = self.conf[self.conf.index("listen 443 ssl;"):self.conf.index("listen 9443 ssl;")]
        self.assertIn("location /                 { return 404; }", public)
        for word in ("/realms/ekka-desks/", "/resources/", "/.well-known/"):
            self.assertIn(word, public, "the sign-in paths stay")


class TheInstaller(unittest.TestCase):
    def test_up_sh_has_no_helper_step(self):
        up = read("up.sh")
        self.assertNotIn("helper_folders", up)
        self.assertNotIn("ekka-artifacts", up)

    def test_the_docs_teach_the_server_side_commands_not_a_laptop_fetch(self):
        for doc in ("docs/COMMANDS.md", "docs/OPERATIONS.md", "docs/SERVER.md", "docs/ASK-YOUR-CODE.md", "README.md"):
            text = read(*doc.split("/"))
            self.assertNotRegex(text, r"`ekka run fetch <run[^`]*`\s+downloads", doc)
        self.assertIn("ekka job answer <run-id>", read("docs", "COMMANDS.md"))
        self.assertIn("No remote door", read("docs", "OPERATIONS.md"))

    def test_the_walk_asserts_the_edges_404_on_every_run_path(self):
        walk = read("tools", "walk.sh")
        for word in ("answer", "receipt", "session"):
            self.assertRegex(walk, rf'\^status=404 \' edge_get GET "/ekka/runs/\$run_id/{word}"')
        self.assertNotIn("FETCH_TICKET_MISSING", walk)
        self.assertNotIn("8081", walk)
        self.assertNotIn("ekka desk chat", walk)

    def test_the_gateway_health_listener_is_untouched(self):
        self.assertIn('health_bind_address = "0.0.0.0:8081"', read("deploy", "gateway", "gateway.toml.template"))


if __name__ == "__main__":
    unittest.main()
