"""The desk image: built from desk/image alone, with the one published set of managed settings."""
import json
import hashlib
from pathlib import Path
import re
import os
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent.parent


class ImageModes(unittest.TestCase):
    def test_server_image_allows_exactly_the_published_clone_and_answer_writes(self):
        # #140: the native agent held `git remote get-url origin` on the real server walk.
        # Permit that exact read, not arbitrary Git options or a broad read-command pattern.
        lines = {clone_line('desk-setup', 4), clone_line('desk-ask', 7)}
        self.assertEqual(len(lines), 1, 'setup and ask clone with the same one command')
        settings = json.loads((ROOT / 'external/desk-base/desk/image/managed-settings.json').read_text())
        self.assertEqual(settings['permissions'], {'allow': [
            'Edit(//sandbox/ekka-workspace/.ekka/**)',
            'Bash(git remote get-url origin)',
            'Bash(' + lines.pop().replace(' <address> <folder>', ' *') + ')',
            'Bash(rm -rf -- /sandbox/ekka-workspace/*)'], 'deny': RM_DENY})

    def test_the_one_clone_runs_unattended_and_an_inline_helper_never_does(self):
        allow = server_allow()
        clone = clone_line('desk-ask', 7).replace('<address> <folder>', 'https://github.com/o/r r')
        self.assertTrue(allowed(allow, clone))
        refused = [
            # desk-setup@3:31, the shape the tester's run 4981d391 waited on
            "git -c credential.helper='!f() { echo username=x-access-token; echo \"password=$GITHUB_TOKEN\"; }; f' clone https://github.com/o/r r",
            clone.replace('credential.helper=/usr/local/bin/ekka-git-credential', "credential.helper='!f() { echo x; }; f'"),
            clone.replace(' clone ', ' -c core.fsmonitor=/tmp/x clone '),
            clone_line('desk-ask', 7).split(' -c ')[0] + ' -c core.fsmonitor=/tmp/x status',
            'git -C r log',
        ]
        for command in refused:
            self.assertFalse(allowed(allow, command), command)

    def test_origin_read_is_exact_and_cannot_authorize_another_command(self):
        allow = server_allow()
        self.assertTrue(allowed(allow, 'git remote get-url origin'))
        for command in [
            'git remote get-url origin --all',
            'git remote get-url other',
            'git remote set-url origin https://example.com/r',
            'git push origin HEAD',
            'git -C r remote get-url origin',
            'git -c core.fsmonitor=/tmp/x remote get-url origin',
            'git remote get-url origin && git push origin HEAD',
            'git remote get-url origin; git push origin HEAD',
            'git remote get-url origin\ngit push origin HEAD',
        ]:
            self.assertFalse(allowed(allow, command), command)

    def test_every_wildcard_bash_rule_ends_in_its_only_wildcard(self):
        # A `*` before the subcommand matches `-c core.fsmonitor=<program>` too (Claude Code's permissions docs).
        for rule in server_allow():
            if rule.startswith('Bash('):
                if rule == 'Bash(git remote get-url origin)':
                    self.assertNotIn('*', rule)
                    continue
                self.assertEqual(rule.count('*'), 1, rule)
                # A clone takes arguments after `-- `; the one rm takes one folder after the workspace's `/`.
                self.assertTrue(rule.endswith(' *)') or rule == 'Bash(rm -rf -- /sandbox/ekka-workspace/*)', rule)

    def test_planted_reds_are_caught(self):
        inline = "git -c credential.helper='!f() { echo u; }; f' clone https://github.com/o/r r"
        old = server_allow() + ["Bash(git -c credential.helper='!f() { echo u; }; f' clone *)"]
        self.assertTrue(allowed(old, inline), 'PLANTED: the v0.1.2 rule would let the inline helper through')
        wide = server_allow() + ['Bash(git -C * log *)']
        self.assertTrue(allowed(wide, 'git -C r -c core.fsmonitor=/tmp/x log --oneline'), 'PLANTED: a middle wildcard runs a program')
        with self.assertRaises(AssertionError):
            for rule in wide:
                if rule.startswith('Bash('):
                    self.assertEqual(rule.count('*'), 1, rule)

    def test_the_credential_helper_answers_only_github_get(self):
        helper = ROOT / 'external/desk-base/desk/image/ekka-git-credential'
        def ask(action, protocol='https', host='github.com', token='ghp-placeholder'):
            env = {k: v for k, v in os.environ.items() if k != 'GITHUB_TOKEN'}
            if token is not None:
                env['GITHUB_TOKEN'] = token
            return subprocess.run(['sh', str(helper), action], input=f'protocol={protocol}\nhost={host}\n\n',
                                  capture_output=True, text=True, env=env, timeout=10).stdout
        self.assertEqual(ask('get'), 'username=x-access-token\npassword=ghp-placeholder\n')
        for no in (ask('store'), ask('erase'), ask('get', host='evil.example'), ask('get', protocol='http'),
                   ask('get', host='github.com.evil.example'), ask('get', token=None), ask('get', token='')):
            self.assertEqual(no, '')
        dockerfile = (ROOT / 'external/desk-base/desk/image/Dockerfile').read_text()
        self.assertIn('COPY ekka-git-credential /usr/local/bin/ekka-git-credential', dockerfile)
        self.assertIn('chown root:root /usr/local/bin/ekka-git-credential && chmod 0755', dockerfile)


