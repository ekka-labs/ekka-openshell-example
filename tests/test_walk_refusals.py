"""tools/walk.sh against runner 0.1.111: what that release refuses on purpose is walked as a refusal, and nothing in
the walk reaches into a desk outside EKKA.

The clean-box walk found the walk expecting seven commands to succeed that 0.1.111 refuses by design, so it would
have reported the walk's own staleness as product failures. Each is now a `no` leg: it passes only when the command
is refused AND the refusal says what to do instead.
"""
import os
import re
import unittest

SCOPE = "scope 'sandbox:write' required"   # the gateway's refusal, measured on the box (walk 3, ekka-agent-desks#167)
ADAPTER = "is not authorized in this release: it opens another way into or out of a governed sandbox"
# `ekka desk shell` on 0.1.119: the CLI's own parser, measured on runner 78b7f6e7 (exit 64). Its old sentence, "nothing
# runs in a desk outside EKKA", no longer exists, so a walk that wants it fails against the release (Regression review).
SHELL_GONE = "unrecognized subcommand 'shell'"
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
REFUSED = {  # the command, and what its refusal must name (runner v0.1.111 removed.rs and person/refused.rs)
    "ekka desk upload": "Copying files into or out of a desk is not available",
    "ekka desk download": "Copying files into or out of a desk is not available",
    "ekka desk profile add": r"sudo \./up\.sh connect",
    "ekka desk access add": r"sudo \./up\.sh invite",
    "ekka desk access remove": r"sudo \./up\.sh revoke",
}


TRAP = re.compile(r"(?:^|;|&&|\|\|)\s*trap\s", re.M)   # a trap command anywhere a command can start


def walk(text=None):
    if text is not None:
        return text
    with open(os.path.join(ROOT, "tools", "walk.sh"), encoding="utf-8") as f:
        return f.read()


def leg_after(text, command):
    """The first check line after `covers "<command>"`."""
    m = re.search(rf'^covers "{re.escape(command)}"\n(.*)$', text, re.M)
    return m.group(1) if m else None


class EveryRefusedCommandIsWalkedAsARefusal(unittest.TestCase):
    def check(self, text):
        for command, says in REFUSED.items():
            leg = leg_after(text, command)
            self.assertIsNotNone(leg, f"{command} is not covered")
            self.assertTrue(leg.startswith("no "), f"{command} must be a refusal leg, not: {leg}")
            self.assertIn(f"'{says}'", leg, f"{command}'s refusal must name what replaced it")
            words = command.split()[1:]   # `ekka desk upload` runs as `P desk upload`
            self.assertIn("P " + " ".join(words), leg)
        # `desk shell` is no longer a command (0.1.119, regression coverage): the CLI's parser refuses it.
        leg = leg_after(text, "ekka desk shell")
        self.assertEqual(leg, f'no "shell is not a command any more" "{SHELL_GONE}" P desk shell "$DESK" -- true')
        connect = [l for l in text.splitlines() if l.startswith("no ") and "desk connect walk-dummy" in l]
        self.assertEqual(len(connect), 1, "a person's connect naming a profile is refused")
        self.assertIn(r"'sudo \./up\.sh connect walk-dummy'", connect[0])

    def test_the_walk(self):
        self.check(walk())

    def test_planted_a_refused_command_expected_to_succeed_again(self):
        text = walk().replace('no "upload is refused"', 't "upload a file into the work folder"')
        with self.assertRaises(AssertionError):
            self.check(text)

    def test_planted_the_old_shell_sentence_is_caught(self):
        text = walk().replace(f'"{SHELL_GONE}" P desk shell', "'nothing runs in a desk outside EKKA' P desk shell")
        self.assertNotEqual(text, walk(), "the plant did not apply")
        with self.assertRaises(AssertionError):
            self.check(text)

    def test_the_shell_leg_is_not_one_of_the_gateway_knocks(self):
        # A parser refusal on this machine is no evidence about the gateway: the knocks stay walk-person's own two.
        text = walk()
        leg = leg_after(text, "ekka desk shell")
        self.assertNotIn("wp_os", leg)
        self.assertNotIn(SCOPE, leg)
        self.assertEqual(len(APersonCannotReachIntoADeskDirectly().legs()), 2)


