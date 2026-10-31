"""deploy/harden.sh on a stock Ubuntu 24.04 server : `sshd -t` needs /run/sshd, which can be missing.

The function is taken from harden.sh itself, so a test never checks a copy. One test stubs `install` and runs
anywhere; the other runs it as real root in a throwaway ubuntu:24.04 container (cached image only, never a pull).
"""
import os
import re
import subprocess
import unittest

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")


def harden():
    with open(os.path.join(ROOT, "deploy", "harden.sh"), encoding="utf-8") as f:
        return f.read()


def privsep_fn(text=None):
    return re.search(r"^privsep_dir\(\) \{.*?\}\n", text or harden(), re.M).group(0)


def have_image(image):
    try:
        return subprocess.run(["docker", "image", "inspect", image], capture_output=True, timeout=20).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


class RunSshdBeforeTheCheck(unittest.TestCase):
    def test_created_before_sshd_checks_its_settings(self):
        text = harden()
        self.assertLess(text.index("\nprivsep_dir /run/sshd\n"), text.index("\nsshd -t "), "made before sshd -t reads it")

    def stubbed(self, fn, present):
        """Run the function on a temp path with `install` replaced by a recorder; what it would have run."""
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            target = os.path.join(d, "sshd")
            if present:
                os.mkdir(target)
            script = fn + 'install() { echo "install $*"; }\nprivsep_dir "$1"\n'
            out = subprocess.run(["bash", "-c", script, "_", target], capture_output=True, text=True, timeout=30)
            return out.stdout.replace(target, "<dir>").strip(), out.returncode

    def test_missing_is_made_root_0755_and_present_is_left_alone(self):
        self.assertEqual(self.stubbed(privsep_fn(), present=False), ("install -d -o root -g root -m 0755 <dir>", 0))
        self.assertEqual(self.stubbed(privsep_fn(), present=True), ("", 0))

    def test_planted_reds_are_caught(self):
        always = "privsep_dir() { install -d -o root -g root -m 0755 \"$1\"; }\n"
        self.assertNotEqual(self.stubbed(always, present=True), ("", 0), "PLANTED: an existing folder is touched")
        never = "privsep_dir() { [[ -d $1 ]] || true; }\n"
        self.assertNotEqual(self.stubbed(never, present=False)[0], "install -d -o root -g root -m 0755 <dir>",
                            "PLANTED: a missing folder is not made")
        moved = harden().replace("\nprivsep_dir /run/sshd\n", "\n").replace(
            'systemctl reload ssh\n', 'systemctl reload ssh\nprivsep_dir /run/sshd\n', 1)
        with self.assertRaises(AssertionError):
            self.assertLess(moved.index("\nprivsep_dir /run/sshd\n"), moved.index("\nsshd -t "))


# ── #148: a Docker restart strands every desk ────────────────────────────────────────────────────────────────────
# The functions are taken from harden.sh itself and run against stub docker, systemctl, dpkg-query, apt-mark and
# apt-get on PATH. Each stub records its arguments, so a test sees exactly what would have run on a server.

STUBS = {
    "docker": '''[[ ${DOCKER_FAILS:-} == 1 ]] && exit 1
[[ $1 == ps ]] && printf '%s' "${DESK_IDS:-}"
exit 0''',
    "systemctl": '''[[ $1 == is-active ]] && { [[ ${DOCKER_ACTIVE:-} == 1 ]]; exit; }
exit 0''',
    "dpkg-query": '''for p in ${INSTALLED:-}; do [[ ${!#} == "$p" ]] && { printf 'install ok installed'; exit 0; }; done
exit 1''',
    "apt-mark": '''[[ $1 == showhold ]] && { for p in ${HELD:-}; do echo "$p"; done; [[ ${FLOOD:-} == 1 ]] && exec seq 1 200000; }  # one process, like apt-mark: SIGPIPE ends it
exit 0''',
    "apt-get": '''exit ${APT_RC:-0}''',
    # fuser exits 0 while a lock is held: held for LOCK_BUSY calls, then free (LOCK_BUSY=forever never frees)
    "fuser": '''n=$(grep -c '^fuser ' "$CALLS"); [[ ${LOCK_BUSY:-0} == forever ]] && exit 0; (( n <= ${LOCK_BUSY:-0} ))''',
    "sleep": '''exit 0''',
}


