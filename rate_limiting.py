#!/usr/bin/env python3
"""
rate_limiting.py
================
Demonstrates Meter API rate limiting: how to read rate-limit headers,
how to trigger HTTP 429 errors intentionally, and how to handle them
correctly using the Retry-After header.

RATE LIMIT SPECIFICATION
------------------------
  Limit     : 500 requests per minute per API key
  Timeout   : 60-second request timeout

RATE-LIMIT RESPONSE HEADERS
----------------------------
Every API response includes these headers regardless of success or failure:

  X-RateLimit-Remaining   — Number of requests remaining in the current
                            60-second window.
                            When this reaches 0, the next request returns 429.

  X-RateLimit-Reset       — RFC 1123 timestamp indicating when the current
                            window expires and the counter resets.
                            Example: "Fri, 07 Mar 2026 12:01:00 GMT"

On HTTP 429 responses only:

  Retry-After             — RFC 1123 timestamp indicating the earliest safe
                            time to retry.
                            Example: "Fri, 07 Mar 2026 12:01:00 GMT"

CORRECT HANDLING OF 429
------------------------
  1. Receive HTTP 429 response.
  2. Parse the Retry-After header (RFC 1123 format).
  3. Sleep until the Retry-After time (or apply exponential back-off
     if the header is absent).
  4. Retry the request ONCE.
  5. Do NOT retry in a tight loop — repeated 429 responses do NOT reset
     the rate-limit window.

PROACTIVE APPROACH (RECOMMENDED)
---------------------------------
  Monitor X-RateLimit-Remaining on every response.
  When the value drops below a threshold (e.g. 50), proactively slow down
  and wait for the window reset instead of waiting to hit 429.

ASYNCIO DESIGN
--------------
This script uses asyncio + concurrent.futures.ThreadPoolExecutor to run
many requests concurrently (using the synchronous `requests` library on
background threads). This causes rapid consumption of the rate-limit
window and eventually triggers 429 responses, demonstrating the handling.

asyncio.to_thread() is used (Python 3.9+) to run each requests.post()
call on the default ThreadPoolExecutor without blocking the event loop.
"""

import asyncio
import json
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import requests
import config

# ── Configuration ──────────────────────────────────────────────────────────────

API_URL   = config.API_URL
API_TOKEN = config.API_TOKEN

# Number of concurrent requests to fire in the flood section.
# 500 is the per-minute limit; firing 600 concurrent requests ensures we
# exhaust the window and see 429 responses.
FLOOD_COUNT = 600

# Proactive back-off threshold: slow down when fewer than this many
# requests remain in the current window.
PROACTIVE_THRESHOLD = 50

# ── ANSI colours ───────────────────────────────────────────────────────────────

BOLD   = "\033[1m"
RED    = "\033[0;31m"
YELLOW = "\033[0;33m"
GREEN  = "\033[0;32m"
CYAN   = "\033[0;36m"
DIM    = "\033[2m"
RESET  = "\033[0m"


def _ok(msg: str)   -> None: print(f"  {GREEN}✓  {msg}{RESET}")
def _err(msg: str)  -> None: print(f"  {RED}✗  {msg}{RESET}")
def _warn(msg: str) -> None: print(f"  {YELLOW}⚠  {msg}{RESET}")
def _info(msg: str) -> None: print(f"  {DIM}→  {msg}{RESET}")


def print_section(title: str, subtitle: str = "") -> None:
    print(f"\n{BOLD}{CYAN}{'━' * 65}{RESET}")
    print(f"{BOLD}{CYAN}  {title}{RESET}")
    if subtitle:
        print(f"{DIM}  {subtitle}{RESET}")
    print(f"{BOLD}{CYAN}{'━' * 65}{RESET}")


# ── Shared rate-limit state ────────────────────────────────────────────────────
# These are updated from every response's headers so the proactive back-off
# logic can check the current remaining count without an extra API call.

_rl_remaining: int | None = None
_rl_reset: datetime | None = None
_rl_lock = asyncio.Lock()  # protects concurrent updates


# ── Header parsing helpers ─────────────────────────────────────────────────────

def parse_rfc1123(value: str | None) -> datetime | None:
    """
    Parse an RFC 1123 date string into a timezone-aware datetime.

    The Meter API uses RFC 1123 format for X-RateLimit-Reset and Retry-After:
        "Fri, 07 Mar 2026 12:01:00 GMT"

    Python's email.utils.parsedate_to_datetime handles this format and
    returns a timezone-aware datetime with UTC offset.

    Args:
        value: RFC 1123 date string, or None.

    Returns:
        timezone-aware datetime in UTC, or None if unparseable.
    """
    if not value:
        return None
    try:
        return parsedate_to_datetime(value)
    except Exception:
        return None


