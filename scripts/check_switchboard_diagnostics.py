#!/usr/bin/env python3
"""Check registry federation diagnostics and return CI-friendly exit status.

Usage:
  python scripts/check_switchboard_diagnostics.py \
    --url http://localhost:6900 \
    --agent mbta-alerts \
    --expect-neu reachable_found \
    --expect-agntcy upstream_unavailable
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List


VALID_STATES = {
    "not_configured",
    "active_local",
    "upstream_unavailable",
    "reachable_empty_result",
    "reachable_schema_mismatch",
    "reachable_schema_mismatch_or_error",
    "reachable_found",
}


def _fetch_json(url: str, timeout: float) -> Dict[str, Any]:
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8")
        return json.loads(raw)


def _state(registries: Dict[str, Any], key: str) -> str:
    entry = registries.get(key)
    if not isinstance(entry, dict):
        return "missing"
    value = entry.get("state")
    if not isinstance(value, str):
        return "missing"
    return value


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Check switchboard federation diagnostics")
    parser.add_argument("--url", default="http://localhost:6900", help="Registry base URL")
    parser.add_argument("--agent", default="mbta-alerts", help="Sample agent name used for diagnostics")
    parser.add_argument("--timeout", type=float, default=8.0, help="HTTP timeout seconds")
    parser.add_argument("--expect-neu", choices=sorted(VALID_STATES), help="Expected NEU state")
    parser.add_argument("--expect-agntcy", choices=sorted(VALID_STATES), help="Expected AGNTCY state")
    parser.add_argument("--require-federation-enabled", action="store_true", help="Fail if federation_enabled is false")
    return parser


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()

    query = urllib.parse.urlencode({"agent": args.agent})
    endpoint = args.url.rstrip("/") + f"/switchboard/diagnostics?{query}"

    try:
        payload = _fetch_json(endpoint, timeout=args.timeout)
    except urllib.error.HTTPError as exc:
        print(f"FAIL diagnostics_http_error status={exc.code} url={endpoint}")
        return 2
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        print(f"FAIL diagnostics_unreachable error={exc} url={endpoint}")
        return 2

    registries = payload.get("registries")
    if not isinstance(registries, dict):
        print("FAIL diagnostics_invalid_payload missing=registries")
        return 2

    fed_enabled = bool(payload.get("federation_enabled"))
    neu_state = _state(registries, "neu")
    agntcy_state = _state(registries, "agntcy")

    failures: List[str] = []
    if args.require_federation_enabled and not fed_enabled:
        failures.append("federation_enabled=false")
    if args.expect_neu and neu_state != args.expect_neu:
        failures.append(f"neu={neu_state} expected={args.expect_neu}")
    if args.expect_agntcy and agntcy_state != args.expect_agntcy:
        failures.append(f"agntcy={agntcy_state} expected={args.expect_agntcy}")

    summary = (
        f"diag federation_enabled={fed_enabled} "
        f"neu={neu_state} agntcy={agntcy_state} "
        f"agent={args.agent} url={args.url.rstrip('/')}"
    )

    if failures:
        print("FAIL " + summary + " checks=" + ";".join(failures))
        return 1

    print("PASS " + summary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
