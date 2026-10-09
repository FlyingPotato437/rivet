"""Read-only checks for public deployment wiring. Never reads local credentials."""

import argparse
import json
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


def origin(value):
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.path not in ("", "/")
        or parsed.query
        or parsed.fragment
    ):
        raise argparse.ArgumentTypeError(
            "Use an HTTPS origin without credentials, a path, query, or fragment."
        )
    return value.rstrip("/")


def fetch(url, frontend, timeout):
    request = Request(url, headers={"Origin": frontend, "Accept": "application/json"})
    try:
        with urlopen(request, timeout=timeout) as response:
            raw = response.read(1024 * 1024)
            try:
                data = json.loads(raw)
            except (ValueError, UnicodeDecodeError):
                data = None
            return response.status, response.url, response.headers, data
    except HTTPError as error:
        return error.code, error.url, error.headers, None
    except (URLError, TimeoutError, OSError):
        return None, url, {}, None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--frontend", required=True, type=origin)
    parser.add_argument("--api", required=True, type=origin)
    parser.add_argument("--timeout", type=int, default=10, choices=range(1, 61), metavar="1-60")
    parser.add_argument(
        "--offline", "--dry-run", dest="offline", action="store_true",
        help="Print the public GET plan without making requests.",
    )
    args = parser.parse_args()
    plan = [
        ("frontend", args.frontend + "/"),
        ("health", args.api + "/api/health"),
        ("authentication_config", args.api + "/api/auth/config"),
        ("anonymous_access", args.api + "/api/records"),
    ]
    if args.offline:
        print(json.dumps({"mode": "offline", "requests": [
            {"method": "GET", "check": check, "url": url} for check, url in plan
        ], "note": "No network requests or environment credentials are used."}, indent=2))
        return 0

    checks = []

    def result(check, passed, detail):
        checks.append({"check": check, "passed": bool(passed), "detail": detail})

    responses = {check: fetch(url, args.frontend, args.timeout) for check, url in plan}
    status, final_url, _, _ = responses["frontend"]
    result("frontend", status == 200, f"HTTP {status or 'unreachable'}")
    result(
        "canonical_origin", final_url.rstrip("/") == args.frontend,
        "Use the final browser origin in the backend and Clerk allowlists.",
    )
    status, final_url, _, data = responses["health"]
    data = data if isinstance(data, dict) else {}
    result(
        "health", status == 200 and data.get("status") == "ok"
        and final_url == args.api + "/api/health",
        f"HTTP {status or 'unreachable'}; requires JSON database health without redirects.",
    )
    result("health_auth_mode", data.get("mode") == "clerk", "Expected authenticated Clerk mode.")
    status, final_url, headers, data = responses["authentication_config"]
    data = data if isinstance(data, dict) else {}
    result(
        "authentication_config", status == 200 and data.get("mode") == "clerk"
        and str(data.get("publishable_key", "")).startswith("pk_live_")
        and data.get("demo") is None and "demo" in data
        and final_url == args.api + "/api/auth/config",
        f"HTTP {status or 'unreachable'}; requires production Clerk and disabled development demo.",
    )
    result(
        "cors", args.api == args.frontend
        or headers.get("Access-Control-Allow-Origin") == args.frontend,
        "The API must explicitly allow the canonical frontend origin.",
    )
    status, final_url, _, _ = responses["anonymous_access"]
    result(
        "anonymous_access", status in (401, 403)
        and final_url == args.api + "/api/records",
        f"HTTP {status or 'unreachable'}; private records must reject an unsigned request.",
    )
    passed = all(check["passed"] for check in checks)
    print(json.dumps({
        "passed": passed, "checks": checks,
        "limitations": "Wiring checks only. Not a sign-in, worker, upload, email, AI accuracy, or backup/restore test. No record writes or credential use.",
    }, indent=2))
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
