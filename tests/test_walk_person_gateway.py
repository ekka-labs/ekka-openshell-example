"""walk-person's gateway is registered for NVIDIA's CLI before its write probe runs (walk B, kit 1301afe).

OpenShell v0.1.4's `gateway add --oidc-issuer` signs in at once in a browser and, when that fails, removes the
registration it just made while still exiting 0. The walk's probe then met "Unknown gateway 'walk-person'" and never
reached authorization. The walk now writes the registration itself, in v0.1.4's shape, and checks it by name first.
"""
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent.parent
START = '  install -d -m 0700 "$osh"\n'
END = "  no \"walk-person's own sign-in is refused a desk write"

# NVIDIA's CLI as v0.1.4 behaves for these two commands: `gateway list` shows a gateway only when its metadata.json
# exists; `gateway add` with an OIDC issuer fails its sign-in here, rolls the registration back and exits 0.
FAKE_OPENSHELL = """#!/bin/bash
d=$XDG_CONFIG_HOME/openshell/gateways
case "$1 $2" in
  "gateway list") found=0; for g in "$d"/*/; do [ -f "$g/metadata.json" ] && { basename "$g"; found=1; }; done
                  [ $found = 1 ] || echo "No gateways found." ;;
  "gateway add")  echo "! Registration for 'walk-person' removed. Fix the issue and retry gateway add." >&2 ;;
esac
exit 0
"""

OLD_BLOCK = '''  install -d -m 0700 "$osh"
  ( export HOME=$osh XDG_CONFIG_HOME=$osh/.config
    "$DESKS_STATE/bin/openshell" gateway add "https://$desks_name:8080" --name walk-person \\
      --oidc-issuer "https://$desks_name/realms/ekka-desks" --oidc-client-id ekka-cli --oidc-audience ekka-desks ) >/dev/null 2>&1
  t "walk-person's gateway is registered for NVIDIA's CLI" '(^|[[:space:]])walk-person([[:space:]]|$)' \\
    bash -c "HOME='$osh' XDG_CONFIG_HOME='$osh/.config' '$DESKS_STATE/bin/openshell' gateway list 2>&1"
'''


class WalkPersonGateway(unittest.TestCase):
    def block(self):
        walk = (ROOT / "tools" / "walk.sh").read_text()
        a = walk.index(START, walk.index("WP_LIVE=$WP"))
        return walk[a:walk.index(END, a)]

    def run_block(self, block):
        with tempfile.TemporaryDirectory() as work:
            w = Path(work)
            state, wp, osh = w / "state", w / ".ekka-walk-person", w / "osh"
            (state / "bin").mkdir(parents=True)
            (state / "bin" / "openshell").write_text(FAKE_OPENSHELL)
            (state / "bin" / "openshell").chmod(0o755)
            (wp / "openshell" / "person").mkdir(parents=True)
            (wp / "openshell" / "person" / "ekka-desks.json").write_text(json.dumps({
                "access_token": "planted-access-token", "refresh_token": "planted-refresh-token",
                "issuer": "https://desks.example.test/realms/ekka-desks", "client_id": "ekka-cli"}))
            script = ("set -u\n"
                      't() { local label=$1 want=$2; shift 2; local out; out=$("$@"); '
                      'if [[ $? == 0 ]] && grep -q -E -- "$want" <<<"$out"; then echo "PASS $label"; '
                      'else echo "FAIL $label"; fi; }\n'
                      + block)
            env = dict(os.environ, DESKS_STATE=str(state), WP=str(wp), osh=str(osh), desks_name="desks.example.test")
            r = subprocess.run(["bash", "-c", script], cwd=ROOT, env=env, capture_output=True, text=True, timeout=30)
            folder = osh / ".config" / "openshell" / "gateways" / "walk-person"
            files = {}
            for name in ("metadata.json", "oidc_token.json"):
                f = folder / name
                if f.exists():
                    files[name] = (json.loads(f.read_text()), stat.S_IMODE(f.stat().st_mode))
            return r, files

    def test_the_registration_is_written_in_v014s_shape_and_checked_by_name(self):
        r, files = self.run_block(self.block())
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("PASS walk-person's gateway is registered for NVIDIA's CLI", r.stdout)
        meta, mode = files["metadata.json"]
        self.assertEqual(meta, {
            "name": "walk-person", "gateway_endpoint": "https://desks.example.test:8080", "is_remote": True,
            "gateway_port": 0, "auth_mode": "oidc", "oidc_issuer": "https://desks.example.test/realms/ekka-desks",
            "oidc_client_id": "ekka-cli", "oidc_audience": "ekka-desks"})
        self.assertEqual(mode, 0o600)
        token, mode = files["oidc_token.json"]
        self.assertEqual(sorted(token), ["access_token", "client_id", "issuer"], "the refresh token never leaves")
        self.assertEqual(mode, 0o600)
        self.assertNotIn("planted-access-token", r.stdout + r.stderr, "the token is never printed")

    def test_planted_the_old_gateway_add_leaves_no_gateway_and_the_guard_fails(self):
        r, files = self.run_block(OLD_BLOCK)
        self.assertIn("FAIL walk-person's gateway is registered for NVIDIA's CLI", r.stdout)
        self.assertNotIn("metadata.json", files)


if __name__ == "__main__":
    unittest.main()