class NothingReachesIntoADeskOutsideEKKA(unittest.TestCase):
    FORBIDDEN = re.compile(r"sandbox exec|ssh-proxy|sandbox connect|\bdocker exec\b[^\n]*sandbox|"
                           r"^\s*t [^\n]*\bP desk (shell|download|upload)\b", re.M)
    # The one exception: a `no` leg, which passes only when the call is REFUSED, made with
    # walk-person's own token (wp_os). That knocks on the door to prove it is shut. Anything else naming exec or
    # connect, a `t` leg through wp_os included, is still a back door. The label and the pattern are double-quoted,
    # where the shell would run a $(...) or `...`, and the knock is erased before FORBIDDEN looks: so neither may
    # hold a $, a backtick or a backslash, or a call hidden in them would be erased with it.
    # And the call itself is exactly one of the two knocks, whole, with nothing after it: a `$(...)` placed among its
    # arguments would run before the refused call and be erased with the line (Regression review).
    KNOCK = re.compile(r'^\s*no "[^"\n$`\\]*"(?: \\\n\s*|\s)"[^"\n$`\\]*" \\\n\s*'
                       r'wp_os sandbox (?:exec --no-tty "\$DESK" -- true|connect "\$DESK")$', re.M)

    def back_doors(self, text):
        return self.FORBIDDEN.findall(self.KNOCK.sub("", text))

    def test_the_walk_has_no_back_door(self):
        self.assertEqual(self.back_doors(walk()), [])

    def test_planted_a_person_exec_expected_to_succeed_is_caught(self):
        # The knock's exact shape with only `no` made `t`: a looser plant is caught for the wrong reason.
        planted = walk() + "\n  t \"run it\" \"scope 'sandbox:write' required\" \\\n    wp_os sandbox exec --no-tty \"$DESK\" -- true\n"
        self.assertTrue(self.back_doors(planted))

    def test_planted_a_call_hidden_in_a_knocks_label_or_pattern_is_caught(self):
        call = "$(./up.sh openshell sandbox exec walk-desk -- cat /etc/hostname)"
        for label, want in ((call, "x"), ("shut", call), ("shut", "`openshell sandbox exec walk-desk -- true`")):
            planted = walk() + f'\n  no "{label}" "{want}" \\\n    wp_os sandbox exec --no-tty "$DESK" -- true\n'
            self.assertTrue(self.back_doors(planted), (label, want))

    def test_planted_a_call_hidden_in_a_knocks_arguments_is_caught(self):
        # Regression review: the extra argument runs first; the exec leg's ending is left intact.
        exec_, connect = 'wp_os sandbox exec --no-tty "$DESK" -- true', 'wp_os sandbox connect "$DESK"'
        hidden = '$(./up.sh openshell sandbox exec "$DESK" -- touch /tmp/plant)'
        for knock, planted_call in ((exec_, f'wp_os sandbox exec --no-tty {hidden} "$DESK" -- true'),
                                    (exec_, f'{exec_} {hidden}'),
                                    (exec_, f'{exec_}; ./up.sh openshell sandbox exec x -- id'),
                                    (connect, f'{connect} {hidden}')):
            planted = walk().replace(knock, planted_call, 1)
            self.assertNotEqual(planted, walk(), f"the plant did not apply: {planted_call}")
            self.assertTrue(self.back_doors(planted), planted_call)

    def test_planted_a_refused_exec_through_the_administrator_is_caught(self):
        planted = walk() + "\n  no \"shut\" 'x' \\\n  ./up.sh openshell sandbox exec walk-desk -- true\n"
        self.assertTrue(self.back_doors(planted))

    def test_planted_a_desk_read_through_the_gateway_admin_is_caught(self):
        planted = walk() + '\nt "read it" "." ./up.sh openshell sandbox exec walk-desk -- cat /etc/hostname\n'
        self.assertTrue(self.FORBIDDEN.search(planted))
        planted = walk() + '\nt "read it" "." P desk shell "$DESK" -- cat x\n'
        self.assertTrue(self.FORBIDDEN.search(planted))

    def test_a_skipped_check_says_why_by_name(self):
        text = walk()
        self.assertIn('NO_DESK_READ="reading a desk\'s files has no EKKA path in runner 0.1.111', text)
        self.assertGreaterEqual(text.count('"$NO_DESK_READ"'), 4)

    def test_answers_are_read_on_the_enclaves_machine(self):
        # With ekka 0.1.112: run show prints the collected answer there, and says
        # it is a local copy, not checked against the signed receipt (runner#1136).
        text = walk()
        self.assertIn('answer_of() { E run show "$1"; }', text)
        self.assertIn("not checked against the run.s signed receipt", text)
        self.assertIn('t "two asks keep two answers"', text)
        self.assertNotIn('skip_leg "each answer comes back by its own run id"', text)

    def two_answers(self, screen_a, screen_b):
        """walk.sh's own two_answers, with answer_of printing the given screens."""
        import subprocess
        fn = re.search(r"^two_answers\(\) \{.*?^\}\n", walk(), re.S | re.M).group(0)
        script = fn + 'answer_of() { if [[ $1 == "$RUN_A" ]]; then printf "%s" "$SCREEN_A"; else printf "%s" "$SCREEN_B"; fi; }\n' \
            + 'two_answers "$RUN_A" "$RUN_B"'
        env = dict(os.environ, RUN_A="11111111-aaaa-4aaa-8aaa-aaaaaaaaaaaa", RUN_B="22222222-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
                   SCREEN_A=screen_a, SCREEN_B=screen_b)
        return subprocess.run(["bash", "-c", script], capture_output=True, text=True, env=env, timeout=30).stdout.strip()

    def test_two_answers_compares_the_answers_not_the_screens(self):
        label = "  This is this machine's local copy of the answer; it is not checked against the run's signed receipt."
        a = "  Run 11111111-aaaa-4aaa-8aaa-aaaaaaaaaaaa\n  The README says hello.\n" + label
        b = "  Run 22222222-bbbb-4bbb-8bbb-bbbbbbbbbbbb\n  Open README first.\n" + label
        self.assertEqual(self.two_answers(a, b), "two")
        same_a = "  Run 11111111-aaaa-4aaa-8aaa-aaaaaaaaaaaa\n  The same words.\n" + label
        same_b = "  Run 22222222-bbbb-4bbb-8bbb-bbbbbbbbbbbb\n  The same words.\n" + label
        self.assertEqual(self.two_answers(same_a, same_b), "", "PLANTED: identical answers under two run ids are one answer")
        none_a = "  Run 11111111-aaaa-4aaa-8aaa-aaaaaaaaaaaa\n  Step 'run' produced no text."
        none_b = "  Run 22222222-bbbb-4bbb-8bbb-bbbbbbbbbbbb\n  Step 'run' produced no text."
        self.assertEqual(self.two_answers(none_a, none_b), "", "PLANTED: two runs with no answer are not two answers")

    FIRST = "11111111-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    STALE = "33333333-cccc-4ccc-8ccc-cccccccccccc"
    LABEL = "  This is this machine's local copy of the answer; it is not checked against the run's signed receipt."
    FIRST_SCREEN = f"  Run {FIRST}\n  The README says Hello World! at README:1.\n" + LABEL

    def stale_refused(self, plan_output, stale_screen):
        """walk.sh's own stale_refused, with answer_of printing the first ask's screen or the stale run's."""
        import subprocess
        fn = re.search(r"^stale_refused\(\) \{.*?^\}\n", walk(), re.S | re.M).group(0)
        script = fn + 'answer_of() { if [[ $1 == "$FIRST" ]]; then printf "%s" "$SCREEN_A"; else printf "%s" "$SCREEN_B"; fi; }\n' \
            + 'stale_refused "$FIRST" "$PLAN_OUT"'
        env = dict(os.environ, FIRST=self.FIRST, SCREEN_A=self.FIRST_SCREEN, SCREEN_B=stale_screen, PLAN_OUT=plan_output)
        return subprocess.run(["bash", "-c", script], capture_output=True, text=True, env=env, timeout=30).stdout.strip()

    def test_a_second_ask_with_no_answer_of_its_own_must_fail(self):
        text = walk()
        self.assertIn('t "a second ask that writes no answer fails, and never shows the first answer" \'^refused$\'', text)
        self.assertIn('o["path"] = f".ekka/answers/{first}.md"', text, "the stale plan collects the FIRST run's own file")
        dispatched = f"→ Run {self.STALE} dispatched.\n"
        failed = dispatched + f"✗ Plan failed (run {self.STALE}).\n"
        self.assertEqual(self.stale_refused(failed, f"  Run {self.STALE}\n  Step 'run' produced no text."), "refused")

    def test_planted_the_v0_1_2_stale_success_is_caught(self):
        # The tester's runs: completed, and showing the first answer byte for byte.
        completed = f"→ Run {self.STALE} dispatched.\n✓ Plan completed (run {self.STALE}).\n  The README says Hello World! at README:1.\n"
        self.assertIn("sealed an answer it never wrote", self.stale_refused(completed, self.FIRST_SCREEN.replace(self.FIRST, self.STALE)))
        # Failed, yet its screen still shows the first run's answer.
        failed = f"→ Run {self.STALE} dispatched.\n✗ Plan failed (run {self.STALE}).\n"
        self.assertIn("shows the first answer", self.stale_refused(failed, self.FIRST_SCREEN.replace(self.FIRST, self.STALE)))
        # A run that never dispatched, or that is the first run itself, is not a refusal.
        self.assertIn("no dispatched run id", self.stale_refused("✗ Plan failed: refused before dispatch.\n", ""))
        self.assertIn("no dispatched run id", self.stale_refused(f"→ Run {self.FIRST} dispatched.\n✗ Plan failed (run {self.FIRST}).\n", ""))


