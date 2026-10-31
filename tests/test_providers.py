"""providers/ holds NVIDIA's profiles, with the JSON `ekka desk profile add` takes. Changed only where
PIN.json says why: the gateway binds a key to every host in its profile, so a profile names only
hosts its key's own vendor runs."""
import hashlib
import json
import os
import unittest

HERE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "providers")


# The hosts a key may reach, per profile. A new host is a decision, not an edit: the key is valid there.
VENDOR_HOSTS = {
    "claude-code": {"api.anthropic.com"},
    "codex": {"api.openai.com", "auth.openai.com", "chatgpt.com", "ab.chatgpt.com"},
    "github": {"api.github.com", "github.com"},
}


class ProvidersMatchTheirPin(unittest.TestCase):
    def test_every_file_matches_pin_and_nothing_is_extra(self):
        with open(os.path.join(HERE, "PIN.json")) as f:
            pin = json.load(f)
        on_disk = sorted(n for n in os.listdir(HERE) if n.endswith((".yaml", ".json")) and n != "PIN.json")
        self.assertEqual(on_disk, sorted(pin["files"]))
        for name, want in pin["files"].items():
            with open(os.path.join(HERE, name), "rb") as f:
                self.assertEqual(hashlib.sha256(f.read()).hexdigest(), want, f"{name} differs from PIN.json")

    def test_each_yaml_has_its_json(self):
        for name in os.listdir(HERE):
            if name.endswith(".yaml"):
                with open(os.path.join(HERE, name[:-5] + ".json")) as f:
                    self.assertEqual(json.load(f)["id"], name[:-5])

    def test_a_key_reaches_only_its_own_vendor(self):
        for name in os.listdir(HERE):
            if name.endswith(".json") and name != "PIN.json":
                with open(os.path.join(HERE, name)) as f:
                    d = json.load(f)
                hosts = {e["host"] for e in d["endpoints"]}
                self.assertEqual(hosts, VENDOR_HOSTS[d["id"]], f"{d['id']}: its key would be valid on every host listed")


if __name__ == "__main__":
    unittest.main()
