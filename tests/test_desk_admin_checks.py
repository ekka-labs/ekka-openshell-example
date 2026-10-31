"""Each desk administration walk leg's judgement, with its planted red case .

The walk runs on an installed machine and cannot run here, so what it decides is tested here: a good input passes,
and one planted bad input per leg fails, naming why.
"""
import copy
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, os.path.join(ROOT, "tools"))
import desk_admin_checks as c  # noqa: E402

ADAPTER = {
    "Config": {"User": "2000:1000"},
    "State": {"Status": "running", "Health": {"Status": "healthy"}},
    "HostConfig": {"ReadonlyRootfs": True, "CapDrop": ["ALL"], "CapAdd": None, "Privileged": False,
                   "SecurityOpt": ["no-new-privileges:true"], "PortBindings": {}},
    # As `docker inspect` reports compose.yml's four volumes for the adapter.
    "Mounts": [{"Type": "volume", "Name": "ekka-desks_ekka-backend-sock",
                "Source": "/var/lib/docker/volumes/ekka-desks_ekka-backend-sock/_data", "Destination": "/run/ekka-backend",
                "Driver": "local", "Mode": "z", "RW": True, "Propagation": ""},
               {"Type": "volume", "Name": "ekka-desks_ekka-backend-state",
                "Source": "/var/lib/docker/volumes/ekka-desks_ekka-backend-state/_data",
                "Destination": "/var/lib/ekka-backend", "Driver": "local", "Mode": "z", "RW": True, "Propagation": ""},
               {"Type": "bind", "Source": "/var/lib/ekka-agent-desks/backend/config.json",
                "Destination": "/etc/ekka-backend/config.json", "Mode": "", "RW": False, "Propagation": "rprivate"},
               {"Type": "bind", "Source": "/var/lib/ekka-agent-desks/tls/ca.crt",
                "Destination": "/etc/ekka-backend/ca.pem", "Mode": "", "RW": False, "Propagation": "rprivate"}],
    "NetworkSettings": {"Networks": {"ekka-desks_backend": {}}},
}
with open(os.path.join(ROOT, "vendor", "ekka-contracts", "governance", "grant-constraints", "samples",
                       "desks.v1.json")) as f:
    LIMITS = json.load(f)
# Govern's GrantSummary rows as `ekka gate grant list --json` prints them: the limits sit in the scope, the same
# verbatim shape tests/test_desk_grant.py builds.
GRANTS = [
    {"grant_id": "g1", "agent_id": "a1", "is_active": True, "expires_at": "2027-05-04T00:00:00Z",
     "scope": {"capability": "job.run", "instance_id": "enclave1Job", "resource_name": "desks/*", "constraints": None}},
    {"grant_id": "g2", "agent_id": "a1", "is_active": True, "expires_at": "2027-05-04T00:00:00Z",
     "scope": {"capability": "api.write", "instance_id": "enclave1Api", "resource_name": "apis/desks",
               "constraints": LIMITS}},
]


def planted(base, path, value):
    d = copy.deepcopy(base)
    node = d
    for k in path[:-1]:
        node = node[k]
    if value is KeyError:
        del node[path[-1]]
    else:
        node[path[-1]] = value
    return d


