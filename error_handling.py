#!/usr/bin/env python3
"""
error_handling.py
=================
Demonstrates every documented error type in the Meter GraphQL API with
colour-coded terminal output and proper Python error handling patterns.

ERROR TAXONOMY
--------------
The Meter API produces two categories of errors:

  HTTP-level errors  — The server rejects the request before GraphQL runs.
                       Identified by a non-200 HTTP status code.

  GraphQL-level errors — The HTTP request succeeds (status 200) but the
                         GraphQL layer returns errors inside the response body.
                         These appear in a top-level "errors" array.

ERROR TYPES COVERED
-------------------
  HTTP 401  Unauthorized
      • Missing Authorization header
      • Invalid, expired, or revoked API key
      Body: { "id": "unauthorized" }

  HTTP 429  Too Many Requests
      • Rate limit of 500 req/min per API key exceeded
      Body: { "id": "too_many_requests" }
      Headers: Retry-After (RFC 1123 timestamp)

  HTTP 400  Bad Request
      • Request body is not valid JSON
      Body: errors array with parse failure message

  HTTP 422  Validation Failed
      • Querying a field that does not exist in the schema
      • Submitting an empty query string
      Body: errors[].extensions.code = "GRAPHQL_VALIDATION_FAILED"

  HTTP 200  GraphQL UNAUTHORIZED
      • HTTP request is valid but the queried resource is inaccessible
        to this API key (wrong company, restricted feature, unknown UUID)
      Body: errors[].extensions.code = "UNAUTHORIZED"

COMMON RESPONSE STRUCTURE
--------------------------
GraphQL errors always appear in the response body as:

    {
        "errors": [
            {
                "message": "Cannot query field ...",
                "extensions": {
                    "code": "GRAPHQL_VALIDATION_FAILED"
                }
            }
        ],
        "data": null
    }
"""

import json
import requests
import config

# ── ANSI colour helpers ────────────────────────────────────────────────────────

BOLD   = "\033[1m"
RED    = "\033[0;31m"
YELLOW = "\033[0;33m"
GREEN  = "\033[0;32m"
CYAN   = "\033[0;36m"
BLUE   = "\033[0;34m"
DIM    = "\033[2m"
RESET  = "\033[0m"


def _ok(msg: str)   -> None: print(f"  {GREEN}✓  {msg}{RESET}")
def _err(msg: str)  -> None: print(f"  {RED}✗  {msg}{RESET}")
def _warn(msg: str) -> None: print(f"  {YELLOW}⚠  {msg}{RESET}")
def _info(msg: str) -> None: print(f"  {DIM}→  {msg}{RESET}")
def _json(data)     -> None: print(f"{DIM}{json.dumps(data, indent=4)}{RESET}")


def print_scenario(number: int, title: str, description: str) -> None:
    """Print a formatted scenario header."""
    print(f"\n{BOLD}{CYAN}{'━' * 65}{RESET}")
    print(f"{BOLD}{CYAN}  Scenario {number} — {title}{RESET}")
    print(f"{CYAN}  {description}{RESET}")
    print(f"{BOLD}{CYAN}{'━' * 65}{RESET}")


# ── Request helpers ────────────────────────────────────────────────────────────

def raw_post(
    url: str,
    headers: dict,
    body,
    timeout: int = 60,
) -> requests.Response:
    """
    Send a raw HTTP POST without raising on non-2xx status codes.

    Unlike requests.post(...).raise_for_status(), this returns the Response
    object regardless of status code so we can inspect error bodies manually.

    Args:
        url:     Target URL.
        headers: HTTP request headers.
        body:    Request body. Dict is JSON-serialised; str is sent as-is.
        timeout: Request timeout in seconds.

    Returns:
        The requests.Response object.
    """
    if isinstance(body, dict):
        return requests.post(url, headers=headers, json=body, timeout=timeout)
    return requests.post(url, headers=headers, data=body, timeout=timeout)


def _post(url: str, headers: dict, body, timeout: int = 60) -> requests.Response:
    """Internal POST that handles both dict and raw string bodies cleanly."""
    kwargs = dict(headers=headers, timeout=timeout)
    if isinstance(body, dict):
        kwargs["json"] = body
    else:
        kwargs["data"] = body
    return requests.post(url, **kwargs)


# ── Error parsing utilities ────────────────────────────────────────────────────

