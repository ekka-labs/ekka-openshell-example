# Third-party notices

This repository's own files are under its LICENSE. These files are not ours:

| Files | Source | License |
|---|---|---|
| `providers/*.yaml` | [NVIDIA OpenShell v0.1.3](https://github.com/NVIDIA/OpenShell/tree/v0.1.3/providers), copied unchanged (pinned by sha256 in `providers/PIN.json`) | Apache-2.0, Copyright (c) NVIDIA CORPORATION & AFFILIATES; full text in [`providers/LICENSE`](providers/LICENSE) |
| `providers/*.json` | converted from the `.yaml` beside each, with OpenShell's own profile parser at the same commit | Apache-2.0, as above |

Nothing else from NVIDIA OpenShell is in this repository. The gateway and supervisor images are pulled from `ghcr.io/nvidia` by digest when you install, under NVIDIA's own license and [notices](https://github.com/NVIDIA/OpenShell/blob/v0.1.4/THIRD-PARTY-NOTICES).

The images this setup builds or pulls (the desk image, Keycloak, Postgres, nginx) carry their own licenses, listed in [Source and licenses](docs/OVERVIEW.md#source-and-licenses). The EKKA release the Enclave runs is under EKKA's terms, separate from this repository's license.

NVIDIA and OpenShell are trademarks of NVIDIA Corporation in the U.S. and other countries.
