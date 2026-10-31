"""Exercise the real server CLI wrapper and public-default bootstrap without a gateway or credentials."""
import json
import os
from pathlib import Path
import pty
import re
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent.parent


def function(name):
    text = (ROOT/'up.sh').read_text()
    return re.search(rf'^{name}\(\) \{{.*?^\}}\n', text, re.S | re.M).group(0)


class ServerCLI(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root/'deploy').mkdir()
        self.log = self.root/'args'
        self.capture = self.root/'copied'
        self.capture.mkdir()
        # Record the actual wrapper's compose invocation. It may copy only public files.
        (self.root/'deploy'/'setup.sh').write_text('''#!/bin/bash
set -eu
printf '%s\\0' "$@" >> "$ARG_LOG"
if [[ $1 == compose && $2 == cp ]]; then
  cp "$3" "$CAPTURE/$(basename "$3")"
elif [[ $1 == compose && $2 == exec ]]; then
  cat > "$CAPTURE/stdin"
fi
''')
        self.env = dict(os.environ, ARG_LOG=str(self.log), CAPTURE=str(self.capture))

    def wrapper(self, args, terminal=False):
        stub = 'enclave_ekka() { printf "%s\\0" "$@" >> "$ARG_LOG"; [[ -t 0 ]] || cat > "$CAPTURE/stdin"; }\n'
        script = 'set -euo pipefail\ndie() { echo "$1" >&2; exit 2; }\n'+stub+function('server_ekka')+'\nserver_ekka "$@"\n'
        argv = ['/bin/bash','-c',script,'wrapper']+args
        if terminal:
            master, slave = pty.openpty()
            try:
                # No input is expected by this fixture's interactive invocation.
                (self.root/'deploy'/'setup.sh').write_text('printf "%s\\0" "$@" > "$ARG_LOG"\n')
                return subprocess.run(argv,cwd=self.root,env=self.env,stdin=slave,stdout=slave,stderr=slave,timeout=10)
            finally:
                os.close(slave); os.close(master)
        return subprocess.run(argv,cwd=self.root,env=self.env,input='public input',capture_output=True,text=True,timeout=10)

    def args(self):
        return self.log.read_bytes().decode().rstrip('\0').split('\0')

    def test_pipe_keeps_stdin_and_disables_tty(self):
        r=self.wrapper(['secret','put','TEST','--stdin'])
        self.assertEqual(r.returncode,0,r.stderr)
        self.assertEqual(self.args(),['secret','put','TEST','--stdin'], 'the ekka words, to the native Enclave as its user')
        self.assertEqual((self.capture/'stdin').read_text(),'public input')

    def test_real_terminal_allows_interactive_desk_login(self):
        r=self.wrapper(['desk','login'],terminal=True)
        self.assertEqual(r.returncode,0)
        self.assertEqual(self.args(),['desk','login'])

    def test_credential_options_are_not_forwarded_or_echoed(self):
        for option in ['--token','--access-token','--refresh-token','--id-token','--password','--client-secret','--api-key','--secret']:
            for args in ([option,'PLANTED_PRIVATE_VALUE'],[option+'=PLANTED_PRIVATE_VALUE']):
                with self.subTest(args=args):
                    r=self.wrapper(['desk','login']+args)
                    self.assertEqual(r.returncode,2)
                    self.assertIn('do not pass a credential',r.stderr)
                    self.assertNotIn('PLANTED_PRIVATE_VALUE',r.stdout+r.stderr)
                    self.assertFalse(self.log.exists(),'compose must never receive the argument')

    def test_token_word_in_a_question_is_not_a_credential_option(self):
        r=self.wrapper(['plan','run','example','--input','question=What does --token mean?'])
        self.assertEqual(r.returncode,0,r.stderr)
        self.assertEqual(self.args()[-1],'question=What does --token mean?')

    def test_bootstrap_copies_no_cached_person_or_secret(self):
        state=self.root/'state'
        people=state/'people'; people.mkdir(parents=True)
        (people/'desks-ca.pem').write_text('PUBLIC_CA')
        (people/'desk-policy.json').write_text('{}')
        (people/'cached-person-token.json').write_text('PLANTED_PRIVATE_TOKEN')
        cli=state/'enclave-cli'; cli.mkdir()
        public={'gateway':'ekka-desks','url':'https://gateway:8080'}
        (cli/'backend.json').write_text(json.dumps(public))
        self.env['DESKS_STATE']=str(state)
        stubs=('ENCLAVE_HOME="$CAPTURE"\nenclave_home_file() { cat > "$CAPTURE/$1"; }\n'
               'ekka_in() { printf "%s\\0" "$@" >> "$ARG_LOG"; }\n')
        r=subprocess.run(['/bin/bash','-c','set -euo pipefail\n'+stubs+function('configure_enclave_cli')+'\nconfigure_enclave_cli'],
                         cwd=self.root,env=self.env,capture_output=True,text=True,timeout=10)
        self.assertEqual(r.returncode,0,r.stderr)
        self.assertEqual({p.name for p in self.capture.iterdir()},{'desks-ca.pem','desk-policy.json','backend.json'})
        self.assertEqual(json.loads((self.capture/'backend.json').read_text()),public)
        self.assertEqual(self.args(),['desk','backend','set',str(self.capture/'backend.json')])
        self.assertNotIn('PLANTED_PRIVATE_TOKEN',''.join(p.read_text() for p in self.capture.iterdir()))