def extract_graphql_errors(response: requests.Response) -> list[dict]:
    """
    Parse GraphQL errors from a response body.

    GraphQL errors are embedded in the response regardless of HTTP status.
    Each error object contains:
        message              — Human-readable description
        extensions.code      — Machine-readable error classification

    Known extension codes:
        GRAPHQL_VALIDATION_FAILED — Field/type does not exist in the schema
        UNAUTHORIZED              — Caller cannot access this resource

    Args:
        response: HTTP response from the Meter API.

    Returns:
        List of error dicts from response["errors"], or empty list if none.
    """
    try:
        return response.json().get("errors", [])
    except Exception:
        return []


def describe_http_error(response: requests.Response) -> None:
    """
    Print a structured, colour-coded description of any API error response.

    Handles all documented error types: HTTP 401, 400, 422, 429, and
    GraphQL-level UNAUTHORIZED embedded in HTTP 200 responses.

    Args:
        response: HTTP response from the Meter API.
    """
    status      = response.status_code
    gql_errors  = extract_graphql_errors(response)

    if status == 401:
        _err(f"HTTP {status} Unauthorized")
        _info("The API key is missing, invalid, expired, or revoked.")
        _info("Fix: verify API_TOKEN in config.py, or regenerate the key")
        _info("     in Dashboard → Settings → Integrations → API keys.")

    elif status == 429:
        retry_after = response.headers.get("Retry-After", "—")
        remaining   = response.headers.get("X-RateLimit-Remaining", "—")
        reset_at    = response.headers.get("X-RateLimit-Reset", "—")
        _err(f"HTTP {status} Too Many Requests — rate limit exceeded.")
        _info(f"X-RateLimit-Remaining : {remaining}")
        _info(f"X-RateLimit-Reset     : {reset_at}")
        _info(f"Retry-After           : {retry_after}")
        _warn("Back off and retry after the Retry-After interval.")
        _warn("Repeated 429s do NOT reset the rate-limit window.")

    elif status == 400:
        _err(f"HTTP {status} Bad Request — the request body is not valid JSON.")
        _info("Ensure the body is valid JSON with a top-level `query` string.")
        _info("Common causes: missing closing brace, unescaped quotes inside")
        _info("               a query string built via string concatenation.")
        if gql_errors:
            for e in gql_errors:
                _info(f"Server message: {e.get('message', '(none)')}")

    elif status == 422:
        _err(f"HTTP {status} Unprocessable Entity — GraphQL validation failed.")
        for e in gql_errors:
            code    = e.get("extensions", {}).get("code", "UNKNOWN")
            message = e.get("message", "(no message)")
            _info(f"code    : {YELLOW}{code}{RESET}")
            _info(f"message : {message}")
        _info("Fix: check field names against the schema at")
        _info("     https://docs.meter.com/reference/api/schema/queries")

    elif status == 200 and gql_errors:
        _warn(f"HTTP {status} OK — but the GraphQL layer returned errors:")
        for e in gql_errors:
            code    = e.get("extensions", {}).get("code", "UNKNOWN")
            message = e.get("message") or "(empty message)"
            _err(f"code    : {code}")
            _info(f"message : {message}")
            if code == "UNAUTHORIZED":
                _info("This resource is not accessible to this API key.")
                _info("Cause: UUID belongs to a different company, or the")
                _info("       feature is not enabled for your account.")

    elif status == 200:
        _ok(f"HTTP {status} OK — no errors.")

    else:
        _err(f"HTTP {status} — unexpected status code.")

    # Always show the raw response body for debugging
    try:
        body = response.json()
        print(f"\n  {BOLD}Response body:{RESET}")
        _json(body)
    except Exception:
        print(f"  Raw body (first 500 chars): {response.text[:500]}")


# ── Scenario 1: Invalid token ──────────────────────────────────────────────────

def scenario_invalid_token(api_url: str) -> None:
    """
    HTTP 401 — deliberately wrong API key.

    The server validates the Bearer token before executing any GraphQL.
    An invalid, expired, or revoked token causes an immediate 401 response.

    Expected response:
        HTTP 401
        Body: { "id": "unauthorized" }

    How to fix in production:
        1. Check that API_TOKEN in config.py matches the Dashboard key exactly.
        2. If the key was revoked or rotated, create a new one in Dashboard.
        3. Do not share keys across environments — use separate keys per integration.
    """
    print_scenario(1, "HTTP 401 — Invalid API Token",
                   "Sending a request with a deliberately wrong Bearer token.")

    headers = {
        "Content-Type": "application/json",
        "Authorization": "Bearer INVALID_TOKEN_THAT_DOES_NOT_EXIST",
    }
    body = {"query": '{ companyBySlug(slug: "meter") { name } }'}

    _info("Request Authorization: Bearer INVALID_TOKEN_THAT_DOES_NOT_EXIST")
    response = _post(api_url, headers, body)
    describe_http_error(response)


