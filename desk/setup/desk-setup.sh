# The setup plan's job (ekka/plans/setup.json): put the repositories a person chose on the desk. A command, not an
# AI: cloning is mechanical, and the desk's managed settings restrict only the AI's tools (ekka-ai/.github#816).
#
# Run as: bash -c 'R=$1; I=$2; shift 2; eval "$(printf %s "$@")"' desk-setup <repos> <run id> <this file, in words>
# <repos> is the run's `repos` input: a JSON list of text, or one entry per line. Each entry is <address>[#<branch>]:
#   https://github.com/<owner>/<repo>[.git] or https://bitbucket.org/<workspace>/<repo>[.git], no user name, password,
#   port, query or other path; the branch in letters, digits and . _ - /, and a name `git check-ref-format --branch`
#   accepts. The value is data: it is only ever an argument, never part of a command line.
# Each repository goes in the workspace folder named after it:
#   absent                              cloned, on the branch asked for (else the repository's default branch)
#   the same address, on that branch    reused as it is: nothing is fetched, pulled, reset, switched or cleaned
#   anything else                       failed, by name; never switched, never deleted
# Then .ekka/desk-map.json, the shape desk-setup@4 wrote, and exit 0. The map's `result` decides the run.
set -u
W=/sandbox/ekka-workspace
O=$W/.ekka
MAP=$O/desk-map.json
MAX_REPOS=100
REPOS=${R-}
RUN=${I-}
# Nothing the caller's environment says about Git reaches a Git command here.
for v in $(compgen -e); do case $v in GIT_*) unset "$v" ;; esac; done
command -v jq >/dev/null || { echo "desk setup: jq is missing on this desk image; nothing was set up" >&2; exit 1; }
command -v git >/dev/null || { echo "desk setup: git is missing on this desk image; nothing was set up" >&2; exit 1; }
[ -d "$W" ] && [ ! -L "$W" ] || { echo "desk setup: the workspace $W is not a folder" >&2; exit 1; }

# The ONE clone command, with the same flags the desk image allows its AI (desk-base managed-settings.json), and
# --branch just before --. GIT_TERMINAL_PROMPT=0: a repository the desk has no key for fails at once, never waits.
clone() {   # clone <address> <folder> [<branch>]
  local b=()
  [ -n "${3-}" ] && b=(--branch "$3")
  (cd / && env -u GIT_CONFIG_PARAMETERS -u GIT_CONFIG_COUNT GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null \
    GIT_TERMINAL_PROMPT=0 git -c core.hooksPath=/dev/null -c core.fsmonitor=false -c protocol.allow=never \
    -c protocol.https.allow=always -c http.followRedirects=false -c credential.helper=/usr/local/bin/ekka-git-credential \
    clone --template= --no-local --no-recurse-submodules ${b[@]+"${b[@]}"} -- "$1" "$2")
}
# A read in a checkout. Only rev-parse, symbolic-ref and config --get: none of them runs a program a repository's
# own config names (no index refresh, no filter, no hook, no network), so an old checkout's config cannot run here.
gitread() {   # gitread <folder> <git args>...
  local f=$1; shift
  (cd / && env -u GIT_CONFIG_PARAMETERS -u GIT_CONFIG_COUNT GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null \
    git -c core.hooksPath=/dev/null -c core.fsmonitor=false -C "$f" "$@") 2>/dev/null
}

