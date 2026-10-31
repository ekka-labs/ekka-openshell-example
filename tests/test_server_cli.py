"""Exercise the real server CLI wrapper and public-default bootstrap without a gateway or credentials."""
import json
import os
from pathlib import Path
import pty
import re
import subprocess
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


if __name__ == '__main__':
    unittest.main()
