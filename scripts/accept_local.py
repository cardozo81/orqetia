"""HTTP acceptance against the real local Docker processes; never prints secrets.

Run from the repository with the locked Python environment. The Caddy CA is
trusted only by this process, without bypassing TLS or changing Windows trust.
"""

from __future__ import annotations

import argparse
import html
import json
import re
import ssl
import subprocess
import time
from pathlib import Path
from uuid import uuid4

import httpx

ROOT = Path(__file__).resolve().parents[1]
BASE = "https://localhost:8443"
EVIDENCE: list[dict[str, object]] = []
SENSITIVE = (
    "provider_secret",
    "secret_reference",
    "provider_account_id",
    "provider_credential_id",
    "provider_balance",
    "provider_cost",
    "internal_currency",
    "client_charge",
    "estimated_cost",
    "estimated_currency",
    "observed_cost",
    "observed_currency",
)


def compose(*args: str) -> str:
    result = subprocess.run(
        ["docker", "compose", *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    if result.returncode:
        raise RuntimeError(f"Compose operation failed ({args[0]}); inspect service status")
    return result.stdout


def private_json(name: str) -> dict:
    return json.loads(
        compose("exec", "-T", "backoffice", "cat", f"/var/lib/orqetia/bootstrap/{name}.json")
    )


def check(name: str, condition: bool, detail: str = "") -> None:
    EVIDENCE.append(
        {"scenario": name, "status": "PASS" if condition else "FAIL", "evidence": detail}
    )
    print(f"{'PASS' if condition else 'FAIL'} {name}", flush=True)
    (ROOT / ".local" / "acceptance.json").write_text(
        json.dumps(EVIDENCE, indent=2),
        encoding="utf-8",
    )
    if not condition:
        raise AssertionError(name)


def client(token: str | None = None) -> httpx.Client:
    return httpx.Client(
        base_url=BASE,
        verify=ssl.create_default_context(cafile=ROOT / ".local/caddy-root.crt"),
        timeout=15,
        trust_env=False,
        headers={} if token is None else {"Authorization": f"Bearer {token}"},
    )


def safe(response: httpx.Response) -> None:
    check(
        "client confidentiality " + response.request.url.path,
        not any(term in response.text.lower() for term in SENSITIVE),
    )


def login(profile: str, tokens: dict, manifest: dict) -> httpx.Client:
    c = client()
    surface = "backoffice" if profile == "backoffice-admin" else "portal"
    start = c.get(f"/{surface}/login")
    check(f"{profile} login redirect", start.status_code == 302)
    page = c.get(start.headers["location"])
    transaction = html.unescape(
        re.search(
            r"name='transaction' value='([^']+)'",
            page.text,
        ).group(1)
    )
    denied = c.post(
        "/dev-idp/select",
        headers={"Origin": BASE},
        data={
            "transaction": transaction,
            "profile": profile,
            "access_token": "invalid",
        },
    )
    check(f"{profile} rejects invalid local token", denied.status_code == 403)
    selected = c.post(
        "/dev-idp/select",
        headers={"Origin": BASE},
        data={
            "transaction": transaction,
            "profile": profile,
            "access_token": tokens[profile],
        },
    )
    check(f"{profile} local authentication", selected.status_code == 303)
    callback = c.get(selected.headers["location"])
    check(f"{profile} session", callback.status_code == 303)
    cookie_headers = callback.headers.get_list("set-cookie")
    check(
        f"{profile} secure session cookie",
        any(
            "HttpOnly" in value and "Secure" in value and "SameSite=lax" in value
            for value in cookie_headers
        ),
    )
    if surface == "portal":
        role = profile.removeprefix("client-")
        response = c.post(
            "/portal/select-membership",
            headers={"Origin": BASE},
            data={
                "membership_id": manifest["memberships"][role],
                "csrf_token": c.cookies.get("__Host-orqetia-portal-csrf"),
            },
        )
        check(f"{profile} owner selection", response.status_code == 303)
    check(f"{profile} authenticated home", c.get(f"/{surface}").status_code == 200)
    return c


def mutation(c: httpx.Client, path: str, data: dict) -> httpx.Response:
    surface = "bo" if path.startswith("/backoffice") else "portal"
    return c.post(
        path,
        headers={"Origin": BASE},
        data={
            **data,
            "csrf_token": c.cookies.get(f"__Host-orqetia-{surface}-csrf"),
        },
    )


def session(c: httpx.Client) -> str:
    r = c.post("/v1/sessions", headers={"Idempotency-Key": str(uuid4())}, json={})
    check("HTTP Session create", r.status_code == 201, str(r.status_code))
    return r.json()["session_id"]


def task(
    c: httpx.Client, sid: str, *, operation: str = "TASK_EXECUTION", model: str | None = None
) -> dict:
    body = {"operation": operation, "input": {"input_text": "synthetic local acceptance"}}
    if model:
        body["execution"] = {
            "mode": "EXPLICIT_TARGET",
            "target": {
                "provider": "ORQETIA_TEST_PROVIDER",
                "model": model,
                "reasoning_profile": "standard",
            },
        }
    key = str(uuid4())
    r = c.post(f"/v1/sessions/{sid}/tasks", json=body, headers={"Idempotency-Key": key})
    check("HTTP Task create", r.status_code == 202, str(r.status_code))
    tid = r.json()["task_id"]
    replay = c.post(f"/v1/sessions/{sid}/tasks", json=body, headers={"Idempotency-Key": key})
    check("Task idempotency", replay.status_code == 202 and replay.json()["task_id"] == tid)
    for _ in range(60):
        current = c.get(f"/v1/tasks/{tid}")
        data = current.json()
        if data.get("terminal_at"):
            safe(current)
            return data
        time.sleep(1)
    raise AssertionError("task did not finish within 60 seconds")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--with-recovery", action="store_true")
    options = parser.parse_args()
    local = ROOT / ".local"
    local.mkdir(exist_ok=True)
    compose(
        "cp", "gateway:/data/caddy/pki/authorities/local/root.crt", str(local / "caddy-root.crt")
    )
    manifest = private_json("manifest")
    tokens = private_json("client-api-tokens")
    human = private_json("local-login-tokens")
    public = client()
    for path in ("/health/live", "/health/ready"):
        check(path, public.get(path).status_code == 200)
    canonical = json.loads(
        (ROOT / "contracts/openapi/orqetia-v1.openapi.json").read_text(encoding="utf-8")
    )
    check("served canonical OpenAPI", public.get("/openapi.json").json() == canonical)
    migrated = compose(
        "exec",
        "-T",
        "postgres",
        "psql",
        "-U",
        "orqetia",
        "-d",
        "orqetia",
        "-Atc",
        "SELECT version_num FROM public.alembic_version;",
    ).strip()
    check("migration head", migrated == "20261006_0032", migrated)
    c = client(tokens["client_a1_full"])
    b = client(tokens["client_b1_full"])
    check("invalid API bearer", client("invalid").get("/v1/providers").status_code == 401)
    for path in ("/v1/providers", "/v1/credentials", "/v1/models"):
        r = c.get(path)
        check(path, r.status_code == 200)
        safe(r)
    sid = session(c)
    auto = task(c, sid)
    check(
        "AUTO defaults to economy",
        auto["status"] == "COMPLETE"
        and auto["requested_execution_mode"] == "AUTO"
        and auto["attempts"][0]["model_id"] == "economy",
    )
    escalation = task(c, sid, operation="LOCAL_ESCALATION")
    check(
        "AUTO cost escalation",
        escalation["status"] == "COMPLETE"
        and [a["model_id"] for a in escalation["attempts"]] == ["economy", "premium"],
    )
    explicit = task(c, sid, operation="LOCAL_ESCALATION", model="economy")
    check(
        "EXPLICIT_TARGET never crosses targets",
        explicit["status"] != "COMPLETE"
        and all(a["model_id"] == "economy" for a in explicit["attempts"]),
    )
    premium = task(c, sid, model="premium")
    check(
        "EXPLICIT_TARGET premium succeeds",
        premium["status"] == "COMPLETE"
        and all(a["model_id"] == "premium" for a in premium["attempts"]),
    )
    tid = auto["task_id"]
    aid = auto["attempts"][0]["attempt_id"]
    for path in (
        f"/v1/tasks/{tid}",
        f"/v1/tasks/{tid}/result",
        f"/v1/tasks/{tid}/attempts",
        f"/v1/tasks/{tid}/attempts/{aid}/exchanges",
    ):
        r = c.get(path)
        check(path, r.status_code == 200)
        safe(r)
        check("API owner isolation " + path, b.get(path).status_code in {403, 404})
    foreign_session = session(b)
    blocked = task(b, foreign_session)
    check(
        "HARD quota before provider dispatch",
        blocked["status"] != "COMPLETE" and not blocked["attempts"],
    )
    for _ in range(10):
        check("benchmark observed sample", task(c, sid)["status"] == "COMPLETE")
    usage = c.get("/v1/usage")
    check("persisted usage projection", usage.status_code == 200 and bool(usage.json()["items"]))
    safe(usage)
    estimate = c.post(
        "/v1/estimates",
        headers={"Idempotency-Key": str(uuid4())},
        json={"operation": "TASK_EXECUTION", "input": {"text": "hi"}},
    )
    check("provider-free observed estimate", estimate.status_code == 200, str(estimate.status_code))
    safe(estimate)
    human_acceptance(manifest, human, foreign_session=foreign_session)
    cors = public.options(
        "/v1/sessions",
        headers={"Origin": "https://evil.invalid", "Access-Control-Request-Method": "POST"},
    )
    check("CORS hostile origin denied", "access-control-allow-origin" not in cors.headers)
    logs = compose("logs", "--no-color")
    check(
        "no authentication secrets in container logs",
        not any(
            value in logs for value in (*tokens.values(), *human.values()) if isinstance(value, str)
        ),
    )
    (local / "persistence-proof.json").write_text(
        json.dumps({"session_id": sid, "task_id": tid}), encoding="utf-8"
    )
    check("acceptance finished", True, "persistence-proof.json identifies durable resources")
    if options.with_recovery:
        recovery(c, sid, tid, manifest)


def human_acceptance(manifest: dict, human: dict, foreign_session: str | None = None) -> None:
    bo = login("backoffice-admin", human, manifest)
    for page in (
        "tenants",
        "users",
        "policies",
        "quotas",
        "providers",
        "client-credentials",
        "intelligence",
    ):
        r = bo.get(f"/backoffice/{page}")
        check("Backoffice " + page, r.status_code == 200)
    issue_data = {
        "tenant_id": manifest["tenants"]["tenant_a"],
        "client_id": manifest["clients"]["client_a1"],
        "display_label": "acceptance-" + str(uuid4()),
        "scopes": "catalog:read",
        "idempotency_key": str(uuid4()),
    }
    issued = mutation(bo, "/backoffice/client-credentials/issue", issue_data)
    check(
        "Backoffice issue credential one-time",
        issued.status_code == 200 and "One-time secret" in issued.text,
    )
    replay = mutation(bo, "/backoffice/client-credentials/issue", issue_data)
    check(
        "Backoffice credential replay hides secret", "secret is not available again" in replay.text
    )
    for role in ("viewer", "operator", "admin"):
        p = login("client-" + role, human, manifest)
        if foreign_session is not None:
            check(
                f"Portal {role} foreign owner URL rejected",
                p.get(f"/portal/sessions/{foreign_session}").status_code == 404,
            )
        for page in ("sessions", "tasks", "providers", "docs"):
            response = p.get("/portal/" + page)
            check(f"Portal {role} {page}", response.status_code == 200)
            safe(response)
        check(
            f"Portal {role} credential RBAC",
            p.get("/portal/credentials").status_code == (403 if role == "viewer" else 200),
        )
        missing_csrf = p.post("/portal/sessions", headers={"Origin": BASE}, data={})
        check(f"Portal {role} missing CSRF", missing_csrf.status_code in {401, 403})
        hostile = p.post("/portal/sessions", headers={"Origin": "https://evil.invalid"}, data={})
        check(f"Portal {role} same-origin", hostile.status_code == 401)
        if role == "viewer":
            forbidden = mutation(p, "/portal/sessions", {"idempotency_key": str(uuid4())})
            check("Viewer mutation forbidden", forbidden.status_code == 403)
        else:
            response = mutation(p, "/portal/sessions", {"idempotency_key": str(uuid4())})
            check(f"{role} creates session", response.status_code == 303)
            for page in ("usage", "estimates"):
                check(f"{role} {page}", p.get("/portal/" + page).status_code == 200)
        check(
            f"{role} cannot select foreign membership",
            mutation(
                p,
                "/portal/select-membership",
                {"membership_id": str(uuid4())},
            ).status_code
            == 401,
        )


def recovery(c: httpx.Client, sid: str, tid: str, manifest: dict) -> None:
    before = c.get(f"/v1/tasks/{tid}/result").json()
    compose("stop", "worker")
    try:
        pending = c.post(
            f"/v1/sessions/{sid}/tasks",
            headers={"Idempotency-Key": str(uuid4())},
            json={"operation": "TASK_EXECUTION", "input": {"text": "recovery"}},
        )
        check("durable task accepted while worker stopped", pending.status_code == 202)
        pending_id = pending.json()["task_id"]
        compose("restart", "api", "scheduler", "worker")
        compose("up", "-d", "--wait", "--wait-timeout", "120")
        for _ in range(60):
            current = c.get(f"/v1/tasks/{pending_id}").json()
            if current.get("terminal_at"):
                break
            time.sleep(1)
        check("restart recovers queued task", current["status"] == "COMPLETE")
        check(
            "restart preserves completed result", c.get(f"/v1/tasks/{tid}/result").json() == before
        )
    finally:
        compose("start", "worker")
    compose("down")
    compose("up", "-d", "--wait", "--wait-timeout", "120")
    check("down/up preserves completed result", c.get(f"/v1/tasks/{tid}/result").json() == before)
    check("down/up preserves session", c.get(f"/v1/sessions/{sid}").status_code == 200)
    after = private_json("manifest")
    check(
        "rebootstrap preserves owners and memberships",
        all(
            manifest[key] == after[key] for key in ("tenants", "clients", "memberships", "provider")
        ),
    )
    check(
        "all eight long-running services healthy",
        all(
            item.get("Health") == "healthy"
            for item in (
                json.loads(line) for line in compose("ps", "--format", "json").splitlines()
            )
        ),
    )


if __name__ == "__main__":
    main()