def harden_functions(text=None):
    text = text or harden()
    parts = [re.search(r"^DOCKER_PACKAGES=.*\n", text, re.M).group(0),
             re.search(r"^APT_LOCK_TIMEOUT=.*\n", text, re.M).group(0),
             re.search(r"^apt_get\(\) +\{[^\n]*\}\n", text, re.M).group(0)]
    for name in ("ok", "die"):
        parts.append(re.search(rf"^{name}\(\) +\{{[^\n]*\}}\n", text, re.M).group(0))
    for name in ("wait_for_apt", "desk_containers", "apply_docker_settings", "upgrade_holding_docker"):
        parts.append(re.search(rf"^{name}\(\) \{{.*?^\}}\n", text, re.S | re.M).group(0))
    return "".join(parts)


def run_harden(call, text=None, **env):
    """Run one harden.sh function under set -euo pipefail with the stubs; (exit, stdout+stderr, calls, folder)."""
    import tempfile
    folder = tempfile.mkdtemp()
    bin_dir = os.path.join(folder, "bin")
    os.mkdir(bin_dir)
    log = os.path.join(folder, "calls")
    for name, body in STUBS.items():
        path = os.path.join(bin_dir, name)
        with open(path, "w") as f:
            f.write(f'#!/usr/bin/env bash\necho "{name} $*" >> "$CALLS"\n{body}\n')
        os.chmod(path, 0o755)
    script = "set -euo pipefail\n" + harden_functions(text) + call + "\n"
    environment = dict(os.environ, PATH=bin_dir + os.pathsep + os.environ["PATH"], CALLS=log, FOLDER=folder)
    environment.pop("ALLOW_DOCKER_RESTART", None)
    environment.update(env)
    out = subprocess.run(["bash", "-c", script], capture_output=True, text=True, env=environment, timeout=30)
    calls = open(log).read().splitlines() if os.path.exists(log) else []
    return out.returncode, out.stdout + out.stderr, calls, folder


WANT = '{ "live-restore": true,\n  "log-driver": "json-file", "log-opts": { "max-size": "20m", "max-file": "3" } }'