class TheAdapterLeg(unittest.TestCase):
    def test_a_locked_down_healthy_adapter_passes(self):
        self.assertEqual(c.adapter([ADAPTER]), [])

    def test_planted_each_way_it_could_be_wider_is_named(self):
        for path, value, why in [
            (("State", "Health", "Status"), "starting", "not healthy"),
            (("Config", "User"), "0:0", "not the Enclave's"),
            (("HostConfig", "ReadonlyRootfs"), False, "writable"),
            (("HostConfig", "CapDrop"), [], "not ALL"),
            (("HostConfig", "CapAdd"), ["NET_ADMIN"], "adds capabilities"),
            (("HostConfig", "Privileged"), True, "privileged"),
            (("HostConfig", "SecurityOpt"), [], "no-new-privileges"),
            (("HostConfig", "PortBindings"), {"9000/tcp": [{"HostPort": "9000"}]}, "publishes ports"),
            (("NetworkSettings", "Networks"), {"ekka-desks_backend": {}, "ekka-desks_front": {}}, "networks"),
        ]:
            with self.subTest(why=why):
                problems = c.adapter([planted(ADAPTER, path, value)])
                self.assertTrue(any(why in p for p in problems), problems)

    def test_planted_the_docker_socket_or_ekka_home_mounted_is_named(self):
        sock = planted(ADAPTER, ("Mounts",), ADAPTER["Mounts"] + [{"Source": "/var/run/docker.sock", "Destination": "/var/run/docker.sock"}])
        self.assertTrue(any("Docker socket" in p for p in c.adapter(sock)))
        home = planted(ADAPTER, ("Mounts",), ADAPTER["Mounts"] + [{"Source": "x", "Destination": "/var/lib/ekka/.ekka"}])
        self.assertTrue(any("mounts" in p for p in c.adapter(home)))

    def test_planted_a_missing_or_writable_configuration_file_is_named(self):
        # regression review: the check knew two mounts and compose gives four; the two files are read-only.
        for i, path in ((2, "/etc/ekka-backend/config.json"), (3, "/etc/ekka-backend/ca.pem")):
            with self.subTest(path=path):
                gone = planted(ADAPTER, ("Mounts",), [m for j, m in enumerate(ADAPTER["Mounts"]) if j != i])
                self.assertTrue(any("not exactly" in p for p in c.adapter([gone])))
                writable = planted(ADAPTER, ("Mounts", i, "RW"), True)
                self.assertEqual(c.adapter([writable]), [f"can write {path}, which is read-only"])
                unsaid = planted(ADAPTER, ("Mounts", i, "RW"), KeyError)
                self.assertEqual(c.adapter([unsaid]), [f"can write {path}, which is read-only"], "unknown is not read-only")

    def test_the_tmp_in_memory_is_not_a_mount_of_the_machine(self):
        tmp = planted(ADAPTER, ("Mounts",), ADAPTER["Mounts"] + [{"Type": "tmpfs", "Destination": "/tmp"}])
        self.assertEqual(c.adapter([tmp]), [])
        bound = planted(ADAPTER, ("Mounts",), ADAPTER["Mounts"] + [{"Type": "bind", "Source": "/", "Destination": "/tmp"}])
        self.assertTrue(any("not exactly" in p for p in c.adapter([bound])))

    def test_no_container_is_a_problem_not_a_pass(self):
        self.assertTrue(c.adapter([]))


class TheGrantLeg(unittest.TestCase):
    def test_one_write_grant_on_apis_desks_with_limits_passes(self):
        self.assertEqual(c.grant(GRANTS), [])

    def test_planted_no_grant_two_grants_a_read_grant_or_no_limits(self):
        self.assertTrue(c.grant(GRANTS[:1]))
        self.assertTrue(c.grant(GRANTS + [copy.deepcopy(GRANTS[1])]))
        self.assertTrue(any("not api.write" in p for p in c.grant([GRANTS[0], planted(GRANTS[1], ("scope", "capability"), "api.read")])))
        # regression review: the check read a top-level field Govern does not print, so these all passed.
        for limits in ({}, None, KeyError, [], "x"):
            with self.subTest(limits=limits):
                self.assertEqual(c.grant([GRANTS[0], planted(GRANTS[1], ("scope", "constraints"), limits)]),
                                 ["the desks grant carries no limits"])

    def test_planted_limits_missing_one_the_contract_requires(self):
        thin = planted(GRANTS[1], ("scope", "constraints", "image_digests"), KeyError)
        self.assertEqual(c.grant([GRANTS[0], thin]), ["the desks grant's limits lack image_digests"])

    def test_an_unreadable_listing_is_never_no_grants(self):
        self.assertEqual(c.grant("not a list"), ["the grant listing could not be read"])


