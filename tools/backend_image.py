"""The backend adapter's image, from deploy/pins.json .

    python3 tools/backend_image.py host            the registry host, e.g. ghcr.io
    python3 tools/backend_image.py image           <registry>/ekka-backend@sha256:<digest>; refused while unpinned
    python3 tools/backend_image.py pinned          exit 0 when a digest is pinned, 1 when not yet
    python3 tools/backend_image.py identity        the signing identity cosign must see, as a regexp
    python3 tools/backend_image.py issuer          the identity's OIDC issuer
    python3 tools/backend_image.py cosign-image    the pinned cosign image that checks the signature

ONE VALUE NAMES WHERE THE IMAGE LIVES: `ekka_backend.registry` (owner, 2026-10-17: `ghcr.io/ekka-labs`, a
public package built from the public ekka-labs/ekka-backend; no registry login). The image and the signing
identity both follow from it, so a move is one line. The identity is the release workflow of the GitHub repository of the same organization and
name, on a version tag: the only signature this setup accepts.
"""
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PINS = os.path.join(ROOT, "deploy", "pins.json")
NAME = "ekka-backend"
ISSUER = "https://token.actions.githubusercontent.com"
REGISTRY = re.compile(r"^ghcr\.io/[a-z0-9][a-z0-9-]{0,38}$")
DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
WORKFLOW = re.compile(r"^\.github/workflows/[A-Za-z0-9._-]+\.ya?ml$")
COSIGN_IMAGE = re.compile(r"^gcr\.io/projectsigstore/cosign:v[0-9.]+@sha256:[0-9a-f]{64}$")


def load(path=PINS):
    with open(path) as f:
        return json.load(f)["ekka_backend"]


def shape_errors(b):
    """Every reason the backend pin is malformed; empty when it is well formed."""
    errors = []
    if not REGISTRY.fullmatch(b.get("registry") or ""):
        errors.append("ekka_backend.registry is not ghcr.io/<organization>")
    if b.get("digest") is not None and not DIGEST.fullmatch(b["digest"]):
        errors.append("ekka_backend.digest is not sha256:<64 lowercase hex> (or null until the first release)")
    if not WORKFLOW.fullmatch(b.get("release_workflow") or ""):
        errors.append("ekka_backend.release_workflow is not a path under .github/workflows/")
    if not COSIGN_IMAGE.fullmatch(b.get("cosign_image") or ""):
        errors.append("ekka_backend.cosign_image is not sigstore's cosign pinned by digest")
    return errors


def host(b):
    return b["registry"].split("/", 1)[0]


def image(b):
    if b.get("digest") is None:
        raise ValueError("no ekka-backend digest is pinned yet in deploy/pins.json")
    return f"{b['registry']}/{NAME}@{b['digest']}"


def identity(b):
    org = b["registry"].split("/", 1)[1]
    # The organization needs no escaping: REGISTRY admits only [a-z0-9-] there, so the regexp reads as written.
    return (f"^https://github\\.com/{org}/{NAME}/{re.escape(b['release_workflow'])}"
            r"@refs/tags/v[0-9]+\.[0-9]+\.[0-9]+$")


def main(argv):
    if len(argv) != 1:
        print(__doc__.strip().splitlines()[2], file=sys.stderr)
        return 2
    b = load()
    errors = shape_errors(b)
    if errors:
        print("REFUSED: " + "; ".join(errors), file=sys.stderr)
        return 1
    what = argv[0]
    try:
        if what == "pinned":
            return 0 if b.get("digest") is not None else 1
        out = {"host": lambda: host(b), "image": lambda: image(b), "identity": lambda: identity(b),
               "issuer": lambda: ISSUER, "cosign-image": lambda: b["cosign_image"]}[what]()
    except KeyError:
        print(f"unknown: {what}", file=sys.stderr)
        return 2
    except ValueError as e:
        print(f"REFUSED: {e}", file=sys.stderr)
        return 1
    print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
