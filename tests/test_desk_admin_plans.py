"""The seven desk administration plans of an organization .

`ekka desk <op>` runs one of them by name: one call through the Enclave's API gate to the backend adapter on
`apis/desks`, run only by an organization admin, reading `outcome` and `desk_ready` from the adapter's answer.
The inputs are the one table the CLI sends (ADK-C3). The routes and the answers are the adapter contract's own,
read from vendor/ekka-contracts (published @ekka-ai/contracts 0.67.0), never restated here.
"""
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
VENDOR = os.path.join(ROOT, "vendor", "ekka-contracts")
sys.path.insert(0, os.path.join(ROOT, "tools"))
import desk_plans  # noqa: E402

GATE = "enclave3a7e592fApi"
OPS = [op for op, *_ in desk_plans.ADMIN_PLANS]
EMPTY_SHA256 = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"

# The one table of plan inputs, as `ekka desk <op>` sends them (ADK-C3), written here from the plan and not read
# from the tool under test.
INPUTS = {
    "create": ["body", "body_sha256"],
    "delete": ["desk", "body_sha256"],
    "stop": ["desk", "body_sha256"],
    "start": ["desk", "body_sha256"],
    "policy": ["desk", "body", "body_sha256"],
    "detach": ["desk", "provider", "body", "body_sha256"],
    "status": ["desk"],
}
# Each plan's operation in the adapter contract.
CONTRACT_OP = {"create": "create", "delete": "delete", "stop": "stop", "start": "start", "policy": "policy_set",
               "detach": "provider_detach", "status": "status"}


def vendored(path):
    with open(os.path.join(VENDOR, path), encoding="utf-8") as f:
        return json.load(f)


def step(plan):
    return plan["definition"]["operations"][0]["steps"][0]


def facts_accept(contract, answer):
    """The plan's fact check on arrival: each declared fact present in a 2xx answer must fit its declaration."""
    for name, decl in contract["properties"].items():
        if name not in answer:
            continue
        value = answer[name]
        if decl.get("type") == "boolean" and not isinstance(value, bool):
            return False
        if decl.get("type") == "string" and not isinstance(value, str):
            return False
        if "enum" in decl and value not in decl["enum"]:
            return False
    return True


class TheVendoredContract(unittest.TestCase):
    def test_is_the_published_files_byte_for_byte(self):
        manifest = vendored("MANIFEST.json")
        self.assertEqual(manifest["version"], "0.67.0")
        for path, digest in manifest["files"].items():
            with open(os.path.join(VENDOR, path), "rb") as f:
                self.assertEqual(hashlib.sha256(f.read()).hexdigest(), digest, path)
        on_disk = sorted(f"backend/samples/{n}" for n in os.listdir(os.path.join(VENDOR, "backend", "samples")))
        self.assertEqual(sorted(p for p in manifest["files"] if p.startswith("backend/samples/")), on_disk,
                         "a sample with no manifest entry, or an entry with no file")