class TheAdminRunLegs(unittest.TestCase):
    """Plan steps 3, 5 and 6 (desk lifecycle checks on EKKA regression coverage)."""

    def test_the_grant_to_revoke_is_the_one_desks_grant(self):
        self.assertEqual(c.grant_id(GRANTS), ("g2", []))

    def test_planted_no_grant_or_two_gives_no_id_to_revoke(self):
        self.assertIsNone(c.grant_id(GRANTS[:1])[0])
        self.assertIsNone(c.grant_id(GRANTS + [copy.deepcopy(GRANTS[1])])[0])

    # `ekka receipts show <run>` as runner 0.1.111 prints it, measured on the walk box (2026-10-19, run 85581750).
    RECEIPT = """
  What your agent did  run 85581750

  \u2713 EKKA approved the request
  \u2713 EKKA issued a one-time key
  \u2713 your Enclave signed for the file it touched
  \u2713 the record was sealed

  4 steps, signed and linked in order. Nothing can be changed afterwards.
"""
    RUN = "85581750-f466-449a-9527-0f8ff0e6ee09"

    def test_a_sealed_receipt_of_that_run_passes(self):
        self.assertEqual(c.sealed(self.RECEIPT, self.RUN), [])

    def test_planted_another_runs_receipt_an_unsealed_one_or_no_run_id(self):
        self.assertTrue(c.sealed(self.RECEIPT, "435dfae2-a280-44d5-9c1e-d12c3a6fb0b9"), "another run's receipt")
        self.assertTrue(c.sealed(self.RECEIPT.replace("the record was sealed", "the record"), self.RUN))
        self.assertTrue(c.sealed(self.RECEIPT, ""), "no run id to look for is never a pass")
        self.assertTrue(c.sealed("", self.RUN))

    def test_same_run_grant_denial_before_work_passes(self):
        run="70c0905d-7e36-41f4-ad33-851c9ae5ec35"
        diag={"run_id":run,"run_status":"failed","error":{"code":"RESOURCE_GRANT_DENIED"},"failure":None}
        self.assertEqual(c.grant_denied(diag,{"entries":[],"malformed_lines":0},run,2),[])

    def test_other_runs_successes_and_other_failures_never_prove_grant_denial(self):
        run="70c0905d-7e36-41f4-ad33-851c9ae5ec35"
        good={"run_id":run,"run_status":"failed","error":{"code":"RESOURCE_GRANT_DENIED"},"failure":None}
        self.assertTrue(c.grant_denied(good,{"entries":[],"malformed_lines":0},run,0))
        self.assertTrue(c.grant_denied(good,{"entries":[],"malformed_lines":0},"435dfae2-a280-44d5-9c1e-d12c3a6fb0b9",2))
        for change in [{"run_status":"completed"},{"error":{"code":"SESSION_EXPIRED"}},
                       {"error":{"code":"NOT_ENROLLED"}},{"error":{"code":"ROLE_REQUIRED"}},
                       {"error":{"message":"refused denied"}}]:
            with self.subTest(change=change):
                self.assertTrue(c.grant_denied(dict(good,**change),{"entries":[],"malformed_lines":0},run,2))
        self.assertTrue(c.grant_denied({},{"entries":[],"malformed_lines":0},run,2))

    def test_real_failure_metadata_is_allowed_but_receipts_must_prove_no_work(self):
        run="70c0905d-7e36-41f4-ad33-851c9ae5ec35"
        diag={"run_id":run,"run_status":"failed","error":{"code":"RESOURCE_GRANT_DENIED"},
              "failure":{"failed_operation":"desk","run_error_code":"RESOURCE_GRANT_DENIED", "gate_error_code":None}}
        self.assertEqual(c.grant_denied(diag,{"entries":[],"malformed_lines":0},run,2),[])
        for receipts in [{},{"entries":[{"kind":"step_started"}],"malformed_lines":0},
                         {"entries":[],"malformed_lines":1}]:
            self.assertTrue(c.grant_denied(diag,receipts,run,2))