ADDR_RE='^https://(github\.com|bitbucket\.org)/([A-Za-z0-9._-]+)/([A-Za-z0-9._-]+)$'
BRANCH_RE='^[A-Za-z0-9._/-]+$'
# Sets WHY to why an entry is refused, else empty, and ADDR, BRANCH and NAME for a good one.
check() {
  local e=$1 owner repo
  WHY=""; ADDR=${e%%#*}; BRANCH=""; NAME=""
  if [[ $e == *#* ]]; then
    BRANCH=${e#*#}
    [ -n "$BRANCH" ] || { WHY="nothing after #: name a branch, or leave out the #"; return; }
  fi
  [[ $ADDR =~ $ADDR_RE ]] || { WHY="not an https://github.com/<owner>/<repo> or https://bitbucket.org/<workspace>/<repo> address with no user name, password, port, query or other path"; return; }
  owner=${BASH_REMATCH[2]}; repo=${BASH_REMATCH[3]}; NAME=${repo%.git}
  case $owner in .|..) WHY="the owner '$owner' is not a name"; return ;; esac
  case $NAME in ""|.|..|.ekka) WHY="the repository name '$repo' cannot be a workspace folder"; return ;; esac
  if [ -n "$BRANCH" ]; then
    if ! [[ $BRANCH =~ $BRANCH_RE ]] || [ ${#BRANCH} -gt 200 ]; then
      WHY="the branch '$BRANCH' is not letters, digits and . _ - / (at most 200)"; return
    fi
    case $BRANCH in -*|/*|*/|*.lock|*.|*..*|*//*) WHY="the branch '$BRANCH' is not a branch name"; return ;; esac
    (cd / && env -u GIT_CONFIG_PARAMETERS -u GIT_CONFIG_COUNT GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null \
      git check-ref-format --branch "$BRANCH") >/dev/null 2>&1 || { WHY="git refuses '$BRANCH' as a branch name"; return; }
  fi
}

ENTRIES=()
INPUT_PROBLEM=""
if [[ $REPOS =~ ^[[:space:]]*\[ ]]; then
  # A JSON list: every item text with no white space or control character, so one item is one entry.
  if jq -e 'type == "array" and all(.[]; type == "string" and (test("[\\s\\p{Cc}]") | not))' <<<"$REPOS" >/dev/null 2>&1; then
    while IFS= read -r line; do ENTRIES+=("$line"); done < <(jq -r '.[]' <<<"$REPOS")
  else
    INPUT_PROBLEM="repos is not a JSON list of addresses, each text with no spaces"
  fi
else
  while IFS= read -r line || [ -n "$line" ]; do
    line=${line%$'\r'}
    line="${line#"${line%%[![:space:]]*}"}"; line="${line%"${line##*[![:space:]]}"}"
    [ -z "$line" ] || ENTRIES+=("$line")
  done <<<"$REPOS"
fi
[ -n "$INPUT_PROBLEM" ] || [ ${#ENTRIES[@]} -gt 0 ] || INPUT_PROBLEM="repos names no repository"
[ -n "$INPUT_PROBLEM" ] || [ ${#ENTRIES[@]} -le $MAX_REPOS ] || INPUT_PROBLEM="repos names ${#ENTRIES[@]} repositories; at most $MAX_REPOS"

ROWS=()
cloned=0; reused=0; failed=0
row() {   # row <address> <folder> <branch> <commit> <status> <reason>
  ROWS+=("$(jq -cn --arg a "$1" --arg f "$2" --arg b "$3" --arg c "$4" --arg s "$5" --arg r "$6" \
    '{address:$a, folder:$f, branch:$b, commit:$c, status:$s, reason:$r, discarded:[], discarded_count:0}')")
  case $5 in cloned) cloned=$((cloned + 1)) ;; reused) reused=$((reused + 1)) ;; *) failed=$((failed + 1)) ;; esac
}
if [ -n "$INPUT_PROBLEM" ]; then
  row "" "" "" "" failed "$INPUT_PROBLEM"
  ENTRIES=()
fi

SEEN=" "
for e in ${ENTRIES[@]+"${ENTRIES[@]}"}; do
  check "$e"
  if [ -n "$WHY" ]; then row "${e%%#*}" "" "" "" failed "$WHY"; continue; fi
  F=$W/$NAME
  if [[ $SEEN == *" $NAME "* ]]; then row "$ADDR" "$F" "" "" failed "another entry already uses the folder $NAME"; continue; fi
  SEEN="$SEEN$NAME "
  if [ ! -e "$F" ] && [ ! -L "$F" ]; then
    if err=$(clone "$ADDR" "$F" "$BRANCH" 2>&1 >/dev/null); then
      row "$ADDR" "$F" "$(gitread "$F" symbolic-ref --short -q HEAD)" "$(gitread "$F" rev-parse -q --verify HEAD)" cloned \
        "cloned${BRANCH:+ on $BRANCH}"
    else
      last=$(printf '%s\n' "$err" | grep -v '^[[:space:]]*$' | tail -1 | cut -c1-300)
      row "$ADDR" "$F" "" "" failed "the clone failed: ${last:-no reason given}"
    fi
    continue
  fi
  if [ -L "$F" ] || [ ! -d "$F" ] || [ -L "$F/.git" ] || [ ! -d "$F/.git" ]; then
    row "$ADDR" "$F" "" "" failed "$NAME is already there and is not a clone; nothing was changed"; continue
  fi
  origin=$(gitread "$F" config --get remote.origin.url)
  if [ "${origin%.git}" != "${ADDR%.git}" ]; then
    row "$ADDR" "$F" "" "" failed "$NAME is a clone of ${origin:-no origin}, not $ADDR; nothing was changed"; continue
  fi
  cur=$(gitread "$F" symbolic-ref --short -q HEAD)
  commit=$(gitread "$F" rev-parse -q --verify HEAD)
  if [ -n "$BRANCH" ] && [ "$cur" != "$BRANCH" ]; then
    row "$ADDR" "$F" "$cur" "$commit" failed "on ${cur:-no branch}, asked for $BRANCH; it was not switched"; continue
  fi
  row "$ADDR" "$F" "$cur" "$commit" reused "reused as it is, on ${cur:-no branch}; nothing was fetched or changed"
done

result=ready
if [ $failed -gt 0 ]; then
  result=failed
  [ $((cloned + reused)) -eq 0 ] || result=partial
fi
# The map, never through a link: .ekka must be a real folder, and the file is renamed into place.
[ ! -L "$O" ] && [ ! -L "$MAP" ] || { echo "desk setup: $O or its map is a link; no map was written" >&2; exit 1; }
mkdir -p "$O" || exit 1
tmp=$(mktemp "$O/.desk-map.XXXXXX") || exit 1
printf '%s\n' "${ROWS[@]}" | jq -s --arg run "$RUN" --argjson c $cloned --argjson u $reused --argjson f $failed \
  --arg r $result '{run_id:$run, repositories:., cloned:$c, reused:$u, updated:0, reset:0, skipped:0, failed:$f,
  result:$r}' >"$tmp" && mv -f "$tmp" "$MAP" || { rm -f "$tmp"; echo "desk setup: the map was not written" >&2; exit 1; }
cat "$MAP"
