#!/usr/bin/env python3
"""Standalone smoke test for the conductor (Stage 1). No network: the single
call seam conductor_core._post_chat is stubbed. Asserts every Stage-1 DoD:

  • presence resolver across {0, 1, several} keys (the "as many or as few" core)
  • ONLY DEEPINFRA set -> steer+synth resolve to deepinfra and work
  • unset ALL -> every role OFF, run_role returns proceed_local (clean local-only)
  • bad deepinfra + good fireworks -> synth SILENTLY falls with a logged one-liner
  • conductor.yaml reorders a chain (defaults < file precedence)
  • USD cap -> paid rung blocked -> proceed_local
  • parallel_draft pool fans out across present free keys AND respects RPM budget
    (an exhausted provider is skipped cleanly)
  • server advertises the conductor tools over MCP

Run: python smoke_conductor.py
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
TEST_PORT = int(os.environ.get("SMOKE_PORT", "19115"))
_TMP = tempfile.mkdtemp(prefix="cond-smoke-")

# Route ledger/budget to temp BEFORE importing the core (paths read at import).
os.environ["CONDUCTOR_LEDGER_PATH"] = os.path.join(_TMP, "ledger.json")
os.environ["CONDUCTOR_BUDGET_PATH"] = os.path.join(_TMP, "budget.json")
# Make sure no real keys leak in from a sourced .env in part A.
for _k in ("DEEPINFRA_API_KEY", "FIREWORKS_API_KEY", "TOGETHER_API_KEY", "DEEPSEEK_API_KEY",
           "MOONSHOT_API_KEY", "CEREBRAS_API_KEY", "GROQ_API_KEY", "GEMINI_API_KEY",
           "ANTHROPIC_API_KEY"):
    os.environ.pop(_k, None)

import conductor_core as cc  # noqa: E402
import conductor_registry as reg  # noqa: E402
import conductor_resolver as rv  # noqa: E402


def _ok(m: str) -> None:
    print(f"  ok: {m}")


def _fail(m: str) -> None:
    print(f"  FAIL: {m}")
    sys.exit(1)


def _fake_post_factory(fail_substr: str | None = None):
    """Canned OpenAI-style response; optionally raise for a base_url substring."""
    def fake(base_url, api_key, model, messages, max_tokens):
        if fail_substr and fail_substr in base_url:
            raise RuntimeError(f"stub failure for {base_url}")
        return {"choices": [{"message": {"content": f"[stub:{model}] ok"}}],
                "usage": {"prompt_tokens": 200, "completion_tokens": 100}}
    return fake


def part_a() -> None:
    print("[A] router core (stubbed call seam)")
    providers = reg.PROVIDERS

    # 1. presence resolver across {0, 1, several}
    chain = reg.DEFAULT_ROLE_CHAINS["synth"]
    assert rv.resolve_chain(chain, providers, {}) == []
    one = rv.resolve_chain(chain, providers, {"DEEPINFRA_API_KEY": "x"})
    assert one == ["deepinfra"], one
    several = rv.resolve_chain(chain, providers,
                              {"DEEPINFRA_API_KEY": "x", "TOGETHER_API_KEY": "y",
                               "ANTHROPIC_API_KEY": "z"})
    assert several == ["deepinfra", "together", "anthropic"], several  # order preserved
    _ok(f"presence resolver: 0->[], 1->{one}, several->{several} (order preserved)")

    # blank/whitespace key is treated as ABSENT
    assert rv.resolve_chain(chain, providers, {"DEEPINFRA_API_KEY": "  "}) == []
    _ok("blank/whitespace key counts as absent")

    # 2. ONLY DEEPINFRA -> steer+synth resolve to deepinfra and work
    cc._post_chat = _fake_post_factory()
    os.environ["DEEPINFRA_API_KEY"] = "test"
    rs = cc.run_role("synth", prompt="hard decomposition")
    rt = cc.run_role("steer", prompt="cheap nudge")
    if not (rs["ok"] and rs["provider"] == "deepinfra" and rs["model"].endswith("V4-Pro")):
        _fail(f"synth did not resolve to deepinfra V4-Pro: {rs}")
    if not (rt["ok"] and rt["provider"] == "deepinfra" and rt["model"].endswith("V4-Flash")):
        _fail(f"steer did not resolve to deepinfra V4-Flash: {rt}")
    _ok(f"only-DEEPINFRA: synth->{rs['model']} (${rs['cost_usd']}), steer->{rt['model']} (${rt['cost_usd']})")

    # 3. unset ALL -> roles OFF, run_role proceeds local
    os.environ.pop("DEEPINFRA_API_KEY", None)
    active = rv.active_roles(reg.DEFAULT_ROLE_CHAINS, providers, dict(os.environ))
    if any(active.values()):
        _fail(f"with no keys, all roles must be OFF: {active}")
    off = cc.run_role("synth", prompt="x")
    if off["ok"] or not off["proceed_local"] or off["role_active"]:
        _fail(f"role OFF must proceed_local: {off}")
    _ok(f"no keys -> roles OFF {active}; run_role(synth) -> proceed_local")

    # 4. bad deepinfra + good fireworks -> synth SILENTLY falls with a logged one-liner
    cc._post_chat = _fake_post_factory(fail_substr="deepinfra")
    os.environ["DEEPINFRA_API_KEY"] = "test"
    os.environ["FIREWORKS_API_KEY"] = "test"
    cc._TRACE.clear()
    r = cc.run_role("synth", prompt="x")
    if not (r["ok"] and r["provider"] == "fireworks"):
        _fail(f"synth should fall deepinfra->fireworks: {r}")
    fell_from = [f["provider"] for f in r["fell"]]
    if "deepinfra" not in fell_from:
        _fail(f"the fall should record deepinfra: {r['fell']}")
    falls = [t for t in cc._TRACE if t["event"] == "rung_fell"]
    if not falls or falls[0]["frm"] != "deepinfra":
        _fail(f"a rung_fell one-liner should be logged: {cc._TRACE}")
    _ok(f"silent fall deepinfra->fireworks, logged: {falls[0]['reason'][:40]}...")
    os.environ.pop("FIREWORKS_API_KEY", None)

    # 5. conductor.yaml reorders a chain (defaults < file)
    yml = os.path.join(_TMP, "conductor.yaml")
    Path(yml).write_text("roles:\n  steer: [groq, deepinfra]\n  synth: [together, deepinfra]\n")
    saved = reg.CONDUCTOR_YAML
    reg.CONDUCTOR_YAML = yml
    cfg = reg.load_config()
    if cfg["role_chains"]["steer"][0] != "groq" or cfg["role_chains"]["synth"][0] != "together":
        _fail(f"conductor.yaml did not reorder chains: {cfg['role_chains']}")
    if not cfg["config_applied"]:
        _fail("config_applied should be True when conductor.yaml overrides")
    _ok(f"conductor.yaml reorder: steer={cfg['role_chains']['steer']}, synth={cfg['role_chains']['synth']}")
    reg.CONDUCTOR_YAML = saved

    # 6. USD cap -> paid rung blocked -> proceed_local
    cc._post_chat = _fake_post_factory()
    over = cc._blank_ledger()
    over["spend_today"] = 99.0  # well over the $1/day default
    cc._save_ledger(over)
    os.environ["DEEPINFRA_API_KEY"] = "test"  # only paid rung present
    r = cc.run_role("synth", prompt="x")
    if r["ok"] or not r["proceed_local"]:
        _fail(f"USD cap should block the paid rung -> proceed_local: {r}")
    if not any(a.get("skipped") == "usd_cap" for a in r["attempts"]):
        _fail(f"the skip reason should be usd_cap: {r['attempts']}")
    _ok("USD cap reached -> paid synth rung skipped -> proceed_local")
    cc._save_ledger(cc._blank_ledger())  # reset spend

    # 7. parallel_draft fan-out across present free keys + RPM budget respected
    cc._post_chat = _fake_post_factory()
    os.environ.pop("DEEPINFRA_API_KEY", None)  # pool = free only
    os.environ["CEREBRAS_API_KEY"] = "test"
    os.environ["GROQ_API_KEY"] = "test"
    # Pre-exhaust Groq's per-minute budget (rpm=30) so it is skipped cleanly.
    now = time.time()
    cc._save_budget({"groq": [now] * 30})
    res = cc.draft_fanout(prompt="implement fn so tests pass", n=5)
    provs_passed = {c["provider"] for c in res["candidates"] if c["ok"]}
    skipped_provs = {s["provider"] for s in res["skipped"]}
    if not res["ok"] or "cerebras" not in provs_passed:
        _fail(f"fan-out should return cerebras candidates: {res}")
    if "groq" not in skipped_provs or not all(
            s["skipped"] == "rpm_rpd_exhausted" for s in res["skipped"] if s["provider"] == "groq"):
        _fail(f"exhausted Groq should be skipped as rpm_rpd_exhausted: {res['skipped']}")
    if "groq" in provs_passed:
        _fail("an exhausted provider must not be drafted from")
    _ok(f"draft fan-out: {res['n_passed']} candidates from {provs_passed}; "
        f"Groq skipped (rpm budget). n_present={res['n_present']}")

    # 7b. zero keys -> draft degrades to N=1-local signal
    os.environ.pop("CEREBRAS_API_KEY", None)
    os.environ.pop("GROQ_API_KEY", None)
    res0 = cc.draft_fanout(prompt="x")
    if res0["ok"] or not res0["proceed_local"]:
        _fail(f"no pool keys -> degrade to local: {res0}")
    _ok("no pool keys -> draft degrades to N=1-local (proceed_local)")


async def _mcp_check(port: int) -> None:
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    async with streamablehttp_client(f"http://127.0.0.1:{port}/mcp") as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            names = {t.name for t in (await session.list_tools()).tools}
            want = {"conductor_steer", "conductor_synthesize", "conductor_status", "parallel_draft_pool"}
            if not want.issubset(names):
                _fail(f"conductor tools not advertised; want {want}, got {names}")
            _ok(f"conductor tools advertised over MCP: {sorted(want)}")
            res = await session.call_tool("conductor_status", {})
            data = res.structuredContent or (json.loads(res.content[0].text) if res.content else {})
            if isinstance(data, dict) and "result" in data and "roles_active" not in data:
                data = data["result"]
            if "roles_active" not in data:
                _fail(f"conductor_status shape unexpected: {data}")
            _ok(f"conductor_status over MCP -> roles_active={data['roles_active']}")


def _wait_health(port: int, timeout: float = 30.0) -> None:
    url = f"http://127.0.0.1:{port}/health"
    deadline = time.monotonic() + timeout
    last = ""
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as r:
                if json.loads(r.read()).get("status") == "ok":
                    _ok(f"/health up on :{port}")
                    return
        except Exception as e:  # noqa: BLE001
            last = str(e)
        time.sleep(0.4)
    _fail(f"server health never came up on :{port} ({last})")


def part_b() -> None:
    print(f"[B] server over MCP streamable-http (:{TEST_PORT})")
    env = dict(os.environ, MCP_ESCALATION_PORT=str(TEST_PORT), MCP_BIND_HOST="127.0.0.1")
    proc = subprocess.Popen([sys.executable, str(HERE / "server.py")], env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    try:
        _wait_health(TEST_PORT)
        asyncio.run(_mcp_check(TEST_PORT))
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == "__main__":
    part_a()
    part_b()
    print("conductor smoke test PASSED")