class TheReadOnlyLegKeepsThePersonsTokenInside(unittest.TestCase):
    def test_the_token_is_copied_file_to_file_and_both_go_after_the_leg(self):
        text = walk()
        # Both private files (the token and the gateway registration) are opened 0600 by one helper; the modes on
        # disk are asserted in tests/test_walk_person_gateway.py.
        self.assertIn("fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)", text)
        self.assertIn('rm -rf "$osh" "$WP"', text)
        self.assertIn('$(basename "$WP") == .ekka-walk-person', text, "only a home made for this leg is deleted")
        self.assertNotRegex(text, r"access_token[^\n]*(echo|printf)", "the token is never printed")

    def test_an_aborted_walk_leaves_no_live_sign_in(self):
        # regression review: the home held walk-person's refresh token until the leg's own end. One exit
        # handler now revokes them in Keycloak (deleting the file alone would leave the session alive) and deletes it.
        text = walk()
        handler = re.search(r"^on_exit\(\) \{.*?^\}\n", text, re.S | re.M).group(0)
        self.assertIn("/usr/local/sbin/ekka desk revoke walk-person", handler)
        self.assertIn('rm -rf "$WP_LIVE"', handler)
        self.assertEqual(len(TRAP.findall(text)), 1, "one EXIT trap; a second would replace it")
        armed = text.index("WP_LIVE=$WP")
        self.assertLess(armed, text.index("wp_os sandbox start"), "armed before the token is used")
        self.assertLess(armed, text.index("python3 - \"$WP/openshell/person/ekka-desks.json\""), "and before it is copied")

    def test_planted_a_second_trap_is_caught(self):
        text = walk() + "\ntrap 'echo planted' EXIT\n"
        self.assertEqual(len(TRAP.findall(text)), 2)

    def test_the_write_passes_only_on_the_missing_scope(self):
        leg = re.search(r'no "walk-person\'s own sign-in is refused a desk write[^\n]*\n[^\n]*', walk()).group(0)
        self.assertIn(f'"{SCOPE}"', leg)
        self.assertIn('sandbox start "$DESK"', leg, "a desk that exists: a not-found cannot stand in for the refusal")
        # UpdateConfig (policy set) needs workspace role admin, refused BEFORE any scope, and the CLI rejected the
        # setup's policy file locally: neither proves the missing scope. StartSandbox needs role user + sandbox:write.
        self.assertNotIn("policy set", leg, "a role refusal or a local parse error is not the missing scope")


