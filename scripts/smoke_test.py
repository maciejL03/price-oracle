#!/usr/bin/env python3
"""Run an isolated, credential-free Docker demonstration of Price Oracle."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
DEMO_ITEM = "AK-47 | Redline (Field-Tested)"


class SmokeFailure(RuntimeError):
    pass


def compose(project: str, env: dict[str, str], *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["docker", "compose", "-p", project, *arguments],
        cwd=ROOT,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )


def request_json(url: str) -> dict[str, Any]:
    with urllib.request.urlopen(url, timeout=3) as response:
        return json.load(response)


def wait_for(description: str, check: Callable[[], Any], timeout: float = 30.0) -> Any:
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            result = check()
            if result:
                print(f"PASS  {description}")
                return result
        except (OSError, ValueError, urllib.error.URLError) as error:
            last_error = error
        time.sleep(0.2)
    detail = "" if last_error is None else f" ({last_error})"
    raise SmokeFailure(f"Timed out: {description}{detail}")


def require(description: str, condition: bool) -> None:
    if not condition:
        raise SmokeFailure(description)
    print(f"PASS  {description}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("demo",), default="demo")
    parser.add_argument("--api-port", type=int, default=18000)
    parser.add_argument("--frontend-port", type=int, default=15173)
    parser.add_argument("--keep", action="store_true", help="Keep the isolated stack for inspection")
    args = parser.parse_args()

    project = f"price-oracle-smoke-{os.getpid()}"
    env = os.environ.copy()
    env.update(
        {
            "CS2_OPERATING_MODE": "demo",
            "CS2_API_PORT": str(args.api_port),
            "CS2_FRONTEND_PORT": str(args.frontend_port),
            "CS2_DATABASE_PATH": "/var/lib/price-oracle/smoke-demo.sqlite3",
        }
    )
    api = f"http://127.0.0.1:{args.api_port}"
    frontend = f"http://127.0.0.1:{args.frontend_port}"

    try:
        print(f"Starting isolated Compose project {project} …")
        started = compose(project, env, "up", "--build", "--detach")
        if started.returncode != 0:
            raise SmokeFailure(f"Compose startup failed:\n{started.stdout}")

        wait_for("API health", lambda: request_json(f"{api}/api/health").get("status") == "ok", 60)
        wait_for(
            "frontend proxies API",
            lambda: request_json(f"{frontend}/api/health").get("status") == "ok",
        )
        with urllib.request.urlopen(frontend, timeout=3) as response:
            html = response.read().decode("utf-8")
        require("frontend HTML is reachable", '<div id="root"></div>' in html)

        status = request_json(f"{api}/api/status")
        require("operating mode is demo", status.get("operating_mode") == "demo")
        require("data is labelled simulated", status.get("data_kind") == "simulated")
        require(
            "only deterministic demo providers are enabled",
            status.get("marketplaces") == ["demo-market-a", "demo-market-b"],
        )
        require("notification delivery is simulated", status.get("notifications") == "simulated")

        watchlists = request_json(f"{api}/api/watchlists").get("watchlists", [])
        require("demo watchlist is persisted", len(watchlists) == 1 and watchlists[0]["items"])

        encoded_item = urllib.parse.quote(DEMO_ITEM, safe="")
        comparison_url = f"{api}/api/items/{encoded_item}/comparison"
        comparison = wait_for(
            "scheduled JobHandler collection produced two stored quotes",
            lambda: (
                value
                if len((value := request_json(comparison_url)).get("quotes", [])) == 2
                else None
            ),
        )
        require("PricingService calculated the deterministic spread", comparison.get("spread") == "0.75")
        require("comparison remains marked simulated", comparison.get("simulated") is True)

        history = request_json(f"{api}/api/items/{encoded_item}/history").get("quotes", [])
        require("SQLite price history contains collected observations", len(history) >= 2)

        events = wait_for(
            "threshold alert produced a durable event",
            lambda: (value if (value := request_json(f"{api}/api/alerts/events").get("events", [])) else None),
        )
        event_id = events[0]["id"]
        delivery = request_json(f"{api}/api/notifications/{event_id}")
        require(
            "simulated notification was durably delivered",
            delivery.get("channel") == "demo" and delivery.get("status") == "succeeded",
        )

        logs = compose(project, env, "logs", "api").stdout
        require("container logs show real quote persistence", "Persisted 1 market quotes" in logs)

        restarted = compose(project, env, "restart", "api")
        if restarted.returncode != 0:
            raise SmokeFailure(f"API restart failed:\n{restarted.stdout}")
        wait_for("API becomes healthy after restart", lambda: request_json(f"{api}/api/health").get("status") == "ok", 60)
        restored = request_json(f"{api}/api/watchlists").get("watchlists", [])
        require("watchlist survives container restart", restored == watchlists)
        restored_events = request_json(f"{api}/api/alerts/events").get("events", [])
        require("alert history survives container restart", restored_events[:1] == events[:1])
        print("\nPrice Oracle offline demo smoke test: PASS")
        return 0
    except (SmokeFailure, OSError, ValueError) as error:
        print(f"\nPrice Oracle offline demo smoke test: FAIL\n{error}", file=sys.stderr)
        diagnostics = compose(project, env, "logs", "--no-color", "--tail", "200")
        if diagnostics.stdout:
            print(diagnostics.stdout, file=sys.stderr)
        return 1
    finally:
        if args.keep:
            print(f"Keeping {project}; stop it with: docker compose -p {project} down --volumes")
        else:
            stopped = compose(project, env, "down", "--volumes", "--remove-orphans")
            if stopped.returncode != 0:
                print(f"Warning: isolated cleanup failed:\n{stopped.stdout}", file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