class DockerIsRestartedOnlyOnAChangeAndNeverUnderDesks(unittest.TestCase):
    def settings(self, existing, text=None, **env):
        """apply_docker_settings on a daemon.json holding `existing` (None: absent)."""
        prepare = "" if existing is None else f"printf '%s\\n' {shlex_quote(existing)} > \"$FOLDER/daemon.json\"\n"
        rc, out, calls, folder = run_harden(
            prepare + f'apply_docker_settings "$FOLDER/daemon.json" {shlex_quote(WANT)}', text, **env)
        path = os.path.join(folder, "daemon.json")
        content = open(path).read() if os.path.exists(path) else None
        return rc, out, calls, content

    def restarted(self, calls):
        return "systemctl restart docker" in calls

    def test_a_rerun_with_unchanged_settings_does_not_restart(self):
        rc, out, calls, content = self.settings(WANT, DOCKER_ACTIVE="1", DESK_IDS="a1\nb2\n")
        self.assertEqual(rc, 0, out)
        self.assertFalse(self.restarted(calls), calls)
        self.assertIn("Docker settings unchanged; Docker was not restarted", out)
        self.assertEqual(content, WANT + "\n")

    def test_changed_settings_with_a_desk_refuse_and_change_nothing(self):
        rc, out, calls, content = self.settings('{ "live-restore": false }', DOCKER_ACTIVE="1", DESK_IDS="a1\nb2\n")
        self.assertEqual(rc, 1, out)
        self.assertFalse(self.restarted(calls), calls)
        self.assertEqual(content, '{ "live-restore": false }\n', "nothing was changed")
        for words in ("2 desk container(s) exist", "ControlSupervisorExited", "the desk installation checks",
                      "ALLOW_DOCKER_RESTART=yes"):
            self.assertIn(words, out)
        self.assertIn("docker ps -a -q --filter label=openshell.ai/managed-by=openshell", calls)

    def test_the_flag_allows_the_restart(self):
        rc, out, calls, content = self.settings('{ "live-restore": false }', DOCKER_ACTIVE="1", DESK_IDS="a1\n",
                                                ALLOW_DOCKER_RESTART="yes")
        self.assertEqual(rc, 0, out)
        self.assertTrue(self.restarted(calls), calls)
        self.assertEqual(content, WANT + "\n")

    def test_changed_settings_with_no_desk_restart(self):
        rc, out, calls, content = self.settings('{ "live-restore": false }', DOCKER_ACTIVE="1", DESK_IDS="")
        self.assertEqual(rc, 0, out)
        self.assertTrue(self.restarted(calls), calls)
        self.assertEqual(content, WANT + "\n")

    def test_a_docker_that_cannot_list_its_containers_refuses(self):
        rc, out, calls, content = self.settings('{ "live-restore": false }', DOCKER_ACTIVE="1", DOCKER_FAILS="1")
        self.assertEqual(rc, 1, out)
        self.assertFalse(self.restarted(calls), calls)
        self.assertIn("could not list its containers", out)
        self.assertEqual(content, '{ "live-restore": false }\n')

    def test_a_first_install_writes_and_starts_docker(self):
        rc, out, calls, content = self.settings(None, DOCKER_ACTIVE="0")
        self.assertEqual(rc, 0, out)
        self.assertTrue(self.restarted(calls), calls)
        self.assertEqual(content, WANT + "\n")

    def test_harden_restarts_docker_nowhere_else(self):
        text = harden()
        body = text.replace(re.search(r"^apply_docker_settings\(\) \{.*?^\}\n", text, re.S | re.M).group(0), "")
        self.assertNotIn("systemctl restart docker", body)
        self.assertNotRegex(body, r"cat > /etc/docker/daemon\.json")
        self.assertIn("apply_docker_settings /etc/docker/daemon.json", text)

    def test_planted_reds_are_caught(self):
        text = harden()
        always = text.replace('if [[ -f $file && $(cat "$file") == "$want" ]]; then', "if false; then")
        self.assertNotEqual(always, text)
        self.assertTrue(self.restarted(self.settings(WANT, always, DOCKER_ACTIVE="1")[2]),
                        "PLANTED: an unchanged rerun restarts Docker")
        blind = text.replace("if (( n > 0 )) && [[ ${ALLOW_DOCKER_RESTART:-} != yes ]]; then", "if false; then")
        self.assertNotEqual(blind, text)
        self.assertTrue(self.restarted(self.settings('{}', blind, DOCKER_ACTIVE="1", DESK_IDS="a1\n")[2]),
                        "PLANTED: desks are ignored")
        ignored = text.replace("[[ ${ALLOW_DOCKER_RESTART:-} != yes ]]", "true")
        self.assertNotEqual(ignored, text)
        self.assertFalse(self.restarted(self.settings('{}', ignored, DOCKER_ACTIVE="1", DESK_IDS="a1\n",
                                                      ALLOW_DOCKER_RESTART="yes")[2]),
                         "PLANTED: the flag is ignored")