class TheSevenPlans(unittest.TestCase):
    def setUp(self):
        self.plans = {op: desk_plans.admin_plan(op, GATE, "agent-1") for op in OPS}

    def test_are_named_as_the_cli_runs_them(self):
        self.assertEqual(OPS, list(INPUTS))
        for op, plan in self.plans.items():
            self.assertEqual(plan["plan"]["code"], f"desk.{op}.v1", "the CLI runs ekka.desk.<op>.v1")
            self.assertEqual(plan["plan"]["agent"], "agent-1")

    def test_take_exactly_the_inputs_the_cli_sends(self):
        for op, plan in self.plans.items():
            self.assertEqual(sorted(plan["definition"]["inputs"]), sorted(INPUTS[op]), op)

    def test_are_one_admin_call_on_apis_desks_through_the_api_gate(self):
        for op, plan in self.plans.items():
            d = plan["definition"]
            self.assertEqual(d["requires_role"], "admin", op)
            self.assertEqual(len(d["operations"]), 1, op)
            self.assertEqual(len(d["operations"][0]["steps"]), 1, op)
            s = step(plan)
            self.assertEqual((s["action_ref"], s["target"], s["inputs"]["resource"]),
                             ("ekka.gate.api.v1", GATE, "apis/desks"), op)
            self.assertTrue(s["identity"]["requires_user_context"], f"{op}: a person starts every desk change")

    def test_call_the_contracts_route_with_its_method(self):
        routes = {o["operation"]: o for o in vendored("backend/operations.json")["operations"]}
        for op, plan in self.plans.items():
            route = routes[CONTRACT_OP[op]]
            s = step(plan)
            path = route["path"].replace("{name}", "@input.desk").replace("{provider}", "@input.provider")
            self.assertEqual(s["inputs"]["key"], path, op)
            if route["method"] == "GET":
                self.assertEqual((s["op"], s["feature"]), ("read", "api.read"), op)
                self.assertNotIn("value", s["inputs"], f"{op}: a read sends no method or body")
            else:
                self.assertEqual((s["op"], s["feature"]), ("write", "api.write"), op)
                self.assertEqual(s["inputs"]["value"]["method"], route["method"], op)

    def test_send_a_body_exactly_when_the_route_takes_one(self):
        routes = {o["operation"]: o for o in vendored("backend/operations.json")["operations"]}
        for op, plan in self.plans.items():
            takes_body = routes[CONTRACT_OP[op]]["body"] is not None
            sent = step(plan)["inputs"].get("value", {})
            self.assertEqual("body" in sent, takes_body, op)
            if takes_body:
                self.assertEqual(sent["body"], "@input.body", f"{op}: the exact body the CLI built and hashed")

    def test_pin_every_change_and_never_a_read(self):
        for op, plan in self.plans.items():
            s = step(plan)
            if s["op"] == "write":
                self.assertEqual(s["inputs"]["body_sha256"], "@input.body_sha256", op)
                self.assertIn("body_sha256", s["step_input_contract"]["properties"],
                              f"{op}: plan create refuses a binding its contract does not declare")
            else:
                self.assertNotIn("body_sha256", s["inputs"], op)
        self.assertEqual(hashlib.sha256(b"").hexdigest(), EMPTY_SHA256, "the pin of a change with no body")

    def test_declare_every_binding_and_bind_every_required_field(self):
        for op, plan in self.plans.items():
            s = step(plan)
            contract = s["step_input_contract"]
            self.assertEqual(sorted(s["inputs"]), sorted(contract["properties"]), op)
            self.assertEqual(sorted(contract["required"]), sorted(set(contract["required"]) & set(s["inputs"])), op)
            self.assertEqual(s["action_input"], {}, op)


class TheFacts(unittest.TestCase):
    """EKKA output validation: each plan's facts, checked against the adapter's own answers."""

    def setUp(self):
        self.samples = {n[len("result."):-len(".json")]: vendored(f"backend/samples/{n}")
                        for n in os.listdir(os.path.join(VENDOR, "backend", "samples"))
                        if n.startswith("result.")}   # the answers only: the config, marker and hold samples are not

    def test_are_outcome_and_desk_ready_and_nothing_else(self):
        for op in OPS:
            contract = step(desk_plans.admin_plan(op, GATE))["step_output_contract"]
            self.assertEqual(contract["properties"], {
                "outcome": {"type": "string", "enum": ["ready", "refused", "unknown"]},
                "desk_ready": {"type": "boolean"},
            }, op)
            self.assertNotIn("required", contract, f"{op}: Govern refuses a required field the gate does not promise")

    def test_take_every_answer_a_plan_reaches_and_refuse_running(self):
        for op in OPS:
            contract = step(desk_plans.admin_plan(op, GATE))["step_output_contract"]
            for name, answer in self.samples.items():
                with self.subTest(op=op, sample=name):
                    self.assertEqual(facts_accept(contract, answer), answer["outcome"] != "running",
                                     "ready, refused and unknown cross; running, the 202 at the deadline, fails")
        self.assertIn("running", {a["outcome"] for a in self.samples.values()}, "the running answer is vendored")

    def test_only_ready_ends_the_run_well(self):
        for op in OPS:
            transitions = desk_plans.admin_plan(op, GATE)["definition"]["operations"][0]["transitions"]
            self.assertEqual(transitions, {"output.outcome == 'ready'": "end", "default": "fail"}, op)


class Refused(unittest.TestCase):
    def test_a_gate_that_is_not_an_instance_id(self):
        for bad in ["", "enclave 1", "-x", "../gate"]:
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                desk_plans.admin_plan("stop", bad)

    def test_an_operation_that_is_not_a_plan(self):
        with self.assertRaises(ValueError):
            desk_plans.admin_plan("connect", GATE)


class TheTool(unittest.TestCase):
    def test_writes_the_seven_plans_and_names_each(self):
        out = tempfile.mkdtemp()
        r = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "desk_plans.py"), "--admin", GATE, out, "a-1"],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(sorted(os.listdir(out)), sorted(f"plan-desk-{op}.json" for op in OPS))
        self.assertEqual([line.split()[1] for line in r.stdout.splitlines()], [f"ekka.desk.{op}.v1" for op in OPS])

    def test_refuses_a_bad_gate_by_name(self):
        r = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "desk_plans.py"), "--admin", "bad gate",
                            tempfile.mkdtemp()], capture_output=True, text=True)
        self.assertEqual(r.returncode, 2)
        self.assertIn("REFUSED: 'bad gate' is not an API gate instance id", r.stderr)


if __name__ == "__main__":
    unittest.main()
