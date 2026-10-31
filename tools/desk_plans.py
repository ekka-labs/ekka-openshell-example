"""The example plans for one desk: setup and ask, from the templates in ekka/plans/.

    python3 tools/desk_plans.py <desk> <out-dir> [<agent id>]

A job step names its desk once, as a literal `resource: desks/<name>`; EKKA refuses a desk taken from a plan
input. So each desk gets its own copy of each plan, made here from one template, and a second desk is set up
and asked through its own plans. A plan code allows only letters and digits in each segment, so the desk name
is written in camelCase there: code-desk becomes example.desk.ask.codeDesk.v1.

Writes <out-dir>/plan-setup-<desk>.json and <out-dir>/plan-ask-<desk>.json, and prints each plan code.

    python3 tools/desk_plans.py --admin <api gate> <out-dir> [<agent id>]

The seven desk administration plans of an organization (ADK-E5): `ekka desk create`, `delete`, `stop`, `start`,
`allow`/`deny`/`policy set`, `disconnect` and `status` each run one of them, by name (ekka.desk.<op>.v1). Each is
one call through the Enclave's API gate to the backend adapter on `apis/desks`, may be run only by an organization
admin, and reads two facts from the adapter's answer: `outcome` and `desk_ready`. Unlike the example plans the
desk is an input, so one set serves every desk. Writes <out-dir>/plan-desk-<op>.json, and prints each plan code.
Standard library only.
"""
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLANS = ("setup", "ask")
DESK = re.compile(r"^[a-z0-9][a-z0-9._-]{0,62}$")   # EKKA's desks/<name>


def desk_code(desk):
    """The desk name as one plan-code segment: code-desk -> codeDesk, 2nd.desk -> desk2ndDesk."""
    if not DESK.match(desk):
        raise ValueError(f"'{desk}' is not a desk name: lowercase letters, digits, '.', '_' or '-', starting with "
                         "a letter or digit, at most 63 characters")
    parts = [p for p in re.split(r"[._-]", desk) if p]
    if not parts:
        raise ValueError(f"'{desk}' has no letters or digits to name a plan with")
    if parts[0][0].isdigit():
        parts.insert(0, "desk")
    return parts[0] + "".join(p[0].upper() + p[1:] for p in parts[1:])


def _fill(value, desk, code):
    if isinstance(value, str):
        return value.replace("{{desk}}", desk).replace("{{deskCode}}", code)
    if isinstance(value, list):
        return [_fill(v, desk, code) for v in value]
    if isinstance(value, dict):
        return {k: _fill(v, desk, code) for k, v in value.items()}
    return value


def render(plan, desk, agent=None):
    """One plan for one desk, from its template; `agent` sets the owning agent."""
    with open(os.path.join(ROOT, "ekka", "plans", f"{plan}.json")) as f:
        d = _fill(json.load(f), desk, desk_code(desk))
    if "{{" in json.dumps(d):
        raise ValueError(f"ekka/plans/{plan}.json has a placeholder this tool does not fill")
    if agent:
        d["plan"]["agent"] = agent
    return d


# The desk administration plans: one table, the inputs exactly as `ekka desk <op>` sends them (ADK-C3).
# (op, name, gate access, method, route, inputs). A route names its desk and provider as path values, which
# Govern fills at dispatch as ONE safe segment each: a value with '/', '..' or '?' refuses the run.
ADMIN_PLANS = (
    ("create", "Create a desk", "write", "POST", "/v1/desks", ("body", "body_sha256")),
    ("delete", "Delete a desk", "write", "DELETE", "/v1/desks/@input.desk", ("desk", "body_sha256")),
    ("stop", "Stop a desk", "write", "POST", "/v1/desks/@input.desk/stop", ("desk", "body_sha256")),
    ("start", "Start a stopped desk", "write", "POST", "/v1/desks/@input.desk/start", ("desk", "body_sha256")),
    ("policy", "Replace a desk's whole network policy", "write", "PUT", "/v1/desks/@input.desk/policy",
     ("desk", "body", "body_sha256")),
    ("detach", "Remove a provider from a desk", "write", "DELETE",
     "/v1/desks/@input.desk/providers/@input.provider", ("desk", "provider", "body", "body_sha256")),
    ("status", "Read a desk", "read", "GET", "/v1/desks/@input.desk", ("desk",)),
)
ADMIN_INPUTS = {
    "desk": "The desk's name.",
    "provider": "The provider's name.",
    "body": "The exact request body, a JSON object, as the CLI built it.",
    "body_sha256": "The SHA-256 of the body as the API gate encodes it, lowercase hex. With no body, the SHA-256 of "
                   "zero bytes, so a body slipped in is refused.",
}
# What a plan reads from the adapter's answer. `running` is left out on purpose: the adapter answers it only at its
# deadline, as an HTTP 202, and a fact outside the enum fails the step. Not `required`: Govern refuses a required
# field the API gate does not promise, and a fact that is missing fails the run through the transitions' default.
ADMIN_FACTS = {
    "type": "object",
    "properties": {
        "outcome": {"type": "string", "enum": ["ready", "refused", "unknown"]},
        "desk_ready": {"type": "boolean"},
    },
}
API_GATE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")   # Govern's gate instance id
ADMIN_USAGE = "python3 tools/desk_plans.py --admin <api gate> <out-dir> [<agent id>]"


