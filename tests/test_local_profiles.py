"""An install's own provider profile wins, and `up.sh profiles` never replaces it .

An install that signs Claude in with its own method keeps that profile in
$DESKS_STATE/providers.local/, out of git. These run up.sh's own functions with the gateway's command
line stubbed: it records what was imported, from which file.
"""
import os
import re
import stat
import subprocess
import tempfile
import unittest

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")


def functions():
    with open(os.path.join(ROOT, "up.sh"), encoding="utf-8") as f:
        text = f.read()
    out = []
    for name in ("profile_source", "profile_names", "sync_profiles"):
        m = re.search(r"^%s\(\) \{.*?^\}\n" % name, text, re.S | re.M)
        assert m, f"{name} is not in up.sh"
        out.append(m.group(0))
    return "\n".join(out)


class LocalProfiles(unittest.TestCase):
    def setUp(self):
        self.state = tempfile.mkdtemp()
        self.local = os.path.join(self.state, "providers.local")
        self.log = os.path.join(self.state, "os.log")

    def own(self, name="claude-code", body="id: claude-code\n# this install's own\n", mode=0o644, folder=0o755):
        os.makedirs(self.local, exist_ok=True)
        os.chmod(self.local, folder)
        path = os.path.join(self.local, f"{name}.yaml")
        with open(path, "w") as f:
            f.write(body)
        os.chmod(path, mode)
        return path

    def run_up(self, call):
        script = f"""
set -euo pipefail
DESKS_STATE={self.state}
ok() {{ echo "ok: $*"; }}
die() {{ echo "die: $*" >&2; exit 1; }}
OS=fake_os
fake_os() {{ echo "$*" >> {self.log}; return 0; }}   # export prints no version: every profile is imported
{functions()}
{call}
"""
        return subprocess.run(["bash", "-c", script], cwd=ROOT, capture_output=True, text=True)

    def imported(self):
        if not os.path.exists(self.log):
            return []
        with open(self.log) as f:
            return [l.split() for l in f if " import " in l]

    def test_without_a_local_profile_the_shipped_one_is_used(self):
        r = self.run_up("profile_source claude-code")
        self.assertEqual(r.stdout.strip(), "providers/claude-code.yaml", r.stderr)

    def test_a_local_profile_wins_and_up_sh_profiles_says_so(self):
        own = self.own()
        r = self.run_up("profile_source claude-code")
        self.assertEqual(r.stdout.strip(), own, r.stderr)
        r = self.run_up("sync_profiles")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(f"ok: profile claude-code imported from {own} (this install's own)", r.stdout)
        self.assertIn("ok: profile github imported from providers/", r.stdout)
        files = {a[a.index("--file") + 1] for a in self.imported()}
        self.assertIn(own, files)
        self.assertNotIn("providers/claude-code.yaml", files, "the shipped profile must never replace the local one")

    def test_a_profile_only_this_install_has_is_imported_too(self):
        own = self.own("my-model", "id: my-model\n")
        r = self.run_up("sync_profiles")
        self.assertIn(f"ok: profile my-model imported from {own} (this install's own)", r.stdout, r.stderr)

    def test_an_unsafe_profile_late_in_the_order_stops_the_sync_before_any_import(self):
        # "zz-late" sorts after every shipped profile: nothing may be imported first.
        self.own("zz-late", "id: zz-late\n", mode=0o666)
        r = self.run_up("sync_profiles")
        self.assertNotEqual(r.returncode, 0, r.stdout)
        self.assertEqual(self.imported(), [], "imported before the refusal")

    def test_planted_an_unsafe_local_profile_is_refused_and_nothing_is_imported(self):
        for case in ("file-0666", "folder-0777", "symlink"):
            with self.subTest(case=case):
                self.setUp()
                if case == "file-0666":
                    self.own(mode=0o666)
                elif case == "folder-0777":
                    self.own(folder=0o777)
                else:
                    target = self.own("elsewhere")
                    os.symlink(target, os.path.join(self.local, "claude-code.yaml"))
                r = self.run_up("sync_profiles")
                self.assertNotEqual(r.returncode, 0, r.stdout)
                self.assertIn("REFUSED", r.stderr + r.stdout)
                self.assertFalse(any("claude-code" in a for a in sum(self.imported(), [])),
                                 "nothing may be imported for an unsafe local profile")


if __name__ == "__main__":
    unittest.main()
