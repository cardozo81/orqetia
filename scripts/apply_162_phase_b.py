#!/usr/bin/env python3
"""One-shot, fail-closed GitHub Phase-B ruleset change via existing gh login.

Default mode is read-only. Use --apply with an explicit, expected main SHA.
No credentials, local backups, Docker state or repository files are changed.
The #164 external security-intake gate remains mandatory before first RC.
"""

from __future__ import annotations

import argparse
import copy
import json
import subprocess
import sys
from typing import Any, Protocol

REPO = "cardozo81/orqetia"
RULESET_ID = 24785675
ACTIONS_APP_ID = 15368
PROOF_PR = 172
PROOF_SHA = "97dac6539669a52f38b6d8126d7673c6da9bcd9c"
CONTEXTS = (
    "baseline",
    "pre-rc-security",
    "schema-compatibility",
    "supply-chain",
    "postgres-restore-drill",
    "availability-contract",
    "observability-contract",
    "operational-runbooks",
    "quality",
)
EXPECTED_BASE_TYPES = frozenset({"deletion", "non_fast_forward"})
EXPECTED_FINAL_TYPES = EXPECTED_BASE_TYPES | {"pull_request", "required_status_checks"}


class Refusal(Exception):
    """A precondition or postcondition could not be proven."""


class GitHub(Protocol):
    def request(self, method: str, path: str, payload: object | None = None) -> Any: ...


