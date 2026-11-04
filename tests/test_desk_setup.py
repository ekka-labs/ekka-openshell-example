"""The setup plan's job, desk/setup/desk-setup.sh (ekka-ai/.github#816): a command, not an AI.

Each case runs the plan's OWN command words, as tools/desk_plans.py renders them, with bash, after one change: the
workspace is a temporary folder instead of /sandbox/ekka-workspace. A fake `git` first on PATH records every clone
and makes a local repository in its place (nothing reaches the network); every other git command is the real git.
Each rule has a plant: the same case on a copy of the script with that rule taken out, which must come out wrong.
"""
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, os.path.join(ROOT, "tools"))
import desk_plans  # noqa: E402

SCRIPT = os.path.join(ROOT, "desk", "setup", "desk-setup.sh")
WORKSPACE_LINE = "W=/sandbox/ekka-workspace\n"
REAL_GIT = shutil.which("git")
# git's own arguments for a clone, and the Git variables it runs with (env sets them; the fake sees them).
CLONE_ENV = "GIT_CONFIG_GLOBAL=/dev/null,GIT_CONFIG_NOSYSTEM=1,GIT_TERMINAL_PROMPT=0"
CLONE_FLAGS = ["-c", "core.hooksPath=/dev/null",
               "-c", "core.fsmonitor=false", "-c", "protocol.allow=never", "-c", "protocol.https.allow=always",
               "-c", "http.followRedirects=false", "-c", "credential.helper=/usr/local/bin/ekka-git-credential",
               "clone", "--template=", "--no-local", "--no-recurse-submodules"]

# The fake git: `clone` is recorded, one argument per line and a blank line after, and becomes a local repository
# on the branch asked for (main without one) whose origin is the address. An address with `missing` in it fails
# the way a branch the remote does not have fails. Everything else is the real git.
FAKE_GIT = r"""#!/bin/bash
real=@REAL@
if [ "$1" = -c ] || [ "$1" = clone ]; then
  args=("$@"); i=0
  while [ $i -lt ${#args[@]} ] && [ "${args[$i]}" != clone ]; do i=$((i + 1)); done
  if [ $i -lt ${#args[@]} ]; then
    printf '%s\n' "$(env | grep '^GIT_' | sort | paste -sd, -)" "${args[@]}" "" >> @LOG@
    branch=main; addr=""; folder=""; j=$((i + 1))
    while [ $j -lt ${#args[@]} ]; do
      case ${args[$j]} in
        --branch) j=$((j + 1)); branch=${args[$j]} ;;
        --) addr=${args[$((j + 1))]}; folder=${args[$((j + 2))]}; break ;;
      esac
      j=$((j + 1))
    done
    case $addr in *missing*) echo "fatal: Remote branch $branch not found in upstream origin" >&2; exit 128 ;; esac
    "$real" init -q -b "$branch" "$folder" && "$real" -C "$folder" remote add origin "$addr" \
      && "$real" -C "$folder" -c user.name=t -c user.email=t@t -c commit.gpgsign=false commit -q --allow-empty -m one
    exit $?
  fi
fi
exec "$real" "$@"
"""


def plain_repo(folder, origin, branch):
    """A clone already on the desk: a repository on `branch` whose origin is `origin`."""
    subprocess.run([REAL_GIT, "init", "-q", "-b", branch, folder], check=True)
    subprocess.run([REAL_GIT, "-C", folder, "remote", "add", "origin", origin], check=True)
    subprocess.run([REAL_GIT, "-C", folder, "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false",
                    "commit", "-q", "--allow-empty", "-m", "one"], check=True)


