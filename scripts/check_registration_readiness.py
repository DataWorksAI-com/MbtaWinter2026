#!/usr/bin/env python3
"""Check agent readiness endpoints and registry alive state for MBTA agents.

Usage:
  python scripts/check_registration_readiness.py \
    --registry-url http://localhost:6900 \
    --agent mbta-alerts=http://localhost:8001 \
    --agent mbta-planner=http://localhost:8002 \
    --agent mbta-stopfinder=http://localhost:8003
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from typing import Any, Dict, List, Tuple


DEFAULT_AGENTS = {
    "mbta-alerts": "http://localhost:8001",
    "mbta-planner": "http://localhost:8002",
    "mbta-stopfinder": "http://localhost:8003",
}


def _fetch_json(url: str, timeout: float) -> Tuple[int, Dict[str, Any]]:
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            return resp.status, json.loads(raw)
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8") if exc.fp else ""
        payload = json.loads(raw) if raw else {}
        return exc.code, payload


def _parse_agents(values: List[str]) -> Dict[str, str]:
    if not values:
        return dict(DEFAULT_AGENTS)

    agents: Dict[str, str] = {}
    for value in values:
        if "=" not in value:
            raise ValueError(f"Invalid --agent value '{value}', expected agent_id=http://host:port")
        agent_id, base_url = value.split("=", 1)
        agent_id = agent_id.strip()
        base_url = base_url.strip().rstrip("/")
        if not agent_id or not base_url:
            raise ValueError(f"Invalid --agent value '{value}', expected agent_id=http://host:port")
        agents[agent_id] = base_url
    return agents


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Check registration readiness for MBTA agents")
    parser.add_argument("--registry-url", default="http://localhost:6900", help="Registry base URL")
    parser.add_argument(
        "--agent",
        action="append",
        default=[],
        help="Agent mapping in the form agent_id=http://host:port. Defaults to localhost ports for alerts/planner/stopfinder.",
    )
    parser.add_argument("--timeout", type=float, default=5.0, help="HTTP timeout seconds")
    parser.add_argument(
        "--skip-registry-check",
        action="store_true",
        help="Only validate agent readiness endpoints and skip registry alive checks.",
    )
    return parser


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()

    try:
        agents = _parse_agents(args.agent)
    except ValueError as exc:
        print(f"FAIL invalid_arguments error={exc}")
        return 2

    failures: List[str] = []
    agent_summaries: List[str] = []

    for agent_id, base_url in agents.items():
        health_url = f"{base_url}/health"
        try:
            status_code, payload = _fetch_json(health_url, timeout=args.timeout)
        except (urllib.error.URLError, TimeoutError, ValueError) as exc:
            failures.append(f"{agent_id}:health_unreachable={exc}")
            continue

        ready = bool(payload.get("ready"))
        configured = bool(payload.get("mbta_api_configured"))
        agent_summaries.append(
            f"{agent_id}:health_status={status_code},ready={ready},configured={configured}"
        )

        if status_code != 200 or not ready:
            failures.append(f"{agent_id}:health_not_ready status={status_code} payload={payload}")

        if args.skip_registry_check:
            continue

        registry_url = args.registry_url.rstrip("/") + f"/agents/{agent_id}"
        try:
            reg_status, reg_payload = _fetch_json(registry_url, timeout=args.timeout)
        except (urllib.error.URLError, TimeoutError, ValueError) as exc:
            failures.append(f"{agent_id}:registry_unreachable={exc}")
            continue

        alive = bool(reg_payload.get("alive"))
        agent_summaries.append(f"{agent_id}:registry_status={reg_status},alive={alive}")
        if reg_status != 200 or not alive:
            failures.append(f"{agent_id}:registry_not_alive status={reg_status} payload={reg_payload}")

    summary = " ".join(agent_summaries)
    if failures:
        print("FAIL registration_readiness " + summary + " checks=" + " | ".join(failures))
        return 1

    print("PASS registration_readiness " + summary)
    return 0


if __name__ == "__main__":
    sys.exit(main())