# Vendored from `@ekka-ai/contracts`

Only what this repository's tests read, copied byte for byte from the published package. `MANIFEST.json` names the version, tag and commit they came from, and the SHA-256 of each file. A test fails if a file here differs from its manifest entry, so a copy edited by hand is caught.

| Files | What they are | Read by |
|-------|---------------|---------|
| `backend/operations.json` | The adapter's operations and the one route each is served on. | `tests/test_desk_admin_plans.py`: each plan calls its operation's route, with its method. |
| `backend/samples/result.*.json` | The backend adapter's answers (`ekka.backend.v1`): one per outcome, a refusal delivered twice, a required provider refused, a change that never reached the desk (`NOT_APPLIED`), and a `status` answer. | `tests/test_desk_admin_plans.py`: each desk administration plan's declared facts take `ready`, `refused` and `unknown`, and refuse `running`. |
| `backend/adapter-config.v1.schema.json`, `backend/samples/adapter-config.json` | The backend adapter's one configuration file, and a sample. | `tests/test_adapter_config.py`: the file `up.sh` writes is accepted by this schema. |
| `backend/settle-marker.v1.schema.json`, `backend/samples/settle-marker*.json` | The marker that settles one unfinished change: a watched desk, and a create that never reached the desk (`backend_id: null`). | `tests/test_desks_settle.py`: the marker `up.sh desks settle` writes has the schema's fields. |
| `backend/settle-hold.v1.schema.json`, `backend/samples/settle-hold.json` | The hold written before the gateway restarts: while it exists, the adapter never re-issues that change. | `tests/test_desks_settle.py`: the hold `up.sh desks settle` writes has the schema's fields. |
| `governance/grant-constraints/desks.v1.schema.json`, `.../samples/desks.v1.json` | The limits on the organization's desks grant. | `tests/test_desk_grant.py`: the limits `up.sh` writes are accepted by this schema. |

To update: copy the same files from the new published version and rewrite `MANIFEST.json` with its version, tag, commit and hashes.