@unittest.skipUnless(shutil.which("jq") and REAL_GIT and shutil.which("bash"), "needs bash, git and jq, as the desk has")
class TheSetupJob(unittest.TestCase):
    # An address with a user name is split in this file, so the address check (test_no_private_addresses) never reads
    # it as an email address.
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.ws = os.path.join(self.tmp, "ws")
        os.mkdir(self.ws)
        self.log = os.path.join(self.tmp, "clones.log")
        bin_dir = os.path.join(self.tmp, "bin")
        os.mkdir(bin_dir)
        fake = os.path.join(bin_dir, "git")
        with open(fake, "w") as f:
            f.write(FAKE_GIT.replace("@REAL@", REAL_GIT).replace("@LOG@", self.log))
        os.chmod(fake, os.stat(fake).st_mode | stat.S_IXUSR)
        self.path = bin_dir + os.pathsep + os.environ["PATH"]

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def command(self, repos, script=None):
        """The setup plan's command for this case: its own words, the run's values in place of the bindings."""
        plan = desk_plans.render("setup", "code-desk")
        words = plan["definition"]["operations"][0]["steps"][0]["inputs"]["command"]
        self.assertEqual(words[4:6], ["@input.repos", "@ekka.run_id"])
        text = "".join(words[6:])
        with open(SCRIPT, encoding="utf-8") as f:
            self.assertEqual(text, f.read(), "the plan's words are not the script")
        if script is not None:
            text = script(text)
        self.assertEqual(text.count(WORKSPACE_LINE), 1)
        text = text.replace(WORKSPACE_LINE, f"W={self.ws}\n")
        return words[:4] + [repos, "run-816"] + [text]

    def run_setup(self, repos, script=None):
        r = subprocess.run(self.command(repos, script), capture_output=True, text=True, timeout=60,
                           env={"PATH": self.path, "HOME": self.tmp, "GIT_DIR": "/nonexistent"})
        self.assertEqual(r.returncode, 0, r.stderr)
        with open(os.path.join(self.ws, ".ekka", "desk-map.json")) as f:
            return json.load(f)

    def clones(self):
        if not os.path.exists(self.log):
            return []
        with open(self.log) as f:
            return [c.split("\n") for c in f.read().split("\n\n") if c]

    def only(self, m):
        self.assertEqual(len(m["repositories"]), 1, m)
        return m["repositories"][0]

    # ---- a missing folder is cloned, with the one hardened command and --branch just before --

    def test_a_missing_folder_is_cloned_with_the_hardened_command(self):
        m = self.run_setup("https://bitbucket.org/acme/claims.git#develop")
        r = self.only(m)
        self.assertEqual((r["status"], r["branch"], r["folder"]), ("cloned", "develop", os.path.join(self.ws, "claims")))
        self.assertRegex(r["commit"], r"^[0-9a-f]{40}$")
        self.assertEqual(self.clones(), [[CLONE_ENV] + CLONE_FLAGS + ["--branch", "develop", "--",
                                                        "https://bitbucket.org/acme/claims.git",
                                                        os.path.join(self.ws, "claims")]])
        self.assertEqual((m["result"], m["cloned"], m["reused"], m["failed"], m["run_id"]), ("ready", 1, 0, 0, "run-816"))

    def test_without_a_branch_the_default_branch_is_cloned(self):
        r = self.only(self.run_setup('["https://github.com/octocat/Hello-World"]'))
        self.assertEqual((r["status"], r["branch"]), ("cloned", "main"))
        self.assertNotIn("--branch", self.clones()[0])

    def test_planted_a_clone_without_its_flags_is_caught(self):
        self.run_setup("https://github.com/o/r", lambda s: s.replace(" --no-recurse-submodules", ""))
        self.assertNotEqual(self.clones()[0], [CLONE_ENV] + CLONE_FLAGS + ["--", "https://github.com/o/r",
                                                                          os.path.join(self.ws, "r")])

    def test_a_clone_that_fails_is_failed_by_name(self):
        m = self.run_setup("https://github.com/o/missing#nope")
        r = self.only(m)
        self.assertEqual((r["status"], m["result"]), ("failed", "failed"))
        self.assertIn("Remote branch nope not found", r["reason"])

    # ---- the address

    def test_a_credentialed_or_plain_http_address_is_refused_and_never_cloned(self):
        for bad in ("https://user:secret" + "@github.com/o/r", "https://x-token" + "@bitbucket.org/w/r", "http://github.com/o/r",
                    "https://github.com:443/o/r", "https://gitlab.com/o/r", "https://github.com/o/r/extra",
                    "https://github.com/o/r?x=1", "https://github.com/../r", "https://github.com/o/.git",
                    "https://github.com/o/.ekka", "git" + "@github.com:o/r.git", "file:///etc"):
            with self.subTest(bad=bad):
                r = self.only(self.run_setup(bad))
                self.assertEqual(r["status"], "failed")
                self.assertEqual(self.clones(), [])
                self.assertEqual(sorted(os.listdir(self.ws)), [".ekka"])

    def test_planted_an_address_rule_that_lets_any_https_through_is_caught(self):
        wide = lambda s: s.replace("ADDR_RE='^https://(github\\.com|bitbucket\\.org)/", "ADDR_RE='^https?://([^/]*)/")
        for bad in ("https://user:secret" + "@github.com/o/r1", "http://github.com/o/r2"):
            with self.subTest(bad=bad):
                self.assertEqual(self.only(self.run_setup(bad, wide))["status"], "cloned", "PLANTED: not refused")

    def test_an_address_is_data_and_never_runs(self):
        canary = os.path.join(self.tmp, "ran")
        for bad in (f"$(touch {canary})", f"`touch {canary}`", f"https://github.com/o/r;touch {canary}",
                    f"https://github.com/o/r#x$(touch {canary})"):
            with self.subTest(bad=bad):
                self.assertEqual(self.only(self.run_setup(bad))["status"], "failed")
                self.assertFalse(os.path.exists(canary))

    # ---- the branch

    BAD_BRANCHES = ("--config=core.fsmonitor=x", "--branch=x", "-x", "a b", "a=b", "x..y", "/x", "x/", "x//y",
                    "x.lock", "x.", "@{-1}", "x~1", "x^", "x:y", "x\\y", "a" * 201)

    def test_a_bad_branch_is_refused_and_never_reaches_git(self):
        for bad in self.BAD_BRANCHES:
            with self.subTest(bad=bad):
                r = self.only(self.run_setup(json.dumps([f"https://github.com/o/r#{bad}"])))
                self.assertEqual(r["status"], "failed")
                self.assertEqual(self.clones(), [])
        r = self.only(self.run_setup("https://github.com/o/r#"))
        self.assertIn("nothing after #", r["reason"])

    def test_planted_a_branch_with_its_rules_taken_out_is_caught(self):
        def open_branch(s):
            s = s.replace("BRANCH_RE='^[A-Za-z0-9._/-]+$'", "BRANCH_RE='.*'")
            s = s.replace("case $BRANCH in -*|/*|*/|*.lock|*.|*..*|*//*)", "case $BRANCH in __never__)")
            return s.replace('git check-ref-format --branch "$BRANCH"', "true")
        self.run_setup(json.dumps(["https://github.com/o/r#--config=x"]), open_branch)
        self.assertEqual(len(self.clones()), 1, "PLANTED: the bad branch did not reach git clone")
        # Even then it is one argument after --branch, before --: git takes it as a (bad) branch name, not an option.
        self.assertEqual(self.clones()[0][-5:-1], ["--branch", "--config=x", "--", "https://github.com/o/r"])

    def test_planted_only_git_check_ref_format_is_caught(self):
        # x~1 passes neither the character rule nor git; with the character rule widened, git still refuses it.
        wide = lambda s: s.replace("BRANCH_RE='^[A-Za-z0-9._/-]+$'", "BRANCH_RE='.*'")
        r = self.only(self.run_setup("https://github.com/o/r#x~1", wide))
        self.assertEqual((r["status"], r["reason"]), ("failed", "git refuses 'x~1' as a branch name"))
        self.assertEqual(self.clones(), [])
        none = lambda s: wide(s).replace('git check-ref-format --branch "$BRANCH"', "true")
        self.run_setup("https://github.com/o/r#x~1", none)
        self.assertEqual(len(self.clones()), 1, "PLANTED: without git check-ref-format it reached git clone")

    # ---- an existing folder

    def test_the_same_address_and_branch_is_reused_untouched(self):
        f = os.path.join(self.ws, "claims")
        plain_repo(f, "https://bitbucket.org/acme/claims.git", "develop")
        with open(os.path.join(f, "work.txt"), "w") as w:
            w.write("uncommitted")
        m = self.run_setup("https://bitbucket.org/acme/claims#develop")
        r = self.only(m)
        self.assertEqual((r["status"], r["branch"], m["result"], m["reused"]), ("reused", "develop", "ready", 1))
        self.assertEqual(self.clones(), [])
        with open(os.path.join(f, "work.txt")) as w:
            self.assertEqual(w.read(), "uncommitted")

    def test_another_branch_is_failed_by_name_and_never_switched(self):
        f = os.path.join(self.ws, "claims")
        plain_repo(f, "https://bitbucket.org/acme/claims", "main")
        m = self.run_setup("https://bitbucket.org/acme/claims#develop")
        r = self.only(m)
        self.assertEqual((r["status"], m["result"]), ("failed", "failed"))
        self.assertEqual(r["reason"], "on main, asked for develop; it was not switched")
        head = subprocess.run([REAL_GIT, "-C", f, "symbolic-ref", "--short", "HEAD"], capture_output=True, text=True)
        self.assertEqual(head.stdout.strip(), "main")
        self.assertEqual(self.clones(), [])

    def test_planted_reuse_without_the_branch_check_is_caught(self):
        plain_repo(os.path.join(self.ws, "claims"), "https://bitbucket.org/acme/claims", "main")
        planted = lambda s: s.replace('if [ -n "$BRANCH" ] && [ "$cur" != "$BRANCH" ]; then',
                                      'if false; then')
        r = self.only(self.run_setup("https://bitbucket.org/acme/claims#develop", planted))
        self.assertEqual(r["status"], "reused", "PLANTED: another branch was not refused")

    def test_a_folder_that_is_not_that_clone_is_failed_and_left_alone(self):
        os.mkdir(os.path.join(self.ws, "plain"))
        plain_repo(os.path.join(self.ws, "other"), "https://github.com/someone/else", "main")
        os.symlink(self.tmp, os.path.join(self.ws, "linked"))
        m = self.run_setup("https://github.com/o/plain\nhttps://github.com/o/other\nhttps://github.com/o/linked\n")
        self.assertEqual([r["status"] for r in m["repositories"]], ["failed"] * 3)
        self.assertIn("is a clone of https://github.com/someone/else", m["repositories"][1]["reason"])
        self.assertEqual(self.clones(), [])
        self.assertEqual(os.listdir(os.path.join(self.ws, "plain")), [])

    # ---- the list and the map

    def test_both_list_forms_and_a_partial_result(self):
        plain_repo(os.path.join(self.ws, "b"), "https://github.com/o/b", "main")
        m = self.run_setup(" https://github.com/o/a \r\n\nhttps://github.com/o/b#main\nhttp://github.com/o/c\n")
        self.assertEqual([r["status"] for r in m["repositories"]], ["cloned", "reused", "failed"])
        self.assertEqual((m["cloned"], m["reused"], m["failed"], m["result"]), (1, 1, 1, "partial"))
        self.assertEqual(set(m), {"run_id", "repositories", "cloned", "reused", "updated", "reset", "skipped", "failed",
                                  "result"})
        self.assertEqual(set(m["repositories"][0]), {"address", "folder", "branch", "commit", "status", "reason",
                                                      "discarded", "discarded_count"})

    def test_a_list_that_is_not_addresses_fails_the_run(self):
        for bad in ('["https://github.com/o/a", 3]', '["https://github.com/o/a\\nhttps://github.com/o/b"]', "[",
                    "[]", "  \n ", json.dumps(["https://github.com/o/r%d" % i for i in range(101)]),
                    "https://github.com/o/a\nhttps://github.com/x/a"):
            with self.subTest(bad=bad[:40]):
                m = self.run_setup(bad)
                self.assertIn(m["result"], ("failed", "partial"))
                self.assertGreaterEqual(m["failed"], 1)

    def test_the_map_is_never_written_through_a_link(self):
        target = os.path.join(self.tmp, "elsewhere.json")
        os.mkdir(os.path.join(self.ws, ".ekka"))
        os.symlink(target, os.path.join(self.ws, ".ekka", "desk-map.json"))
        r = subprocess.run(self.command("https://github.com/o/r"), capture_output=True, text=True, timeout=60,
                           env={"PATH": self.path, "HOME": self.tmp})
        self.assertNotEqual(r.returncode, 0)
        self.assertFalse(os.path.exists(target))


