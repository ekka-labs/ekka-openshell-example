"""The backend adapter's service, as Docker Compose resolves it .

The adapter takes desk changes from the Enclave's API gate over a Unix socket, so its container is the narrowest
in this file: the Enclave's user, a read-only root, every capability dropped, no ports, no Docker socket, and one
internal network shared only with the gateway and the sign-in front. It is OFF until up.sh turns on the desk-admin
profile, so an install that has not asked for it runs exactly what it ran before.

Read from `docker compose config`, the configuration Compose will actually run, not from the YAML text.
"""
import json
import os
import shutil
import subprocess
import tempfile
import unittest

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
DEPLOY = os.path.join(ROOT, "deploy")
IMAGE = "ghcr.io/ekka-labs/ekka-backend@sha256:" + "a" * 64
BEFORE = ["edge", "ekka-enclave", "gateway", "gateway-init", "keycloak", "keycloak-db"]  # backend-init is desk-admin too


def have_compose():
    if not shutil.which("docker"):
        return False
    return subprocess.run(["docker", "compose", "version"], capture_output=True).returncode == 0


def waits_for_backend_init(cfg):
    """The adapter starts only after backend-init made its state folder the Enclave's user's, 0700."""
    dep = cfg["services"]["ekka-backend"].get("depends_on", {}).get("backend-init", {})
    return dep.get("condition") == "service_completed_successfully"


def config(profile=None, image=None, compose_text=None):
    """The resolved configuration, or (returncode, stderr) when Compose refuses it. `compose_text` replaces
    compose.yml, in a copy of deploy/ (a plant)."""
    deploy = DEPLOY
    if compose_text is not None:
        deploy = tempfile.mkdtemp()
        for name in os.listdir(DEPLOY):
            if name != "compose.yml":
                os.symlink(os.path.join(DEPLOY, name), os.path.join(deploy, name))
        with open(os.path.join(deploy, "compose.yml"), "w") as f:
            f.write(compose_text)
    state = tempfile.mkdtemp()
    os.makedirs(os.path.join(state, "secrets"))
    for f in ("keycloak-db.env", "keycloak.env"):
        open(os.path.join(state, "secrets", f), "w").close()
    env = dict(os.environ, DESKS_STATE=state, DESKS_NAME="desks.example.test", DESKS_DOCKER_GID="999")
    env.pop("EKKA_BACKEND_IMAGE", None)
    if image:
        env["EKKA_BACKEND_IMAGE"] = image
    args = ["docker", "compose", "-f", "compose.yml"] + (["--profile", profile] if profile else [])
    r = subprocess.run(args + ["config", "--format", "json"], cwd=deploy, capture_output=True, text=True, env=env)
    return json.loads(r.stdout) if r.returncode == 0 else (r.returncode, r.stderr)