class TheEkkaVerbEnds(unittest.TestCase):
    """`./up.sh ekka <command>` runs the command and EXITS. The native enclave_ekka returns where the container version
    exec'd into compose, and the install sequence after the verb dispatch then ran on the walk box (measured:
    "./up.sh: line 158: MODE: unbound variable" after a successful `whoami`)."""

    def test_up_sh_ekka_exits_after_the_command_and_runs_no_install_step(self):
        with tempfile.TemporaryDirectory() as d:
            for name, body in (("id", "echo 0\n"), ("runuser", 'printf "%s\\n" "$@" > "$LOG"\n')):
                Path(d, name).write_text("#!/bin/bash\n" + body); os.chmod(Path(d, name), 0o755)
            env = dict(os.environ, PATH=d + ":" + os.environ["PATH"], LOG=str(Path(d, "log")), DESKS_STATE=str(Path(d, "state")))
            r = subprocess.run(["bash", str(ROOT/"up.sh"), "ekka", "whoami"], capture_output=True, text=True, env=env, timeout=30, cwd=ROOT)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(r.stderr, "", "no install step spoke")
            self.assertEqual(Path(d, "log").read_text().splitlines()[-1], "whoami")
            # and a failing command's status is the verb's status
            Path(d, "runuser").write_text("#!/bin/bash\nexit 7\n")
            r = subprocess.run(["bash", str(ROOT/"up.sh"), "ekka", "whoami"], capture_output=True, text=True, env=env, timeout=30, cwd=ROOT)
            self.assertEqual(r.returncode, 7)


class TheGrantVerb(unittest.TestCase):
    """`sudo ekka desk grant show|discovery|restore` (ekka-ai/.github#784), run as the real up.sh verb with `id` stubbed
    and a state folder holding the limits the tool writes. `discovery` is driven only to its refusals here: the digest
    step needs the adapter image."""

    def run_verb(self, d, *args):
        env = dict(os.environ, PATH=d + ":" + os.environ["PATH"], DESKS_STATE=str(Path(d, "state")))
        return subprocess.run(["bash", str(ROOT/"up.sh"), "grant", *args], capture_output=True, text=True, env=env, timeout=60, cwd=ROOT)

    def test_show_restore_and_the_refusals(self):
        with tempfile.TemporaryDirectory() as d:
            Path(d, "id").write_text("#!/bin/bash\necho 0\n"); os.chmod(Path(d, "id"), 0o755)
            state = Path(d, "state"); (state/"backend").mkdir(parents=True)
            (state/"up.env").write_text("DESKS_NAME=desks.example.test\nEKKA_BACKEND_IMAGE=ghcr.io/example/backend@sha256:" + "f" * 64 + "\n")
            lim = subprocess.run([sys.executable, str(ROOT/"tools"/"desk_grant.py"), "limits", "--image-id", "sha256:" + "d" * 64,
                                  "--policy-digest", "sha256:" + "e" * 64], capture_output=True, text=True, check=True, cwd=ROOT).stdout
            (state/"backend"/"desks-grant.json").write_text(lim)
            r = self.run_verb(d, "show"); self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("no discovery window: the shipped limits", r.stdout)
            self.assertIn("ports: 443   address ranges: none", r.stdout)
            r = self.run_verb(d, "restore"); self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("no discovery window is open", r.stdout)
            r = self.run_verb(d, "discovery", "--image", "ghcr.io/x/y:latest", "--policy", str(ROOT/"desk"/"policy-discovery.json"))
            self.assertEqual(r.returncode, 1); self.assertIn("a tag can move; a digest cannot", r.stderr)
            r = self.run_verb(d, "discovery", "--image", "ghcr.io/x/y@sha256:" + "a" * 64, "--policy", "/nonexistent.json")
            self.assertEqual(r.returncode, 1); self.assertIn("--policy must name a policy file", r.stderr)
            r = self.run_verb(d, "nonsense"); self.assertEqual(r.returncode, 1); self.assertIn("usage: sudo ekka desk grant", r.stderr)
            self.assertFalse((state/"backend"/"desks-grant.extras.json").exists(), "no refusal left a window behind")


