"""Public cloning needs no token; the shipped wall still excludes push and other programs."""
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'tools'))
import desk_grant


class PublicGitPolicy(unittest.TestCase):
    def setUp(self):
        self.policy = json.loads((ROOT / 'desk/policy.json').read_text())

    def test_only_git_has_smart_http_read_routes(self):
        rules = self.policy['networkPolicies']
        self.assertEqual(list(rules), ['github_clone'])
        rule = rules['github_clone']
        self.assertEqual(rule['binaries'], [{'path': '/usr/bin/git'}])
        self.assertEqual(rule['endpoints'], [{
            'host': 'github.com', 'port': 443, 'protocol': 'rest',
            'enforcement': 'NETWORK_ENFORCEMENT_MODE_ENFORCE',
            'rules': [{'allow': {'method': 'GET', 'path': '/**/info/refs*'}},
                      {'allow': {'method': 'POST', 'path': '/**/git-upload-pack'}}]}])
        self.assertEqual(desk_grant.baseline_hosts(self.policy), ['github.com'])
