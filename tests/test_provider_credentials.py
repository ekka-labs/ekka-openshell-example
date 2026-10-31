"""Exercise the real connect functions across their nested Bash scopes."""
import pathlib
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]


class ProviderCredentials(unittest.TestCase):
    def test_model_credential_survives_gateway_admin_login(self):
        text = (ROOT / 'up.sh').read_text()
        functions = []
        for name in ['as_gateway_admin', 'connect_cmd', 'connect_provider']:
            start = text.index(name + '() {')
            functions.append(text[start:text.index('\n}\n', start) + 3])
        for env_name in ['ANTHROPIC_API_KEY', 'GITHUB_TOKEN']:
            with self.subTest(env_name=env_name), tempfile.TemporaryDirectory() as temp:
                root = pathlib.Path(temp)
                script = 'set -uo pipefail\n' + '\n'.join(functions) + r'''
install_cli() { :; }
openssl() { return 0; }
kc() { if [[ $2 == on ]]; then printf %s gateway-login-test-value; fi; }
profile_names() { printf 'claude-code\n'; }
profile_source() { printf profile; }
python3() {
  case $1 in
    tools/provider_connect.py) printf '%s\n' "$CREDENTIAL_ENV" ;;
    tools/openshell_allow.py) return 0 ;;
    *) return 99 ;;
  esac
}
step_desk_grant() { :; }
die() { printf '%s\n' "$*" >&2; exit 1; }
ok() { :; }
fake_cli() {
  if [[ ${1:-} == -g ]]; then shift 2; fi
  case "$*" in
    'gateway add '*|'gateway login '*) return 0 ;;
    'provider get '*) return 1 ;;
    'provider create '*)
      [[ $OPENSHELL_OIDC_CLIENT_SECRET == gateway-login-test-value ]] || return 98
      printf '%s' "${!CREDENTIAL_ENV}" > "$CAPTURE"
      ;;
    *) return 97 ;;
  esac
}
OS=fake_cli
NAME=localhost
printf '%s' model-credential-test-value | connect_cmd claude-code
'''
                out = subprocess.run(['bash', '-c', script], cwd=ROOT,
                                     env={'PATH': '/usr/bin:/bin', 'DESKS_STATE': str(root),
                                          'CAPTURE': str(root / 'captured'), 'CREDENTIAL_ENV': env_name},
                                     capture_output=True, text=True)
                self.assertEqual(out.returncode, 0, out.stderr)
                self.assertEqual((root / 'captured').read_text(), 'model-credential-test-value')
                self.assertNotIn('model-credential-test-value', out.stdout + out.stderr)
                self.assertNotIn('gateway-login-test-value', out.stdout + out.stderr)