class TheRegistryAndImageVerbs(unittest.TestCase):
    """`sudo ekka desk registry login|show|logout` and `sudo ekka desk image pull` (ekka-ai/.github#784 step 6), as the real
    up.sh verbs with `id` stubbed. Driven to their refusals and to `show`; the login and the pull themselves need a registry."""

    def run_verb(self, d, verb, *args, stdin=None):
        env = dict(os.environ, PATH=d + ":" + os.environ["PATH"], DESKS_STATE=str(Path(d, "state")))
        return subprocess.run(["bash", str(ROOT/"up.sh"), verb, *args], capture_output=True, text=True, env=env, timeout=60, cwd=ROOT,
                              input=stdin, stdin=None if stdin is not None else subprocess.DEVNULL)

    def test_refusals_keep_nothing_and_show_reads_back(self):
        with tempfile.TemporaryDirectory() as d:
            Path(d, "id").write_text("#!/bin/bash\necho 0\n"); os.chmod(Path(d, "id"), 0o755)
            state = Path(d, "state"); (state/"secrets").mkdir(parents=True)
            (state/"up.env").write_text("DESKS_NAME=desks.example.test\n")
            r = self.run_verb(d, "registry", "show"); self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("no registry credential kept", r.stdout)
            r = self.run_verb(d, "registry", "login", "ghcr.io", "someone", stdin="not-a-classic-token")
            self.assertEqual(r.returncode, 1); self.assertIn("needs a classic token", r.stderr)
            r = self.run_verb(d, "registry", "login", "ghcr.io", "someone", stdin="ghp_two words")
            self.assertEqual(r.returncode, 1); self.assertIn("exactly one token", r.stderr)
            r = self.run_verb(d, "registry", "login", "bad host", "someone", stdin="x"); self.assertEqual(r.returncode, 1); self.assertIn("usage", r.stderr)
            self.assertEqual(list((state/"secrets").glob("registry/*")), [], "nothing kept after a refusal")
            self.assertEqual([p.name for p in (state/"secrets").iterdir()], [], "no throwaway folder left behind")
            r = self.run_verb(d, "image", "pull", "ghcr.io/x/y:latest"); self.assertEqual(r.returncode, 1); self.assertIn("a tag can move", r.stderr)
            r = self.run_verb(d, "image", "pull", "ghcr.io/x/y@sha256:" + "a" * 64, "--signed-by", "id"); self.assertEqual(r.returncode, 1); self.assertIn("go together", r.stderr)
            r = self.run_verb(d, "registry", "logout", "ghcr.io"); self.assertEqual(r.returncode, 0, r.stderr)


class TheRootOwnedCopy(unittest.TestCase):
    """`sudo ekka desk connect|invite|revoke|plans|grant|registry|image` run up.sh from the root-owned copy the installer
    makes (snapshot_stack), never the checkout. Every repository folder the tools read by ROOT must be in that copy.
    Measured 2026-10-31: `ekka desk plans` failed on the server because `ekka/plans/` was not copied."""

    def test_the_copy_holds_every_folder_the_tools_read(self):
        import re as _re
        up = (ROOT/"up.sh").read_text()
        line = next(l for l in up.splitlines() if l.strip().startswith('cp -a ') and '$SNAPSHOT.new/' in l)
        copied = set(line.split('cp -a', 1)[1].split('"$SNAPSHOT.new/"')[0].split())
        read = set()
        for f in (ROOT/"tools").glob("*.py"):
            read |= set(_re.findall(r'os\.path\.join\(ROOT, "([a-z_-]+)"', f.read_text()))
        self.assertTrue(read, "the scan found the folders the tools read")
        self.assertEqual(sorted(read - copied), [], "folders the tools read that the root-owned copy leaves out")
        self.assertTrue({"tools", "up.sh"} <= copied)


if __name__ == '__main__':
    unittest.main()
