# Desk policies

`policy.json` is every desk's policy: the agent runs as uid 1000, writes only to its workspace and /tmp, Landlock is required,
and the network is closed except GitHub clone/fetch for git. Providers add their own hosts when a desk is created with them.
The desks grant names this file's digest; a desk cannot be created with a policy the grant does not name.

There is no second policy for onboarding a project: the discovery desk runs this same policy, and NVIDIA OpenShell's
auto approval mode, switched on for that one desk while a discovery window is open, records what its build reaches
(`sudo ekka desk grant discovery`, [OPERATIONS.md](../docs/OPERATIONS.md#onboarding-a-project-the-discovery-window)).
The project's closed policy is then written from that record: one program to one host and port, each with a reason.