# ── Scenario 2: Missing Authorization header ───────────────────────────────────

def scenario_missing_auth_header(api_url: str) -> None:
    """
    HTTP 401 — Authorization header absent entirely.

    Omitting the Authorization header is treated the same as an invalid token.
    The server does not fall back to any other auth mechanism.

    Expected response:
        HTTP 401
        Body: { "id": "unauthorized" }

    How to fix:
        Ensure every request includes:
            Authorization: Bearer YOUR_API_KEY
    """
    print_scenario(2, "HTTP 401 — Missing Authorization Header",
                   "Sending a request with no Authorization header.")

    headers = {"Content-Type": "application/json"}
    body    = {"query": '{ companyBySlug(slug: "meter") { name } }'}

    _info("No Authorization header in this request.")
    response = _post(api_url, headers, body)
    describe_http_error(response)


# ── Scenario 3: Malformed JSON body ───────────────────────────────────────────

def scenario_malformed_json(api_url: str, api_token: str) -> None:
    """
    HTTP 400 — request body is not valid JSON.

    The Meter API parses the request body as JSON before passing the
    `query` field to GraphQL. Invalid JSON causes an immediate 400
    response before any GraphQL processing begins.

    Expected response:
        HTTP 400
        Body: errors array with a JSON decode failure message

    Common causes:
        • String concatenation when building query payloads:
              '{"query": "' + raw_query + '"}'
          If raw_query contains unescaped quotes or newlines this breaks JSON.
        • Missing closing brace in a hand-crafted payload string.

    How to fix:
        Always serialise the payload with json.dumps():
            payload = json.dumps({"query": query_string})
        Or pass a dict to requests.post(..., json={...}) and let requests
        handle serialisation automatically.
    """
    print_scenario(3, "HTTP 400 — Malformed JSON Body",
                   "Sending a request whose body is not valid JSON.")

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_token}",
    }
    # Deliberately broken JSON: missing closing quote and brace
    malformed = '{"query": "{ companyBySlug(slug: \\"meter\\") { name } "'

    _info(f"Sending raw body: {malformed}")
    response = _post(api_url, headers, malformed)
    describe_http_error(response)


# ── Scenario 4: Non-existent schema field ─────────────────────────────────────

def scenario_invalid_field(api_url: str, api_token: str) -> None:
    """
    HTTP 422 — querying a field that does not exist in the Meter schema.

    After JSON parsing succeeds, the GraphQL engine validates the query
    against the schema. If the query references an unknown field the server
    returns HTTP 422 with extension code GRAPHQL_VALIDATION_FAILED.

    Expected response:
        HTTP 422
        Body: {
            "errors": [{
                "message": "Cannot query field 'nonExistentField' on type 'Company'.",
                "extensions": { "code": "GRAPHQL_VALIDATION_FAILED" }
            }]
        }

    Common causes:
        • Typo in a field name (e.g. "websiteURL" instead of "websiteDomain").
        • Using a field from a different API version.
        • Querying a field your account does not have access to.

    How to fix:
        Consult the schema reference:
            https://docs.meter.com/reference/api/schema/types
    """
    print_scenario(4, "HTTP 422 — Invalid Field Name (GRAPHQL_VALIDATION_FAILED)",
                   "Querying 'nonExistentField' which is not in the Company type.")

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_token}",
    }
    body = {
        "query": '{ companyBySlug(slug: "meter") { name nonExistentField } }'
    }

    _info("Query: { companyBySlug(slug: \"meter\") { name nonExistentField } }")
    response = _post(api_url, headers, body)
    describe_http_error(response)


# ── Scenario 5: Empty query string ────────────────────────────────────────────

def scenario_empty_query(api_url: str, api_token: str) -> None:
    """
    HTTP 422 — the `query` field is an empty string.

    When the JSON body is valid but the `query` value contains no GraphQL
    operation, the server returns HTTP 422 with the message
    "no operation provided".

    Expected response:
        HTTP 422
        Body: {
            "errors": [{
                "message": "no operation provided",
                "extensions": { "code": "GRAPHQL_VALIDATION_FAILED" }
            }]
        }

    Common causes:
        • Accidentally assigning an empty string to the query variable
          when building queries programmatically.
        • A conditional that produces an empty string on a code path.

    How to fix:
        Validate that the query string is non-empty before sending:
            if not query.strip():
                raise ValueError("GraphQL query cannot be empty")
    """
    print_scenario(5, "HTTP 422 — Empty Query String (GRAPHQL_VALIDATION_FAILED)",
                   "Sending a request where the `query` field is an empty string.")

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_token}",
    }
    body = {"query": ""}

    _info("Sending: { \"query\": \"\" }")
    response = _post(api_url, headers, body)
    describe_http_error(response)