class TheReadOnlyPeopleLeg(unittest.TestCase):
    def test_the_four_read_scopes_pass(self):
        self.assertEqual(c.scopes(["sandbox:read", "provider:read", "workspace:read", "config:read"]), [])

    def test_identity_scopes_do_not_grant_openshell_writes(self):
        held=list(c.PEOPLE_SCOPES|c.IDENTITY_SCOPES)
        self.assertEqual(c.scopes(held),[])
        for extra in ["sandbox:write","provider:write","workspace:write","config:write","openshell:all","profile:write","unknown"]:
            with self.subTest(extra=extra):
                self.assertTrue(c.scopes(held+[extra]))

    def test_planted_a_write_scope_or_openshell_all_is_named(self):
        for extra in ("sandbox:write", "openshell:all", "config:write"):
            with self.subTest(extra=extra):
                self.assertTrue(any(extra in p for p in c.scopes(["sandbox:read", "provider:read", "workspace:read", "config:read", extra])))


class TheRefusalLegs(unittest.TestCase):
    def test_a_refusal_naming_its_limit_passes(self):
        self.assertEqual(c.refused("run r-1: refused CONSTRAINT_IMAGE_DIGESTS (receipt 41)", "CONSTRAINT_IMAGE_DIGESTS"), [])

    def test_planted_a_success_or_another_reason_is_named(self):
        self.assertTrue(c.refused("run r-1: ready", "CONSTRAINT_IMAGE_DIGESTS"))
        self.assertTrue(any("does not name" in p for p in c.refused("refused CONSTRAINT_PROVIDERS", "CONSTRAINT_IMAGE_DIGESTS")))

    def test_an_outcome_is_one_of_the_contracts_words(self):
        # As runner 0.1.111 prints them, measured on the walk box (2026-10-19).
        self.assertEqual(c.outcome("\u2713 Desk walk-probe: stop completed (ready)", "ready"), [])
        self.assertEqual(c.outcome("ekka desk create: desk 'walk-probe': create ended failed (running). Receipt", "running"), [])
        self.assertTrue(c.outcome("create ended failed (running)", "ready"))
        self.assertTrue(c.outcome("the desk is already there", "ready"), "a whole word: already is not ready")
        self.assertEqual(c.outcome("run r-2: duplicate refused", "duplicate"), [])
        self.assertTrue(c.outcome("run r-2: ready", "duplicate"))
        self.assertTrue(c.outcome("run r-3: succeeded", "succeeded"), "verdict is withdrawn: the fact is the outcome")


class TheNoCredentialLeg(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        with open(os.path.join(self.dir, "op-1.json"), "w") as f:
            f.write('{"operation_id":"x","state":"settled"}')

    def test_a_store_without_the_value_passes(self):
        self.assertEqual(c.clean([self.dir], "planted-cred-value"), [])

    def test_planted_the_value_or_a_bearer_header_on_disk_is_named(self):
        with open(os.path.join(self.dir, "op-2.json"), "w") as f:
            f.write('{"auth":"planted-cred-value"}')
        self.assertTrue(any("planted value" in p for p in c.clean([self.dir], "planted-cred-value")))
        os.remove(os.path.join(self.dir, "op-2.json"))
        with open(os.path.join(self.dir, "adapter.log"), "w") as f:
            f.write("calling gateway with Authorization: Bearer eyJ...")
        self.assertTrue(any("bearer header" in p for p in c.clean([self.dir], "planted-cred-value")))

    def test_no_value_to_search_for_never_passes(self):
        self.assertTrue(c.clean([self.dir], ""))

    def test_the_value_comes_on_stdin_never_argv(self):
        r = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "desk_admin_checks.py"), "clean", self.dir],
                           input="planted-cred-value", capture_output=True, text=True)
        self.assertEqual((r.returncode, r.stdout.strip()), (0, "ok"))