class APersonCannotReachIntoADeskDirectly(unittest.TestCase):
    """EKKA regression coverage: ExecSandbox and CreateSshSession, called with walk-person's own token, are refused."""

    def legs(self):
        return re.findall(r'^\s*no "([^"\n]*)"(?: \\\n\s*|\s)"([^"\n]*)" \\\n\s*(wp_os sandbox (?:exec|connect)[^\n]*)$',
                          walk(), re.M)

    def test_both_doors_are_knocked_on_and_must_stay_shut(self):
        legs = self.legs()
        self.assertEqual([c.split()[2] for _, _, c in legs], ["exec", "connect"])
        # ExecSandbox has one lock, the scope: its leg must not accept the adapter's words, or scope drift
        # would pass unseen. CreateSshSession has two, the scope and the adapter: either refusal is its pass.
        self.assertEqual([w for _, w, _ in legs], [SCOPE, f"{SCOPE}|{ADAPTER}"])
        for label, want, command in legs:
            self.assertIn('"$DESK"', command, "a desk that exists: a not-found cannot stand in for the refusal")
        self.assertIn("--no-tty", legs[0][2], "exec stays on ExecSandbox")
        self.assertTrue(legs[0][2].endswith("-- true"), "a refusal that fails to come changes nothing")

    def test_they_run_while_an_aborted_walk_still_revokes_walk_person(self):
        text = walk()
        armed, disarmed = text.index("WP_LIVE=$WP"), text.index('WP_LIVE=""', text.index("WP_LIVE=$WP"))
        for door in ("wp_os sandbox exec", "wp_os sandbox connect"):
            self.assertTrue(armed < text.index(door) < disarmed, door)

    def test_after_revoke_the_read_is_refused_for_the_measured_reason(self):
        leg = re.search(r'^\s*no "walk-person\'s next read is refused"[^\n]*$', walk(), re.M).group(0)
        self.assertIn('"not a member of workspace \'desks\'" PP desk list', leg)

    def test_the_call_cannot_hang_the_walk(self):
        wp_os = re.search(r"^  wp_os\(\) \{.*?\}$", walk(), re.S | re.M).group(0)
        self.assertIn("timeout 60", wp_os)
        self.assertIn("</dev/null", wp_os)


if __name__ == "__main__":
    unittest.main()