# ── Scenario 6: GraphQL UNAUTHORIZED (HTTP 200) ───────────────────────────────

def scenario_graphql_unauthorized(api_url: str, api_token: str) -> None:
    """
    HTTP 200 with GraphQL UNAUTHORIZED error.

    This is the most subtle error type. The HTTP request itself is valid and
    properly authenticated, but the query accesses a resource that is
    outside this API key's scope:
        • A network UUID belonging to a different company
        • A virtual device UUID the key cannot access
        • A feature not enabled for this account

    Unlike HTTP 401 (token is invalid), this returns HTTP 200 with
    an errors array in the response body. The extension code is UNAUTHORIZED
    and the message field is typically empty.

    Expected response:
        HTTP 200
        Body: {
            "errors": [{
                "message": "",
                "extensions": { "code": "UNAUTHORIZED" }
            }],
            "data": null
        }

    How to distinguish from HTTP 401:
        HTTP 401 → token itself is bad (fix: rotate the key)
        HTTP 200 + UNAUTHORIZED → token is valid but resource is off-limits
                                   (fix: use a UUID your key can access)

    How to detect in code:
        Always check for response["errors"] even when status == 200:
            data = response.json()
            if "errors" in data:
                handle_graphql_errors(data["errors"])
    """
    print_scenario(6, "HTTP 200 with GraphQL UNAUTHORIZED Error",
                   "Querying a UUID that belongs to a different company.")

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_token}",
    }
    # A syntactically valid UUID that is not accessible to this API key
    foreign_uuid = "00000000-0000-0000-0000-000000000000"
    body = {
        "query": f'{{ networkClients(networkUUID: "{foreign_uuid}") {{ macAddress }} }}'
    }

    _info(f"Querying networkClients for UUID: {foreign_uuid}")
    _info("This UUID does not belong to this API key's company.")
    response = _post(api_url, headers, body)
    describe_http_error(response)


# ── Scenario 7: Successful request (baseline) ─────────────────────────────────

def scenario_success(api_url: str, api_token: str, company_slug: str) -> None:
    """
    HTTP 200 OK — successful query (reference baseline).

    Shows what a valid, fully-successful response looks like so developers
    can clearly distinguish it from the error scenarios above.

    A successful response:
        • HTTP status 200
        • Body contains a `data` key (not null)
        • Body contains NO `errors` key (or an empty list)
        • Rate-limit headers are present

    Best practice — always check both status code AND errors array:

        response = requests.post(url, headers=headers, json=payload)
        response.raise_for_status()          # catches HTTP 4xx/5xx
        body = response.json()
        if "errors" in body:
            handle_graphql_errors(body["errors"])  # catches HTTP 200 + errors
        data = body["data"]
    """
    print_scenario(7, "HTTP 200 OK — Successful Request (baseline)",
                   "A valid token querying an accessible resource.")

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_token}",
    }
    body = {
        "query": f'{{ companyBySlug(slug: "{company_slug}") {{ uuid name slug isCustomer }} }}'
    }

    _info(f"Querying companyBySlug(slug: \"{company_slug}\")")
    response = _post(api_url, headers, body)

    status = response.status_code
    rl_remaining = response.headers.get("X-RateLimit-Remaining", "—")
    rl_reset     = response.headers.get("X-RateLimit-Reset", "—")

    if status == 200:
        parsed = response.json()
        if "errors" not in parsed:
            _ok(f"HTTP {status} OK — query succeeded.")
            print(f"\n  {BOLD}Rate-limit headers:{RESET}")
            _info(f"X-RateLimit-Remaining : {rl_remaining}")
            _info(f"X-RateLimit-Reset     : {rl_reset}")
            print(f"\n  {BOLD}Response data:{RESET}")
            _json(parsed)
        else:
            _warn("HTTP 200 but GraphQL errors are present:")
            describe_http_error(response)
    else:
        describe_http_error(response)


# ── Recommended error-handling wrapper ────────────────────────────────────────

