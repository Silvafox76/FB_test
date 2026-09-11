"""Can this host reach the sources the registry says are enabled?

Every connector step begins by recording a fixture from a real call, so a session
that cannot reach a source cannot do that step. This answers the question in one
command, before a step starts rather than halfway through it.

    uv run python scripts/check_egress.py

Reads the host out of each sources/*.yaml, makes one HEAD request per host, and
reports reachable, blocked or failed. It is a connectivity check and nothing else:
it does not parse, store or record anything, and a blocked host is reported rather
than worked around.
"""

from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urlparse

import httpx
import yaml

REPO = Path(__file__).resolve().parent.parent
SOURCES_DIR = REPO / "sources"
TIMEOUT_SECONDS = 15.0


def hosts() -> list[tuple[str, str, bool]]:
    """(source id, host, enabled) for every registry entry, in id order."""
    found = []
    for path in sorted(SOURCES_DIR.glob("*.yaml")):
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
        url = document.get("api_url") or document.get("list_url") or ""
        host = urlparse(url).netloc
        if host:
            found.append((document["id"], host, bool(document.get("enabled"))))
    return found


def main() -> int:
    agent = os.environ.get("MONITOR_USER_AGENT", "FreeBalance-OpportunityMonitor/0.1 (+egress check)")
    blocked = []

    print(f"{'source':<12} {'host':<40} result")
    for source_id, host, enabled in hosts():
        flag = "" if enabled else "  (not enabled)"
        try:
            response = httpx.head(
                f"https://{host}/",
                timeout=TIMEOUT_SECONDS,
                headers={"User-Agent": agent},
                follow_redirects=True,
            )
            print(f"{source_id:<12} {host:<40} reachable, HTTP {response.status_code}{flag}")
        except httpx.ProxyError as error:
            blocked.append(host)
            print(f"{source_id:<12} {host:<40} BLOCKED by egress policy ({error}){flag}")
        except httpx.HTTPError as error:
            blocked.append(host)
            print(f"{source_id:<12} {host:<40} failed: {type(error).__name__}{flag}")

    if blocked:
        print(f"\n{len(blocked)} host(s) unreachable. A connector step cannot record its fixture without them.")
        print("This is an environment egress policy decision, not something to route around.")
        return 1

    print("\nall source hosts reachable")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