@unittest.skipUnless(have_compose(), "needs docker compose")
class TheAdapterService(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.on = config("desk-admin", IMAGE)
        cls.svc = cls.on["services"]["ekka-backend"]

    def test_is_off_unless_the_desk_admin_profile_is_on(self):
        self.assertEqual(sorted(config()["services"]), BEFORE, "an install that did not ask runs what it ran before")

    def test_refuses_to_start_without_its_pinned_image(self):
        refused = config("desk-admin")
        self.assertIsInstance(refused, tuple)
        self.assertIn("neither an image nor a build context", refused[1])

    def test_runs_as_the_enclaves_user_with_nothing_extra(self):
        s = self.svc
        self.assertEqual(s["user"], "2000:1000")
        self.assertIs(s["read_only"], True)
        self.assertEqual(s["cap_drop"], ["ALL"])
        self.assertEqual(s["security_opt"], ["no-new-privileges:true"])
        self.assertEqual((s["pids_limit"], int(s["mem_limit"]), s["cpus"]), (256, 512 * 1024 * 1024, 1))
        self.assertEqual(s["tmpfs"], ["/tmp"])
        self.assertEqual(s["image"], IMAGE)

    def test_publishes_no_port_and_holds_no_docker_socket(self):
        self.assertFalse(self.svc.get("ports"))
        self.assertFalse(self.svc.get("privileged"))
        volumes = {v["target"]: v for v in self.svc["volumes"] if v["type"] == "volume"}
        files = {v["target"]: v for v in self.svc["volumes"] if v["type"] == "bind"}
        self.assertEqual(sorted((v["source"], t) for t, v in volumes.items()),
                         [("ekka-backend-sock", "/run/ekka-backend"), ("ekka-backend-state", "/var/lib/ekka-backend")],
                         "the socket folder and the recovery store, and no other volume: no ekka-home")
        # And two FILES, read-only: its configuration and the gateway's CA. Never a folder, never docker.sock.
        self.assertEqual(sorted(files), ["/etc/ekka-backend/ca.pem", "/etc/ekka-backend/config.json"])
        self.assertTrue(all(v.get("read_only") is True for v in files.values()))
        self.assertEqual(sorted(os.path.basename(v["source"]) for v in files.values()), ["ca.crt", "config.json"])
        self.assertEqual(len(self.svc["volumes"]), 4)

    def test_reaches_only_the_gateway_and_the_sign_in_front(self):
        self.assertEqual(list(self.svc["networks"]), ["backend"])
        self.assertIs(self.on["networks"]["backend"].get("internal"), True, "no route out")
        on_backend = sorted(n for n, s in self.on["services"].items() if "backend" in (s.get("networks") or {}))
        self.assertEqual(on_backend, ["edge", "ekka-backend", "ekka-enclave"])

    def test_reaches_the_gateway_by_the_name_its_certificate_and_its_config_carry(self):
        aliases = (self.on["services"]["ekka-enclave"]["networks"]["backend"] or {}).get("aliases") or []
        # The adapter's configuration (tools/adapter_config.py, ADK-E3) names https://host.openshell.internal:8080.
        self.assertIn("host.openshell.internal", aliases)

    def test_reads_its_one_configuration_file_and_the_ca_read_only(self):
        # `serve` first: the adapter serves only with it, and anything else prints its usage and exits (ekka-backend
        # main.rs `command`). This test used to pin the command WITHOUT it, so the adapter never started.
        self.assertEqual(self.svc["command"], ["serve", "--config", "/etc/ekka-backend/config.json"])
        mounts = {v["target"]: v for v in self.svc["volumes"]}
        for target in ("/etc/ekka-backend/config.json", "/etc/ekka-backend/ca.pem"):
            self.assertIn(target, mounts)
            self.assertIs(mounts[target].get("read_only"), True, target)

    def test_shares_its_socket_folder_with_the_enclave_only(self):
        holders = sorted(n for n, s in self.on["services"].items()
                         if any(v.get("source") == "ekka-backend-sock" for v in s.get("volumes") or []))
        self.assertEqual(holders, ["ekka-backend", "ekka-enclave"])
        self.assertEqual(self.svc["healthcheck"]["test"], ["CMD", "/usr/local/bin/ekka-backend", "health"],
                         "the image is distroless: the binary checks itself")

    def test_the_configuration_and_ca_are_read_only_long_syntax_binds(self):
        # Long syntax, so Compose honors compose.yml's `create_host_path: false`: a missing source is refused
        # ("bind source path does not exist"), never made a directory. Measured on Compose v2.38.2 (CI) and v5.5.1.
        # NOT asserted from the rendered config: v2.38.2 honors the setting but does not PRINT it, which turned
        # main red. The guard that does not depend on any Compose is tools/backend_preflight.py, and
        # test_backend_preflight.py asserts its behavior.
        for target in ("/etc/ekka-backend/config.json", "/etc/ekka-backend/ca.pem"):
            v = next(v for v in self.svc["volumes"] if v["target"] == target)
            self.assertEqual(v["type"], "bind", target)
            self.assertIs(v.get("read_only"), True, target)

    def test_its_state_folder_is_made_the_enclaves_user_0700_before_it_starts(self):
        # regression review: distroless owns no /var/lib/ekka-backend, so a fresh volume is root's 0755, and
        # the store refuses it (STORE_UNSAFE): serve would exit and restart forever. backend-init fixes the owner.
        init = self.on["services"]["backend-init"]
        self.assertEqual(self.svc["depends_on"]["backend-init"]["condition"], "service_completed_successfully")
        self.assertEqual(init["command"], ["sh", "-c", "chown 2000:1000 /state && chmod 0700 /state"])
        self.assertEqual(self.svc["user"], "2000:1000", "the same user the folder is given to")
        state = next(v for v in self.svc["volumes"] if v["target"] == "/var/lib/ekka-backend")
        self.assertEqual([v["source"] for v in init["volumes"] if v["target"] == "/state"], [state["source"]])
        self.assertEqual(init["network_mode"], "none")
        self.assertEqual(init["restart"], "no")
        self.assertRegex(init["image"], r"^alpine:[0-9.]+@sha256:[0-9a-f]{64}$", "a pinned digest, never a tag")

    def test_planted_without_backend_init_the_adapter_would_start_on_a_root_owned_folder(self):
        with open(os.path.join(DEPLOY, "compose.yml")) as f:
            text = f.read()
        planted = text.replace("      backend-init: {condition: service_completed_successfully}", "", 1)
        self.assertNotEqual(planted, text, "the plant changed nothing")
        self.assertTrue(waits_for_backend_init(self.on))
        self.assertFalse(waits_for_backend_init(config("desk-admin", IMAGE, compose_text=planted)),
                         "without the wait the adapter can start before its folder is the Enclave's user's")


class TheEnclaveImage(unittest.TestCase):
    def test_owns_the_socket_folder_so_the_new_volume_is_the_enclaves(self):
        # A new named volume copies the folder's owner and mode from the first image that mounts it, and the
        # Enclave starts first. Without this the folder is root's and the adapter cannot create its socket.
        with open(os.path.join(DEPLOY, "enclave", "Dockerfile"), encoding="utf-8") as f:
            self.assertIn("install -d -o 2000 -g 1000 -m 0700 /run/ekka-backend", f.read())


if __name__ == "__main__":
    unittest.main()