def seconds_until(dt: datetime | None) -> float:
    """
    Return the number of seconds from now until the given datetime.

    Used to calculate how long to sleep before retrying after a 429.

    Args:
        dt: A timezone-aware datetime representing a future point in time.

    Returns:
        Non-negative float. Returns 0.0 if dt is in the past or None.
    """
    if dt is None:
        return 0.0
    now = datetime.now(timezone.utc)
    return max(0.0, (dt - now).total_seconds())


async def update_rate_limit_state(headers: dict) -> None:
    """
    Update the shared rate-limit counters from a response's headers.

    Called after every request to keep the proactive back-off logic
    informed about the current window state.

    Args:
        headers: HTTP response headers dict from a Meter API response.
    """
    global _rl_remaining, _rl_reset
    remaining_str = headers.get("X-RateLimit-Remaining")
    reset_str     = headers.get("X-RateLimit-Reset")

    async with _rl_lock:
        if remaining_str is not None:
            try:
                _rl_remaining = int(remaining_str)
            except ValueError:
                pass
        if reset_str:
            _rl_reset = parse_rfc1123(reset_str)


def format_headers(headers: dict) -> str:
    """Format rate-limit headers as a concise one-liner for log output."""
    remaining   = headers.get("X-RateLimit-Remaining", "—")
    reset_str   = headers.get("X-RateLimit-Reset", "—")
    retry_after = headers.get("Retry-After")
    parts = [f"remaining={remaining}", f"reset={reset_str}"]
    if retry_after:
        parts.append(f"retry-after={retry_after}")
    return "  ".join(parts)


# ── Core async request function ────────────────────────────────────────────────

SIMPLE_QUERY = '{ companyBySlug(slug: "meter") { name } }'

HEADERS = {
    "Content-Type": "application/json",
    "Authorization": f"Bearer {API_TOKEN}",
}


async def single_async_request(request_id: int, semaphore: asyncio.Semaphore) -> dict:
    """
    Execute one GraphQL POST request on a background thread via asyncio.to_thread().

    Uses asyncio.to_thread() (Python 3.9+) to run the blocking requests.post()
    call without blocking the event loop, allowing many requests to run
    concurrently within a single async application.

    The semaphore limits how many requests are in-flight at once to avoid
    overwhelming the OS connection pool (even though we want to exhaust the
    rate limit, we don't want to open 600 simultaneous TCP connections).

    Rate-limit state is updated from the response headers after each request.

    Args:
        request_id: Integer label used in log output.
        semaphore:  Limits concurrent in-flight requests.

    Returns:
        Dict with keys:
            id          — request_id
            status      — HTTP status code, or None on network error
            remaining   — X-RateLimit-Remaining header value
            reset       — X-RateLimit-Reset header value
            retry_after — Retry-After header value (only on 429)
            error       — Error message string, or None on success
    """
    async with semaphore:
        try:
            response = await asyncio.to_thread(
                requests.post,
                API_URL,
                headers=HEADERS,
                json={"query": SIMPLE_QUERY},
                timeout=60,
            )
            status  = response.status_code
            hdrs    = response.headers
            await update_rate_limit_state(hdrs)

            result = {
                "id":          request_id,
                "status":      status,
                "remaining":   hdrs.get("X-RateLimit-Remaining", "—"),
                "reset":       hdrs.get("X-RateLimit-Reset", "—"),
                "retry_after": hdrs.get("Retry-After"),
                "error":       None,
            }

            if status == 200:
                color = GREEN
                label = "200 OK"
            elif status == 429:
                color = RED
                retry = hdrs.get("Retry-After", "—")
                label = f"429 Too Many Requests  Retry-After: {retry}"
                result["error"] = f"rate limited — retry after {retry}"
            else:
                color = YELLOW
                label = f"HTTP {status}"
                result["error"] = f"unexpected status {status}"

            # Print one line per request (interleaved output from async tasks)
            print(
                f"  {color}[{request_id:>3}] {label:<55}"
                f" remaining={result['remaining']}{RESET}"
            )
            return result

        except requests.Timeout:
            print(f"  {YELLOW}[{request_id:>3}] TIMEOUT{RESET}")
            return {"id": request_id, "status": None, "remaining": None,
                    "reset": None, "retry_after": None, "error": "timeout"}
        except requests.ConnectionError as exc:
            print(f"  {YELLOW}[{request_id:>3}] CONNECTION ERROR: {exc}{RESET}")
            return {"id": request_id, "status": None, "remaining": None,
                    "reset": None, "retry_after": None, "error": str(exc)}


# ── Retry-After back-off ───────────────────────────────────────────────────────