RM_DENY = [
    'Bash(rm -rf -- /sandbox/ekka-workspace/)',
    'Bash(rm -rf -- /sandbox/ekka-workspace/.)',
    'Bash(rm -rf -- /sandbox/ekka-workspace/.ekka)',
    'Bash(rm -rf -- /sandbox/ekka-workspace/*/*)',
    'Bash(rm -rf -- /sandbox/ekka-workspace/* *)',
    'Bash(rm -rf -- *..*)',
    'Bash(rm -rf -- *$*)',
    'Bash(rm -rf -- *`*)',
]


class OnlyRepositoryCopiesAreRemoved(unittest.TestCase):
    """#161 (design D5): `rm -rf` is narrowed from anything to one immediate repository copy under the workspace.
    A deny rule beats an allow rule in Claude Code, so the collection files cannot also be allowed precisely next to
    a deny on nested paths; desk-ask@7 writes them fresh instead, and EKKA (0.1.116 on) collects only what a run
    wrote."""

    def permitted(self, command, settings=None):
        p = settings or json.loads((ROOT / 'external/desk-base/desk/image/managed-settings.json').read_text())['permissions']
        return allowed(p['allow'], command) and not allowed(p.get('deny', []), command)

    def test_one_repository_copy_is_removed(self):
        for folder in ('Hello-World', 'example-api', '.github', 'my.repo'):
            self.assertTrue(self.permitted(f'rm -rf -- /sandbox/ekka-workspace/{folder}'), folder)

    def test_nothing_else_is_removed(self):
        for command in ('rm -rf -- /sandbox/ekka-workspace/', 'rm -rf -- /sandbox/ekka-workspace/.',
                        'rm -rf -- /sandbox/ekka-workspace/.ekka', 'rm -rf -- /sandbox/ekka-workspace/.ekka/answer.md',
                        'rm -rf -- /sandbox/ekka-workspace/repo/src', 'rm -rf -- /sandbox/ekka-workspace/repo /etc',
                        'rm -rf -- /sandbox/ekka-workspace/../../etc', 'rm -rf -- /sandbox/ekka-workspace/$X',
                        'rm -rf -- /sandbox/ekka-workspace/`id`', 'rm -rf -- /etc', 'rm -rf -- /',
                        'rm -rf /sandbox/ekka-workspace/repo', 'rm -rf -- /sandbox/ekka-workspace'):
            self.assertFalse(self.permitted(command), command)

    def test_the_prompt_removes_only_what_the_rules_allow(self):
        ask = json.loads((ROOT / 'ekka/plans/ask.json').read_text())['definition']['operations'][0]['steps'][0]['prompt']
        text = (ROOT / 'prompts/platform' / f"{ask['slug']}@{ask['version']}.txt").read_text()   # the version the plan pins
        self.assertIn('rm -rf -- /sandbox/ekka-workspace/<validated-folder>', text)
        self.assertIn('1. Do not remove anything in .ekka.', text)
        self.assertNotIn('rm -rf -- /sandbox/ekka-workspace/.ekka', text)
        for line in text.splitlines():
            if 'rm -rf' in line and '<folder>' not in line and '<validated-folder>' not in line:
                self.fail(f"desk-ask@{ask['version']} removes something other than a repository copy: {line.strip()}")

    def test_planted_reds_are_caught(self):
        old = {'allow': ['Bash(rm -rf -- *)']}
        self.assertTrue(self.permitted('rm -rf -- /etc', old), 'PLANTED: the v0.1.117 rule removes anything')
        no_deny = {'allow': ['Bash(rm -rf -- /sandbox/ekka-workspace/*)']}
        self.assertTrue(self.permitted('rm -rf -- /sandbox/ekka-workspace/repo /etc', no_deny),
                        'PLANTED: without the deny list a second argument escapes')
        self.assertTrue(self.permitted('rm -rf -- /sandbox/ekka-workspace/.ekka', no_deny),
                        'PLANTED: without the deny list the collection folder goes')


def clone_line(slug, version):
    prompt = f'{slug}@{version}.txt'
    text = (ROOT / 'prompts/platform' / prompt).read_text()
    lines = [line.strip() for line in text.splitlines() if 'clone --template=' in line]
    assert len(lines) == 1, prompt
    return lines[0]


def server_allow():
    return json.loads((ROOT / 'external/desk-base/desk/image/managed-settings.json').read_text())['permissions']['allow']


def allowed(rules, command):
    """Claude Code's Bash rule match for one simple command: `*` is any text, and a trailing ` *` also
    matches the bare command (code.claude.com/docs/en/permissions, Wildcard patterns)."""
    for rule in rules:
        if not rule.startswith('Bash('):
            continue
        pattern = rule[5:-1]
        regex = re.escape(pattern).replace(r'\*', '.*')
        if re.fullmatch(regex, command, re.S) or (pattern.endswith(' *') and command == pattern[:-2]):
            return True
    return False
