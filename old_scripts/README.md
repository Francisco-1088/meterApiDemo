# Meter Public API — Demo Project

This project demonstrates how to query the [Meter](https://meter.com) GraphQL public API. It includes three entry points targeting different use cases: a one-off Python script, an interactive Bash demo, and a live web dashboard.

---

## Files Overview

### `config.py`

Shared configuration used by both Python files. Contains:

- `API_URL` — the Meter GraphQL endpoint
- `API_TOKEN` — your Bearer token for authentication
- `COMPANY_SLUG` / `COMPANY_UUID` — identifies the company
- `NETWORK_UUID` — the network to query
- `VIRTUAL_DEVICE_UUID` — a specific switch/AP device UUID

Update this file with your own credentials and UUIDs before running anything.

---

### `queries.py`

A library of functions that build GraphQL query payloads. Each function accepts relevant UUIDs (and optional time-range parameters) and returns a JSON string ready to POST to the API.

| Function | Description |
|---|---|
| `companyBySlug(slug)` | Fetch company details by slug |
| `networksUplinkQualities(uuid, ...)` | WAN uplink quality metrics over a time range |
| `networkClients(uuid)` | Active clients with IP, MAC, VLAN, SSID, signal |
| `companyClients(company_uuid, network_uuid)` | Clients across all networks for a company |
| `uplinkPhyInterfacesForNetwork(uuid)` | WAN uplink port configuration |
| `bssidsForNetwork(uuid)` | Wireless access point radios (BSSIDs) |
| `activeClients(uuid, ...)` | Wired vs. wireless client counts over time |
| `networkUplinkThroughput(uuid, ...)` | WAN bandwidth metrics per interface |
| `networkUplinkQuality(uuid, ...)` | WAN latency/jitter/packet-loss metrics |
| `recentEventLogEventsPage(uuid, limit)` | Most recent network events |
| `phyInterfacesForVirtualDevice(uuid)` | All ports on a specific device |
| `switchPortStats(uuid)` | Per-port traffic and error counters |

---

### `main.py`

A minimal script for running a single query and printing the result. It imports `config` and `queries`, calls `queries.companyBySlug()` with your configured slug, POSTs to the API, and pretty-prints the JSON response.

**Run it:**

```bash
python main.py
```

Use this as a starting point for experimenting with individual queries. Swap `queries.companyBySlug(...)` for any other function in `queries.py`.

---

### `public-api-demo.sh`

A self-contained Bash script that runs a curated set of ~12 GraphQL queries in sequence and prints colorized output to the terminal. It uses `curl` to make HTTP requests and `python3 -m json.tool` to pretty-print responses.

**Queries covered:**

1. Company Info
2. Multi-Network Uplink Quality
3. Network Clients
4. Multi-Network Clients
5. Uplink Physical Interfaces
6. BSSIDs
7. Active Clients Count
8. Uplink Throughput Metrics
9. Uplink Quality Metrics
10. Event Log
11. Physical Interfaces for Device
12. Switch Port Stats

**Run all queries:**

```bash
bash public-api-demo.sh
```

**Run a single query by name (partial match):**

```bash
bash public-api-demo.sh "Event Log"
bash public-api-demo.sh "Clients"
```

**Disable color output:**

```bash
NO_COLOR=1 bash public-api-demo.sh
```

The script skips any query where a required UUID placeholder has not been filled in.

---

### `server.py`

A Flask web application that serves a live network dashboard in the browser.

**How it works:**

1. On startup, a background thread immediately polls the Meter GraphQL API and then repeats every 5 minutes.
2. Results are cached in memory behind a thread lock.
3. The Flask server exposes two routes:
   - `GET /` — serves the dashboard HTML
   - `GET /api/data` — returns the cached data as JSON, along with a `last_updated` timestamp
4. The browser-side JavaScript polls `/api/data` every 30 seconds and re-renders the UI.

**Dashboard tabs:**

| Tab | Data shown |
|---|---|
| Uplink Quality | Per-interface WAN quality score over the last 4 hours |
| Network Clients | All active clients: IP, MAC, type, signal, VLAN, SSID |
| Physical Interfaces | WAN uplink port status, speed, and VLAN |
| Throughput | Upload/download bandwidth per interface |
| Event Log | Most recent 20 network events |
| Switch Ports | Per-port RX/TX bytes, packets, and error counts |

**Run it:**

```bash
pip install flask requests
python server.py
```

Then open `http://localhost:8080` in your browser. The port can be overridden with the `PORT` environment variable:

```bash
PORT=3000 python server.py
```

---

## Configuration

All three tools read credentials from `config.py` (Python) or from hardcoded variables at the top of `public-api-demo.sh` (Bash). Update these values before running:

| Variable | Description |
|---|---|
| `API_URL` | `https://api.meter.com/api/v1/graphql` |
| `API_TOKEN` | Your Meter API Bearer token |
| `COMPANY_SLUG` | Your company's slug (e.g. `"acme"`) |
| `COMPANY_UUID` | Your company UUID |
| `NETWORK_UUID` | The network UUID to query |
| `VIRTUAL_DEVICE_UUID` | A switch or AP UUID for device-level queries |

## Dependencies

**Python (main.py / server.py):**

```bash
pip install requests flask
```

**Bash (public-api-demo.sh):**

- `curl` — for HTTP requests
- `python3` — optional, used for JSON pretty-printing
