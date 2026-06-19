#!/usr/bin/env python3
"""Smoke test Pod Point Home App endpoints with a temporary bearer token."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

BASE_URL = "https://mobile-api.pod-point.com"
SECRET_KEYS = {
    "authorization",
    "bearer",
    "token",
    "id",
    "uid",
    "uuid",
    "enodeUserId",
    "enodeVehicleId",
    "vehicleRegistrationPlate",
}


def load_env(path: Path) -> dict[str, str]:
    values = {}

    for line in path.read_text().splitlines():
        line = line.strip()

        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip("'\"")

    return values


def request_json(path: str, bearer: str) -> tuple[int, Any]:
    request = Request(
        f"{BASE_URL}{path}",
        headers={
            "Authorization": f"Bearer {bearer}",
            "Content-Type": "application/json; charset=UTF-8",
        },
        method="GET",
    )

    try:
        with urlopen(request, timeout=20) as response:
            body = response.read().decode("utf-8")
            return response.status, json.loads(body) if body else None
    except HTTPError as error:
        body = error.read().decode("utf-8")
        try:
            payload = json.loads(body) if body else None
        except json.JSONDecodeError:
            payload = body[:300]

        return error.status, payload
    except URLError as error:
        raise SystemExit(f"Network error: {error}") from error


def redact(value: Any, key: str | None = None) -> Any:
    if key in SECRET_KEYS:
        return "<redacted>"

    if isinstance(value, dict):
        return {item_key: redact(item_value, item_key) for item_key, item_value in value.items()}

    if isinstance(value, list):
        return [redact(item) for item in value[:3]]

    if isinstance(value, str) and len(value) > 48:
        return f"{value[:12]}...<redacted>"

    return value


def summarize(name: str, status: int, payload: Any) -> None:
    print(f"\n{name}: HTTP {status}")

    if isinstance(payload, list):
        print(f"items: {len(payload)}")
        if payload:
            print(json.dumps(redact(payload[0]), indent=2, sort_keys=True))
        return

    if isinstance(payload, dict):
        print(json.dumps(redact(payload), indent=2, sort_keys=True))
        return

    print(redact(payload))


def main() -> int:
    env_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(".devcontainer/test.env")
    env = {**load_env(env_path), **os.environ}
    bearer = env.get("BEARER_KEY") or env.get("PODPOINT_BEARER_TOKEN")

    if not bearer:
        print("Missing BEARER_KEY or PODPOINT_BEARER_TOKEN", file=sys.stderr)
        return 2

    endpoints = [
        ("vehicles", "/smart-charging/delegated-controls/vehicles"),
        ("reward_wallet", "/reward-wallet"),
    ]

    ppid = env.get("PODPOINT_PPID")
    vehicle_status = None
    vehicles_payload = None

    for name, path in endpoints:
        status, payload = request_json(path, bearer)
        summarize(name, status, payload)

        if name == "vehicles":
            vehicle_status = status
            vehicles_payload = payload

    if not ppid and vehicle_status == 200 and isinstance(vehicles_payload, list):
        for allocation in vehicles_payload:
            if isinstance(allocation, dict) and allocation.get("ppid"):
                ppid = allocation["ppid"]
                break

    if ppid:
        for name, path in [
            ("delegated_controls", f"/smart-charging/delegated-controls/{ppid}"),
            ("charge_overrides", f"/chargers/{ppid}/charge-overrides"),
            ("tariffs", f"/chargers/{ppid}/tariffs"),
            ("remote_lock", f"/remote-lock/{ppid}"),
            (
                "preferences",
                f"/smart-charging/delegated-controls/{ppid}/preferences",
            ),
        ]:
            status, payload = request_json(path, bearer)
            summarize(name, status, payload)
    else:
        print("\nNo PPID found. Set PODPOINT_PPID to test per-charger endpoints.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
