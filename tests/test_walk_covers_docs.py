"""Every command the docs show is run by the walk (tools/walk.sh), or declared there as not walked, with a reason.

A command reaches the docs only with a walk that runs it, so no page can show a command nobody has checked.
"""
import os
import re
import unittest

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
DOCS = ["README.md"] + [os.path.join("docs", f) for f in sorted(os.listdir(os.path.join(ROOT, "docs"))) if f.endswith(".md")]

# How many words name a command: `ekka desk policy show` is three after ekka, `ekka desk create` two.
GROUPS = {("desk", "profile"), ("desk", "policy"), ("desk", "access"), ("desk", "backend"), ("gate", "grant")}
SINGLE = {"login", "logout", "whoami"}
WORD = re.compile(r"^[a-z][a-z-]*$")


def command_key(words):
    """`ekka desk create code-desk --policy x` -> 'ekka desk create'."""
    if not words:
        return None
    if words[0] in SINGLE:
        return "ekka " + words[0]
    if len(words) >= 3 and (words[0], words[1]) in GROUPS and WORD.match(words[2]):
        return "ekka " + " ".join(words[:3])
    if len(words) >= 2 and WORD.match(words[1]):
        return "ekka " + " ".join(words[:2])
    return None


def documented():
    found = {}
    for doc in DOCS:
        with open(os.path.join(ROOT, doc), encoding="utf-8") as f:
            text = f.read()
        # commands in code: `...` spans and ```sh blocks, after `ekka`, `./up.sh ekka`, or as `./up.sh <sub>`
        for chunk in re.findall(r"```(?:sh|bash)?\n(.*?)```", text, re.S) + re.findall(r"`([^`\n]+)`", text):
            for line in chunk.splitlines():
                line = line.split("#", 1)[0]
                for m in re.finditer(r"(?:^|[\s|;(])(?:sudo\s+)?(?:\./up\.sh\s+)?ekka\s+([^|;&)]*)", line):
                    key = command_key(m.group(1).split())
                    if key:
                        found.setdefault(key, doc)
                for m in re.finditer(r"\./up\.sh\s+([a-z][a-z-]*)", line):   # desk-plans is one word
                    found.setdefault("./up.sh " + m.group(1), doc)
                if re.search(r"(^|\s)\./install\.sh\b", line):
                    found.setdefault("./install.sh", doc)
    return found


def declared():
    with open(os.path.join(ROOT, "tools", "walk.sh"), encoding="utf-8") as f:
        walk = f.read()
    return set(re.findall(r'(?:covers|not_walked) "([^"]+)"', walk))


class TheWalkCoversTheDocs(unittest.TestCase):
    def test_every_documented_command_is_walked_or_declared(self):
        missing = {k: v for k, v in documented().items() if k not in declared()}
        self.assertEqual(missing, {}, "documented but not in tools/walk.sh (command: first page)")

    def test_the_walk_declares_nothing_the_docs_do_not_show(self):
        extra = declared() - set(documented())
        self.assertEqual(sorted(extra), [], "declared in tools/walk.sh but shown on no page")


if __name__ == "__main__":
    unittest.main()
