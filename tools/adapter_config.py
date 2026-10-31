"""The backend adapter's one configuration file, written at install .

    <the Enclave's EKKA_TENANT_ID, EKKA_NODE_ID and EKKA_GOVERN_URL lines> | python3 tools/adapter_config.py write <path> <public name>

The shape is @ekka-ai/contracts 0.73.0 backend/adapter-config.v1.schema.json (vendored). Every value comes from the machine:

  organization_id     the Enclave's EKKA_TENANT_ID
  api_gate_instance   enclave<first 8 hex of EKKA_NODE_ID>Api, as the runner names this Enclave's API gate
  govern_keys         Govern's `verify_keys` entries with purpose `gate_token`, from
                      <EKKA_GOVERN_URL>/.well-known/ekka-configuration: kid -> key_id, public_key_b64 as is
  backend_gateway     this machine's gateway, as the adapter reaches it on its own network, with this setup's CA, and
                      the issuer it signs in at: https://<public name>/realms/ekka-desks, EXACTLY what the gateway
                      validates `iss` against (deploy/gateway/gateway.toml.template) and Keycloak puts in it

It holds NO credential: the gateway credential is in the Enclave's vault (ADK-E3's connect_desk_admin). The file is
written beside, then renamed, so the adapter never reads half of one.
"""
import json
import os
import re
import sys
import tempfile
import urllib.request

UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
KEY_B64 = re.compile(r"^[A-Za-z0-9+/]{42}[AEIMQUYcgkosw048]=$")
KID = re.compile(r"^[!-~]{1,128}$")
WANTED = ("EKKA_TENANT_ID", "EKKA_NODE_ID", "EKKA_GOVERN_URL")
# The gateway as the adapter reaches it: compose gives the gateway this name on the adapter's network, and the
# gateway's certificate carries it (deploy/certs.sh). The same identity the settle procedure writes.
GATEWAY_URL = "https://host.openshell.internal:8080"
CLIENT_ID = "ekka-desk-admin"
WORKSPACE = "desks"
CA_IN_ADAPTER = "/etc/ekka-backend/ca.pem"
REALM = "ekka-desks"
NAME = re.compile(r"^[a-z0-9]([a-z0-9.-]*[a-z0-9])?$")


class Refused(Exception):
    pass


def enclave_values(text):
    """The three values from the Enclave's enclave.env lines; nothing else is read."""
    out = {}
    for line in text.splitlines():
        key, sep, value = line.partition("=")
        if sep and key in WANTED:
            out[key] = value.strip()
    missing = [k for k in WANTED if not out.get(k)]
    if missing:
        raise Refused(f"the Enclave did not name {', '.join(missing)}; is it enrolled?")
    if not UUID.match(out["EKKA_TENANT_ID"]) or not UUID.match(out["EKKA_NODE_ID"]):
        raise Refused("the Enclave's organization or node id is not a lowercase UUID")
    if not re.match(r"^https://[^\s/]+(/[^\s]*)?$", out["EKKA_GOVERN_URL"]):
        raise Refused("the Enclave's Govern URL is not https")
    return out


def gate_token_keys(well_known):
    """`verify_keys` with purpose gate_token, as the contract's govern_keys: one, or two while a key rotates."""
    keys = [{"key_id": k.get("kid"), "public_key_b64": k.get("public_key_b64")}
            for k in (well_known.get("verify_keys") or []) if k.get("purpose") == "gate_token"]
    if not 1 <= len(keys) <= 2:
        raise Refused(f"Govern published {len(keys)} gate-token keys; the adapter takes one, or two while one rotates")
    for k in keys:
        if not isinstance(k["key_id"], str) or not KID.match(k["key_id"]):
            raise Refused("a gate-token key has no usable kid")
        if not isinstance(k["public_key_b64"], str) or not KEY_B64.match(k["public_key_b64"]):
            raise Refused("a gate-token key is not a 32-byte Ed25519 key in standard base64")
    return keys


def build(values, well_known, public_name):
    if not NAME.match(public_name or "") or ".." in public_name:
        raise Refused("this machine's public name is not a host name")
    node = values["EKKA_NODE_ID"].replace("-", "")
    return {
        "config_version": 1,
        "organization_id": values["EKKA_TENANT_ID"],
        "api_gate_instance": f"enclave{node[:8]}Api",
        "govern_keys": gate_token_keys(well_known),
        "backend_gateway": {"url": GATEWAY_URL, "client_id": CLIENT_ID, "workspace": WORKSPACE,
                            "issuer": f"https://{public_name}/realms/{REALM}", "ca_pem_path": CA_IN_ADAPTER},
    }


def fetch_well_known(govern_url, opener=urllib.request.urlopen):
    url = govern_url.rstrip("/") + "/.well-known/ekka-configuration"
    req = urllib.request.Request(url, headers={"User-Agent": "ekka-agent-desks"})
    with opener(req, timeout=30) as r:
        if getattr(r, "status", 200) != 200:
            raise Refused(f"{url} answered {r.status}")
        return json.loads(r.read().decode("utf-8"))


def write(path, config):
    """0644 (it holds no secret; the adapter reads it as another user), written beside and renamed."""
    d = os.path.dirname(path)
    os.makedirs(d, mode=0o755, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".config.", suffix=".json")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(config, f, indent=2)
            f.write("\n")
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def main(argv, stdin=sys.stdin, opener=urllib.request.urlopen):
    if len(argv) != 3 or argv[0] != "write":
        print("usage: <enclave.env lines> | python3 tools/adapter_config.py write <path> <public name>", file=sys.stderr)
        return 2
    try:
        values = enclave_values(stdin.read())
        config = build(values, fetch_well_known(values["EKKA_GOVERN_URL"], opener), argv[2])
        write(argv[1], config)
    except Refused as e:
        print(f"REFUSED: {e}; the adapter's configuration was not written", file=sys.stderr)
        return 1
    except (OSError, ValueError) as e:
        print(f"REFUSED: Govern's keys could not be read ({e.__class__.__name__}); nothing was written", file=sys.stderr)
        return 1
    print(f"the backend adapter's configuration: {config['api_gate_instance']}, "
          f"{len(config['govern_keys'])} Govern key(s), no credential")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
