"""The backend adapter never starts on a missing or fake configuration or CA, whatever the Compose version.

Main went red on CI's Compose v2.38.2 because a test asserted that `docker compose config` PRINTS
`create_host_path: false`. That Compose does not print it, and still honors it (measured on the runner: a missing
bind source is refused, nothing is created). The printed field was never the point. These tests assert the
behavior instead: tools/backend_preflight.py refuses, and `setup.sh compose up` stops before any docker call.
"""
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import backend_preflight as bp  # noqa: E402


def state(profile="desk-admin", config="file", ca="file"):
    """A state folder: up.env with `profile`, and each file as `file`, `dir`, `link` or None (missing)."""
    d = tempfile.mkdtemp()
    os.makedirs(os.path.join(d, "backend"))
    os.makedirs(os.path.join(d, "tls"))
    with open(os.path.join(d, "up.env"), "w") as f:
        f.write("DESKS_NAME=desks.example.com\n" + (f"COMPOSE_PROFILES={profile}\n" if profile else ""))
    with open(os.path.join(d, "desks.env"), "w") as f:
        f.write("DESKS_NAME=desks.example.com\n")
    real = os.path.join(d, "elsewhere")
    with open(real, "w") as f:
        f.write("{}")
    for rel, kind in (("backend/config.json", config), ("tls/ca.crt", ca)):
        path = os.path.join(d, rel)
        if kind == "file":
            with open(path, "w") as f:
                f.write("{}")
        elif kind == "dir":
            os.makedirs(path)
        elif kind == "link":
            os.symlink(real, path)
    return d


class TheCheck(unittest.TestCase):
    def test_real_files_pass(self):
        self.assertEqual(bp.main([state()]), 0)

    def test_planted_a_missing_configuration_is_refused(self):
        self.assertEqual(bp.main([state(config=None)]), 1)
        self.assertTrue(any("config.json does not exist" in r for r in bp.reasons(state(config=None))))

    def test_planted_a_directory_in_place_of_the_configuration_is_refused(self):
        # What a short-syntax bind of a missing file leaves behind.
        d = state(config="dir")
        self.assertEqual(bp.main([d]), 1)
        self.assertTrue(any("is a directory" in r for r in bp.reasons(d)))

    def test_planted_a_link_in_place_of_the_configuration_is_refused(self):
        d = state(config="link")
        self.assertEqual(bp.main([d]), 1)
        self.assertTrue(any("is a link" in r for r in bp.reasons(d)))

    def test_planted_a_missing_ca_is_refused(self):
        self.assertEqual(bp.main([state(ca=None)]), 1)

    def test_nothing_is_checked_while_the_adapter_is_off(self):
        # Before enrolment the profile is off and the files do not exist yet: nothing starts the adapter.
        self.assertEqual(bp.main([state(profile=None, config=None, ca=None)]), 0)

    def test_the_profile_counts_among_others(self):
        self.assertEqual(bp.main([state(profile="other,desk-admin", config=None)]), 1)

    def test_running_the_adapter_by_name_is_checked_even_with_the_profile_off(self):
        self.assertEqual(bp.main([state(profile=None, config=None), "--always"]), 1)


class TheComposeWrapper(unittest.TestCase):
    """`setup.sh compose up` is how up.sh and a person start containers: it must stop before any docker call."""

    def run_wrapper(self, d, args=("up", "-d"), extra_env=None):
        fake = tempfile.mkdtemp()
        log = os.path.join(fake, "docker.log")
        with open(os.path.join(fake, "docker"), "w") as f:
            f.write(f'#!/bin/sh\necho "$@" >> {log}\nexit 0\n')
        os.chmod(os.path.join(fake, "docker"), 0o755)
        env = dict(os.environ, DESKS_STATE=d, PATH=fake + os.pathsep + os.environ["PATH"])
        env.pop("COMPOSE_PROFILES", None)   # only what the case sets
        env.update(extra_env or {})
        r = subprocess.run(["bash", os.path.join(ROOT, "deploy", "setup.sh"), "compose", *args],
                           capture_output=True, text=True, env=env, timeout=60)
        calls = ""
        if os.path.exists(log):
            with open(log) as f:
                calls = f.read()
        return r, calls

    def test_planted_a_missing_configuration_stops_compose_up_before_any_docker_call(self):
        r, calls = self.run_wrapper(state(config=None))
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("the backend adapter was not started", r.stderr)
        self.assertEqual(calls, "", "no docker call at all: nothing was started")

    def test_real_files_let_compose_up_through_to_docker(self):
        _, calls = self.run_wrapper(state())
        self.assertIn("compose", calls, "the adapter's check passed, so the wrapper went on to docker")


    def assert_refused(self, r, calls):
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("the backend adapter was not started", r.stderr)
        self.assertEqual(calls, "", "no docker call at all: nothing was started")

    # agent2's review of #106: naming the adapter starts it whatever up.env says.
    def test_planted_the_adapter_named_with_its_profile_off_is_still_checked(self):
        self.assert_refused(*self.run_wrapper(state(profile=None, config=None), ("up", "-d", "ekka-backend")))

    def test_planted_an_option_before_the_subcommand_does_not_skip_the_checks(self):
        self.assert_refused(*self.run_wrapper(state(profile=None, config=None), ("--profile", "desk-admin", "up", "-d")))

    def test_planted_the_profile_from_the_environment_is_checked(self):
        self.assert_refused(*self.run_wrapper(state(profile=None, config=None), ("up", "-d"),
                                              {"COMPOSE_PROFILES": "desk-admin"}))

    def test_a_command_that_starts_nothing_is_not_checked(self):
        _, calls = self.run_wrapper(state(config=None), ("exec", "ekka-enclave", "true"))
        self.assertIn("exec", calls, "exec starts nothing, so it went straight to docker")

    def test_another_service_with_the_adapter_off_is_not_refused(self):
        _, calls = self.run_wrapper(state(profile=None, config=None), ("up", "-d", "gateway"))
        self.assertIn("compose", calls)


if __name__ == "__main__":
    unittest.main()
