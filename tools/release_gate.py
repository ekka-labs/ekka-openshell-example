"""Is this Enclave release new enough for desk administration through EKKA?

    python3 tools/release_gate.py <have> <need>      exit 0 when <have> >= <need>, 1 when older, 2 when unreadable

Releases are numbered MAJOR.MINOR.PATCH, with or without a leading v. Anything else is unreadable, never "new
enough": a gate that passed a version it could not read would let an old Enclave in. Standard library only.
"""
import re
import sys

RELEASE = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")


def parse(text):
    m = RELEASE.match(text.strip())
    return tuple(int(x) for x in m.groups()) if m else None


def main(argv):
    if len(argv) != 2:
        print(__doc__.strip().splitlines()[2].strip(), file=sys.stderr)
        return 2
    have, need = parse(argv[0]), parse(argv[1])
    if have is None or need is None:
        print(f"release_gate: cannot read {argv[0]!r} or {argv[1]!r} as MAJOR.MINOR.PATCH", file=sys.stderr)
        return 2
    return 0 if have >= need else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