def safe_query(
    query: str,
    api_url: str,
    api_token: str,
) -> dict | None:
    """
    Production-ready GraphQL request wrapper with comprehensive error handling.

    Covers all documented Meter API error scenarios:
        • requests.HTTPError  for HTTP 401, 400, 422, 429
        • GraphQL errors       embedded in HTTP 200 responses
        • Network errors       (timeout, connection refused)

    Args:
        query:     GraphQL query string.
        api_url:   Meter API endpoint URL.
        api_token: Bearer token for authentication.

    Returns:
        The `data` dict from a successful response, or None on any error.
    """
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_token}",
    }
    try:
        response = requests.post(
            api_url, headers=headers, json={"query": query}, timeout=60
        )

        # ── HTTP-level error handling ──────────────────────────────────────
        if response.status_code == 401:
            _err("Authentication failed (HTTP 401). Check your API token.")
            return None

        if response.status_code == 429:
            retry = response.headers.get("Retry-After", "unknown")
            _err(f"Rate limited (HTTP 429). Retry after: {retry}")
            return None

        if response.status_code == 400:
            _err("Malformed request (HTTP 400). Inspect your query string.")
            return None

        if response.status_code == 422:
            gql_errors = extract_graphql_errors(response)
            for e in gql_errors:
                _err(f"Validation error: {e.get('message')}")
            return None

        response.raise_for_status()

        # ── GraphQL-level error handling (HTTP 200 with errors array) ─────
        body = response.json()
        if "errors" in body:
            for e in body["errors"]:
                code = e.get("extensions", {}).get("code", "UNKNOWN")
                msg  = e.get("message") or "(no message)"
                _err(f"GraphQL error [{code}]: {msg}")
            return None

        return body.get("data")

    except requests.Timeout:
        _err("Request timed out after 60 seconds.")
        return None
    except requests.ConnectionError as exc:
        _err(f"Network error: {exc}")
        return None


# ── Main ──────────────────────────────────────────────────────────────────────���

if __name__ == "__main__":
    API_URL      = config.API_URL
    API_TOKEN    = config.API_TOKEN
    COMPANY_SLUG = config.COMPANY_SLUG

    print(f"\n{BOLD}Meter API — Error Handling Demo{RESET}")
    print(f"Endpoint : {API_URL}")
    print()
    print("This script intentionally triggers every documented error type")
    print("to demonstrate detection, description, and recommended fixes.")

    scenario_invalid_token(API_URL)
    scenario_missing_auth_header(API_URL)
    scenario_malformed_json(API_URL, API_TOKEN)
    scenario_invalid_field(API_URL, API_TOKEN)
    scenario_empty_query(API_URL, API_TOKEN)
    scenario_graphql_unauthorized(API_URL, API_TOKEN)
    scenario_success(API_URL, API_TOKEN, COMPANY_SLUG)

    # ── Production pattern demo ───────────────────────────────────────────────
    print(f"\n{BOLD}{CYAN}{'━' * 65}{RESET}")
    print(f"{BOLD}{CYAN}  Production Error-Handling Wrapper (safe_query){RESET}")
    print(f"{CYAN}  Demonstrates the recommended pattern for real applications.{RESET}")
    print(f"{BOLD}{CYAN}{'━' * 65}{RESET}")

    _info("Running safe_query with a valid query...")
    data = safe_query(
        f'{{ companyBySlug(slug: "{COMPANY_SLUG}") {{ name }} }}',
        API_URL,
        API_TOKEN,
    )
    if data:
        _ok(f"safe_query returned: {data}")

    _info("Running safe_query with an invalid token...")
    data = safe_query(
        '{ companyBySlug(slug: "meter") { name } }',
        API_URL,
        "INVALID_TOKEN",
    )
    if data is None:
        _warn("safe_query returned None (error was caught and logged above).")

    # ── Summary ───────────────────────────────────────────────────────────────
    print(f"\n{BOLD}Error type summary:{RESET}")
    print(f"  {RED}HTTP 401{RESET}  Unauthorized          — invalid or missing token")
    print(f"  {RED}HTTP 429{RESET}  Too Many Requests     — 500 req/min limit exceeded")
    print(f"  {RED}HTTP 400{RESET}  Bad Request           — request body is not valid JSON")
    print(f"  {YELLOW}HTTP 422{RESET}  Validation Failed     — unknown field or empty query")
    print(f"  {YELLOW}HTTP 200{RESET}  GraphQL UNAUTHORIZED  — valid token, inaccessible resource")
    print(f"  {GREEN}HTTP 200{RESET}  Success               — data in response[\"data\"]")
    print()