class GhCli:
    """Access repository administration through an existing authenticated CLI."""

    def request(self, method: str, path: str, payload: object | None = None) -> Any:
        if method not in {"GET", "PUT", "PATCH"}:
            raise Refusal("unsupported GitHub API method")
        command = ["gh", "api", "-X", method, path]
        if payload is not None:
            command += ["--input", "-"]
        try:
            result = subprocess.run(
                command,
                input=json.dumps(payload) if payload is not None else None,
                capture_output=True,
                text=True,
                check=False,
                timeout=50,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise Refusal("GitHub CLI unavailable or timed out") from exc
        if result.returncode != 0:
            # Do not echo stderr: it can contain sensitive local data.
            raise Refusal(f"GitHub API {method} failed; stop and inspect privately")
        try:
            return json.loads(result.stdout)
        except ValueError as exc:
            raise Refusal("GitHub API returned invalid JSON") from exc


def _path(relative: str) -> str:
    return f"repos/{REPO}/{relative}"


def _types(ruleset: dict[str, Any]) -> frozenset[str]:
    rules = ruleset.get("rules")
    if not isinstance(rules, list) or any(not isinstance(x, dict) for x in rules):
        raise Refusal("ruleset rules missing or malformed")
    kinds = [x.get("type") for x in rules]
    if len(kinds) != len(set(kinds)) or not all(isinstance(x, str) for x in kinds):
        raise Refusal("duplicate or malformed ruleset rule types")
    return frozenset(kinds)


def _baseline(ruleset: dict[str, Any]) -> None:
    if (
        ruleset.get("id") != RULESET_ID
        or ruleset.get("name") != "orqetia-main-integrity-development"
        or ruleset.get("target") != "branch"
        or ruleset.get("enforcement") != "active"
        or ruleset.get("conditions") != {
            "ref_name": {"exclude": [], "include": ["~DEFAULT_BRANCH"]}
        }
        or ruleset.get("bypass_actors") != []
    ):
        raise Refusal("Phase-A ruleset identity, protection target or bypass differs")
    rules = ruleset.get("rules", [])
    for kind in EXPECTED_BASE_TYPES:
        expected = [x for x in rules if x.get("type") == kind]
        if expected != [{"type": kind}]:
            raise Refusal("Phase-A deletion or non-fast-forward rule changed")


def _target(ruleset: dict[str, Any]) -> dict[str, Any]:
    """Compose exact approved ruleset without removing any Phase-A protection."""
    _baseline(ruleset)
    payload = {
        "name": ruleset["name"],
        "target": ruleset["target"],
        "enforcement": ruleset["enforcement"],
        "conditions": copy.deepcopy(ruleset["conditions"]),
        "bypass_actors": [],
        "rules": [
            {"type": "deletion"},
            {"type": "non_fast_forward"},
            {
                "type": "pull_request",
                "parameters": {
                    "dismiss_stale_reviews_on_push": False,
                    "require_code_owner_review": False,
                    "require_last_push_approval": False,
                    "required_approving_review_count": 0,
                    "required_review_thread_resolution": False,
                    "required_reviewers": [],
                    "require_extra_approval_for_unattributed_changes": False,
                    "allowed_merge_methods": ["merge", "squash", "rebase"],
                },
            },
            {
                "type": "required_status_checks",
                "parameters": {
                    "strict_required_status_checks_policy": True,
                    "do_not_enforce_on_create": False,
                    "required_status_checks": [
                        {"context": name, "integration_id": ACTIONS_APP_ID}
                        for name in CONTEXTS
                    ],
                },
            },
        ],
    }
    return payload


def _normalized(ruleset: dict[str, Any]) -> dict[str, Any]:
    """Only compare fields we explicitly own; don't mix read-only API metadata."""
    return {
        "name": ruleset.get("name"),
        "target": ruleset.get("target"),
        "enforcement": ruleset.get("enforcement"),
        "conditions": ruleset.get("conditions"),
        "bypass_actors": ruleset.get("bypass_actors"),
        "rules": ruleset.get("rules"),
    }


def _final(ruleset: dict[str, Any]) -> None:
    _baseline(ruleset)
    if _types(ruleset) != EXPECTED_FINAL_TYPES:
        raise Refusal("required PR or status-check rule missing or extra rule present")
    expected = _target(ruleset)
    if _normalized(ruleset) != expected:
        raise Refusal("ruleset read-back does not match approved protection settings")


def _proof(github: GitHub) -> None:
    pr = github.request("GET", _path(f"pulls/{PROOF_PR}"))
    if not isinstance(pr, dict) or (
        pr.get("merged") is not True
        or pr.get("head", {}).get("sha") != PROOF_SHA
        or pr.get("base", {}).get("ref") != "main"
    ):
        raise Refusal("previous substantive PR proof was not confirmed")
    run_data = github.request("GET", _path(f"commits/{PROOF_SHA}/check-runs?per_page=100"))
    if not isinstance(run_data, dict):
        raise Refusal("historic PR check response missing")
    runs = run_data.get("check_runs")
    if not isinstance(runs, list) or run_data.get("total_count") != len(runs):
        raise Refusal("historic PR check listing incomplete or truncated")
    for name in CONTEXTS:
        candidates = [x for x in runs if isinstance(x, dict) and x.get("name") == name]
        if len(candidates) != 1:
            raise Refusal(f"historic PR check identity absent or ambiguous: {name}")
        check = candidates[0]
        app = check.get("app") or {}
        if (
            check.get("status") != "completed"
            or check.get("conclusion") != "success"
            or check.get("head_sha") != PROOF_SHA
            or app.get("id") != ACTIONS_APP_ID
            or app.get("slug") != "github-actions"
        ):
            raise Refusal(f"historic substantive PR check mismatch: {name}")


def _snapshot(github: GitHub, expected_head: str) -> tuple[dict[str, Any], dict[str, Any]]:
    metadata = github.request("GET", _path("").rstrip("/"))
    branch = github.request("GET", _path("branches/main"))
    ruleset = github.request("GET", _path(f"rulesets/{RULESET_ID}"))
    rulesets = github.request("GET", _path("rulesets?per_page=100"))
    open_prs = github.request("GET", _path("pulls?state=open&per_page=100"))
    head_checks = github.request("GET", _path(f"commits/{expected_head}/check-runs?per_page=100"))
    if not isinstance(head_checks, dict) or (
        not isinstance(head_checks.get("check_runs"), list)
        or head_checks.get("total_count") != len(head_checks["check_runs"])
        or not head_checks["check_runs"]
        or any(
            check.get("status") != "completed" or check.get("conclusion") != "success"
            for check in head_checks["check_runs"]
        )
    ):
        raise Refusal("main SHA has incomplete or non-successful CI checks")
    if (
        not isinstance(metadata, dict)
        or metadata.get("full_name") != REPO
        or metadata.get("default_branch") != "main"
        or metadata.get("permissions", {}).get("admin") is not True
        or not isinstance(branch, dict)
        or branch.get("commit", {}).get("sha") != expected_head
        or branch.get("protected") is not True
    ):
        raise Refusal("repository administration, protected main or expected SHA mismatch")
    if not isinstance(ruleset, dict):
        raise Refusal("ruleset response malformed")
    _baseline(ruleset)
    if _types(ruleset) not in {EXPECTED_BASE_TYPES, EXPECTED_FINAL_TYPES}:
        raise Refusal("unexpected partial or additional ruleset configuration")
    if not isinstance(rulesets, list) or (
        len(rulesets) != 1 or rulesets[0].get("id") != RULESET_ID
    ):
        raise Refusal("additional rulesets detected; evaluate separately")
    if not isinstance(open_prs, list) or open_prs:
        raise Refusal("open pull requests detected; avoid changing integration mid-flight")
    return metadata, ruleset


def execute(github: GitHub, expected_head: str, *, apply: bool) -> str:
    """Preflight, optional authorized PUT/PATCH and exact read-back."""
    metadata, ruleset = _snapshot(github, expected_head)
    _proof(github)
    target = _target(ruleset)
    kinds = _types(ruleset)
    if kinds == EXPECTED_FINAL_TYPES:
        _final(ruleset)
        configured = True
    else:
        configured = False
    if not apply:
        return ("DRY-RUN: existing Phase-B rules match" if configured
                else "DRY-RUN: authorized Phase-B transition is ready")

    if not configured:
        # Optimistic concurrency: refuse if branch or ruleset changed since validation.
        newest = github.request("GET", _path("branches/main"))
        reread = github.request("GET", _path(f"rulesets/{RULESET_ID}"))
        if newest.get("commit", {}).get("sha") != expected_head or (
            _normalized(reread) != _normalized(ruleset)
        ):
            raise Refusal("repository changed during preflight; no administrative write")
        github.request("PUT", _path(f"rulesets/{RULESET_ID}"), target)
        readback = github.request("GET", _path(f"rulesets/{RULESET_ID}"))
        _final(readback)
    else:
        _final(ruleset)

    # Automatic branch deletion is only configured after GitHub has accepted/read
    # back the PR-enforcement ruleset, and #172 proved a substantive PR workflow.
    if metadata.get("delete_branch_on_merge") is not True:
        github.request("PATCH", _path("").rstrip("/"), {"delete_branch_on_merge": True})
    final_repo = github.request("GET", _path("").rstrip("/"))
    final_rules = github.request("GET", _path(f"rulesets/{RULESET_ID}"))
    final_branch = github.request("GET", _path("branches/main"))
    _final(final_rules)
    if (
        final_repo.get("delete_branch_on_merge") is not True
        or final_branch.get("commit", {}).get("sha") != expected_head
        or final_branch.get("protected") is not True
    ):
        raise Refusal("post-write repository settings or main SHA mismatch")
    return "APPLIED: PR + nine required checks verified; branch auto-delete enabled"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-head", required=True, help="exact 40-char main SHA")
    parser.add_argument("--apply", action="store_true", help="authorize administrative PUT/PATCH")
    args = parser.parse_args(argv)
    head = args.expected_head
    if len(head) != 40 or any(x not in "0123456789abcdef" for x in head):
        print("REFUSED: expected head must be exactly 40 lowercase hex characters")
        return 2
    try:
        result = execute(GhCli(), head, apply=args.apply)
    except Refusal as exc:
        print(f"REFUSED: {exc}")
        print("#162 remains OPEN; #164 remains mandatory before RC; DEVELOPMENT")
        return 2
    print(result)
    print("#164 and #46 still independently block RC; DEVELOPMENT; no release")
    return 0


if __name__ == "__main__":
    sys.exit(main())