def admin_plan(op, api_gate, agent=None):
    """One desk administration plan, ekka.desk.<op>.v1, calling the adapter through `api_gate`."""
    if not API_GATE.match(api_gate or ""):
        raise ValueError(f"'{api_gate}' is not an API gate instance id, for example enclave3a7e592fApi")
    rows = {r[0]: r for r in ADMIN_PLANS}
    if op not in rows:
        raise ValueError(f"'{op}' is not a desk administration plan; there are: {', '.join(rows)}")
    _, name, access, method, route, inputs = rows[op]
    call = {"resource": "apis/desks", "key": route}
    # The API gate's own fields, as its action contract names them. `body_sha256` is an input Govern pins into the
    # token, not a gate field, so it is bound but not listed here.
    contract = {"resource": {"type": "string"}, "key": {"type": "string"}}
    if access == "write":
        call["value"] = {"method": method, **({"body": "@input.body"} if "body" in inputs else {})}
        call["body_sha256"] = "@input.body_sha256"
        contract["value"] = {"type": "object"}
        contract["body_sha256"] = {"type": "string"}
    else:
        call["expect"] = {"status": 200}
        contract["expect"] = {"type": "object"}
    plan = {
        "plan": {"code": f"desk.{op}.v1", "name": name},
        "definition": {
            "schema_version": "ekka.plan.v2",
            "requires_role": "admin",
            "inputs": {i: ADMIN_INPUTS[i] for i in inputs},
            "operations": [{
                "id": "desk",
                "display_name": f"{name}, through the backend adapter",
                "execution": {"preferred": {"mode": "async", "runtime": "node"}},
                "max_visits": 1,
                "steps": [{
                    "id": "call",
                    "action_ref": "ekka.gate.api.v1",
                    "target": api_gate,
                    "op": access,
                    "feature": f"api.{access}",
                    "identity": {"requires_user_context": True},
                    "action_input": {},
                    "inputs": call,
                    "step_input_contract": {"type": "object", "properties": contract, "required": sorted(contract)},
                    "step_output_contract": ADMIN_FACTS,
                }],
                "transitions": {"output.outcome == 'ready'": "end", "default": "fail"},
            }],
        },
    }
    if agent:
        plan["plan"]["agent"] = agent
    return plan


def main_admin(argv):
    if len(argv) not in (2, 3):
        print(ADMIN_USAGE, file=sys.stderr)
        return 2
    api_gate, out = argv[0], argv[1]
    agent = argv[2] if len(argv) == 3 else None
    try:
        for op, *_ in ADMIN_PLANS:
            d = admin_plan(op, api_gate, agent)
            path = os.path.join(out, f"plan-desk-{op}.json")
            with open(path, "w") as f:
                json.dump(d, f, indent=2)
            print(f"{path} ekka.{d['plan']['code']}")
    except ValueError as e:
        print(f"REFUSED: {e}", file=sys.stderr)
        return 2
    return 0


def main(argv):
    if argv[:1] == ["--admin"]:
        return main_admin(argv[1:])
    if len(argv) not in (2, 3):
        print(__doc__.strip().splitlines()[2].strip(), file=sys.stderr)
        return 2
    desk, out = argv[0], argv[1]
    agent = argv[2] if len(argv) == 3 else None
    try:
        for plan in PLANS:
            d = render(plan, desk, agent)
            path = os.path.join(out, f"plan-{plan}-{desk}.json")
            with open(path, "w") as f:
                json.dump(d, f, indent=2)
            print(f"{path} ekka.{d['plan']['code']}")
    except ValueError as e:
        print(f"REFUSED: {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
