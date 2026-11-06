# Working in this repository

## Build and test on a developer desk

The offline suite is Python's standard library plus bash, git, jq and openssl. It needs the `external/desk-base`
submodule checked out, because three tests read files under it: clone with `--recurse-submodules`, or run
`git submodule update --init --checkout` first.

- Local setup: `git submodule update --init --checkout`
- Fast suite: `python3 -m unittest discover -s tests`
- Full suite: `python3 -m unittest discover -s tests -v`
- Cross-service smoke: `bash -n up.sh`

```sh
python3 -m unittest discover -s tests -v
```

Docker is a stub on PATH in most tests. The tests that need a real docker, a root user or a pulled image skip
themselves (`test_compose_backend.py`, `test_harden.py`, `test_native_enclave.py`). `test_trust_ca.py` calls
`openssl` with no skip guard, so a machine without openssl fails it rather than skipping.

Gates that stay in CI and why:

- The same suite on Amazon Linux 2023's Python 3.9: runs in a container (`docker run`).
- `python3 tools/verify_pins.py --online --images`: reads NVIDIA's releases and the pinned images over the network.
- The cosign check of the pinned backend adapter: `docker run` plus a registry pull.

`up.sh` is the installer, not a check. It needs root and a server.
