"""The one key a provider profile needs, for `sudo ekka desk connect <provider>` (ADK-E8, runner#1042).

    python3 tools/provider_connect.py key <profile file>

Prints the environment name NVIDIA's CLI reads the key from: the first `env_vars` entry of the profile's one required
credential. A profile that needs more than one key (Codex: an access token, a refresh token and an account id) is
refused: one key comes on stdin, and a second one would have to come some other way.

Standard library only: the profile files are small YAML with one fixed shape (providers/*.yaml, NVIDIA's own).
"""
import re
import sys

ENV = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
# Names a shell, the loader, TLS or NVIDIA's CLI itself reads. The key lands in this variable for a CLI run as root,
# so a profile naming one of these would turn the key into a search path, a library to load or a gateway to call.
RESERVED = {"PATH", "HOME", "SHELL", "IFS", "ENV", "BASH_ENV", "USER", "TMPDIR",
            "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY"}
RESERVED_PREFIXES = ("LD_", "DYLD_", "SSL_", "OPENSHELL_", "PYTHON", "SUDO_", "RUST_", "BASH_FUNC_")


def required_keys(text):
    """[(credential name, first env var)] of every required credential in the profile."""
    block = re.search(r"^credentials:\s*\n((?:[ \t-].*\n?|\s*\n)*)", text, re.M)
    if not block:
        return []
    out = []
    for entry in re.split(r"^\s*-\s+(?=name:)", block.group(1), flags=re.M)[1:]:
        name = re.match(r"name:\s*(\S+)", entry)
        env = re.search(r"^\s*env_vars:\s*\[\s*([A-Za-z0-9_]+)", entry, re.M)
        required = re.search(r"^\s*required:\s*true\s*$", entry, re.M)
        if name and required:
            out.append((name.group(1), env.group(1) if env else None))
    return out


def key(text):
    keys = required_keys(text)
    if len(keys) != 1:
        raise ValueError(f"this provider needs {len(keys)} keys; `ekka desk connect` sets a provider that needs exactly one")
    name, env = keys[0]
    if env is None or not ENV.match(env):
        raise ValueError(f"the profile's key '{name}' names no environment variable NVIDIA's CLI can read")
    if env in RESERVED or env.startswith(RESERVED_PREFIXES):
        raise ValueError(f"the profile's key '{name}' names {env}, which the system itself reads; "
                         "a key never goes there. Fix the profile in providers/ to name the provider's own variable")
    return env


def main(argv):
    if len(argv) != 2 or argv[0] != "key":
        print("usage: provider_connect.py key <profile file>", file=sys.stderr)
        return 2
    try:
        with open(argv[1], encoding="utf-8") as f:
            print(key(f.read()))
    except (OSError, ValueError) as e:
        print(f"REFUSED: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