async def request_with_retry(max_attempts: int = 5) -> dict | None:
    """
    Execute a query with Retry-After–aware back-off on 429 errors.

    This is the recommended pattern for production applications:

    1. Check X-RateLimit-Remaining before sending (proactive back-off).
       If the window is almost exhausted, wait for the reset timestamp
       instead of sending a request that will likely be rejected.

    2. On HTTP 429: read the Retry-After header and sleep until that time.
       Use exponential back-off as a fallback if Retry-After is absent.

    3. Do NOT retry in a tight loop. The Meter documentation states:
       "Repeated 429s will not reset the rate-limit window."

    Args:
        max_attempts: Maximum number of send attempts before giving up.

    Returns:
        Parsed response dict on success, or None after all attempts fail.
    """
    base_delay = 1.0

    for attempt in range(1, max_attempts + 1):
        # ── Proactive check ───────────────────────────────────────────────
        async with _rl_lock:
            remaining = _rl_remaining
            reset_dt  = _rl_reset

        if remaining is not None and remaining < PROACTIVE_THRESHOLD:
            wait_secs = seconds_until(reset_dt) or base_delay
            _warn(
                f"[Attempt {attempt}] Proactive back-off: only {remaining} requests "
                f"remaining. Waiting {wait_secs:.1f}s for window reset."
            )
            await asyncio.sleep(wait_secs)

        # ── Send the request ──────────────────────────────────────────────
        _info(f"[Attempt {attempt}/{max_attempts}] Sending request...")

        try:
            response = await asyncio.to_thread(
                requests.post,
                API_URL,
                headers=HEADERS,
                json={"query": SIMPLE_QUERY},
                timeout=60,
            )
        except (requests.Timeout, requests.ConnectionError) as exc:
            _err(f"Network error on attempt {attempt}: {exc}")
            break

        hdrs   = response.headers
        status = response.status_code
        await update_rate_limit_state(hdrs)

        _info(f"Response headers: {format_headers(hdrs)}")

        if status == 200:
            body = response.json()
            if "errors" not in body:
                _ok(f"Success on attempt {attempt}.")
                return body

        if status == 429:
            retry_after_dt = parse_rfc1123(hdrs.get("Retry-After"))
            wait_secs = seconds_until(retry_after_dt)
            if wait_secs == 0:
                # Retry-After absent or already past — use exponential back-off
                wait_secs = base_delay * (2 ** (attempt - 1))
                _warn(
                    f"[Attempt {attempt}] HTTP 429. No valid Retry-After; "
                    f"using exponential back-off: {wait_secs:.1f}s."
                )
            else:
                _warn(
                    f"[Attempt {attempt}] HTTP 429. "
                    f"Retry-After: {hdrs.get('Retry-After')}. "
                    f"Sleeping {wait_secs:.1f}s."
                )
            await asyncio.sleep(wait_secs)
            continue

        _err(f"Unexpected HTTP {status} on attempt {attempt}. Stopping.")
        break

    _err(f"All {max_attempts} attempts failed.")
    return None


# ── Section runners ────────────────────────────────────────────────────────────

async def section_observe_headers() -> None:
    """
    Section 1: Make one normal request and display its rate-limit headers.

    Establishes a baseline so developers can see what the headers look like
    before any rate-limit pressure is applied.
    """
    print_section(
        "Section 1 — Observing Rate-Limit Headers",
        "Single normal request to read X-RateLimit-Remaining and X-RateLimit-Reset."
    )

    semaphore = asyncio.Semaphore(1)
    result    = await single_async_request(1, semaphore)

    print()
    print(f"  {BOLD}Headers on every Meter API response:{RESET}")
    print(f"  {'X-RateLimit-Remaining':<30} Requests left in the current 60s window")
    print(f"  {'X-RateLimit-Reset':<30} RFC 1123 timestamp when the window resets")
    print(f"  {'Retry-After':<30} RFC 1123 retry time (only on HTTP 429)")
    print()
    _info(f"Current remaining : {result['remaining']}")
    _info(f"Current reset time: {result['reset']}")


async def section_flood_to_trigger_429() -> dict:
    """
    Section 2: Fire FLOOD_COUNT concurrent requests to exhaust the rate limit.

    asyncio.gather() schedules all coroutines concurrently. Each coroutine
    calls asyncio.to_thread() to run its requests.post() on a background thread,
    allowing genuine parallelism despite Python's GIL.

    A semaphore limits simultaneous in-flight requests to 50 to avoid
    exhausting OS file descriptors, while still sending requests fast enough
    to trigger 429 responses before the window resets.

    Returns:
        Dict with counts: ok, rate_limited, errors.
    """
    print_section(
        f"Section 2 — Triggering HTTP 429 ({FLOOD_COUNT} concurrent requests)",
        f"The 500 req/min limit means ~{FLOOD_COUNT - 500} requests should receive 429."
    )
    print(
        f"  Firing {FLOOD_COUNT} requests concurrently "
        f"(semaphore limits to 50 in-flight at once)...\n"
    )

    semaphore = asyncio.Semaphore(50)
    t_start   = time.monotonic()

    results = await asyncio.gather(
        *[single_async_request(i, semaphore) for i in range(1, FLOOD_COUNT + 1)]
    )

    elapsed = time.monotonic() - t_start

    ok_count    = sum(1 for r in results if r["status"] == 200)
    count_429   = sum(1 for r in results if r["status"] == 429)
    error_count = sum(1 for r in results if r["status"] not in (200, 429) or r["error"])

    print(f"\n  {BOLD}Flood results after {elapsed:.1f}s:{RESET}")
    print(f"  {GREEN}HTTP 200 OK          : {ok_count}{RESET}")
    print(f"  {RED}HTTP 429 rate limited: {count_429}{RESET}")
    if error_count:
        print(f"  {YELLOW}Other / network errors: {error_count}{RESET}")

    return {"ok": ok_count, "rate_limited": count_429, "errors": error_count}


