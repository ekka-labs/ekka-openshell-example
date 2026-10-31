"""An upgrade that changes the edge's config restarts the edge, and nothing else does .

nginx reads its config only when it starts, and `git pull` replaces deploy/edge/nginx.conf with a new file that a
running container never sees (measured with Docker 29.8: the replaced file is visible inside only after a restart).
Without this, a server upgraded to a release that changed a route kept the edge on the old one, and every request
to the new route failed at the edge.
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


def up_function(name):
    m = re.search(rf"^{name}\(\) \{{.*?^\}}\n|^{name}\(\) \{{[^\n]*\}}\n", up_text(), re.S | re.M)
    assert m, f"{name} is not in up.sh"
    return m.group(0)


class EdgeReload(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp()
        self.state = os.path.join(self.home, "state")
        os.makedirs(os.path.join(self.home, "deploy", "edge"))
        os.makedirs(self.state)
        self.conf("events {}\n")
        self.calls = os.path.join(self.home, "calls")
        self.restart(0)

    def tearDown(self):
        shutil.rmtree(self.home)

    def conf(self, text):
        with open(os.path.join(self.home, "deploy", "edge", "nginx.conf"), "w") as f:
            f.write(text)

    def restart(self, rc):   # the compose wrapper: records what it was asked, answers rc
        with open(os.path.join(self.home, "deploy", "setup.sh"), "w") as f:
            f.write(f'echo "$*" >>"{self.calls}"\nexit {rc}\n')

    def run_edge(self):
        script = ('ok() { echo "ok: $*"; }\ndie() { echo "die: $*" >&2; exit 1; }\n'
                  f"DESKS_STATE={self.state}\n{up_function('edge_conf_sha')}{up_function('edge_current')}edge_current\n")
        return subprocess.run(["bash", "-c", "set -euo pipefail\n" + script], cwd=self.home, capture_output=True,
                              text=True, stdin=subprocess.DEVNULL, timeout=30)

    def asked(self):
        if not os.path.exists(self.calls):
            return []
        with open(self.calls) as f:
            return f.read().splitlines()

    def saved(self):
        p = os.path.join(self.state, "edge-conf.sha")
        if not os.path.exists(p):
            return None
        with open(p) as f:
            return f.read().strip()

    def test_a_box_with_no_saved_config_restarts_its_edge_once(self):
        # Every box installed before this change: its edge may be running any older config.
        r = self.run_edge()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.asked(), ["compose restart edge"])
        self.assertIn("the edge restarted on its new config", r.stdout)
        self.assertRegex(self.saved() or "", r"^[0-9a-f]{64}$")
        r = self.run_edge()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.asked(), ["compose restart edge"], "an unchanged config never restarts the edge")

    def test_a_changed_config_restarts_the_edge(self):
        self.run_edge()
        first = self.saved()
        self.conf("events {}\nhttp { server { location /health/ {} } }\n")
        r = self.run_edge()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.asked(), ["compose restart edge"] * 2)
        self.assertNotEqual(self.saved(), first)

    def test_a_failed_restart_stops_the_upgrade_and_is_tried_again_next_time(self):
        self.restart(1)
        r = self.run_edge()
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("die: the edge did not restart on its new config", r.stderr)
        self.assertIsNone(self.saved(), "a config the edge never started with is not recorded")


class UpShUsesIt(unittest.TestCase):
    def test_the_upgrade_path_checks_the_edge_and_a_fresh_install_records_it(self):
        start = up_function("step_start")
        upgrade = start[:start.index("; return; fi")]
        self.assertLess(upgrade.rindex("wait_gateway"), upgrade.rindex("edge_current"),
                        "after the stack is reconciled, before up.sh says it is running")
        fresh = start[start.index("; return; fi"):]
        self.assertIn('edge_conf_sha >"$DESKS_STATE/edge-conf.sha"', fresh)
        self.assertNotIn("edge_current", fresh, "a fresh install just started its edge on this config")


if __name__ == "__main__":
    unittest.main()
