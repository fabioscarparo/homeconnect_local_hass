# ruff: noqa: INP001, S603, S607, T201  # a standalone script that runs git and gh
"""
Track upstream pull requests that the fork hasn't taken yet.

Run daily by .github/workflows/upstream-tracker.yaml. For each open upstream PR (and each
PR merged upstream since the date in .github/upstream-prs.yaml) that isn't ported or
excluded there, it cherry-picks the PR's commits onto beta and pushes the result to
upstream/pr-<number>. One issue lists what applies cleanly, what needs a manual port and
which PRs are proposed for exclusion.

It never opens pull requests. The issue links to prefilled compare pages, so a person
opens each PR, and CI runs on it as usual. Upstream code is only cherry-picked, never run.

Usage: python .github/scripts/upstream_tracker.py [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

import yaml

UPSTREAM = "chris-mc1/homeconnect_local_hass"
UPSTREAM_GIT = f"https://github.com/{UPSTREAM}.git"
BASE = "beta"
CONFIG = Path(".github/upstream-prs.yaml")
BRANCH_PREFIX = "upstream/pr-"
EXCLUSIONS_BRANCH = "upstream-tracker/exclusions"
LABEL = "upstream-tracker"
# A close only counts after this long, in case it was an accident and gets reopened
CLOSE_GRACE = timedelta(days=1)
# PRs updated more recently than this are marked as still changing
SETTLE_TIME = timedelta(days=3)
# redirect.github.com links don't add a "mentioned this" entry to the upstream PR
UPSTREAM_LINK = f"https://redirect.github.com/{UPSTREAM}/pull/{{}}"
PORTED_RE = re.compile(
    rf"\bport(?:ed|s)?\b(?:\s+from)?\s+{re.escape(UPSTREAM)}#(\d+)", re.IGNORECASE
)
# Times in the issue are shown in the maintainer's time zone, PST or PDT
DISPLAY_TZ = ZoneInfo("America/Los_Angeles")
PR_FIELDS = "number,title,author,state,updatedAt,closedAt,mergedAt,additions,deletions"


@dataclass
class Result:
    """What the tracker found for one upstream PR."""

    pr: dict[str, Any]
    status: str  # clean, conflict, workflows, empty or in_fork
    note: str = ""
    fork_pr: int | None = None


def run(*args: str, check: bool = True, cwd: Path | None = None) -> str:
    """Run a command and return its output."""
    return subprocess.run(args, check=check, capture_output=True, text=True, cwd=cwd).stdout.strip()


def gh_json(*args: str) -> Any:
    """Run a gh command that prints JSON."""
    return json.loads(run("gh", *args))


def parse_time(value: str | None) -> datetime | None:
    """Parse a GitHub timestamp."""
    return datetime.fromisoformat(value) if value else None


def plain(text: str) -> str:
    """Keep upstream text from mentioning people or linking issues in this repo."""
    return text.replace("@", "@\u200b").replace("#", "#\u200b")


def load_upstream(since: datetime) -> list[dict[str, Any]]:
    """Get the open upstream PRs and the ones closed since the given date."""
    common = ("pr", "list", "-R", UPSTREAM, "--limit", "500", "--json", PR_FIELDS)
    open_prs = gh_json(*common, "--state", "open")
    closed = gh_json(*common, "--state", "all", "--search", f"closed:>={since.date()}")
    return open_prs + [pr for pr in closed if pr["state"] != "OPEN"]


def fork_references(now: datetime) -> tuple[set[int], dict[int, int], dict[int, int]]:
    """
    Find upstream PRs this repo's own PRs took.

    Returns the ported ones (a merged PR), the ones in progress (an open PR) and the ones
    turned down (a PR closed without merging, past the grace period), the last two mapped to
    the fork PR.
    """
    ported: set[int] = set()
    in_progress: dict[int, int] = {}
    rejected: dict[int, int] = {}
    fields = "number,body,state,headRefName,closedAt"
    for pr in gh_json("pr", "list", "--state", "all", "--limit", "500", "--json", fields):
        numbers = {int(n) for n in PORTED_RE.findall(pr["body"] or "")}
        if pr["headRefName"].startswith(BRANCH_PREFIX):
            numbers.add(int(pr["headRefName"].removeprefix(BRANCH_PREFIX)))
        for number in numbers:
            if pr["state"] == "MERGED":
                ported.add(number)
            elif pr["state"] == "OPEN":
                in_progress[number] = pr["number"]
            elif now - parse_time(pr["closedAt"]) > CLOSE_GRACE:  # type: ignore[operator]
                rejected[number] = pr["number"]
    return ported, in_progress, {n: f for n, f in rejected.items() if n not in ported}


# A key one side of a merge doesn't have
MISSING = object()


class MergeConflictError(Exception):
    """Both sides changed the same translation string."""


def merge_json(base: Any, ours: Any, theirs: Any) -> Any:
    """Three-way merge parsed JSON, keeping our key order and adding new keys at the end."""
    if theirs in (ours, base):
        return ours
    if base == ours:
        return theirs
    if isinstance(ours, dict) and isinstance(theirs, dict):
        base = base if isinstance(base, dict) else {}
        merged = {}
        for key in [*ours, *(key for key in theirs if key not in ours)]:
            value = merge_json(
                base.get(key, MISSING), ours.get(key, MISSING), theirs.get(key, MISSING)
            )
            if value is not MISSING:
                merged[key] = value
        return merged
    raise MergeConflictError


def resolve_translation(path: str, worktree: Path) -> bool:
    """
    Resolve a conflicted translation file by merging the strings.

    PRs add their strings at the same places, so translation files conflict even when the
    strings don't. Only files written the way json.dumps(indent=2) writes them are merged,
    so the result doesn't reformat anything else.
    """
    if not re.fullmatch(r"custom_components/homeconnect_ws/translations/[^/]+\.json", path):
        return False
    stages = []
    for stage in (1, 2, 3):
        shown = subprocess.run(
            ["git", "show", f":{stage}:{path}"], check=False, capture_output=True, cwd=worktree
        )
        if shown.returncode:
            return False
        stages.append(shown.stdout.decode())
    base, ours, theirs = (json.loads(text) for text in stages)
    if stages[1] != json.dumps(ours, indent=2, ensure_ascii=False) + "\n":
        return False
    try:
        merged = merge_json(base, ours, theirs)
    except MergeConflictError:
        return False
    (worktree / path).write_text(
        json.dumps(merged, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n"
    )
    run("git", "add", path, cwd=worktree)
    return True


def replicate(pr: dict[str, Any], worktree: Path, *, dry_run: bool) -> Result:
    """Cherry-pick an upstream PR onto beta and push it to its branch."""
    number = pr["number"]
    branch = f"{BRANCH_PREFIX}{number}"
    head = f"refs/upstream/pr/{number}"
    base = run("git", "merge-base", "refs/upstream/main", head)
    commits = run("git", "rev-list", "--reverse", "--no-merges", f"{base}..{head}").split()
    if not commits:
        return Result(pr, "empty", "the PR has no commits of its own")

    pushed = run("git", "log", "-1", "--format=%B", f"refs/remotes/origin/{branch}", check=False)
    if f"cherry picked from commit {commits[-1]}" in pushed:
        return Result(pr, "clean")

    changed = run("git", "diff", "--name-only", base, head).splitlines()
    if any(path.startswith(".github/workflows/") for path in changed):
        # The workflow's token can't push changes to workflows
        return Result(pr, "workflows", "changes workflows, so it can't be pushed from here")

    run("git", "checkout", "--quiet", "--detach", f"origin/{BASE}", cwd=worktree)
    for commit in commits:
        picked = subprocess.run(
            ["git", "cherry-pick", "-x", "--empty=drop", commit],
            check=False,
            capture_output=True,
            cwd=worktree,
        )
        if not picked.returncode:
            continue
        conflicts = run("git", "diff", "--name-only", "--diff-filter=U", cwd=worktree).split()
        if conflicts and all(resolve_translation(path, worktree) for path in conflicts):
            run("git", "-c", "core.editor=true", "cherry-pick", "--continue", cwd=worktree)
            continue
        run("git", "cherry-pick", "--abort", cwd=worktree, check=False)
        files = ", ".join(f"`{path}`" for path in conflicts) or "see the cherry-pick output"
        return Result(pr, "conflict", f"conflicts in {files}")
    if run("git", "rev-parse", "HEAD", cwd=worktree) == run("git", "rev-parse", f"origin/{BASE}"):
        return Result(pr, "empty", f"all of it is already in {BASE}")
    if not dry_run:
        run("git", "push", "--force", "origin", f"HEAD:refs/heads/{branch}", cwd=worktree)
    return Result(pr, "clean")


def update_exclusions(exclusions: dict[int, str], worktree: Path, *, dry_run: bool) -> None:
    """Push the proposed exclusions to their branch, or delete it when there are none."""
    exists = bool(run("git", "ls-remote", "--heads", "origin", EXCLUSIONS_BRANCH))
    if not exclusions:
        if exists and not dry_run:
            run("git", "push", "origin", "--delete", EXCLUSIONS_BRANCH)
        return
    run("git", "checkout", "--quiet", "--detach", f"origin/{BASE}", cwd=worktree)
    config = worktree / CONFIG
    lines = "".join(f"  {n}: {json.dumps(reason)}\n" for n, reason in sorted(exclusions.items()))
    config.write_text(config.read_text() + lines)
    pushed = run("git", "show", f"origin/{EXCLUSIONS_BRANCH}:{CONFIG}", check=False)
    if exists and pushed == config.read_text().strip():
        return
    numbers = ", ".join(f"#{n}" for n in sorted(exclusions))
    run("git", "commit", "--quiet", "-am", f"Exclude upstream PRs {numbers}", cwd=worktree)
    if not dry_run:
        run(
            "git", "push", "--force", "origin", f"HEAD:refs/heads/{EXCLUSIONS_BRANCH}", cwd=worktree
        )


def compare_link(repo: str, branch: str, **fields: str) -> str:
    """Link to a prefilled new-PR page for a branch."""
    return f"https://github.com/{repo}/compare/{BASE}...{branch}?expand=1&{urlencode(fields)}"


def pr_line(result: Result, repo: str, now: datetime) -> str:
    """Describe one upstream PR in the issue."""
    pr = result.pr
    number, author = pr["number"], pr["author"]["login"]
    parts = [
        f"[{number}]({UPSTREAM_LINK.format(number)}) {plain(pr['title'])}",
        plain(author),
        f"+{pr['additions']} -{pr['deletions']}",
    ]
    if pr["state"] == "MERGED":
        parts.append("merged upstream")
    elif pr["state"] == "CLOSED":
        parts.append("closed upstream in the last day")
    updated = parse_time(pr["updatedAt"])
    if updated and now - updated < SETTLE_TIME:
        parts.append(f"⏳ still changing, updated {updated.astimezone(DISPLAY_TZ).date()}")
    if result.note:
        parts.append(result.note)
    if result.fork_pr:
        parts.append(f"#{result.fork_pr}")
    if result.status == "clean":
        body = f"Ported from {UPSTREAM}#{number} by @{author}, cherry-picked with their commits.\n"
        branch = f"{BRANCH_PREFIX}{number}"
        parts.append(f"[open a PR]({compare_link(repo, branch, title=pr['title'], body=body)})")
    return "- " + " · ".join(parts)


def issue_body(results: list[Result], exclusions: dict[int, str], repo: str, now: datetime) -> str:
    """Build the tracking issue."""
    sections = [
        ("clean", f"Cherry-picks cleanly onto `{BASE}`"),
        ("in_fork", "In progress here"),
        ("conflict", "Needs a manual port"),
        ("workflows", "Needs a manual port (changes workflows)"),
        ("empty", f"Already in `{BASE}`: add to `ported`"),
    ]
    tracker = f"https://github.com/{repo}/blob/{BASE}/.github/workflows/upstream-tracker.yaml"
    lines = [
        (
            f"Upstream PRs from {UPSTREAM} that aren't in this fork yet. The "
            f"[upstream tracker]({tracker}) updates this daily; last run "
            f"{now.astimezone(DISPLAY_TZ):%Y-%m-%d %-I:%M %p %Z}."
        ),
        "",
        (
            "Clean ones are pushed as `upstream/pr-<number>` branches with the original "
            "commits. Clean only means no conflicts: CI on the PR shows what's left, like the "
            "German strings this fork requires. To leave a PR out, add it to "
            f"[`{CONFIG}`](https://github.com/{repo}/blob/{BASE}/{CONFIG})."
        ),
    ]
    for status, title in sections:
        matching = [r for r in results if r.status == status]
        if matching:
            lines += ["", f"### {title} ({len(matching)})", ""]
            lines += [pr_line(r, repo, now) for r in matching]
    if exclusions:
        link = compare_link(repo, EXCLUSIONS_BRANCH, title="Exclude closed upstream PRs", body="")
        lines += ["", f"### Proposed exclusions ({len(exclusions)})", ""]
        lines += [f"- [{n}]({UPSTREAM_LINK.format(n)}) {plain(r)}" for n, r in exclusions.items()]
        lines += ["", f"[Open the exclusions PR]({link})"]
    if not results and not exclusions:
        lines += ["", "Nothing to port right now."]
    return "\n".join(lines) + "\n"


def publish_issue(body: str) -> None:
    """Create or update the tracking issue."""
    found = gh_json("issue", "list", "--label", LABEL, "--state", "open", "--json", "number")
    if found:
        run("gh", "issue", "edit", str(found[0]["number"]), "--body", body)
        return
    run("gh", "label", "create", LABEL, "--color", "C5DEF5", check=False)
    run(
        "gh", "issue", "create", "--title", "Upstream PRs to port", "--label", LABEL, "--body", body
    )


def main() -> None:
    """Run the tracker."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--dry-run", action="store_true", help="print the issue, push nothing")
    dry_run = parser.parse_args().dry_run

    now = datetime.now(UTC)
    repo = gh_json("repo", "view", "--json", "nameWithOwner")["nameWithOwner"]
    config = yaml.safe_load(CONFIG.read_text())
    since = datetime.combine(config["since"], time(), UTC)
    handled = set(config.get("ported") or {}) | set(config.get("excluded") or {})
    ignore = set(config.get("ignore_authors") or [])
    ported, in_progress, rejected = fork_references(now)

    pending: list[dict[str, Any]] = []
    exclusions: dict[int, str] = {}
    for pr in sorted(load_upstream(since), key=lambda pr: -pr["number"]):
        number = pr["number"]
        if pr["author"].get("is_bot") or pr["author"]["login"] in ignore:
            continue
        if number in handled or number in ported:
            continue
        closed = parse_time(pr["closedAt"])
        if number in rejected and number not in in_progress:
            exclusions[number] = f"{pr['title']} (fork PR #{rejected[number]} closed)"
        elif pr["state"] == "CLOSED" and closed and now - closed > CLOSE_GRACE:
            exclusions[number] = (
                f"{pr['title']} (closed upstream on {closed.astimezone(DISPLAY_TZ).date()})"
            )
        else:
            pending.append(pr)

    run("git", "fetch", "--quiet", "--prune", "origin")
    refspecs = [f"+refs/pull/{pr['number']}/head:refs/upstream/pr/{pr['number']}" for pr in pending]
    run("git", "fetch", "--quiet", UPSTREAM_GIT, "+refs/heads/main:refs/upstream/main", *refspecs)

    results: list[Result] = []
    with tempfile.TemporaryDirectory() as tmp:
        worktree = Path(tmp) / "worktree"
        run("git", "worktree", "add", "--quiet", "--detach", str(worktree), f"origin/{BASE}")
        try:
            for pr in pending:
                if (fork_pr := in_progress.get(pr["number"])) is not None:
                    results.append(Result(pr, "in_fork", fork_pr=fork_pr))
                else:
                    results.append(replicate(pr, worktree, dry_run=dry_run))
            update_exclusions(exclusions, worktree, dry_run=dry_run)
        finally:
            run("git", "worktree", "remove", "--force", str(worktree), check=False)

    # Drop branches nothing uses any more: no longer clean, or no longer pending
    keep = {f"{BRANCH_PREFIX}{r.pr['number']}" for r in results if r.status in ("clean", "in_fork")}
    branches = run("git", "for-each-ref", "--format=%(refname:lstrip=3)", "refs/remotes/origin/")
    stale = [b for b in branches.split() if b.startswith(BRANCH_PREFIX) and b not in keep]

    body = issue_body(results, exclusions, repo, now)
    if dry_run:
        print(body)
        print("Would delete:", ", ".join(stale) or "nothing")
        return
    for branch in stale:
        run("git", "push", "--quiet", "origin", "--delete", branch)
    publish_issue(body)


if __name__ == "__main__":
    main()