class Exited(unittest.TestCase):
    """Step 17c: a stop settle waited for is finished, every container of the desk exited."""

    def test_every_container_exited(self):
        self.assertEqual(c.exited([["a" * 64, "exited", "sb-1"], ["b" * 64, "exited", "sb-1"]]), [])

    def test_planted_one_still_running_beside_an_exited_one(self):
        self.assertEqual(c.exited([["a" * 64, "exited", "sb-1"], ["b" * 64, "running", "sb-1"]]),
                         ["container bbbbbbbbbbbb is running, not exited"])

    def test_planted_no_container_at_all_never_passes(self):
        self.assertEqual(c.exited([]), ["no container of the desk was found"])

    def test_planted_an_unreadable_row_never_passes(self):
        self.assertEqual(c.exited([["a" * 64, "exited"]]), ["a container row could not be read"])

    def test_reads_the_listing_on_stdin(self):
        tool = os.path.join(ROOT, "tools", "desk_admin_checks.py")
        for rows, want in (([["a" * 64, "exited", "sb"]], (0, "ok")), ("not json", (1, None))):
            r = subprocess.run([sys.executable, tool, "exited"], input=rows if isinstance(rows, str) else json.dumps(rows),
                               capture_output=True, text=True)
            self.assertEqual(r.returncode, want[0], r.stdout)
            if want[1]:
                self.assertEqual(r.stdout.strip(), want[1])


