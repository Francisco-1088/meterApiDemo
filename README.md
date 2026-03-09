# Meter API — Example Scripts Documentation

This document covers the example scripts and Jupyter notebooks included in this project. Each Python script is self-contained and runnable with `python <script>.py` after installing dependencies and setting credentials in [config.py](config.py). Each script also has a matching `.ipynb` notebook that is identical in logic but executable cell-by-cell with live output.

---

## Table of Contents

1. [Prerequisites](#prerequisites)
2. [Quick Start](#quick-start)
3. [basic_query.py](#basic_querypy)
4. [bundled_query.py](#bundled_querypy)
5. [error_handling.py](#error_handlingpy)
6. [rate_limiting.py](#rate_limitingpy)
7. [modified_server.py](#modified_serverpy)
8. [Jupyter Notebooks](#jupyter-notebooks)
9. [Quick Reference](#quick-reference)

---

## Prerequisites

**Install dependencies**

```bash
pip install -r requirements.txt
```

**Configure credentials in [config.py](config.py)**

| Variable | Description |
|---|---|
| `API_URL` | `https://api.meter.com/api/v1/graphql` |
| `API_TOKEN` | Your Meter API Bearer token |
| `COMPANY_SLUG` | Your company slug (e.g. `"acme"`) |
| `COMPANY_UUID` | Your company UUID |
| `NETWORK_UUID` | The network UUID to query |
| `VIRTUAL_DEVICE_UUID` | A switch UUID for device-level queries |

API tokens are created in the Dashboard under **Settings → Integrations → API keys**.

---

## Quick Start

**1. Install dependencies and set credentials**

```bash
pip install -r requirements.txt
```

Edit [config.py](config.py) with your `API_TOKEN`, `COMPANY_SLUG`, `COMPANY_UUID`, and `NETWORK_UUID`.

**2. Verify authentication**

```bash
python basic_query.py
```

This confirms your token is valid and prints your company, networks, devices, and active clients.

**3. Launch the real-time dashboard**

```bash
python modified_server.py
# Open http://localhost:8080
```

**Where to go next**

| Goal | Script |
|---|---|
| Understand auth and individual queries | [basic_query.py](#basic_querypy) |
| Reduce API calls with query bundling | [bundled_query.py](#bundled_querypy) |
| Handle errors robustly in production | [error_handling.py](#error_handlingpy) |
| Manage the 500 req/min rate limit | [rate_limiting.py](#rate_limitingpy) |
| Run a live multi-network web dashboard | [modified_server.py](#modified_serverpy) |

---

## basic_query.py

**Purpose:** Demonstrates how to authenticate and perform simple, individual queries against the Meter GraphQL API. Intended as the first script a new developer should read.

**Run it**

```bash
python basic_query.py
```

### What it covers

#### Authentication

Every request requires two HTTP headers:

```
Content-Type: application/json
Authorization: Bearer YOUR_API_KEY
```

`make_headers(token)` builds this dict. The token is the full API key string exactly as displayed in the Dashboard — no extra encoding needed.

#### Core request function

`run_query(query, token, url)` sends an HTTP POST to the GraphQL endpoint with the query embedded as a JSON body:

```json
{ "query": "{ companyBySlug(slug: \"acme\") { name } }" }
```

Raises `requests.HTTPError` on 4xx/5xx responses. Returns the parsed JSON response dict.

#### Query functions

| Function | GraphQL query | Key fields returned |
|---|---|---|
| `get_company(slug)` | `companyBySlug` | uuid, name, slug, isCustomer, websiteDomain |
| `get_networks(company_slug)` | `networksForCompany` | UUID, label, slug |
| `get_virtual_devices(network_uuid)` | `virtualDevicesForNetwork` | UUID, label, deviceType, deviceModel, isOnline |
| `get_network_clients(network_uuid)` | `networkClients` | macAddress, ip, clientName, isWireless, signal, lastSeen, connectedVLAN, connectedSSID |
| `get_hardware_device(serial_number)` | `hardwareDevice` | serialNumber, deviceType, deviceModel, isConnectedToBackend, macAddress, networkUUID, virtualDeviceUUID |

#### Execution flow

```
Step 1 — Verify authentication (companyBySlug)
Step 2 — Discover networks (networksForCompany)
Step 3 — List virtual devices (virtualDevicesForNetwork)
Step 4 — List active clients (networkClients)
Step 5 — Print full raw JSON response
```

#### Rate-limit headers

The helper `print_rate_limit_info(response_headers)` logs two headers present on every API response:

- `X-RateLimit-Remaining` — requests left in the current 60-second window
- `X-RateLimit-Reset` — RFC 1123 timestamp when the window resets

---

## bundled_query.py

**Purpose:** Demonstrates GraphQL query bundling — combining multiple resource types into a single HTTP request to minimise API calls and stay within the rate limit.

**Run it**

```bash
python bundled_query.py
```

### What it covers

#### Why bundle?

A REST API requires one HTTP request per resource type. GraphQL allows multiple top-level fields in a single operation, returning all datasets in one response. The Meter documentation explicitly recommends this:

> "Batch related data into a single query to reduce the number of requests you make."

#### How aliases work

When the same field name appears twice with different arguments, a GraphQL alias disambiguates them:

```graphql
{
  companyInfo:      companyBySlug(slug: "acme") { name }
  uplinkInterfaces: uplinkPhyInterfacesForNetwork(networkUUID: "...") { label }
}
```

Both results are returned under their alias keys in the response `data` object.

#### The four bundles

**Bundle 1 — Network Overview** (`network_overview_bundle`)

Combines four resource types in one request:

| Alias | Query | Data |
|---|---|---|
| `companyInfo` | `companyBySlug` | Company name, UUID, slug |
| _(none)_ | `networkClients` | All active clients |
| `uplinkInterfaces` | `uplinkPhyInterfacesForNetwork` | WAN port status |
| `eventLog` | `recentEventLogEventsPage` | Last 10 events |

Without bundling: **4 requests**. With bundling: **1 request**.

**Bundle 2 — Metrics** (`metrics_bundle`)

Combines three time-series metrics queries (last 4 hours, 5-minute buckets):

| Alias | Query | Data |
|---|---|---|
| `uplinkQuality` | `networksUplinkQualities` | WAN quality scores |
| `uplinkThroughput` | `networkUplinkThroughput` | Upload/download bandwidth |
| _(none)_ | `switchPortStats` | Port traffic counters |

Without bundling: **3 requests**. With bundling: **1 request**.

**Bundle 3 — Inventory** (`inventory_bundle`)

Combines four list resources:

| Alias | Query | Data |
|---|---|---|
| `devices` | `virtualDevicesForNetwork` | All switches, APs, controllers |
| `clients` | `networkClients` | All active clients |
| `ssids` | `ssidsForNetwork` | Wi-Fi SSIDs and encryption |
| `vlans` | `vlans` | VLAN configuration |

Without bundling: **4 requests**. With bundling: **1 request**.

**Bundle 4 — Multi-network clients** (`multi_network_clients_bundle`)

Demonstrates aliasing the same field twice with different UUIDs alongside a separate query:

| Alias | Query |
|---|---|
| `primaryNetworkClients` | `networkClients(networkUUID: "uuid-a")` |
| `secondaryNetworkClients` | `networkClients(networkUUID: "uuid-b")` |
| `companyClients` | `networksClients(companyUUID: "...", ...)` |

Without bundling: **3 requests**. With bundling: **1 request**.

> **Note:** Bundle 4 is defined in the script but not called in `main()`. It is demonstrated in full in the [Jupyter notebook](#bundled_queryipynb).

#### Request count comparison (script `main()`)

| Approach | HTTP requests |
|---|---|
| Separate REST-style calls | 11+ |
| This demo (3 bundled queries) | **3** |

---

## error_handling.py

**Purpose:** Demonstrates every documented Meter API error type with colour-coded terminal output and recommended handling patterns for each.

**Run it**

```bash
python error_handling.py
```

### Error taxonomy

The Meter API produces two categories of errors:

**HTTP-level errors** — The server rejects the request before GraphQL runs. Identified by a non-200 HTTP status code.

**GraphQL-level errors** — The HTTP request succeeds (status 200) but the GraphQL layer returns errors inside the `errors` array in the response body.

### Error types

#### HTTP 401 — Unauthorized

**Trigger:** Missing or invalid `Authorization` header, or an expired/revoked API key.

**Response body:**
```json
{ "id": "unauthorized" }
```

**Scenarios demonstrated:**
- `scenario_invalid_token()` — wrong token value
- `scenario_missing_auth_header()` — header omitted entirely

**Fix:** Verify `API_TOKEN` in `config.py`. Regenerate the key in Dashboard → Settings → Integrations → API keys.

---

#### HTTP 400 — Bad Request

**Trigger:** The request body is not valid JSON.

**Scenario demonstrated:** `scenario_malformed_json()` — sends a raw string with a missing closing quote.

**Common cause:** String concatenation when building query payloads:
```python
# BAD — breaks if query contains unescaped quotes
payload = '{"query": "' + raw_query + '"}'

# GOOD
payload = json.dumps({"query": raw_query})
# or
requests.post(url, json={"query": raw_query})
```

---

#### HTTP 422 — Validation Failed

**Trigger:** The JSON body is valid but the GraphQL query is semantically wrong.

**Response extension code:** `GRAPHQL_VALIDATION_FAILED`

**Scenarios demonstrated:**

- `scenario_invalid_field()` — queries `nonExistentField` on the `Company` type
  ```
  "Cannot query field 'nonExistentField' on type 'Company'."
  ```

- `scenario_empty_query()` — sends `{"query": ""}`
  ```
  "no operation provided"
  ```

**Fix:** Check field names against the schema at `https://docs.meter.com/reference/api/schema/types`.

---

#### HTTP 429 — Too Many Requests

**Trigger:** More than 500 requests per minute sent from the same API key.

**Additional header on 429 responses:**
```
Retry-After: Fri, 07 Mar 2026 12:01:00 GMT
```

See [rate_limiting.py](#rate_limitingpy) for a dedicated deep-dive.

---

#### HTTP 200 with GraphQL UNAUTHORIZED

**Trigger:** The token is valid, but the queried resource is outside the key's scope (wrong company UUID, inaccessible feature, unknown UUID).

**Response body (status 200):**
```json
{
  "errors": [{ "message": "", "extensions": { "code": "UNAUTHORIZED" } }],
  "data": null
}
```

**Scenario demonstrated:** `scenario_graphql_unauthorized()` — queries `networkClients` with a UUID that doesn't belong to this API key.

**Key distinction:**

| Error | Cause | Fix |
|---|---|---|
| HTTP 401 | Token itself is bad | Rotate the key |
| HTTP 200 + UNAUTHORIZED | Token valid, resource off-limits | Use a UUID your key can access |

**Detection pattern — always check for errors even on HTTP 200:**
```python
response.raise_for_status()          # catches 401, 400, 422, 429
body = response.json()
if "errors" in body:                 # catches 200 + UNAUTHORIZED
    handle_errors(body["errors"])
data = body["data"]
```

---

#### HTTP 200 OK — Successful Request (Scenario 7)

**Scenario demonstrated:** `scenario_success()` — baseline showing what a fully successful response looks like.

A successful response has:
- HTTP status 200
- A `data` key that is not null
- No `errors` key (or an empty list)
- Rate-limit headers present

---

### Production wrapper: `safe_query()`

The script includes a `safe_query(query, api_url, api_token)` function demonstrating the recommended production pattern:

```python
data = safe_query(
    '{ companyBySlug(slug: "acme") { name } }',
    API_URL,
    API_TOKEN,
)
if data:
    print(data["companyBySlug"]["name"])
```

Handles all error types, logs them, and returns `None` on failure.

### Terminal output colours

| Colour | Meaning |
|---|---|
| Green `✓` | Success |
| Red `✗` | Error |
| Yellow `⚠` | Warning / partial |
| Dim `→` | Informational detail |

---

## rate_limiting.py

**Purpose:** Demonstrates the Meter API rate limit in practice: how to read rate-limit headers, how to intentionally trigger HTTP 429 errors using `asyncio`, and how to handle them correctly.

**Run it**

```bash
python rate_limiting.py
```

> **Note:** This script intentionally sends 600 concurrent requests to exhaust the 500 req/min rate limit. It will consume real API quota and block your key for up to 60 seconds.

### Rate limit specification

| Property | Value |
|---|---|
| Limit | 500 requests per minute per API key |
| Request timeout | 60 seconds |
| Window | 60 seconds (rolling) |

### Rate-limit headers

| Header | Present on | Description |
|---|---|---|
| `X-RateLimit-Remaining` | Every response | Requests left in the current window |
| `X-RateLimit-Reset` | Every response | RFC 1123 timestamp when the window resets |
| `Retry-After` | HTTP 429 only | RFC 1123 timestamp — earliest safe retry time |

**RFC 1123 format example:** `Fri, 07 Mar 2026 12:01:00 GMT`

### asyncio design

The script uses `asyncio.to_thread()` (Python 3.9+) to run blocking `requests.post()` calls on a background `ThreadPoolExecutor` without blocking the event loop. This enables genuine concurrency (bypassing the GIL for I/O) without requiring `aiohttp`.

```python
response = await asyncio.to_thread(
    requests.post, url, headers=headers, json=payload, timeout=60
)
```

A semaphore limits simultaneous in-flight requests to 50, preventing OS file-descriptor exhaustion while still sending requests fast enough to trigger 429 responses.

### Sections

**Section 1 — Observing Headers**

Makes one normal request and prints the rate-limit header values as a baseline.

**Section 2 — Triggering HTTP 429**

`section_flood_to_trigger_429()` launches 600 concurrent tasks (`FLOOD_COUNT = 600`) via `asyncio.gather()`. Each task calls `single_async_request()` which logs one line per response:

```
[  1] 200 OK  remaining=499
[  2] 200 OK  remaining=498
...
[501] 429 Too Many Requests  Retry-After: Fri, 07 Mar 2026 12:01:00 GMT
```

**Section 3 — Retry with Back-off**

`request_with_retry(max_attempts=5)` demonstrates the correct retry pattern:

1. Check `X-RateLimit-Remaining` before sending — if below `PROACTIVE_THRESHOLD` (50), sleep until `X-RateLimit-Reset`.
2. Send the request.
3. On 429: parse `Retry-After`, sleep exactly that many seconds, then retry.
4. If `Retry-After` is absent, use exponential back-off (`1s`, `2s`, `4s`, `8s`...).

**Section 4 — Proactive Monitoring Demo**

Runs 5 sequential requests while logging the remaining count, then prints the full best-practices summary:

```
✓  Log X-RateLimit-Remaining on every response
✓  Slow down proactively when remaining < 50
✓  On 429: read Retry-After, sleep, retry exactly once
✓  Bundle multiple queries into one GraphQL request
✓  Use asyncio.to_thread() to keep async code non-blocking
✗  Do NOT retry immediately in a tight loop after 429
✗  Do NOT ignore 429 — repeated 429s do not reset the window
✗  Do NOT fire 500+ unchecked requests per minute per key
```

### Key functions

| Function | Description |
|---|---|
| `parse_rfc1123(value)` | Parses RFC 1123 header string into a timezone-aware `datetime` |
| `seconds_until(dt)` | Returns seconds until a future datetime, clamped to 0 |
| `update_rate_limit_state(headers)` | Updates shared `_rl_remaining` / `_rl_reset` from response headers |
| `single_async_request(id, semaphore)` | One async HTTP request with rate-limit state update |
| `request_with_retry(max_attempts)` | Full proactive + reactive retry loop |

---


## modified_server.py

**Purpose:** A multi-network real-time web dashboard for Meter network infrastructure. Serves a Flask application that polls the Meter API on a background thread and presents all data through an interactive browser UI.

**Run it**

```bash
python modified_server.py
# Dashboard available at http://localhost:8080
```

Override the port with the `PORT` environment variable:

```bash
PORT=9000 python modified_server.py
```

### Architecture

```
Background thread (every 300s)          Browser (polling every 30s)
──────────────────────────────          ──────────────────────────
fetch_all()                   ──────→   GET /api/data  →  JavaScript
  └─ _do_fetch()              ←──────   POST /api/refresh  (manual)
       └─ 6-step pipeline
            └─ _commit() → _cache
```

The Flask server is single-process. A `threading.Lock` serialises all reads and writes to `_cache`. The browser polls `/api/data` every 30 seconds and re-renders the active tab on every update.

### Data pipeline

`_do_fetch()` runs six sequential steps on every refresh cycle:

| Step | API query | Data collected |
|---|---|---|
| 1 | `networksForCompany` | Discover all networks in the company |
| 2 | Per-network bundle | Clients, uplink interfaces, event log, virtual devices (one request per network) |
| 3 | `networksUplinkQualities` | WAN quality scores — all networks in a single request (last 4 h, 5-min buckets) |
| 4 | `networkUplinkThroughput` | WAN throughput — all networks bundled via GraphQL aliases |
| 5 | `switchPortStats` | Per-port traffic counters — all switches bundled, batched in groups of 15 |
| 6 | `phyInterfacesForVirtualDevice` | Connected devices per switch port → MAC-to-switch/port lookup map |

### Rate limiting

The server tracks `X-RateLimit-Remaining` and `X-RateLimit-Reset` on every API response via `_update_rl()`:

- **Proactive sleep:** when `remaining < PROACTIVE_THRESHOLD` (20), the pipeline sleeps until the reset timestamp before sending the next request.
- **HTTP 429 handling:** reads `Retry-After`, sleeps the specified interval, then retries.
- **Retry logic:** up to `MAX_RETRIES` (3) attempts per request with exponential fallback between non-429 failures.

### Flask routes

| Route | Method | Description |
|---|---|---|
| `/` | GET | Serves the full HTML/JS dashboard (rendered via `render_template_string`) |
| `/api/data` | GET | Returns the current cache as JSON: `{ data: {...}, last_updated: "..." }` |
| `/api/refresh` | POST | Triggers an immediate non-blocking re-fetch in a daemon thread |

### Web UI

The dashboard is a dark-themed single-page application embedded in the Python file as an HTML template string. All rendering happens client-side in JavaScript using data from `/api/data`.

#### Layout

- **Fixed header** (52 px) — logo, network count, last-updated timestamp with live pulse indicator
- **Left sidebar** (220 px) — navigation between the six tabs
- **Content area** — scrollable, renders the active tab

#### Tabs

| Tab | Sidebar label | Data shown |
|---|---|---|
| `uplink-quality` | Uplink Quality | WAN quality score per interface, last 4 h, colour-coded bars |
| `throughput` | Throughput | Upload/download bandwidth per interface with visual bars |
| `clients` | Network Clients | Active wired and wireless clients with signal strength, VLAN, SSID, device |
| `phy-ifaces` | Physical Interfaces | WAN uplink port configuration, link status, speed, native VLAN |
| `switch-ports` | Switch Ports | Cumulative RX/TX bytes and packets per switch port with error highlighting |
| `events` | Event Log | Most recent 50 events per network, sorted newest first, colour-coded by severity |

#### Toolbar (per tab)

Each tab has a toolbar with three controls:

1. **Network dropdown** — filter to a single network or show all
2. **Search box** — case-insensitive substring search across all relevant columns; supports port range syntax on the Switch Ports tab (e.g. `1-8` or `3`)
3. **Refresh button** — triggers `/api/refresh` and polls until new data arrives

#### Table sorting

Every column header is clickable. Clicking a header sorts the table by that column ascending; clicking again reverses to descending. The active sort column shows ↑ or ↓; inactive columns show ⇅. Sorting is applied after all filters.

Numeric columns (bytes, packets, port numbers, signal strength, sample counts) sort numerically. Text columns use locale-aware string comparison.

#### Column include/exclude filters

Every column in every table has a **≡** filter button in its header. Clicking it opens a floating popover with:

- **Include / Exclude** radio toggle — choose whether the selected values are shown or hidden
- **Filter values search** — type to narrow the list of checkboxes
- **Value checkboxes** — one per unique value in that column across all current rows
- **Clear** button — removes all active filters for that column
- **Done** button — closes the popover (filters stay active)

Active filters appear as blue chips below the toolbar, each showing `column = value` (include) or `column ≠ value` (exclude). Click ✕ on a chip to remove that filter.

Column filters are applied after the text search and before sorting.

#### Search autocomplete

Each search box is backed by a `<datalist>` that is updated on every render with up to 200 unique text values drawn from the current visible rows. The browser shows these as native autocomplete suggestions while typing.

#### Network Clients tab — columns

| Column | Description |
|---|---|
| Name | Client hostname or alias |
| IP | Assigned IP address |
| MAC | Hardware MAC address |
| Type | Wi-Fi (blue badge) or Wired (dim badge) |
| Signal | Wi-Fi signal strength — 4-bar visualisation + dBm value |
| VLAN | VLAN name and ID |
| SSID | Connected SSID (wireless clients only) |
| Device | Access point label (wireless) or switch label (wired) |
| Network | Network label (shown only when displaying all networks) |
| Last Seen | Relative time since last observed activity |

> **Note:** The Port column (previously showing the switch port number for wired clients) has been removed. Switch-to-port mappings are still fetched and used to populate the Device column.

#### Multi-network view

When the network dropdown is set to **All Networks**, an additional **Network** column appears in every table. All data from all networks is merged and displayed together, with each row badged with its network label.

### Configuration constants

| Constant | Default | Description |
|---|---|---|
| `REFRESH_INTERVAL` | `300` | Seconds between automatic background refreshes |
| `MAX_RETRIES` | `3` | Max attempts per GraphQL call before giving up |
| `PROACTIVE_THRESHOLD` | `20` | Sleep proactively when fewer than this many requests remain |
| `SWITCH_BATCH_SIZE` | `15` | Max switches bundled in a single `switchPortStats` request |

---

## Jupyter Notebooks

Each script has a matching Jupyter notebook with identical logic. Notebooks are best for exploring the API interactively — run individual cells, inspect live output, and modify queries without restarting a full script.

**Open all notebooks**

```bash
jupyter lab
# or
jupyter notebook
```

Credentials are loaded from `config.py` in all notebooks, the same as the scripts.

---

### basic_query.ipynb

**Mirrors:** [basic_query.py](#basic_querypy)

**Cells**

| Cell | Description |
|---|---|
| Setup | Imports `requests`, `config`; prints endpoint, company, network |
| `make_headers` | Defines and demonstrates the auth header builder |
| `run_query` + `print_rate_limit_info` | Core request function; rate-limit header helper |
| Step 1 — `get_company` | Defines and runs `companyBySlug`; prints company name, UUID, isCustomer |
| Step 2 — `get_networks` | Defines and runs `networksForCompany`; lists all networks with UUIDs |
| Step 3 — `get_virtual_devices` | Defines and runs `virtualDevicesForNetwork`; lists devices with online status |
| Step 4 — `get_network_clients` | Defines and runs `networkClients`; shows total/wireless/wired counts and first 5 clients |
| Step 5 — `get_hardware_device` | Defines `hardwareDevice` query; placeholder — replace serial number to run |
| Step 6 — Raw response | Prints the full unprocessed JSON from `companyBySlug` |
| Summary | Query reference table |

**Differences from the script:** The notebook has a dedicated Step 5 for `get_hardware_device` (the script combines it into the query function definitions), and a separate Step 6 for the raw response (Step 5 in the script).

---

### bundled_query.ipynb

**Mirrors:** [bundled_query.py](#bundled_querypy)

**Cells**

| Cell | Description |
|---|---|
| Setup | Imports; prints endpoint and network UUID |
| `run_query` + `summarise` | Core helpers |
| Bundle 1 — definition | Defines `network_overview_bundle` |
| Bundle 1 — run | Executes and prints results; shows company name, event count |
| Bundle 2 — definition | Defines `metrics_bundle` |
| Bundle 2 — run | Executes and prints results; shows quality data point count |
| Bundle 3 — definition | Defines `inventory_bundle` |
| Bundle 3 — run | Executes and prints results; lists all SSIDs with encryption and status |
| Bundle 4 — definition | Defines `multi_network_clients_bundle` |
| Bundle 4 — run | Executes using `COMPANY_UUID` from `config.py`; demonstrates alias syntax |
| Summary | Total elapsed time, request count comparison |

**Differences from the script:** The notebook runs all four bundles including Bundle 4 (`multi_network_clients_bundle`), whereas the script's `main()` runs only Bundles 1–3. The notebook summary therefore reports 4 HTTP requests replacing 14+ REST calls.

**Live output example (Bundle 3):**
```
devices    43 item(s)
clients   139 item(s)
ssids      11 item(s)
vlans      12 item(s)
```

---

### error_handling.ipynb

**Mirrors:** [error_handling.py](#error_handlingpy)

**Cells**

| Cell | Description |
|---|---|
| Setup | Imports; prints endpoint |
| Helpers | Defines `_post`, `extract_graphql_errors`, `describe_http_error` |
| Scenario 1 | HTTP 401 — invalid token; shows actual 401 response body |
| Scenario 2 | HTTP 401 — missing Authorization header |
| Scenario 3 | HTTP 400 — malformed JSON; shows parse error from server |
| Scenario 4 | HTTP 422 — `nonExistentField` on `Company` type |
| Scenario 5 | HTTP 422 — empty query string |
| Scenario 6 | HTTP 200 + `UNAUTHORIZED` — foreign UUID; shows `data.networkClients: null` |
| Scenario 7 | HTTP 200 OK — baseline success; shows rate-limit headers and response data |
| `safe_query` definition | Production-ready wrapper function |
| `safe_query` — valid token | Runs and prints `{'companyBySlug': {'name': 'Meter'}}` |
| `safe_query` — invalid token | Demonstrates `None` return on 401 |
| Summary | Error type reference table; "golden rule" detection pattern |

**Differences from the script:** Each scenario is an individual executable cell with its actual response body printed as output. The notebook includes cell outputs showing the real server responses (e.g. the exact JSON body for each error code), which the script prints at runtime only.

**Key actual response bodies captured in notebook output:**

- HTTP 401: `{ "title": "Unauthorized. Please include your API credentials", "id": "unauthorized", "status": 401 }`
- HTTP 422: `{ "errors": [{ "message": "Cannot query field \"nonExistentField\" on type \"Company\".", "extensions": { "code": "GRAPHQL_VALIDATION_FAILED" } }] }`
- HTTP 200 + UNAUTHORIZED: `{ "errors": [{ "message": "", "extensions": { "code": "UNAUTHORIZED" } }], "data": { "networkClients": null } }`

---

### rate_limiting.ipynb

**Mirrors:** [rate_limiting.py](#rate_limitingpy)

**Cells**

| Cell | Description |
|---|---|
| Setup | Imports; prints endpoint, flood count (600), proactive threshold (50) |
| Shared state | Initialises `_rl_remaining`, `_rl_reset`, `_rl_lock` |
| Helpers | Defines `parse_rfc1123`, `seconds_until`, `update_rate_limit_state`, `format_headers`; demonstrates RFC 1123 parsing |
| `single_async_request` | Core async request function using `asyncio.to_thread()` |
| `request_with_retry` | Proactive + reactive retry function |
| Section 1 — Observe | Single request; prints current remaining and reset time |
| Section 2 — Flood | 600 concurrent requests via `asyncio.gather()`; prints per-request status; shows final 200/429 counts |
| Section 3 — Retry | Calls `request_with_retry`; shows proactive back-off kicking in after the flood |
| Section 4 — Monitoring | 5 sequential requests; prints best-practices summary |
| Summary | Rate limit quick reference; asyncio pattern; RFC 1123 parsing snippet |

**Differences from the script:** Notebook cells use `await` directly (Jupyter supports top-level await). The flood section output is large — the notebook captures the first successful requests and first 429 responses inline. Section 3 output captured in the notebook shows:

```
⚠ [Attempt 1] Proactive back-off: only 0 requests remaining. Waiting 1.0s for window reset.
→ [Attempt 1/5] Sending request...
✓ Success on attempt 1.
```

> **Warning:** Running Section 2 exhausts your rate-limit window for up to 60 seconds. Run Sections 1, 3, and 4 independently to avoid this if you only want to study the retry logic.

---

## Quick Reference

### Run all scripts

```bash
python basic_query.py       # Auth + individual queries
python bundled_query.py     # Multiple queries per request
python error_handling.py    # All error types demonstrated
python rate_limiting.py     # 429 handling with asyncio
python modified_server.py   # Real-time web dashboard (http://localhost:8080)
```

### modified_server.py — key constants

```python
REFRESH_INTERVAL    = 300   # background poll interval (seconds)
MAX_RETRIES         = 3     # retries per API call
PROACTIVE_THRESHOLD = 20    # sleep early when < 20 requests remain
SWITCH_BATCH_SIZE   = 15    # switches per switchPortStats bundle
```