class UpdatesNeverRestartDocker(unittest.TestCase):
    def test_installed_docker_packages_are_held_around_the_upgrade(self):
        rc, out, calls, _ = run_harden("upgrade_holding_docker", INSTALLED="docker-ce containerd.io runc", HELD="runc")
        self.assertEqual(rc, 0, out)
        actions = [c for c in calls if c.startswith(("apt-mark hold", "apt-mark unhold", "apt-get"))]
        self.assertEqual(actions[0], "apt-mark hold docker-ce")
        self.assertEqual(actions[1], "apt-mark hold containerd.io")
        self.assertTrue(actions[2].startswith("apt-get -o DPkg::Lock::Timeout=600 -y -qq") and actions[2].endswith(" upgrade"), actions)
        self.assertEqual(actions[3], "apt-mark unhold docker-ce containerd.io", "an operator's own hold on runc stays")
        self.assertEqual(len(actions), 4, actions)

    def test_an_operators_hold_survives_a_long_hold_list(self):
        # agent2's review of #149: under pipefail, `apt-mark showhold | grep -q` can lose its writer to SIGPIPE.
        rc, out, calls, _ = run_harden("upgrade_holding_docker", INSTALLED="runc", HELD="runc", FLOOD="1")
        self.assertEqual(rc, 0, out)
        self.assertFalse([c for c in calls if c.startswith(("apt-mark hold", "apt-mark unhold"))], calls)

    def test_planted_a_piped_hold_check_releases_an_operators_hold(self):
        text = harden()
        piped = text.replace('grep -qx -- "$pkg" <<<"$holds" && continue', 'apt-mark showhold | grep -qx "$pkg" && continue')
        self.assertNotEqual(piped, text)
        calls = run_harden("upgrade_holding_docker", piped, INSTALLED="runc", HELD="runc", FLOOD="1")[2]
        self.assertIn("apt-mark unhold runc", calls, "PLANTED: harden releases a hold it never made")

    def test_a_failed_upgrade_still_releases_its_holds_and_stops(self):
        rc, out, calls, _ = run_harden("upgrade_holding_docker", INSTALLED="docker-ce", APT_RC="100")
        self.assertEqual(rc, 1)
        self.assertIn("the security upgrade failed (apt-get exit 100)", out)
        self.assertEqual(calls[-1], "apt-mark unhold docker-ce")

    def test_unattended_upgrades_and_needrestart_leave_docker_alone(self):
        text = harden()
        self.assertIn('Unattended-Upgrade::Package-Blacklist { "docker-ce"; "docker-ce-cli"; "containerd.io"; '
                      '"docker.io"; "containerd"; "runc"; };', text)
        self.assertIn("$nrconf{override_rc}{qr(^docker)} = 0;\n$nrconf{override_rc}{qr(^containerd)} = 0;", text)
        self.assertIn("cat > /etc/needrestart/conf.d/90-ekka-desks.conf <<'EOF'", text)
        self.assertEqual(text.count("apt_get -y -qq -o Dpkg::Options::=--force-confdef"), 1,
                         "the one upgrade is the held one")

    def test_planted_reds_are_caught(self):
        text = harden()
        no_unhold = text.replace('(( ${#held[@]} == 0 )) || apt-mark unhold "${held[@]}" >/dev/null', ":")
        self.assertNotEqual(no_unhold, text)
        calls = run_harden("upgrade_holding_docker", no_unhold, INSTALLED="docker-ce", APT_RC="100")[2]
        self.assertNotIn("apt-mark unhold docker-ce", calls, "PLANTED: a failed upgrade leaves Docker held")
        no_hold = text.replace('apt-mark hold "$pkg" >/dev/null || die "could not hold $pkg before the security upgrade"\n    held+=("$pkg")', ":")
        self.assertNotEqual(no_hold, text)
        calls = run_harden("upgrade_holding_docker", no_hold, INSTALLED="docker-ce")[2]
        self.assertFalse(any(c.startswith("apt-mark hold") for c in calls), "PLANTED: the upgrade can restart Docker")


def shlex_quote(s):
    import shlex
    return shlex.quote(s)


