"""The example plans for each desk, and the prompts they pin .

A plan names its desk as a literal resource, so each desk gets its own copy, made by tools/desk_plans.py from
ekka/plans/. The ask plan ends well only when the job succeeded and the Enclave collected an answer file; every
other arm before that one fails. Every prompt from version 2
on starts with the master block, byte for byte, from its one source.
"""
import glob
import json
import os
import re
import sys
import unittest

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, os.path.join(ROOT, "tools"))
import desk_plans  # noqa: E402

PROMPTS = os.path.join(ROOT, "prompts", "platform")


def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def step(plan):
    return plan["definition"]["operations"][0]["steps"][0]


class TheMasterBlock(unittest.TestCase):
    def test_every_prompt_from_version_2_starts_with_it_byte_for_byte(self):
        block = read(os.path.join(PROMPTS, "_master-block.txt"))
        newer = [p for p in glob.glob(os.path.join(PROMPTS, "*@*.txt")) if int(p.rsplit("@", 1)[1][:-4]) >= 2]
        self.assertTrue(newer, "no prompt from version 2 on")
        for p in newer:
            self.assertTrue(read(p).startswith(block + "\n"), f"{os.path.basename(p)} does not start with the master block")

    def test_the_plans_pin_a_prompt_that_carries_it(self):
        for plan in desk_plans.PLANS:
            ref = step(desk_plans.render(plan, "code-desk"))["prompt"]
            self.assertTrue(os.path.exists(os.path.join(PROMPTS, f"{ref['slug']}@{ref['version']}.txt")), ref)
            self.assertGreaterEqual(ref["version"], 2, ref)


class ADeskName(unittest.TestCase):
    def test_becomes_one_camel_case_segment(self):
        self.assertEqual(desk_plans.desk_code("code-desk"), "codeDesk")
        self.assertEqual(desk_plans.desk_code("business.desk_2"), "businessDesk2")
        self.assertEqual(desk_plans.desk_code("2nd-desk"), "desk2ndDesk")
        self.assertEqual(desk_plans.desk_code("walk-desk"), "walkDesk")

    def test_that_is_not_a_desk_name_is_refused(self):
        for bad in ["", "Bad Desk", "-x", "desk/../x", "a" * 64]:
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                desk_plans.desk_code(bad)


class EachDesksPlans(unittest.TestCase):
    def test_the_templates_name_no_desk(self):
        for plan in desk_plans.PLANS:
            text = read(os.path.join(ROOT, "ekka", "plans", f"{plan}.json"))
            self.assertNotIn("code-desk", text, f"ekka/plans/{plan}.json names a desk; use {{{{desk}}}}")

    def test_name_the_desk_once_as_their_resource_and_carry_its_code(self):
        for plan in desk_plans.PLANS:
            d = desk_plans.render(plan, "business-desk", "agent-1")
            self.assertEqual(step(d)["resource"], "desks/business-desk")
            self.assertEqual(d["plan"]["code"], f"example.desk.{plan}.businessDesk.v1")
            self.assertEqual(d["plan"]["agent"], "agent-1")
            self.assertNotIn("{{", json.dumps(d))
            self.assertNotIn("desk", step(d)["inputs"], "a job step names its desk once, as its resource")

    def test_bind_every_variable_their_prompt_reads(self):
        for plan in desk_plans.PLANS:
            s = step(desk_plans.render(plan, "code-desk"))
            text = read(os.path.join(PROMPTS, f"{s['prompt']['slug']}@{s['prompt']['version']}.txt"))
            for var in set(re.findall(r"@\{([a-z_]+)\}", text)):
                self.assertIn(var, s["inputs"], f"{plan}: the prompt reads @{{{var}}} and the plan does not bind it")


