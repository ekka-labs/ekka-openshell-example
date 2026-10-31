# Desk policies

`policy.json` is every desk's policy: the agent runs as uid 1000, writes only to its workspace and /tmp, Landlock is required,
and the network is closed except GitHub clone/fetch for git. Providers add their own hosts when a desk is created with them.
The desks grant names this file's digest; a desk cannot be created with a policy the grant does not name.

`policy-discovery.json` is for ONE run when a project is onboarded: the same policy with one network rule that lets any
program reach any public address on ports 443 and 80. The rule has no host name, lists the public IPv4 internet as 51
address ranges, and relays TLS without opening it. NVIDIA OpenShell refuses `0.0.0.0/0` as one range (it holds loopback,
link-local and the zero address) and refuses a host-less rule spelled `protocol: tcp`, so this is the shape it accepts.
Private, loopback, link-local and multicast addresses stay unreachable even here. Every connection is in OpenShell's log
as ALLOWED with the program, host and port, so the log is the list of what the project needs.

The grant must be widened for it first (`sudo ekka desk grant discovery …`), the desk is disposable and gets no
production credential, and the window ends with `sudo ekka desk grant restore`. The project's closed policy is then
written from the log: one program to one host and port, each with a reason.