async def section_retry_with_backoff() -> None:
    """
    Section 3: Demonstrate the correct retry pattern after triggering 429s.

    Calls request_with_retry() which:
      1. Checks X-RateLimit-Remaining before each send (proactive back-off).
      2. On 429: sleeps for Retry-After seconds before retrying.
      3. Falls back to exponential back-off if Retry-After is absent.
    """
    print_section(
        "Section 3 — Retry with Retry-After Back-off",
        "Correct production retry pattern after the flood."
    )
    print("  Steps:")
    print("    1. Check X-RateLimit-Remaining — sleep if below threshold")
    print("    2. Send request")
    print("    3. On 429: parse Retry-After, sleep, retry once")
    print("    4. Never retry in a tight loop\n")

    result = await request_with_retry(max_attempts=5)

    if result:
        company = result.get("data", {}).get("companyBySlug", {})
        _ok(f"Final successful response: {company}")
    else:
        _warn("Could not complete after 5 attempts — window may still be exhausted.")
        _info("Wait for X-RateLimit-Reset before retrying in production.")


async def section_best_practices() -> None:
    """
    Section 4: Best practices summary with a proactive monitoring demo.

    Makes 5 sequential requests while logging the remaining count after each,
    showing how to monitor the window in a real application.
    """
    print_section(
        "Section 4 — Proactive Monitoring Demo",
        "5 sequential requests logging X-RateLimit-Remaining after each."
    )

    semaphore = asyncio.Semaphore(1)
    for i in range(1, 6):
        result = await single_async_request(i, semaphore)
        async with _rl_lock:
            remaining = _rl_remaining
        if remaining is not None and remaining < PROACTIVE_THRESHOLD:
            _warn(f"  Threshold hit ({remaining} < {PROACTIVE_THRESHOLD}). "
                  "Would slow down in a real application.")
        await asyncio.sleep(0.2)  # small delay between sequential requests

    print(f"\n  {BOLD}Best practices:{RESET}")
    print(f"  {GREEN}✓{RESET}  Log X-RateLimit-Remaining on every response")
    print(f"  {GREEN}✓{RESET}  Slow down proactively when remaining < {PROACTIVE_THRESHOLD}")
    print(f"  {GREEN}✓{RESET}  On 429: read Retry-After, sleep, retry exactly once")
    print(f"  {GREEN}✓{RESET}  Bundle multiple queries into one GraphQL request to reduce count")
    print(f"  {GREEN}✓{RESET}  Use asyncio.to_thread() to keep async code non-blocking")
    print(f"  {RED}✗{RESET}  Do NOT retry immediately in a tight loop after 429")
    print(f"  {RED}✗{RESET}  Do NOT ignore 429 — repeated 429s do not reset the window")
    print(f"  {RED}✗{RESET}  Do NOT fire 500+ unchecked requests per minute per key")


# ── Main ───────────────────────────────────────────────────────────────────────

async def main() -> None:
    print(f"\n{BOLD}Meter API — Rate Limiting Demo{RESET}")
    print(f"Endpoint  : {API_URL}")
    print(f"Rate limit: 500 requests / 60 seconds per API key")
    print(f"Flood size: {FLOOD_COUNT} concurrent requests")
    print(f"Threshold : slow down below {PROACTIVE_THRESHOLD} remaining")

    await section_observe_headers()
    stats = await section_flood_to_trigger_429()

    if stats["rate_limited"] > 0:
        print(f"\n  {GREEN}✓{RESET}  Successfully triggered {stats['rate_limited']} "
              "HTTP 429 responses. Proceeding to retry demo...")
    else:
        print(f"\n  {YELLOW}⚠{RESET}  No 429s triggered — the window may have reset "
              "between runs. The retry demo will still execute.")

    await section_retry_with_backoff()
    await section_best_practices()

    print(f"\n{BOLD}Done.{RESET}\n")


if __name__ == "__main__":
    asyncio.run(main())
