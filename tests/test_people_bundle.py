"""The people bundle's desk image is the built image's content ID, the value the desks grant pins.

`ekka desk create` reads `desk.image` from the desk backend config install.sh writes on a person's computer. The
runner refuses a tag, and the desks grant (up.sh step_desk_grant) pins the image by its content ID, so the bundle
names that same ID: a create then asks for exactly what the grant allows (regression review). The real
stage_people runs here, with docker and the Keycloak lookup stubbed.
"""
import json
import os
import re
import subprocess
import tempfile
import unittest

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
CONTENT_ID = "sha256:" + "4f" * 32


def shell_function(path, name):
    with open(os.path.join(ROOT, path), encoding="utf-8") as f:
        return re.search(rf"^{name}\(\) \{{.*?^\}}\n", f.read(), re.S | re.M).group(0)


def bundle(inspect_answer, server=False):
    """Run stage_people with `docker image inspect` answering `inspect_answer` (None: the image is not built)."""
    state, t = tempfile.mkdtemp(), tempfile.mkdtemp()
    with open(os.path.join(state, "desks.env"), "w") as f:
        f.write("DESKS_NAME=desks.example.com\n")
    open(os.path.join(t, "ca.crt"), "w").close()
    docker = (f'docker() {{ [[ "$1 $2" == "image inspect" ]] && echo {inspect_answer}; }}' if inspect_answer
              else 'docker() { return 1; }')
    script = "\n".join([
        "set -euo pipefail", f"DESKS_STATE={state}; T={t}; DESKS_NAME=desks.example.com",
        'say() { echo "say: $*"; }', 'die() { echo "die: $*" >&2; exit 1; }',
        "stage_enclave_subject() { echo 9a8b7c6d-0000-4000-8000-000000000001; }", docker,
        shell_function("deploy/setup.sh", "desk_backend_json"),
        shell_function("deploy/setup.sh", "stage_people"), "stage_people"])
    r = subprocess.run(["/bin/bash", "-c", script], cwd=os.path.join(ROOT, "deploy"), capture_output=True, text=True,
                       timeout=30)
    path = os.path.join(state, "enclave-cli", "backend.json") if server else os.path.join(state, "people", "install.sh")
    from pathlib import Path
    text = Path(path).read_text() if os.path.exists(path) else ""
    return r, text


class TheDeskImage(unittest.TestCase):
    def test_server_defaults_are_public_and_use_the_read_only_person_client(self):
        r, text = bundle(CONTENT_ID, server=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        b = json.loads(text)
        self.assertEqual(set(b), {'gateway', 'url', 'ca_pem_path', 'workspace', 'enclave_subject', 'desk', 'oidc', 'tls_domain'})
        self.assertEqual(b['url'], 'https://127.0.0.1:8080')
        self.assertEqual(b['tls_domain'], 'desks.example.com')
        self.assertEqual(b['desk'], {'image': CONTENT_ID, 'policy_file': '/var/lib/ekka/.ekka/desk-policy.json'})
        self.assertEqual(b['ca_pem_path'], '/var/lib/ekka/.ekka/desks-ca.pem')
        self.assertEqual(b['oidc'], {'issuer': 'https://desks.example.com/realms/ekka-desks',
                                    'client_id': 'ekka-cli', 'audience': 'ekka-desks'})

    def test_generated_person_installer_still_expands_home_and_workspace(self):
        r, text = bundle(CONTENT_ID)
        self.assertEqual(r.returncode, 0, r.stderr)
        with tempfile.TemporaryDirectory() as folder:
            from pathlib import Path
            root = Path(folder)
            (root/'install.sh').write_text(text)
            (root/'desks-ca.pem').write_text('public CA')
            (root/'desk-policy.json').write_text('{}')
            bin_dir = root/'bin'; bin_dir.mkdir()
            ekka = bin_dir/'ekka'
            ekka.write_text('#!/usr/bin/env python3\nimport sys,os,pathlib\n'
                            'assert sys.argv[1:4] == ["desk","backend","set"]\n'
                            'pathlib.Path(os.environ["CAPTURE"]).write_text(pathlib.Path(sys.argv[4]).read_text())\n')
            ekka.chmod(0o755)
            env = dict(os.environ, EKKA_HOME=str(root/'person home'), CAPTURE=str(root/'captured.json'),
                       PATH=str(bin_dir)+os.pathsep+os.environ['PATH'])
            out = subprocess.run(['/bin/bash',str(root/'install.sh'),'chosen-workspace'],env=env,
                                 capture_output=True,text=True,timeout=30)
            self.assertEqual(out.returncode,0,out.stderr)
            b=json.loads((root/'captured.json').read_text())
            self.assertEqual(b['workspace'],'chosen-workspace')
            self.assertEqual(b['ca_pem_path'],str(root/'person home'/'desks-ca.pem'))
            self.assertEqual(b['desk']['policy_file'],str(root/'person home'/'desk-policy.json'))

    def test_the_bundle_names_the_built_image_by_its_content_id(self):
        r, text = bundle(CONTENT_ID)
        self.assertEqual(r.returncode, 0, r.stderr)
        backend = json.loads(re.search(r"<<JSON\n(.*?)\nJSON\n", text, re.S).group(1).replace("\\$", "$"))
        self.assertEqual(backend["desk"]["image"], CONTENT_ID, "the value the desks grant pins")

    def test_planted_a_tag_never_reaches_the_bundle(self):
        r, text = bundle(CONTENT_ID)
        self.assertNotIn("ekka-desk:local", text, "a tag: the runner refuses it and the grant does not pin it")

    def test_planted_an_unbuilt_image_stops_the_bundle_and_says_why(self):
        r, text = bundle(None)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("the desk image ekka-desk:local is not built", r.stderr)
        self.assertEqual(text, "", "no bundle that names nothing")

    def test_planted_an_answer_that_is_not_a_content_id_is_refused(self):
        r, _ = bundle("ekka-desk:local")
        self.assertNotEqual(r.returncode, 0)


if __name__ == "__main__":
    unittest.main()
