# shellcheck shell=bash
#
# Helpers for workflow steps that read or write the gh-pages branch. Source it:
#
#     source .github/scripts/ghpages.sh
#
# Why this exists
# ---------------
# Every one of those steps used to run `git fetch origin gh-pages || true` and
# then decide whether the branch "exists" by looking for the remote-tracking ref.
# That cannot tell "the branch does not exist yet" from "GitHub could not be
# reached", so a network blip was read as a first run:
#
#   * the ledger of posted stories was treated as empty, so stories repeat, and
#     the tracked placeholder file later replaced the real ledger on the remote;
#   * the CSV history was rebuilt from nothing and published over the real one
#     (59 rows became 1);
#   * a worktree was based on a ref cached hours earlier — daily-post now waits
#     hours for its publish slot, and collect-insights pushes in the meantime —
#     so the push was rejected and the post never went out.
#
# The two cases are told apart with `git ls-remote --exit-code`: exit 0 means the
# branch exists, 2 means it genuinely does not, and anything else means the
# remote could not be read. Only 2 is allowed to mean "first run".
#
# Callers run under `set -e`, so a function that returns non-zero must be invoked
# as `rc=0; fetch_gh_pages || rc=$?` or inside an `if`.
#
# Every command below that can fail is checked explicitly (`|| return 1`) instead
# of relying on `set -e`. Bash switches errexit off inside any function that is
# called as an `if` condition, and a caller does exactly that to print a warning
# when a push fails — with errexit silently off, a refusal from the apply step
# would be swallowed and reported as "nothing to change".

# Fetch gh-pages into refs/remotes/origin/gh-pages, retrying on network errors.
# Returns 0 if fetched, 2 if the branch does not exist, 1 if the remote could not
# be read after four attempts.
fetch_gh_pages() {
  local attempt rc
  for attempt in 1 2 3 4; do
    rc=0
    git ls-remote --exit-code --heads origin gh-pages >/dev/null 2>&1 || rc=$?
    if [ "$rc" -eq 2 ]; then
      return 2
    fi
    if [ "$rc" -eq 0 ] && git fetch --quiet origin gh-pages; then
      return 0
    fi
    echo "gh-pages: could not read the remote (attempt ${attempt}/4)" >&2
    if [ "$attempt" -lt 4 ]; then
      sleep $((attempt * 5))
    fi
  done
  return 1
}

# Create ./gh-pages-wt checked out on gh-pages, or reuse it if it already exists.
# Reuse matters: git refuses to check one branch out into two worktrees, which is
# what run #133 hit. On the very first run, when the branch does not exist yet, an
# orphan branch is started instead.
open_gh_pages_worktree() {
  local rc=0
  if [ -d gh-pages-wt ]; then
    return 0
  fi
  fetch_gh_pages || rc=$?
  case "$rc" in
    0) git worktree add -q -B gh-pages gh-pages-wt origin/gh-pages || return 1 ;;
    2) git worktree add -q --orphan -b gh-pages gh-pages-wt || return 1 ;;
    *)
      echo "gh-pages: the remote could not be read; refusing to guess" >&2
      return 1
      ;;
  esac
  git -C gh-pages-wt config user.email "actions@github.com" || return 1
  git -C gh-pages-wt config user.name "TriAgent Bot" || return 1
}

# Usage: gh_pages_commit_and_push "<commit message>" <apply-function>
#
# <apply-function> runs inside the worktree and must copy in only the files this
# step owns. If it returns non-zero the whole call fails at once — that is a
# refusal (for example, a CSV that would shrink), not something to retry.
#
# It runs where errexit is off (inside `&&` / `||`), so end every command in it
# that can fail with `|| return 1`: a `cp` that silently fails would otherwise be
# pushed as if it had worked.
#
# gh-pages has more than one writer (daily-post pushes the card and the ledger,
# collect-insights pushes the CSVs), so each attempt starts from the remote's
# latest state and re-applies the step's files on top of it. A rejected push
# re-syncs and tries again instead of failing — after the post has gone out that
# would leave the stories unrecorded. Resetting discards nothing: everything this
# run pushed earlier is already on the remote, and the files being applied are
# written only by the step that owns them.
gh_pages_commit_and_push() {
  local message=$1 apply=$2 attempt rc staged
  for attempt in 1 2 3 4 5; do
    rc=0
    fetch_gh_pages || rc=$?
    case "$rc" in
      0) git -C gh-pages-wt reset -q --hard origin/gh-pages || return 1 ;;
      2) : ;; # no branch yet: the first push creates it
      *)
        echo "gh-pages: the remote could not be read; not pushing blind" >&2
        return 1
        ;;
    esac
    (cd gh-pages-wt && "$apply" && git add -A) || return 1
    staged=0
    git -C gh-pages-wt diff --cached --quiet || staged=1
    if [ "$staged" -eq 1 ]; then
      git -C gh-pages-wt commit -q -m "$message" || return 1
    elif [ "$rc" -eq 0 ] || ! git -C gh-pages-wt rev-parse --verify -q HEAD >/dev/null 2>&1; then
      # Nothing staged. That means "done" only when we are level with a branch
      # that exists (rc 0 just reset us to it), or when there is nothing at all.
      echo "gh-pages: nothing to change"
      return 0
    fi
    # Reaching here with nothing staged and rc 2 means an earlier attempt
    # committed to a branch the remote still does not have and its push failed.
    # That commit is unpushed work, not "nothing to change": push it. Reporting
    # success here would let a failed first publish pass, and the publisher
    # would then wait out its timeout on a 404.
    if git -C gh-pages-wt push -q origin gh-pages; then
      return 0
    fi
    echo "gh-pages: push rejected (attempt ${attempt}/5): another workflow wrote in between; retrying" >&2
    sleep $((attempt * 3))
  done
  echo "gh-pages: gave up after 5 attempts" >&2
  return 1
}