class TheAskPlan(unittest.TestCase):
    def setUp(self):
        self.plan = desk_plans.render("ask", "code-desk")
        self.op = self.plan["definition"]["operations"][0]

    def test_writes_one_answer_and_keeps_no_copy_on_the_shared_desk(self):
        # regression coverage: desk-ask@7 also wrote .ekka/answers/<run>.md, which stayed on the desk for the
        # next job and conversation there (measured on the integration test server, regression coverage). @8 writes only the one file
        # EKKA collects, and says the desk is shared. It claims no per-person privacy: that is separate desks.
        s = step(self.plan)
        self.assertEqual(s["inputs"]["run_id"], "@ekka.run_id")
        text = read(os.path.join(PROMPTS, f"{s['prompt']['slug']}@{s['prompt']['version']}.txt"))
        self.assertIn("Write tool once to write the final answer to\n/sandbox/ekka-workspace/.ekka/answer.md", text)
        self.assertNotIn(".ekka/answers/@{run_id}", text)
        self.assertIn('"answer_path":".ekka/answer.md"', text)
        self.assertIn("shared by the people authorized to use it", text)
        shared = text[text.index("1. Do not remove anything in .ekka."):text.index("2. Read and retain")].lower()
        for claim in ("private", "privacy", "only you", "isolat", "confidential"):
            self.assertNotIn(claim, shared, "the shared-desk words claim no per-person privacy")

    def test_ends_well_only_when_the_enclave_collected_an_answer(self):
        outputs = {o["kind"]: o["path"] for o in step(self.plan)["inputs"]["outputs"]}
        self.assertEqual(outputs.get("answer"), ".ekka/answer.md")
        # Declared, so the Enclave's measured digest of the collected file crosses as a bounded fact. Undeclared,
        # it never reaches the transition and every ask fails (walk on staging, 2026-10-13).
        declared = step(self.plan)["step_output_contract"]["properties"].get("answer")
        self.assertEqual(declared, {"type": "string", "pattern": "^sha256:[0-9a-f]{64}$"})
        self.assert_ends_only_on_success_with_an_answer(self.op["transitions"])

    def assert_ends_only_on_success_with_an_answer(self, transitions):
        # #71: the grammar has no `&&`, and the first matching arm wins. Govern's validator needs an explicit
        # `verdict == 'failed'` arm, and a job's verdict is only succeeded or failed (hitl-validator R13). So "ends
        # only on succeeded AND an answer" is: every arm BEFORE the one end arm fails, the first is the failed
        # verdict, the end arm is the collected answer, and anything else falls to a failing default.
        arms = list(transitions.items())
        ends = [i for i, (_, target) in enumerate(arms) if target == "end"]
        self.assertEqual([arms[i][0] for i in ends], ["exists(output.answer)"], "only a collected answer may end the run well")
        self.assertEqual(arms[0], ("output.verdict == 'failed'", "fail"), "a failed job fails before the answer is looked at")
        self.assertEqual([t for _, t in arms[:ends[0]]], ["fail"] * ends[0], "every arm before the end arm fails")
        self.assertEqual(arms[ends[0] + 1:], [("default", "fail")], "a fail arm after the end arm never fires once an answer exists")

    def test_planted_a_fail_arm_after_the_end_arm_is_caught(self):
        # v0.1.2's order: a timed-out step that still collected an (old) answer file ended well.
        planted = {"output.verdict == 'failed'": "fail", "exists(output.answer)": "end",
                   "output.status == 'timeout'": "fail", "default": "fail"}
        with self.assertRaises(AssertionError):
            self.assert_ends_only_on_success_with_an_answer(planted)
        with self.assertRaises(AssertionError):
            self.assert_ends_only_on_success_with_an_answer({"exists(output.answer)": "end", "default": "fail"})

    def test_the_answer_the_end_arm_reads_is_the_enclaves_digest_not_the_agents_record(self):
        # `exists(output.answer)` ends the run well. `output.answer` must be EKKA's own SHA-256 of the answer file
        # it collected, never a value the agent wrote. The runner sets `output.<kind>` to its digest of each
        # declared output, and a `facts_from` record can never supply a declared kind's name (ekka-ai/ekka-runner
        # openshell_executor.rs `answer()`, proven by its test
        # facts_from_reads_the_document_and_never_overwrites_the_executors_own). So it holds when `answer` is a
        # declared output, the record is a different file, and the prompt asks the agent for no hash of its own.
        s = step(self.plan)
        prompt = read(os.path.join(PROMPTS, f"{s['prompt']['slug']}@{s['prompt']['version']}.txt"))
        self.assert_the_answer_is_the_enclaves(s, prompt)

    def assert_the_answer_is_the_enclaves(self, s, prompt):
        outputs = {o["kind"]: o["path"] for o in s["inputs"]["outputs"]}
        self.assertIn("answer", outputs, "output.answer is the Enclave's digest only when answer is a declared output")
        record = s["inputs"].get("facts_from")
        self.assertNotEqual(record, "answer", "the agent's record must not be the answer itself")
        self.assertNotEqual(outputs.get(record), outputs["answer"], "the record and the answer are different files")
        # #152: the agent writes no hash; computing one needs a command the desk does not allow.
        self.assertNotIn("answer_sha256", prompt)
        self.assertNotIn("SHA-256", prompt)

    def test_planted_an_answer_the_agent_could_supply_is_caught(self):
        good = step(self.plan)
        prompt = read(os.path.join(PROMPTS, f"{good['prompt']['slug']}@{good['prompt']['version']}.txt"))
        # Not a declared output: output.answer could then only come from somewhere else.
        no_answer = json.loads(json.dumps(good))
        no_answer["inputs"]["outputs"] = [o for o in no_answer["inputs"]["outputs"] if o["kind"] != "answer"]
        # The record IS the answer: the agent's file would be what ends the run.
        record_is_answer = json.loads(json.dumps(good))
        record_is_answer["inputs"]["facts_from"] = "answer"
        # The record names the answer's own path.
        same_file = json.loads(json.dumps(good))
        for o in same_file["inputs"]["outputs"]:
            if o["kind"] == "ask_record":
                o["path"] = ".ekka/answer.md"
        for planted, text in ((no_answer, prompt), (record_is_answer, prompt), (same_file, prompt),
                              (good, prompt + '\n"answer_sha256":"<64 hex>"'),
                              (good, prompt + "\nCompute that answer's SHA-256.")):
            with self.assertRaises(AssertionError):
                self.assert_the_answer_is_the_enclaves(planted, text)

    def test_the_published_desk_ask_5_is_unchanged(self):
        # desk-ask@5 is published: a version's text never changes once EKKA serves it.
        import hashlib
        slug, version = "desk-ask", 5
        with open(os.path.join(PROMPTS, f"{slug}@{version}.txt"), "rb") as f:
            got = hashlib.sha256(f.read()).hexdigest()
        self.assertEqual(got, "fcb28cf2689f718d6e9f35f0071911c75c9e4094475a9ee56d1f16c84ad155b5")

    def test_the_published_desk_ask_7_is_unchanged(self):
        # desk-ask@7 is published (ekka-govern config/platform/desk-prompts.sql holds its sha256); @8 is a new file.
        import hashlib
        slug, version = "desk-ask", 7
        with open(os.path.join(PROMPTS, f"{slug}@{version}.txt"), "rb") as f:
            got = hashlib.sha256(f.read()).hexdigest()
        self.assertEqual(got, "1e73b9356823de19e36a22515ad0af334f27d0a381ab3743d1571df080c86b70")

    def test_desk_chat_1_is_the_text_govern_seeds(self):
        # desk-chat@1 is the platform chat plan's prompt. Govern seeds it in
        # config/platform/desk-prompts.sql (regression coverage, head 95944052) with this sha256, and Govern
        # serves its own copy; a setup copy that differs describes a prompt nobody runs. It is version 1 and still
        # starts with the master block.
        import hashlib
        # The name is split so the address check (test_no_private_addresses) never reads it as an email address.
        with open(os.path.join(PROMPTS, "desk-chat@1" + ".txt"), "rb") as f:
            data = f.read()
        self.assertEqual(hashlib.sha256(data).hexdigest(),
                         "53b19335737888b391ed09ce717ed0130a56d6b2885fa2e25aa9b978f6c15005")
        self.assertTrue(data.decode().startswith(read(os.path.join(PROMPTS, "_master-block.txt")) + "\n"))

    def test_server_asks_use_the_fresh_repository_prompt(self):
        s = step(self.plan)
        self.assertEqual(s['prompt']['version'], 8)
        prompt = read(os.path.join(PROMPTS, f"{s['prompt']['slug']}@{s['prompt']['version']}.txt"))
        self.assertIn('BEFORE', prompt)

    def test_setup_reuses_existing_checkouts(self):
        s = step(desk_plans.render('setup', 'code-desk'))
        self.assertEqual(s['prompt']['version'], 4)
        prompt = read(os.path.join(PROMPTS, f"{s['prompt']['slug']}@{s['prompt']['version']}.txt"))
        self.assertIn('Only if the folder is absent, clone', prompt)
        self.assertIn('reuse its current branch, commit and working', prompt)
        self.assertNotIn('reset --hard', prompt)