class TheSetupPlan(unittest.TestCase):
    def test_is_a_command_job_with_no_prompt(self):
        s = desk_plans.render("setup", "code-desk")["definition"]["operations"][0]["steps"][0]
        self.assertNotIn("prompt", s)
        self.assertEqual(s["inputs"]["run_id"], "@ekka.run_id")
        self.assertEqual(s["inputs"]["limits"], {"max_seconds": 1800, "max_retries": 0})
        self.assertEqual(s["inputs"]["facts_from"], "desk_map")
        self.assertEqual(s["inputs"]["outputs"], [{"kind": "desk_map", "path": ".ekka/desk-map.json"}])
        words = s["inputs"]["command"]
        self.assertLessEqual(len(words), desk_plans.MAX_WORDS)
        self.assertTrue(all(0 < len(w.encode()) <= desk_plans.MAX_WORD for w in words))
        # The run's values are whole words of their own, never inside the script bash evaluates.
        self.assertEqual(words[:6], ["/bin/bash", "-c", 'R=$1; I=$2; shift 2; eval "$(printf %s "$@")"', "desk-setup",
                                     "@input.repos", "@ekka.run_id"])
        self.assertNotIn("@input", "".join(words[6:]))

    def test_planted_the_repos_inside_the_script_is_caught(self):
        words = desk_plans.setup_command()
        planted = words[:6] + [words[6].replace("REPOS=${R-}", "REPOS=@input.repos")] + words[7:]
        with self.assertRaises(AssertionError):
            self.assertNotIn("@input", "".join(planted[6:]))


if __name__ == "__main__":
    unittest.main()
