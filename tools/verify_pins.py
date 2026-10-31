"""Check deploy/pins.json, this setup's committed pins for NVIDIA's OpenShell gateway and supervisor.

    python3 tools/verify_pins.py                    shape only: digests and checksums are well formed
    python3 tools/verify_pins.py --online           + each release tarball matches NVIDIA's checksums file,
                                                      and the binary inside it matches the committed sha256
    python3 tools/verify_pins.py --online --images  + the binary inside each pinned image matches too (needs docker)

NVIDIA signs neither image, so the binary is the check: the committed sha256 must equal the binary in
NVIDIA's checksummed release tarball AND the binary in the image this setup runs. Setup compares against
these committed values, never against a page it fetches at run time.
"""
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import tarfile
import tempfile
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PINS = os.path.join(ROOT, "deploy", "pins.json")
HEX64 = re.compile(r"^[0-9a-f]{64}$")
IMAGE = re.compile(r"^ghcr\.io/nvidia/openshell/[a-z-]+@sha256:[0-9a-f]{64}$")
COMPONENTS = ("gateway", "supervisor")


def load(path=PINS):
    with open(path) as f:
        return json.load(f)["openshell"]


def shape_errors(pins):
    """Every reason the pins are malformed; empty when they are well formed."""
    errors = []
    if not re.fullmatch(r"\d+\.\d+\.\d+", pins.get("version", "")):
        errors.append("version is not MAJOR.MINOR.PATCH")
    if pins.get("release") != f"https://github.com/NVIDIA/OpenShell/releases/download/v{pins.get('version')}":
        errors.append("release is not NVIDIA's download URL for this version")
    for name in COMPONENTS:
        c = pins.get(name) or {}
        if not IMAGE.fullmatch(c.get("image", "")):
            errors.append(f"{name}.image is not pinned by digest (ghcr.io/nvidia/openshell/...@sha256:<64 hex>)")
        if not HEX64.fullmatch(c.get("binary_sha256", "")):
            errors.append(f"{name}.binary_sha256 is not 64 lowercase hex")
        if not c.get("binary_in_image", "").startswith("/"):
            errors.append(f"{name}.binary_in_image is not an absolute path")
        for key in ("release_asset", "release_checksums"):
            if not c.get(key):
                errors.append(f"{name}.{key} is missing")
    return errors


def fetch(url):
    with urllib.request.urlopen(url, timeout=120) as r:
        return r.read()


def release_binary_sha256(pins, name):
    """The sha256 of the one binary in NVIDIA's release tarball, after checking the tarball itself."""
    c = pins[name]
    sums = fetch(f"{pins['release']}/{c['release_checksums']}").decode()
    want = [line.split()[0] for line in sums.splitlines() if line.endswith(" " + c["release_asset"])]
    if len(want) != 1:
        raise SystemExit(f"{name}: {c['release_asset']} is not listed exactly once in {c['release_checksums']}")
    blob = fetch(f"{pins['release']}/{c['release_asset']}")
    if hashlib.sha256(blob).hexdigest() != want[0]:
        raise SystemExit(f"{name}: {c['release_asset']} does not match NVIDIA's checksums file")
    with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as t:
        files = [m for m in t.getmembers() if m.isfile()]
        if len(files) != 1:
            raise SystemExit(f"{name}: expected one file in {c['release_asset']}, found {len(files)}")
        return hashlib.sha256(t.extractfile(files[0]).read()).hexdigest()


def image_binary_sha256(pins, name):
    """The sha256 of the binary inside the pinned image (docker pull, create, cp; nothing runs)."""
    c = pins[name]
    subprocess.run(["docker", "pull", "-q", c["image"]], check=True, stdout=subprocess.DEVNULL)
    cid = subprocess.run(["docker", "create", c["image"], "x"], check=True, capture_output=True, text=True).stdout.strip()
    try:
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "bin")
            subprocess.run(["docker", "cp", f"{cid}:{c['binary_in_image']}", out], check=True)
            with open(out, "rb") as f:
                return hashlib.sha256(f.read()).hexdigest()
    finally:
        subprocess.run(["docker", "rm", cid], stdout=subprocess.DEVNULL)


def main(argv):
    pins = load()
    errors = shape_errors(pins)
    if errors:
        print("\n".join(f"pins: {e}" for e in errors))
        return 1
    print(f"pins: shape OK (OpenShell {pins['version']})")
    failed = False
    for name in COMPONENTS:
        want = pins[name]["binary_sha256"]
        checks = []
        if "--online" in argv:
            checks.append(("NVIDIA's release tarball", release_binary_sha256(pins, name)))
        if "--images" in argv:
            checks.append(("the pinned image", image_binary_sha256(pins, name)))
        for where, got in checks:
            ok = got == want
            failed |= not ok
            print(f"pins: {name} binary in {where}: {'matches' if ok else 'DIFFERS: ' + got}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