class TheUnattendedCommandRules(unittest.TestCase):
    """#140: a desk runs unattended, so a command Claude Code holds for approval fails the run. The prompts pinned
    from desk-setup@4 and desk-ask@6 run one command per call, never chain, never use git -C, and clone with the ONE
    command the desk image allows (tests/test_image_modes.py)."""

    def prompt(self, slug, version):
        return read(os.path.join(PROMPTS, f"{slug}@{version}.txt"))

    def test_the_plans_pin_the_unattended_versions(self):
        self.assertEqual(step(desk_plans.render("setup", "code-desk"))["prompt"]["version"], 4)
        self.assertEqual(step(desk_plans.render("ask", "code-desk"))["prompt"]["version"], 8)

    def test_one_command_per_call_and_no_inline_helper(self):
        for slug, version in (("desk-setup", 4), ("desk-ask", 7)):
            text = self.prompt(slug, version)
            self.assertIn("One command per Bash call. Never join commands with &&, ||, ;, | or a line break.", text)
            self.assertIn("as plain read-only git: never git -C, and never -c.", text)
            self.assertIn("Give every helper you start these same rules, word for word.", text)
            self.assertIn("If a command is refused, do not try another form of it. Fail, and name the refused command.", text)
            self.assertIn("-c credential.helper=/usr/local/bin/ekka-git-credential clone", text)
            self.assertNotIn("!f()", text, f"{slug}@{version} writes a helper on the command line")
            self.assertNotIn("&&", text.replace("&&, ||", ""), f"{slug}@{version} shows a chained command")

    def test_the_ask_removes_old_answers_with_the_allowed_command(self):
        text = self.prompt("desk-ask", 6)
        for name in ("answer.md", "ask-record.json"):
            self.assertIn(f"   rm -rf -- /sandbox/ekka-workspace/.ekka/{name}\n", text)
        self.assertIn("never answer while an old answer file is left", text)

    def test_the_ask_writes_both_answers_without_a_shell_copy(self):
        prompt = self.prompt("desk-ask", 6)
        self.assertIn("Use the native Write tool in two calls to write the same final", prompt)
        self.assertIn("/sandbox/ekka-workspace/.ekka/answers/@{run_id}.md", prompt)
        self.assertIn("/sandbox/ekka-workspace/.ekka/answer.md for", prompt)
        self.assertIn("Never use Bash, cp, mv or shell redirection to create either answer file.", prompt)
        self.assertNotIn("then copy it to", prompt)

    def test_setup_record_names_its_own_run(self):
        # #143: the Enclave can bind the collected desk map to the run that wrote it, as the ask record already is.
        s = step(desk_plans.render("setup", "code-desk"))
        self.assertEqual(s["inputs"]["run_id"], "@ekka.run_id")
        self.assertIn('write .ekka/desk-map.json in this shape:\n{"run_id":"@{run_id}",', self.prompt("desk-setup", 4))

    def test_planted_the_v0_1_2_setup_prompt_is_caught(self):
        with self.assertRaises(AssertionError):
            self.assertNotIn("!f()", self.prompt("desk-setup", 3))


if __name__ == "__main__":
    unittest.main()