@unittest.skipUnless(have_image("ubuntu:24.04"), "needs a cached ubuntu:24.04 image; no pull")
class AsRealRoot(unittest.TestCase):
    def run_root(self, prepare):
        script = privsep_fn() + prepare + "\nprivsep_dir /run/sshd && stat -c '%U:%G %a' /run/sshd\n"
        out = subprocess.run(["docker", "run", "--rm", "--network", "none", "--pull", "never", "ubuntu:24.04",
                              "bash", "-c", script], capture_output=True, text=True, timeout=120)
        self.assertEqual(out.returncode, 0, out.stderr)
        return out.stdout.strip()

    def test_absent_and_present_inside_a_throwaway_container(self):
        self.assertEqual(self.run_root("rm -rf /run/sshd"), "root:root 755")
        self.assertEqual(self.run_root("install -d -m 0711 /run/sshd"), "root:root 711", "an existing folder keeps its mode")


# ── #155: a fresh Ubuntu's own updater holds the package lock at first boot ──────────────────────────────────────
# Measured on the integration test server 2026-10-22: `apt-get update && apt-get install docker-ce ...` lost the lock, `set -e` does not
# stop on the left side of `&&`, Docker was silently not installed, and harden died later on "docker.service not found".

class EveryAptCallWaitsForTheLockAndDockerMustExist(unittest.TestCase):
    def test_a_held_lock_is_waited_for_then_the_step_runs(self):
        rc, out, calls, _ = run_harden("wait_for_apt; echo PASSED", LOCK_BUSY="3")
        self.assertEqual(rc, 0, out)
        self.assertIn("waiting for Ubuntu's package updater", out)
        self.assertIn("PASSED", out)
        self.assertEqual(sum(c.startswith("fuser ") for c in calls), 4)

    def test_a_lock_that_is_never_freed_stops_harden_naming_it(self):
        rc, out, _, _ = run_harden("wait_for_apt; echo SHOULD_NOT_PRINT", LOCK_BUSY="forever", APT_LOCK_TIMEOUT="15")
        self.assertNotEqual(rc, 0)
        self.assertIn("still holds the package lock after 15s", out)
        self.assertNotIn("SHOULD_NOT_PRINT", out)

    def test_apt_get_always_carries_the_lock_timeout(self):
        rc, out, calls, _ = run_harden("apt_get update -qq", APT_LOCK_TIMEOUT="77")
        self.assertEqual(rc, 0, out)
        self.assertIn("apt-get -o DPkg::Lock::Timeout=77 update -qq", calls)

    def test_no_bare_apt_get_call_and_no_apt_call_chained_with_and(self):
        for n, line in enumerate(harden().splitlines(), 1):
            code = re.sub(r'"[^"]*"', '""', line.split("#", 1)[0])   # a message that names apt-get is not a call
            if re.search(r"(^|[\s;(|&])apt-get\s", code) and not code.lstrip().startswith("apt_get()"):
                self.fail(f"harden.sh:{n} calls apt-get without the lock timeout: {line.strip()}")
            if re.search(r"apt_get\b[^\n]*&&", code):
                self.fail(f"harden.sh:{n} chains an apt call with &&, which set -e does not stop on: {line.strip()}")

    def test_docker_must_exist_after_the_install_step(self):
        text = harden()
        step = text[text.index('say "Docker"'):text.index("install -d /etc/docker")]
        self.assertRegex(step, r'command -v docker >/dev/null \|\| die "Docker is not installed')
        self.assertIn("wait_for_apt", step)

    def test_a_failed_hold_stops_the_upgrade(self):
        stubs_apt_mark = STUBS["apt-mark"]
        try:
            STUBS["apt-mark"] = '''[[ $1 == showhold ]] && exit 0
[[ $1 == hold ]] && exit 1
exit 0'''
            rc, out, calls, _ = run_harden("upgrade_holding_docker", INSTALLED="docker-ce")
        finally:
            STUBS["apt-mark"] = stubs_apt_mark
        self.assertNotEqual(rc, 0)
        self.assertIn("could not hold docker-ce", out)
        self.assertFalse(any(c.startswith("apt-get") and "upgrade" in c for c in calls), calls)