class TheWalk(unittest.TestCase):
    def setUp(self):
        with open(os.path.join(ROOT, "tools", "walk.sh"), encoding="utf-8") as f:
            self.text = f.read()

    def test_runs_the_desk_administration_legs_only_when_the_adapter_is_on(self):
        self.assertIn('section "desk administration through EKKA"', self.text)
        self.assertIn("desk_admin_on", self.text)

    def test_walks_plan_steps_3_5_and_6_with_receipts_and_restores_the_grant(self):
        for leg in ("step 3: an admin creates a desk", "step 5: stop", "step 5: start", "step 5: a narrowing policy change",
                    "step 6: this run is RESOURCE_GRANT_DENIED before any step"):
            self.assertIn(leg, self.text)
        self.assertIn("its receipt is that run's, sealed", self.text)
        self.assertNotIn("its receipt names the admin", self.text, "0.1.111 prints no identity on a receipt")
        self.assertIn('skip_leg "step 3 and 5: each receipt names the admin"', self.text)
        refused, restored = self.text.index("step 6: this run is RESOURCE_GRANT_DENIED"), self.text.index("step 6: up.sh run again")
        self.assertLess(refused, restored, "the grant comes back only after the refusal is walked")
        self.assertIn("from the same limits file", self.text)
        self.assertNotIn("waits for ADK-E4's limits file", self.text, "step 6 is walked now, not skipped")
        self.assertIn("the walk's probe desk is deleted", self.text)

    def judge_exit(self, check, rc, arg, output, text=None):
        """walk.sh's own judge_exit, run in bash against the real checks."""
        import subprocess
        text = text or self.text
        fn = re.search(r"^judge_exit\(\) \{.*?^\}\n", text, re.S | re.M).group(0)
        r = subprocess.run(["bash", "-c", fn + 'judge_exit "$@"', "_", check, str(rc), arg, output],
                           cwd=ROOT, capture_output=True, text=True, timeout=30)
        return r.stdout.strip()

    def test_judged_reads_the_exit_code_with_the_words(self):
        # The line runner 0.1.111 prints for a run that FAILED after the adapter answered ready (governed.rs:244).
        failed = "ekka desk status: desk 'walk-probe': status ended failed (ready). Receipt: run 7ec01894-d90f-406c-a9d9-2627fe5802ec"
        self.assertEqual(self.judge_exit("outcome", 0, "ready", "✓ Desk walk-probe: status completed (ready)"), "ok")
        self.assertNotEqual(self.judge_exit("outcome", 1, "ready", failed), "ok", "PLANTED: a failed run is not ready")
        self.assertEqual(self.judge_exit("refused", 2, "CONSTRAINT_PROVIDERS",
                                         "create ended failed (refused: CONSTRAINT_PROVIDERS)"), "ok")
        self.assertNotEqual(self.judge_exit("refused", 0, "CONSTRAINT_PROVIDERS", "refused CONSTRAINT_PROVIDERS"), "ok",
                            "PLANTED: a refusal that exited 0 refused nothing")
        judged = self.text[self.text.index("judged() {"):self.text.index("judge_exit() {")]
        self.assertIn('out=$("$@" 2>&1); rc=$?', judged)

    def test_planted_judged_without_the_exit_code_is_caught(self):
        line = '    outcome) [[ $2 == 0 ]] || { echo "the command exited $2"; return; } ;;\n'
        self.assertEqual(self.text.count(line), 1)
        failed = "desk 'walk-probe': status ended failed (ready). Receipt: run 7ec01894-d90f-406c-a9d9-2627fe5802ec"
        self.assertEqual(self.judge_exit("outcome", 1, "ready", failed, text=self.text.replace(line, "")), "ok",
                         "without the exit code the words pass a failed run: what the guard exists for")

    def test_every_admin_change_must_succeed_and_answer_ready(self):
        # Regression review #116: a failed create must never count as passing. No adapter is excused any more.
        body = self.text[self.text.index("  admin_run() {"):self.text.index('  skip_leg "step 3 and 5')]
        self.assertIn('out=$(E desk "$@"); rc=$?', body)
        self.assertIn('ready_and_ok "$rc" "$out"', body)
        for gone in ("LATE_SETTLE", "late_settle", "settled_by_status", "outcome running"):
            self.assertNotIn(gone, self.text)

    def test_policy_changes_are_held_to_the_grant_and_narrowing_waits_for_its_fixture(self):
        # The shipped grant's hosts come only from providers, and the adapter allows no new program (enforce.rs), so
        # an allow of curl to example.com is refused and a desk has nothing to lose: no widened grant to green it.
        self.assertIn("no \"an allow outside the desks grant is refused by the adapter, naming CONSTRAINT_ALLOWED_HOSTS\" "
                      "'CONSTRAINT_ALLOWED_HOSTS'", self.text)
        self.assertIn('no "a deny of a host the desk never allowed is refused before a change" \'NO_SUCH_RULE\'', self.text)
        self.assertIn("'already has this wall; nothing was sent'", self.text)
        self.assertEqual(self.text.count("narrowing is walked with the approved test fixture"), 2)
        self.assertNotRegex(self.text, r"(?m)^\s*t \"[^\"]*\" '[^']*' P desk allow ", "an allow is never expected to succeed")

    def test_walks_step_17c_only_when_asked_on_a_disposable_box(self):
        gate = self.text.index('if [[ ${WALK_SLOW_STOP:-} == 1 && -n ${WALK_SLOW_STOP_DESK:-} ]] && (( slow_timeout >= 60 ))')
        kill = self.text.index("docker kill ekka-desks-gateway-1")
        self.assertLess(gate, kill, "the gateway is killed only inside the opt-in")
        self.assertIn('skip_leg "step 17c', self.text)

    def test_asks_for_a_change_only_once_the_job_is_seen_holding_the_desk(self):
        busy = self.text.index('./up.sh ekka desk stop "$DESK" --json > "$TMP/queued-stop.out"')
        wait = self.text.index('for _ in $(seq 1 90); do h=$(lock_holder)')
        self.assertLess(wait, busy, "a stop asked before the job holds the desk would stop the desk under it")
        self.assertIn('$h != admin:*', self.text, "a change's own lock is not a job's")

    def admin_section(self):
        return self.text[self.text.index('section "desk administration through EKKA"'):self.text.index('section "clean up"')]

    def test_every_openshell_call_it_makes_is_one_up_sh_lets_through(self):
        # Regression check: the kill-switch leg restarted the desk with `up.sh openshell sandbox start`, which the allow-list
        # refuses, so the leg could never pass. Each call is put to the allow-list itself, desk names filled in.
        sys.path.insert(0, os.path.join(ROOT, "tools"))
        import openshell_allow
        calls = re.findall(r'\./up\.sh openshell ([^\n]*)', self.text)
        self.assertTrue(calls)
        for call in calls:
            argv = [a.strip('"') for a in call.split() if not a.startswith(("2>", ">"))]
            argv = ["walk-desk" if a.startswith("$") else a for a in argv]
            with self.subTest(call=call):
                openshell_allow.check(argv)

    def test_a_desk_command_in_the_admin_legs_is_judged_never_taken_as_any_answer(self):
        # Regression check: step 17c's last leg accepted any output of `ekka desk status`. Every `P desk` leg here is judged:
        # an outcome, a refusal, a receipt, never the regex '.'.
        loose = re.findall(r"""^\s*t "[^"]*" '\.' [PE] desk [^\n]*""", self.admin_section(), re.M)
        self.assertEqual(loose, [])

    def test_step_17c_ends_with_the_stop_settled_and_the_desk_let_go(self):
        section = self.admin_section()
        self.assertIn('outcome ready E desk status "$SLOW"', section)
        self.assertIn("released \"$SLOW\"", section)
        self.assertIn("desk_admin_checks.py exited", section)

    def released_answers(self, desks_dir, desk="walk-desk"):
        """Run the walk's own `released`, cut from tools/walk.sh, on a folder (the native Enclave's, read in place)."""
        start = self.text.index("  released() {")
        body = self.text[start:self.text.index("\n  }\n", start) + 4]
        script = f"{body}\ndesks_dir={desks_dir!r}\nreleased {desk} || echo held\n"
        r = subprocess.run(["bash", "-c", script], capture_output=True, text=True)
        return r.stdout.strip()

    def test_released_only_when_the_state_folder_is_there_and_the_lock_is_not(self):
        # regression review: an empty read of the lock is not a released desk. A ready or refused release
        # removes the lock; an unknown one keeps it, marked.
        with tempfile.TemporaryDirectory() as d:
            os.mkdir(os.path.join(d, ".state"))
            self.assertEqual(self.released_answers(d), "released")
            open(os.path.join(d, ".state", "walk-desk.lock"), "w").close()   # held, even with nothing in it
            self.assertEqual(self.released_answers(d), "held")

    def test_planted_a_lock_that_cannot_be_read_is_never_released(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(self.released_answers(os.path.join(d, "not-the-desks")), "held", "a wrong folder")
            os.mkdir(os.path.join(d, ".state"))
            os.chmod(os.path.join(d, ".state"), 0)   # a state folder that cannot be read: `test ! -e` would lie
            try:
                if os.geteuid() != 0:
                    self.assertEqual(self.released_answers(d), "held", "a lock that cannot be read is never released")
            finally:
                os.chmod(os.path.join(d, ".state"), 0o700)
            self.assertEqual(self.released_answers(""), "held", "no desks folder found")

    def test_no_false_outage_proof_from_the_hosts_hosts_file(self):
        section = self.admin_section()
        self.assertIn('skip_leg "the kill switch with EKKA unreachable"', section)
        self.assertIn('inside the Enclave container', section)
        self.assertNotIn('/etc/hosts\n', section)
        self.assertNotIn('WALK_KILL_SWITCH', section)

    def test_walks_desk_status_now_that_this_setup_needs_the_release_that_has_it(self):
        # Regression check: required walk legs must not be skipped. The floor (DESK_ADMIN_MIN_EKKA) is the release that carries
        # `ekka desk status`, so "this setup does not pin it yet" is no longer true.
        self.assertNotIn("this setup does not pin it yet", self.text)
        self.assertIn('outcome ready E desk status "$PROBE"', self.text)

    def test_never_puts_a_planted_value_on_a_command_line(self):
        self.assertIn("desk_admin_checks.py clean", self.text)
        self.assertNotRegex(self.text, r"desk_admin_checks\.py clean [^\n]*\$\(kc admin-client")


if __name__ == "__main__":
    unittest.main()


class ProviderIsolation(unittest.TestCase):
    def test_provider_snapshot_is_complete_and_distinguishes_other_desks(self):
        row={"connections":[{"name":"claude-code","desks":["walk-desk"],"other_backend_desks":0}]}
        self.assertEqual(c.provider_usage(row,"claude-code"),(["walk-desk"],0))
        row["connections"][0]["desks"].append("customer-desk")
        self.assertNotEqual(c.provider_usage(row,"claude-code"),(["walk-desk"],0))
        row["connections"][0]["other_backend_desks"]=1
        self.assertEqual(c.provider_usage(row,"claude-code")[1],1)

    def test_malformed_or_missing_attachment_data_never_proves_isolation(self):
        for row in [{},{"connections":[]},{"connections":[{"name":"claude-code","desks":[]}]}]:
            with self.subTest(row=row), self.assertRaises(ValueError):
                c.provider_usage(row,"claude-code")


class GovernedRecreation(unittest.TestCase):
    def test_only_a_healthy_pin_for_the_expected_desk_supplies_identity(self):
        row={"state":"pinned","desk":{"desk":"walk-desk","backend_id":"old-id"}}
        self.assertEqual(c.pinned_id(row,"walk-desk"),"old-id")
        for bad in [{},dict(row,state="REPLACED (DESK_REPLACED)"),
                    {"state":"pinned","desk":{"desk":"other","backend_id":"old-id"}},
                    {"state":"pinned","desk":{"desk":"walk-desk","backend_id":"../escape"}}]:
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                c.pinned_id(bad,"walk-desk")

    def test_new_pin_cli_requires_a_different_healthy_backend_id(self):
        import contextlib, io
        with tempfile.TemporaryDirectory() as folder:
            before, after = os.path.join(folder,"before.json"), os.path.join(folder,"after.json")
            old={"state":"pinned","desk":{"desk":"walk-desk","backend_id":"old-id"}}
            new={"state":"pinned","desk":{"desk":"walk-desk","backend_id":"new-id"}}
            with open(before,"w") as f: json.dump(old,f)
            with open(after,"w") as f: json.dump(new,f)
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(c.main(["new-pin",before,"walk-desk",after]),0)
                with open(after,"w") as f: json.dump(old,f)
                self.assertEqual(c.main(["new-pin",before,"walk-desk",after]),1)
                old["state"]="not pinned yet"
                with open(after,"w") as f: json.dump(old,f)
                self.assertEqual(c.main(["new-pin",before,"walk-desk",after]),1)


class QueuedTaskOrder(unittest.TestCase):
    def test_actual_rows_must_be_completed_unique_and_in_the_observed_order(self):
        rows={"runs":[{"id":"job","status":"completed","completed_at":"2026-10-20T01:00:00Z"},
                      {"id":"stop","status":"completed","completed_at":"2026-10-20T01:00:02Z"}]}
        self.assertEqual(c.completed_in_order(rows,"job","stop"),[])
        self.assertTrue(c.completed_in_order(rows,"stop","job"))
        self.assertTrue(c.completed_in_order(rows,"missing","stop"))
        self.assertTrue(c.completed_in_order(rows,"job","job"))
        for change in [{"status":"failed"},{"completed_at":None},{"completed_at":"2026-10-20T01:00:00"}]:
            bad=copy.deepcopy(rows); bad["runs"][1].update(change)
            self.assertTrue(c.completed_in_order(bad,"job","stop"))
        duplicate=copy.deepcopy(rows); duplicate["runs"].append(duplicate["runs"][0])
        self.assertTrue(c.completed_in_order(duplicate,"job","stop"))
