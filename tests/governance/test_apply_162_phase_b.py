from __future__ import annotations

import copy

import pytest

from scripts.apply_162_phase_b import (
    ACTIONS_APP_ID,
    CONTEXTS,
    PROOF_SHA,
    REPO,
    RULESET_ID,
    Refusal,
    execute,
)

HEAD = "a" * 40
BASE_PATH = f"repos/{REPO}"


class FakeGitHub:
    def __init__(self) -> None:
        self.operations: list[tuple[str, str]] = []
        self.repo = {
            "full_name": REPO,
            "default_branch": "main",
            "permissions": {"admin": True},
            "delete_branch_on_merge": False,
        }
        self.branch = {"name": "main", "protected": True, "commit": {"sha": HEAD}}
        self.ruleset = {
            "id": RULESET_ID,
            "name": "orqetia-main-integrity-development",
            "target": "branch",
            "enforcement": "active",
            "conditions": {"ref_name": {"exclude": [], "include": ["~DEFAULT_BRANCH"]}},
            "bypass_actors": [],
            "rules": [{"type": "deletion"}, {"type": "non_fast_forward"}],
        }
        self.rulesets = [{"id": RULESET_ID}]
        self.open_prs: list[object] = []
        self.proof = {
            "merged": True, "head": {"sha": PROOF_SHA}, "base": {"ref": "main"}
        }
        self.proof_checks = {
            "total_count": len(CONTEXTS),
            "check_runs": [
                {
                    "name": context,
                    "head_sha": PROOF_SHA,
                    "status": "completed",
                    "conclusion": "success",
                    "app": {"id": ACTIONS_APP_ID, "slug": "github-actions"},
                }
                for context in CONTEXTS
            ],
        }
        self.main_checks = {
            "total_count": 1,
            "check_runs": [{"name": "quality", "status": "completed", "conclusion": "success"}],
        }
        self.drift_rule_on_second_read = False
        self.rule_gets = 0

    def request(self, method: str, path: str, payload: object | None = None) -> object:
        self.operations.append((method, path))
        assert path.startswith(BASE_PATH), path
        resource = path[len(BASE_PATH):]
        if method == "PUT":
            assert resource == f"/rulesets/{RULESET_ID}"
            assert isinstance(payload, dict)
            self.ruleset = {"id": RULESET_ID, **copy.deepcopy(payload)}
            return copy.deepcopy(self.ruleset)
        if method == "PATCH":
            assert resource == "" and payload == {"delete_branch_on_merge": True}
            self.repo["delete_branch_on_merge"] = True
            return copy.deepcopy(self.repo)
        assert method == "GET", method
        if resource == "":
            value = self.repo
        elif resource == "/branches/main":
            value = self.branch
        elif resource == f"/rulesets/{RULESET_ID}":
            self.rule_gets += 1
            if self.drift_rule_on_second_read and self.rule_gets == 2:
                self.ruleset["name"] = "external-change"
            value = self.ruleset
        elif resource == "/rulesets?per_page=100":
            value = self.rulesets
        elif resource == "/pulls?state=open&per_page=100":
            value = self.open_prs
        elif resource == "/pulls/172":
            value = self.proof
        elif resource == f"/commits/{PROOF_SHA}/check-runs?per_page=100":
            value = self.proof_checks
        elif resource == f"/commits/{HEAD}/check-runs?per_page=100":
            value = self.main_checks
        else:
            raise AssertionError(resource)
        return copy.deepcopy(value)


def test_read_only_mode_never_writes() -> None:
    fake = FakeGitHub()
    assert "DRY-RUN" in execute(fake, HEAD, apply=False)
    assert fake.ruleset["rules"] == [
        {"type": "deletion"}, {"type": "non_fast_forward"}
    ]
    assert all(method == "GET" for method, _ in fake.operations)


def test_apply_and_readback_preserve_protections_and_all_nine_checks() -> None:
    fake = FakeGitHub()
    assert execute(fake, HEAD, apply=True).startswith("APPLIED:")
    assert [method for method, _ in fake.operations if method != "GET"] == ["PUT", "PATCH"]
    assert fake.repo["delete_branch_on_merge"] is True
    assert fake.ruleset["bypass_actors"] == []
    assert fake.ruleset["conditions"] == {
        "ref_name": {"exclude": [], "include": ["~DEFAULT_BRANCH"]}
    }
    rules = {entry["type"]: entry for entry in fake.ruleset["rules"]}
    assert set(rules) == {
        "deletion", "non_fast_forward", "pull_request", "required_status_checks"
    }
    assert rules["pull_request"]["parameters"]["required_approving_review_count"] == 0
    assert rules["required_status_checks"]["parameters"]["required_status_checks"] == [
        {"context": context, "integration_id": ACTIONS_APP_ID} for context in CONTEXTS
    ]
    assert fake.branch["commit"]["sha"] == HEAD


def test_idempotent_replay_never_rewrites_settings() -> None:
    fake = FakeGitHub()
    execute(fake, HEAD, apply=True)
    fake.operations.clear()
    assert execute(fake, HEAD, apply=True).startswith("APPLIED:")
    assert all(method == "GET" for method, _ in fake.operations)


@pytest.mark.parametrize(
    "modify",
    [
        lambda obj: obj.repo.update({"permissions": {"admin": False}}),
        lambda obj: obj.branch["commit"].update({"sha": "0" * 40}),
        lambda obj: obj.ruleset.update({"bypass_actors": [{"actor_type": "OrganizationAdmin"}]}),
        lambda obj: obj.rulesets.append({"id": 999}),
        lambda obj: obj.open_prs.append({"number": 300}),
        lambda obj: obj.proof.update({"merged": False}),
        lambda obj: obj.proof_checks["check_runs"][0].update({"conclusion": "neutral"}),
        lambda obj: obj.proof_checks["check_runs"][0]["app"].update({"id": 999}),
        lambda obj: obj.main_checks["check_runs"][0].update({"status": "in_progress"}),
    ],
)
def test_preflight_refuses_and_never_writes(modify: object) -> None:
    fake = FakeGitHub()
    modify(fake)
    with pytest.raises(Refusal):
        execute(fake, HEAD, apply=True)
    assert all(method == "GET" for method, _ in fake.operations)


def test_concurrent_ruleset_change_is_refused_without_writes() -> None:
    fake = FakeGitHub()
    fake.drift_rule_on_second_read = True
    with pytest.raises(Refusal, match="changed during preflight"):
        execute(fake, HEAD, apply=True)
    assert all(method == "GET" for method, _ in fake.operations)


def test_partial_ruleset_state_is_refused_without_writes() -> None:
    fake = FakeGitHub()
    fake.ruleset["rules"].append({"type": "pull_request"})
    with pytest.raises(Refusal, match="partial"):
        execute(fake, HEAD, apply=True)
    assert all(method == "GET" for method, _ in fake.operations)
