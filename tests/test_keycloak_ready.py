"""Keycloak is healthy only when it answers a realm request, not when its health endpoint first says UP.

Measured on a server: /health/ready said UP at 04:45:27, the bootstrap finished at 04:45:29.7, and in between the
gateway's one discovery request got a 503. The gateway exits on that, --abort-on-container-exit stops the stack, and
an install still running read nothing from Keycloak. The check Compose waits on must close that window.

The real check, as `docker compose config` resolves it, runs here in bash against two small servers standing in for
the management port and the HTTP port. Only the two port numbers are changed.
"""
import http.server
import json
import subprocess
import threading
import unittest

from test_compose_backend import config, have_compose


def serve(status, body):
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            data = body.encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *a):
            pass

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def closed_port():
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), http.server.BaseHTTPRequestHandler)
    port = srv.server_address[1]
    srv.server_close()
    return port


@unittest.skipUnless(have_compose(), "needs docker compose")
class TheSignInServiceIsReady(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        test = config()["services"]["keycloak"]["healthcheck"]["test"]
        assert test[0] == "CMD-SHELL", test
        cls.check = test[1]

    def run_check(self, health, realm):
        servers = []
        ports = []
        for spec in (health, realm):
            if spec is None:
                ports.append(closed_port())
            else:
                s = serve(*spec)
                servers.append(s)
                ports.append(s.server_address[1])
        cmd = self.check.replace("127.0.0.1/9000", f"127.0.0.1/{ports[0]}").replace("127.0.0.1/8080", f"127.0.0.1/{ports[1]}")
        try:
            return subprocess.run(["bash", "-c", cmd], capture_output=True, timeout=10).returncode
        finally:
            for s in servers:
                s.shutdown()
                s.server_close()

    UP = (200, json.dumps({"status": "UP"}))
    DOWN = (503, json.dumps({"status": "DOWN"}))
    REALM = (200, json.dumps({"issuer": "https://desks.example.test/realms/master"}))
    BOOTING = (503, json.dumps({"error": "Keycloak is starting"}))

    def test_healthy_when_up_and_the_realm_answers(self):
        self.assertEqual(self.run_check(self.UP, self.REALM), 0)

    def test_not_healthy_in_the_bootstrap_window(self):
        # The case that stopped a real install: UP on the management port, 503 on every realm.
        self.assertNotEqual(self.run_check(self.UP, self.BOOTING), 0)

    def test_not_healthy_when_health_says_down(self):
        self.assertNotEqual(self.run_check(self.DOWN, self.REALM), 0)

    def test_not_healthy_when_the_http_port_is_closed(self):
        self.assertNotEqual(self.run_check(self.UP, None), 0)


if __name__ == "__main__":
    unittest.main()
