"""Every OpenShell provider profile in this repository enforces each endpoint it declares.

An endpoint without `enforcement: enforce` is audit-only in OpenShell: a violation is logged and the traffic goes
through. On the OpenShell gateway a provider registered that way
contributes an audit rule for api.anthropic.com, and OpenShell then refuses every sandbox whose profile enforces the
same host ("overlap on port(s) 443 with conflicting metadata: enforcement=1 vs 2"). Where nothing else covers the
host, the sandbox would run audit-only instead. Offline; standard library only.
"""

import os
import re
import subprocess
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def profiles():
    out = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.split()
    return [p for p in out if re.search(r"(^|/)providers/[^/]+\.ya?ml$", p)]


def endpoints(text):
    """Each endpoint item of the top-level `endpoints:` list, as its own text (block or flow style)."""
    items, inside, current, indent = [], False, None, None
    for line in text.splitlines():
        if re.match(r"^endpoints:\s*$", line):
            inside = True
            continue
        if inside and re.match(r"^\S", line):
            break
        if not inside or not line.strip() or line.strip().startswith("#"):
            continue
        m = re.match(r"^(\s*)-\s", line)
        if m and indent is None:
            indent = len(m.group(1))
        # Only an item at the endpoints list's own indent starts an endpoint; a deeper `- allow:` is one of its rules.
        if m and len(m.group(1)) == indent:
            if current is not None:
                items.append(current)
            current = line
        elif current is not None:
            current += "\n" + line
    if current is not None:
        items.append(current)
    return items


def unenforced(text):
    return [e for e in endpoints(text) if not re.search(r"\benforcement:\s*enforce\b", e)]


class ProviderProfiles(unittest.TestCase):
    def test_every_endpoint_of_every_provider_profile_is_enforced(self):
        found = profiles()
        self.assertTrue(found, "no provider profile found; the check would pass vacuously")
        for path in found:
            with open(os.path.join(ROOT, path)) as h:
                text = h.read()
            self.assertTrue(endpoints(text), f"{path}: no endpoints parsed")
            self.assertEqual(unenforced(text), [], f"{path}: an endpoint without `enforcement: enforce` is audit-only")

    def test_nested_rules_belong_to_their_endpoint(self):
        text = ("id: x\nendpoints:\n  - host: a.example\n    port: 443\n    enforcement: enforce\n    rules:\n"
                "      - allow: { method: GET, path: \"**\" }\n  - host: b.example\n    port: 443\nbinaries: [/x]\n")
        self.assertEqual(len(endpoints(text)), 2)
        self.assertEqual([e.splitlines()[0].strip() for e in unenforced(text)], ["- host: b.example"])

    def test_the_check_refuses_an_audit_only_endpoint_in_either_style(self):
        block = "id: x\nendpoints:\n  - host: a.example\n    port: 443\n  - host: b.example\n    port: 443\n    enforcement: enforce\nbinaries: [/x]\n"
        self.assertEqual(len(unenforced(block)), 1)
        flow = "endpoints:\n  - {host: a.example, port: 443, enforcement: audit}\n  - {host: b.example, port: 443, enforcement: enforce}\n"
        self.assertEqual(len(unenforced(flow)), 1)


if __name__ == "__main__":
    unittest.main()
