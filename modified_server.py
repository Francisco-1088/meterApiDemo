#!/usr/bin/env python3
"""
modified_server.py
==================
Meter Network Dashboard — multi-network Flask server with rate limiting,
error handling, left navigation pane, and per-tab search / network filter / refresh.

Data pipeline (runs on startup and every REFRESH_INTERVAL seconds):
  Step 1  networksForCompany          → discover all networks in the company
  Step 2  Per-network bundled query   → clients + uplink ifaces + event log + devices
           (one bundled GraphQL request per network; aliases combine four fields)
  Step 3  networksUplinkQualities     → all networks in a single request
  Step 4  networkUplinkThroughput     → all networks bundled via GraphQL aliases
  Step 5  switchPortStats             → all switches bundled via GraphQL aliases (batched)
  Step 6  phyInterfacesForVirtualDevice → per-switch connected devices → MAC→switch/port map

Rate limiting:
  - Tracks X-RateLimit-Remaining and X-RateLimit-Reset on every response.
  - Sleeps proactively when remaining < PROACTIVE_THRESHOLD.
  - On HTTP 429 reads Retry-After (RFC 1123) and sleeps accordingly.
  - Retries up to MAX_RETRIES times with exponential fallback.

Error handling:
  - HTTP 401 / 400 / 422 / 429 handled explicitly.
  - GraphQL-level UNAUTHORIZED and GRAPHQL_VALIDATION_FAILED detected from body.
  - All per-fetch errors collected in _cache["fetchErrors"] and shown as a banner.
"""

import os
import threading
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import config
import requests
from flask import Flask, jsonify, render_template_string

app = Flask(__name__)

# ── Configuration ──────────────────────────────────────────────────────────────

API_URL      = config.API_URL
API_TOKEN    = config.API_TOKEN
COMPANY_SLUG = config.COMPANY_SLUG

REFRESH_INTERVAL    = 300   # seconds between automatic background refreshes
MAX_RETRIES         = 3     # max attempts per GQL call before giving up
PROACTIVE_THRESHOLD = 20    # sleep proactively when fewer than this many requests remain
SWITCH_BATCH_SIZE   = 15    # max switches bundled in a single switchPortStats request

# ── Thread-safe cache ──────────────────────────────────────────────────────────

_cache: dict           = {}
_last_updated: str | None = None
_data_lock             = threading.Lock()

_fetch_in_progress     = False
_fetch_lock            = threading.Lock()

# ── Rate-limit state ───────────────────────────────────────────────────────────

_rl_remaining: int | None      = None
_rl_reset: datetime | None     = None
_rl_lock                       = threading.Lock()


def _parse_rfc1123(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return parsedate_to_datetime(value)
    except Exception:
        return None


def _update_rl(headers) -> None:
    global _rl_remaining, _rl_reset
    with _rl_lock:
        r = headers.get("X-RateLimit-Remaining")
        if r is not None:
            try:
                _rl_remaining = int(r)
            except ValueError:
                pass
        rs = headers.get("X-RateLimit-Reset")
        if rs:
            _rl_reset = _parse_rfc1123(rs)


def _proactive_sleep() -> None:
    """Sleep until the rate-limit window resets when almost exhausted."""
    with _rl_lock:
        remaining = _rl_remaining
        reset_dt  = _rl_reset
    if remaining is not None and remaining < PROACTIVE_THRESHOLD:
        if reset_dt:
            wait = max(0.0, (reset_dt - datetime.now(timezone.utc)).total_seconds()) + 1.0
        else:
            wait = 5.0
        print(f"  ⚠ Rate limit low ({remaining} remaining) — sleeping {wait:.1f}s", flush=True)
        time.sleep(wait)


# ── GraphQL helper ─────────────────────────────────────────────────────────────

_GQL_HEADERS = {
    "Content-Type":  "application/json",
    "Authorization": f"Bearer {API_TOKEN}",
}


def gql(query: str) -> dict:
    """
    Execute a GraphQL query with rate-limit awareness and full error handling.

    Returns:
        Parsed response dict. Contains an "error" key on any failure.
    """
    for attempt in range(1, MAX_RETRIES + 1):
        _proactive_sleep()
        try:
            resp = requests.post(
                API_URL,
                json={"query": query},
                headers=_GQL_HEADERS,
                timeout=30,
            )
            _update_rl(resp.headers)

            # ── HTTP-level errors ────────────────────────────────────────────
            if resp.status_code == 429:
                retry_dt = _parse_rfc1123(resp.headers.get("Retry-After"))
                if retry_dt:
                    wait = max(0.0, (retry_dt - datetime.now(timezone.utc)).total_seconds()) + 1.0
                else:
                    wait = 60.0 * attempt
                print(f"  HTTP 429 attempt {attempt}/{MAX_RETRIES} — sleeping {wait:.1f}s", flush=True)
                time.sleep(wait)
                continue

            if resp.status_code == 401:
                return {"error": "HTTP 401 Unauthorized — check API_TOKEN in config.py", "code": 401}

            if resp.status_code in (400, 422):
                try:
                    body = resp.json()
                except Exception:
                    body = {}
                msgs = [e.get("message", "") for e in body.get("errors", [])]
                return {"error": f"HTTP {resp.status_code}", "messages": msgs, "code": resp.status_code}

            resp.raise_for_status()

            # ── GraphQL-level errors (HTTP 200 with errors array) ────────────
            body = resp.json()
            if "errors" in body and body.get("data") is None:
                codes = [e.get("extensions", {}).get("code", "UNKNOWN") for e in body["errors"]]
                msgs  = [e.get("message") or "" for e in body["errors"]]
                return {"error": f"GraphQL {', '.join(codes)}", "messages": msgs, "body": body}

            return body

        except requests.Timeout:
            print(f"  Timeout on attempt {attempt}/{MAX_RETRIES}", flush=True)
            if attempt < MAX_RETRIES:
                time.sleep(2 ** attempt)
        except requests.ConnectionError as exc:
            return {"error": f"Connection error: {exc}"}

    return {"error": f"All {MAX_RETRIES} attempts failed"}


# ── Alias helpers ──────────────────────────────────────────────────────────────

def _alias(prefix: str, uuid: str) -> str:
    """Build a valid GraphQL alias from a prefix and UUID (hyphens → underscores)."""
    return f"{prefix}_{uuid.replace('-', '_')}"


# ── Data fetch pipeline ────────────────────────────────────────────────────────

def fetch_all() -> None:
    global _fetch_in_progress
    with _fetch_lock:
        if _fetch_in_progress:
            print("  Fetch already in progress — skipping", flush=True)
            return
        _fetch_in_progress = True
    try:
        _do_fetch()
    finally:
        with _fetch_lock:
            _fetch_in_progress = False


def _do_fetch() -> None:
    global _last_updated

    new: dict = {
        "networks":         [],   # [{UUID, label, slug}]
        "uplinkQuality":    {},   # networkUUID → {metadata, values[]}
        "networkClients":   {},   # networkUUID → [client, …]
        "uplinkPhyIfaces":  {},   # networkUUID → [iface, …]
        "uplinkThroughput": {},   # networkUUID → {metadata, values[]}
        "eventLog":         {},   # networkUUID → {total, events[]}
        "switches":         {},   # networkUUID → [{UUID, label, networkUUID, networkLabel, ports[]}]
        "virtualDevices":   {},   # networkUUID → [device, …]
        "switchClientMap":  {},   # networkUUID → {macAddress → {switchLabel, portNumber}}
        "fetchErrors":      [],
    }

    # ── Step 1: Discover all networks ─────────────────────────────────────────
    print("  [1/5] Fetching networks…", flush=True)
    r = gql(f"""
    {{
      networksForCompany(companySlug: "{COMPANY_SLUG}") {{
        UUID label slug
      }}
    }}
    """)
    if "error" in r:
        new["fetchErrors"].append(f"networks: {r['error']}")
        _commit(new)
        return

    networks = r.get("data", {}).get("networksForCompany", [])
    new["networks"] = networks
    print(f"  Found {len(networks)} network(s): {[n['label'] for n in networks]}", flush=True)

    if not networks:
        _commit(new)
        return

    uuids = [n["UUID"] for n in networks]

    # ── Step 2: Per-network bundle (clients + ifaces + events + devices) ──────
    print(f"  [2/6] Per-network bundles ({len(networks)} request(s))…", flush=True)
    for net in networks:
        nid   = net["UUID"]
        label = net.get("label", nid)
        print(f"    → '{label}'…", flush=True)

        r = gql(f"""
        {{
          clients: networkClients(networkUUID: "{nid}") {{
            macAddress ip clientName isWireless signal lastSeen
            connectedVLAN {{ name vlanID }}
            connectedSSID {{ ssid }}
            accessPoint {{ UUID label }}
          }}
          phyIfaces: uplinkPhyInterfacesForNetwork(networkUUID: "{nid}") {{
            UUID label portNumber isEnabled isUplink isUplinkActive portSpeedMbps
            virtualDeviceUUID
            nativeVLAN {{ name vlanID }}
          }}
          events: recentEventLogEventsPage(networkUUID: "{nid}", limit: 50) {{
            total
            events {{ eventType eventTypeAPIName generatedAt networkUUID }}
          }}
          devices: virtualDevicesForNetwork(networkUUID: "{nid}") {{
            UUID label deviceType deviceModel isOnline
          }}
        }}
        """)

        if "error" in r:
            new["fetchErrors"].append(f"{label}: {r['error']}")
            continue

        d = r.get("data", {})
        new["networkClients"][nid]  = d.get("clients",  [])
        new["uplinkPhyIfaces"][nid] = d.get("phyIfaces", [])
        new["eventLog"][nid]        = d.get("events",   {"total": 0, "events": []})
        new["virtualDevices"][nid]  = d.get("devices",  [])

        switches = [dv for dv in d.get("devices", []) if dv.get("deviceType") == "SWITCH"]
        new["switches"][nid] = [
            {"UUID": sw["UUID"], "label": sw.get("label", sw["UUID"]),
             "networkUUID": nid, "networkLabel": label, "ports": []}
            for sw in switches
        ]

    # ── Step 3: Uplink quality — all networks in one request ──────────────────
    print("  [3/6] Fetching uplink qualities…", flush=True)
    uuids_gql = ", ".join(f'"{u}"' for u in uuids)
    r = gql(f"""
    {{
      networksUplinkQualities(
        networkUUIDs: [{uuids_gql}],
        filter: {{ durationSeconds: 14400, stepSeconds: 300 }}
      ) {{
        metadata {{ minValue maxValue }}
        values {{ timestamp value phyInterfaceUUID networkUUID }}
      }}
    }}
    """)
    if "error" not in r:
        for entry in r.get("data", {}).get("networksUplinkQualities", []):
            meta = entry.get("metadata", {})
            for val in entry.get("values", []):
                nid = val.get("networkUUID")
                if not nid:
                    continue
                if nid not in new["uplinkQuality"]:
                    new["uplinkQuality"][nid] = {"metadata": meta, "values": []}
                new["uplinkQuality"][nid]["values"].append(val)
    else:
        new["fetchErrors"].append(f"uplinkQuality: {r['error']}")

    # ── Step 4: Throughput — all networks bundled via aliases ─────────────────
    print("  [4/6] Fetching throughput (bundled)…", flush=True)
    parts = [
        f"""  {_alias('tput', u)}: networkUplinkThroughput(
    networkUUID: "{u}",
    filter: {{ durationSeconds: 14400, stepSeconds: 300 }}
  ) {{
    metadata {{ minValue maxValue }}
    values {{ timestamp value direction phyInterfaceUUID }}
  }}"""
        for u in uuids
    ]
    r = gql("{\n" + "\n".join(parts) + "\n}")
    if "error" not in r:
        d = r.get("data", {})
        for u in uuids:
            a = _alias("tput", u)
            if a in d and d[a]:
                new["uplinkThroughput"][u] = d[a]
    else:
        new["fetchErrors"].append(f"throughput: {r['error']}")

    # ── Step 5: Switch port stats — all switches bundled (batched) ────────────
    all_switches = [
        (nid, sw)
        for nid, sws in new["switches"].items()
        for sw in sws
    ]
    if all_switches:
        print(f"  [5/6] Fetching switch port stats ({len(all_switches)} switch(es))…", flush=True)
        for i in range(0, len(all_switches), SWITCH_BATCH_SIZE):
            batch = all_switches[i : i + SWITCH_BATCH_SIZE]
            parts = [
                f"""  {_alias('sw', sw['UUID'])}: switchPortStats(virtualDeviceUUID: "{sw['UUID']}") {{
    portNumber totalRxBytes totalTxBytes totalRxPackets totalTxPackets
    errorRxPackets errorTxPackets
  }}"""
                for _, sw in batch
            ]
            r = gql("{\n" + "\n".join(parts) + "\n}")
            if "error" not in r:
                d = r.get("data", {})
                for nid, sw in batch:
                    a = _alias("sw", sw["UUID"])
                    if a in d and d[a]:
                        sw["ports"] = d[a]
            else:
                new["fetchErrors"].append(f"switchPorts: {r['error']}")
    else:
        print("  [5/6] No switches found — skipping port stats", flush=True)

    # ── Step 6: Switch connected devices — MAC → switch/port lookup map ────────
    if all_switches:
        print(f"  [6/6] Fetching switch connected devices ({len(all_switches)} switch(es))…", flush=True)
        for nid in new["switches"]:
            new["switchClientMap"][nid] = {}
        for i in range(0, len(all_switches), SWITCH_BATCH_SIZE):
            batch = all_switches[i : i + SWITCH_BATCH_SIZE]
            parts = [
                f"""  {_alias('scd', sw['UUID'])}: phyInterfacesForVirtualDevice(virtualDeviceUUID: "{sw['UUID']}") {{
    connectedDevices {{ client {{ macAddress }} portNumber }}
  }}"""
                for _, sw in batch
            ]
            r = gql("{\n" + "\n".join(parts) + "\n}")
            if "error" not in r:
                d = r.get("data", {})
                for nid, sw in batch:
                    a = _alias("scd", sw["UUID"])
                    for iface in (d.get(a) or []):
                        for cd in (iface.get("connectedDevices") or []):
                            client = cd.get("client")
                            if client and client.get("macAddress"):
                                mac = client["macAddress"]
                                new["switchClientMap"][nid][mac] = {
                                    "switchLabel": sw.get("label", sw["UUID"]),
                                    "portNumber":  cd.get("portNumber"),
                                }
            else:
                new["fetchErrors"].append(f"switchClientMap: {r['error']}")
    else:
        print("  [6/6] No switches — skipping connected devices", flush=True)

    _commit(new)


def _commit(data: dict) -> None:
    global _last_updated
    with _data_lock:
        _cache.update(data)
        _last_updated = datetime.now(timezone.utc).isoformat()
    errs = data.get("fetchErrors", [])
    if errs:
        print(f"  Done with {len(errs)} error(s): {errs}", flush=True)
    else:
        print(f"  Done — {_last_updated}", flush=True)


def background_loop() -> None:
    while True:
        print(f"[{datetime.now().strftime('%H:%M:%S')}] Polling Meter API…", flush=True)
        try:
            fetch_all()
        except Exception as exc:
            print(f"  UNHANDLED ERROR: {exc}", flush=True)
        time.sleep(REFRESH_INTERVAL)


# ── Flask routes ───────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template_string(HTML_TEMPLATE)


@app.route("/api/data")
def api_data():
    with _data_lock:
        return jsonify({"data": dict(_cache), "last_updated": _last_updated})


@app.route("/api/refresh", methods=["POST"])
def api_refresh():
    """Trigger an immediate non-blocking re-fetch in a background thread."""
    t = threading.Thread(target=fetch_all, daemon=True)
    t.start()
    return jsonify({"status": "refresh started"})


# ── HTML template ──────────────────────────────────────────────────────────────

HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Meter — Network Dashboard</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600&family=Roboto+Mono:wght@400;500&display=swap">
<style>
:root {
  --bg:        #282a3d;
  --bg1:       #1e202e;
  --bg2:       #343647;
  --bg3:       #4e5161;
  --border:    #4e5161;
  --border2:   #343647;
  --text:      #e4e6f0;
  --muted:     #9799ad;
  --faint:     #66687a;
  --brand:     #5461c8;
  --brand-bg:  rgba(84,97,200,.15);
  --brand-dim: rgba(84,97,200,.08);
  --green:     #22c55e;
  --green-bg:  rgba(34,197,94,.12);
  --red:       #f45757;
  --red-bg:    rgba(244,87,87,.12);
  --yellow:    #f59e0b;
  --yellow-bg: rgba(245,158,11,.12);
  --radius:    6px;
  --sidebar-w: 220px;
  --header-h:  52px;
}
*{box-sizing:border-box;margin:0;padding:0;}
body{
  background:var(--bg);color:var(--text);
  font-family:'Inter',-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
  font-size:13px;line-height:1.5;height:100vh;overflow:hidden;
}

/* ── Header ── */
header{
  position:fixed;top:0;left:0;right:0;z-index:200;
  height:var(--header-h);padding:0 24px;
  display:flex;align-items:center;justify-content:space-between;
  background:rgba(30,32,46,.97);backdrop-filter:blur(12px);
  border-bottom:1px solid var(--border);
}
.logo{font-size:16px;font-weight:600;letter-spacing:-.3px;color:#fff;}
.logo-dot{color:var(--brand);}
.header-right{display:flex;align-items:center;gap:12px;color:var(--muted);font-size:12px;}
.pulse-wrap{
  display:flex;align-items:center;gap:7px;padding:4px 10px;
  background:var(--bg1);border:1px solid var(--border);border-radius:6px;
}
.pulse{width:6px;height:6px;border-radius:50%;background:var(--green);animation:pulse 2.2s ease-in-out infinite;}
@keyframes pulse{0%,100%{opacity:1;}50%{opacity:.2;}}

/* ── Layout ── */
.layout{
  display:flex;
  margin-top:var(--header-h);
  height:calc(100vh - var(--header-h));
}

/* ── Sidebar ── */
.sidebar{
  width:var(--sidebar-w);min-width:var(--sidebar-w);
  background:var(--bg1);border-right:1px solid var(--border);
  display:flex;flex-direction:column;overflow-y:auto;flex-shrink:0;
}
.sidebar-group{padding:16px 0 6px;}
.sidebar-group-label{
  font-size:10px;font-weight:600;text-transform:uppercase;letter-spacing:.7px;
  color:var(--faint);padding:0 18px 6px;
}
.nav-item{
  display:flex;align-items:center;gap:10px;
  padding:9px 18px;cursor:pointer;color:var(--muted);font-size:13px;
  border-left:3px solid transparent;transition:color .14s,background .14s;
  user-select:none;
}
.nav-item:hover{background:var(--bg2);color:var(--text);}
.nav-item.active{
  background:var(--brand-dim);color:#fff;
  border-left-color:var(--brand);font-weight:500;
}
.nav-icon{width:15px;text-align:center;font-size:11px;flex-shrink:0;opacity:.7;}

/* ── Content area ── */
.content-area{flex:1;overflow-y:auto;background:var(--bg);}
.section{display:none;}
.section.active{display:block;}
.sec-inner{padding:24px 28px;}

/* ── Section header ── */
.sec-hdr{margin-bottom:14px;}
.sec-hdr h2{font-size:15px;font-weight:600;color:#fff;letter-spacing:-.1px;}
.sec-hdr p{font-size:12px;color:var(--muted);margin-top:3px;}

/* ── Toolbar ── */
.toolbar{
  display:flex;align-items:center;gap:8px;
  margin-bottom:18px;flex-wrap:wrap;
}
.toolbar select,
.toolbar input[type=text]{
  background:var(--bg1);border:1px solid var(--border);border-radius:var(--radius);
  color:var(--text);padding:6px 10px;font-size:12px;font-family:inherit;
  outline:none;transition:border-color .14s;
}
.toolbar select:focus,
.toolbar input[type=text]:focus{border-color:var(--brand);}
.toolbar input[type=text]{flex:1;min-width:160px;}
.toolbar select{min-width:160px;}
.btn-refresh{
  display:flex;align-items:center;gap:5px;
  background:var(--bg1);border:1px solid var(--border);border-radius:var(--radius);
  color:var(--muted);padding:6px 12px;cursor:pointer;
  font-size:12px;font-family:inherit;white-space:nowrap;
  transition:color .14s,background .14s;
}
.btn-refresh:hover{background:var(--bg2);color:var(--text);}
.btn-refresh.spinning .ri{display:inline-block;animation:spin .7s linear infinite;}

/* ── Stats row ── */
.stats{display:flex;gap:10px;margin-bottom:18px;flex-wrap:wrap;}
.stat{
  flex:1;min-width:110px;
  background:var(--bg1);border:1px solid var(--border);border-radius:var(--radius);
  padding:12px 16px;
}
.stat-lbl{font-size:10px;text-transform:uppercase;letter-spacing:.6px;color:var(--muted);}
.stat-val{font-size:22px;font-weight:600;color:#fff;margin-top:3px;line-height:1.1;}
.stat-sub{font-size:11px;color:var(--muted);margin-top:2px;}

/* ── Table ── */
.tbl-wrap{border:1px solid var(--border);border-radius:var(--radius);overflow:hidden;margin-bottom:24px;}
table{width:100%;border-collapse:collapse;}
thead tr{background:var(--bg1);}
th{
  padding:9px 14px;font-size:10px;font-weight:500;
  text-transform:uppercase;letter-spacing:.55px;
  color:var(--faint);text-align:left;
  border-bottom:1px solid var(--border);white-space:nowrap;
}
td{
  padding:9px 14px;border-bottom:1px solid var(--border2);
  vertical-align:middle;background:var(--bg2);
}
tr:last-child td{border-bottom:none;}
tbody tr{transition:background .1s;}
tbody tr:hover td{background:var(--bg3);}


/* ── Badges ── */
.badge{
  display:inline-flex;align-items:center;gap:4px;
  padding:2px 7px;border-radius:4px;
  font-size:11px;font-weight:500;white-space:nowrap;
}
.badge.green{background:var(--green-bg);color:var(--green);}
.badge.red{background:var(--red-bg);color:var(--red);}
.badge.yellow{background:var(--yellow-bg);color:var(--yellow);}
.badge.blue{background:var(--brand-bg);color:#9ca8e8;}
.badge.dim{background:var(--bg1);color:var(--muted);border:1px solid var(--border);}

/* ── Status dot ── */
.dot{display:inline-block;width:7px;height:7px;border-radius:50%;flex-shrink:0;}
.dot.green{background:var(--green);box-shadow:0 0 5px rgba(34,197,94,.5);}
.dot.red{background:var(--red);}
.dot.yellow{background:var(--yellow);}
.dot.gray{background:var(--faint);}

/* ── Mono ── */
.mono{font-family:'Roboto Mono','SF Mono',ui-monospace,monospace;font-size:11.5px;}

/* ── Empty / Loading ── */
.empty,.loading{padding:52px;text-align:center;color:var(--muted);}
.empty .title,.loading .title{font-size:14px;font-weight:500;color:var(--faint);margin-bottom:6px;}
.spinner{
  width:18px;height:18px;
  border:2px solid var(--border);border-top-color:var(--brand);
  border-radius:50%;animation:spin .7s linear infinite;
  margin:0 auto 12px;
}
@keyframes spin{to{transform:rotate(360deg);}}

/* ── Signal bars ── */
.sig{display:inline-flex;align-items:flex-end;gap:2px;height:14px;}
.sig-bar{width:3px;border-radius:1px;}

/* ── Throughput bar ── */
.tbar-wrap{display:flex;align-items:center;gap:8px;min-width:130px;}
.tbar-bg{flex:1;height:3px;background:var(--border2);border-radius:2px;overflow:hidden;}
.tbar-fill{height:100%;border-radius:2px;background:var(--green);transition:width .5s ease;}
.tbar-fill.up{background:#9ca8e8;}

/* ── Error banner ── */
.err-banner{
  background:var(--red-bg);border:1px solid rgba(244,87,87,.3);border-radius:var(--radius);
  padding:10px 14px;margin-bottom:16px;font-size:12px;color:var(--red);
}

/* ── Sortable th ── */
.th-sort{cursor:pointer;user-select:none;}
.th-sort:hover{color:var(--text);}
.th-inner{display:flex;align-items:center;gap:4px;white-space:nowrap;}
.sort-ico{font-size:9px;color:var(--brand);}
.sort-ico.dim{color:var(--faint);}
.cf-btn{
  margin-left:auto;font-size:11px;color:var(--faint);
  padding:1px 4px;border-radius:3px;cursor:pointer;
  transition:color .12s,background .12s;line-height:1.2;
}
.cf-btn:hover{color:var(--text);background:var(--bg2);}
.cf-btn.cf-active{color:var(--brand);}

/* ── Column filter popover ── */
#col-filter-pop{
  display:none;position:fixed;z-index:600;
  width:230px;background:var(--bg1);border:1px solid var(--border);
  border-radius:var(--radius);box-shadow:0 8px 24px rgba(0,0,0,.45);
  font-size:12px;
}
.cfp-hdr{
  display:flex;justify-content:space-between;align-items:center;
  padding:8px 12px;border-bottom:1px solid var(--border2);
  font-weight:600;color:var(--text);
}
.cfp-x{background:none;border:none;color:var(--muted);cursor:pointer;font-size:13px;padding:0;}
.cfp-x:hover{color:var(--text);}
.cfp-mode{
  display:flex;gap:14px;padding:7px 12px;
  border-bottom:1px solid var(--border2);color:var(--muted);
}
.cfp-mode label{cursor:pointer;display:flex;align-items:center;gap:5px;}
.cfp-search{padding:6px 8px;border-bottom:1px solid var(--border2);}
.cfp-search input{
  width:100%;background:var(--bg2);border:1px solid var(--border);border-radius:4px;
  color:var(--text);padding:4px 8px;font-size:11px;font-family:inherit;outline:none;
}
.cfp-list{max-height:180px;overflow-y:auto;padding:4px 0;}
.cfv-row{
  display:flex;align-items:center;gap:8px;
  padding:5px 12px;cursor:pointer;color:var(--muted);transition:background .1s;
}
.cfv-row:hover{background:var(--bg2);color:var(--text);}
.cfv-row input[type=checkbox]{cursor:pointer;accent-color:var(--brand);}
.cfp-ftr{
  display:flex;justify-content:flex-end;gap:6px;
  padding:7px 12px;border-top:1px solid var(--border2);
}
.cfp-btn{
  background:var(--bg2);border:1px solid var(--border);border-radius:4px;
  color:var(--muted);padding:3px 10px;font-size:11px;cursor:pointer;
  font-family:inherit;transition:color .12s,background .12s;
}
.cfp-btn:hover{color:var(--text);background:var(--bg3);}
.cfp-done{background:var(--brand-bg);color:#9ca8e8;border-color:var(--brand);}
.cfp-done:hover{background:var(--brand);color:#fff;}

/* ── Active filter chips ── */
.cf-chips{display:flex;flex-wrap:wrap;gap:6px;margin-bottom:12px;}
.cf-chip{
  display:inline-flex;align-items:center;gap:4px;
  background:var(--brand-bg);border:1px solid rgba(84,97,200,.3);border-radius:20px;
  padding:3px 8px;font-size:11px;color:#9ca8e8;
}
.cf-chip-col{font-weight:600;}
.cf-chip-mode{color:var(--muted);}
.cf-chip-val{max-width:110px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}
.cf-chip-x{
  background:none;border:none;color:var(--muted);cursor:pointer;
  font-size:10px;padding:0;margin-left:2px;line-height:1;
}
.cf-chip-x:hover{color:var(--text);}
</style>
</head>
<body>

<header>
  <div class="logo">meter<span class="logo-dot">.</span></div>
  <div class="header-right">
    <span id="net-count" style="color:var(--faint);font-size:11px;"></span>
    <div class="pulse-wrap">
      <div class="pulse"></div>
      <span id="status-text">Loading…</span>
    </div>
  </div>
</header>

<div class="layout">

  <!-- ── Sidebar ── -->
  <aside class="sidebar">
    <div class="sidebar-group">
      <div class="sidebar-group-label">Metrics</div>
      <div class="nav-item active" data-tab="uplink-quality">
        <span class="nav-icon">▲</span>Uplink Quality
      </div>
      <div class="nav-item" data-tab="throughput">
        <span class="nav-icon">⇅</span>Throughput
      </div>
    </div>
    <div class="sidebar-group">
      <div class="sidebar-group-label">Network</div>
      <div class="nav-item" data-tab="clients">
        <span class="nav-icon">◎</span>Network Clients
      </div>
      <div class="nav-item" data-tab="phy-ifaces">
        <span class="nav-icon">⬡</span>Physical Interfaces
      </div>
    </div>
    <div class="sidebar-group">
      <div class="sidebar-group-label">Infrastructure</div>
      <div class="nav-item" data-tab="switch-ports">
        <span class="nav-icon">▦</span>Switch Ports
      </div>
      <div class="nav-item" data-tab="events">
        <span class="nav-icon">≡</span>Event Log
      </div>
    </div>
  </aside>

  <!-- ── Content area ── -->
  <div class="content-area">

    <div id="uplink-quality" class="section active">
      <div class="sec-inner">
        <div class="sec-hdr">
          <h2>Uplink Quality</h2>
          <p>WAN quality score per uplink interface — last 4 hours, 5-minute buckets</p>
        </div>
        <div id="uplink-quality-toolbar"></div>
        <div id="uplink-quality-body"><div class="loading"><div class="spinner"></div><div class="title">Fetching data…</div></div></div>
      </div>
    </div>

    <div id="throughput" class="section">
      <div class="sec-inner">
        <div class="sec-hdr">
          <h2>Uplink Throughput</h2>
          <p>WAN bandwidth per interface — last 4 hours, 5-minute buckets</p>
        </div>
        <div id="throughput-toolbar"></div>
        <div id="throughput-body"><div class="loading"><div class="spinner"></div><div class="title">Fetching data…</div></div></div>
      </div>
    </div>

    <div id="clients" class="section">
      <div class="sec-inner">
        <div class="sec-hdr">
          <h2>Network Clients</h2>
          <p>Active clients with connection details, VLAN assignment, and signal strength</p>
        </div>
        <div id="clients-toolbar"></div>
        <div id="clients-body"><div class="loading"><div class="spinner"></div><div class="title">Fetching data…</div></div></div>
      </div>
    </div>

    <div id="phy-ifaces" class="section">
      <div class="sec-inner">
        <div class="sec-hdr">
          <h2>Physical Interfaces</h2>
          <p>WAN uplink port configuration and link status</p>
        </div>
        <div id="phy-ifaces-toolbar"></div>
        <div id="phy-ifaces-body"><div class="loading"><div class="spinner"></div><div class="title">Fetching data…</div></div></div>
      </div>
    </div>

    <div id="switch-ports" class="section">
      <div class="sec-inner">
        <div class="sec-hdr">
          <h2>Switch Ports</h2>
          <p>Cumulative traffic and error counters — ordered by network › switch › port</p>
        </div>
        <div id="switch-ports-toolbar"></div>
        <div id="switch-ports-body"><div class="loading"><div class="spinner"></div><div class="title">Fetching data…</div></div></div>
      </div>
    </div>

    <div id="events" class="section">
      <div class="sec-inner">
        <div class="sec-hdr">
          <h2>Event Log</h2>
          <p>Most recent 50 events per network, sorted newest first</p>
        </div>
        <div id="events-toolbar"></div>
        <div id="events-body"><div class="loading"><div class="spinner"></div><div class="title">Fetching data…</div></div></div>
      </div>
    </div>

  </div><!-- .content-area -->
</div><!-- .layout -->

<!-- ── Column filter popover (shared, positioned dynamically) ── -->
<div id="col-filter-pop">
  <div class="cfp-hdr">
    <span id="cfp-title"></span>
    <button class="cfp-x" onclick="closeColFilter()">✕</button>
  </div>
  <div class="cfp-mode">
    <label><input type="radio" name="cfpmode" value="include" onchange="setCFMode(this.value)"> Include</label>
    <label><input type="radio" name="cfpmode" value="exclude" onchange="setCFMode(this.value)"> Exclude</label>
  </div>
  <div class="cfp-search"><input type="text" placeholder="Filter values…" oninput="cfpSearch(this.value)"></div>
  <div class="cfp-list" id="cfp-list"></div>
  <div class="cfp-ftr">
    <button class="cfp-btn" onclick="clearCF()">Clear</button>
    <button class="cfp-btn cfp-done" onclick="closeColFilter()">Done</button>
  </div>
</div>

<script>
// ── State ─────────────────────────────────────────────────────────────────────
let appData       = {};
let networks      = [];
let activeTab     = 'uplink-quality';
let lastUpdated   = null;
let toolbarsReady = false;

const TABS = ['uplink-quality','throughput','clients','phy-ifaces','switch-ports','events'];
const filterState = {};
TABS.forEach(t => { filterState[t] = { network: 'all', search: '', sortCol: null, sortDir: 'asc', colFilters: {} }; });

// unique column values populated during each render, keyed by tabId+'_'+col
const _colValues = {};

// ── Utilities ─────────────────────────────────────────────────────────────────
function esc(s) {
  if (s == null) return '';
  return String(s)
    .replace(/&/g,'&amp;').replace(/</g,'&lt;')
    .replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}
function fmtBytes(b) {
  if (!b) return '0 B';
  const u = ['B','KB','MB','GB','TB'];
  const i = Math.min(Math.floor(Math.log(Math.abs(b)) / Math.log(1024)), u.length - 1);
  return (b / Math.pow(1024, i)).toFixed(i ? 1 : 0) + '\u202f' + u[i];
}
function fmtBps(v) {
  if (!v) return '0 bps';
  const u = ['bps','Kbps','Mbps','Gbps'];
  const i = Math.min(Math.floor(Math.log(Math.abs(v)) / Math.log(1000)), u.length - 1);
  return (v / Math.pow(1000, i)).toFixed(i ? 1 : 0) + '\u202f' + u[i];
}
function timeAgo(ts) {
  if (!ts) return '—';
  const s = Math.floor((Date.now() - new Date(ts)) / 1000);
  if (s < 60)    return s + 's ago';
  if (s < 3600)  return Math.floor(s / 60) + 'm ago';
  if (s < 86400) return Math.floor(s / 3600) + 'h ago';
  return Math.floor(s / 86400) + 'd ago';
}
function shortId(uuid) { return uuid ? uuid.slice(-8) : '—'; }
function badge(label, cls) { return `<span class="badge ${cls}">${esc(label)}</span>`; }
function dot(cls) { return `<span class="dot ${cls}"></span>`; }
function empty(msg, sub) {
  return `<div class="empty"><div class="title">${esc(msg)}</div>${sub ? `<div>${esc(sub)}</div>` : ''}</div>`;
}
function netLabel(uuid) {
  const n = networks.find(x => x.UUID === uuid);
  return n ? n.label : (uuid ? '…' + uuid.slice(-8) : '—');
}
function hits(cells, q) {
  if (!q) return true;
  const ql = q.toLowerCase();
  return cells.some(c => c != null && String(c).toLowerCase().includes(ql));
}

// ── Sort helpers ──────────────────────────────────────────────────────────────
function sortClick(tabId, col) {
  const s = filterState[tabId];
  if (s.sortCol === col) s.sortDir = s.sortDir === 'asc' ? 'desc' : 'asc';
  else { s.sortCol = col; s.sortDir = 'asc'; }
  renderTab(tabId);
}
function sortIcon(tabId, col) {
  const s = filterState[tabId];
  if (s.sortCol !== col) return '<span class="sort-ico dim">⇅</span>';
  return `<span class="sort-ico">${s.sortDir === 'asc' ? '↑' : '↓'}</span>`;
}
function applySort(rows, tabId, getVal) {
  const { sortCol, sortDir } = filterState[tabId];
  if (!sortCol) return rows;
  return rows.slice().sort((a, b) => {
    const va = getVal(a, sortCol), vb = getVal(b, sortCol);
    if (va == null && vb == null) return 0;
    if (va == null) return sortDir === 'asc' ? 1 : -1;
    if (vb == null) return sortDir === 'asc' ? -1 : 1;
    if (typeof va === 'number' && typeof vb === 'number')
      return sortDir === 'asc' ? va - vb : vb - va;
    return sortDir === 'asc'
      ? String(va).localeCompare(String(vb), undefined, { numeric: true })
      : String(vb).localeCompare(String(va), undefined, { numeric: true });
  });
}

// ── Column filter helpers ──────────────────────────────────────────────────────
function hasColFilter(tabId, col) {
  const cf = (filterState[tabId].colFilters || {})[col];
  return !!(cf && cf.values && cf.values.size > 0);
}
function colFilterMatch(tabId, col, val) {
  const cf = (filterState[tabId].colFilters || {})[col];
  if (!cf || !cf.values || !cf.values.size) return true;
  const has = cf.values.has(String(val ?? ''));
  return cf.mode === 'include' ? has : !has;
}
function applyColFilters(rows, tabId, getVal) {
  const cf = filterState[tabId].colFilters || {};
  const active = Object.keys(cf).filter(col => cf[col].values && cf[col].values.size > 0);
  if (!active.length) return rows;
  return rows.filter(row => active.every(col => colFilterMatch(tabId, col, getVal(row, col))));
}
function storeColVals(tabId, col, vals) {
  _colValues[tabId + '_' + col] =
    [...new Set(vals.filter(v => v != null && v !== '').map(String))].sort((a,b) =>
      a.localeCompare(b, undefined, { numeric: true }));
}

// ── Column filter popover ──────────────────────────────────────────────────────
let _cfTab = null, _cfCol = null;

function openColFilter(e, tabId, col) {
  e.stopPropagation();
  if (_cfTab === tabId && _cfCol === col) { closeColFilter(); return; }
  _cfTab = tabId; _cfCol = col;

  const cf = (filterState[tabId].colFilters || {})[col] || { mode: 'include', values: new Set() };
  const vals = _colValues[tabId + '_' + col] || [];

  document.getElementById('cfp-title').textContent = col;
  document.querySelectorAll('#col-filter-pop input[name=cfpmode]').forEach(r => {
    r.checked = r.value === (cf.mode || 'include');
  });
  document.querySelector('.cfp-search input').value = '';

  const list = document.getElementById('cfp-list');
  list.innerHTML = vals.length
    ? vals.map(v => {
        const sv = String(v);
        const checked = cf.values && cf.values.has(sv) ? 'checked' : '';
        return `<label class="cfv-row"><input type="checkbox" value="${esc(sv)}" ${checked}
          onchange="toggleCFV(this.value,this.checked)"><span>${esc(sv) || '<em style="color:var(--faint)">empty</em>'}</span></label>`;
      }).join('')
    : '<div style="padding:10px 12px;color:var(--faint)">No values</div>';

  const th = e.target.closest('th');
  const rect = th ? th.getBoundingClientRect() : e.target.getBoundingClientRect();
  const pop = document.getElementById('col-filter-pop');
  pop.style.top  = (rect.bottom + 2) + 'px';
  pop.style.left = Math.min(rect.left, window.innerWidth - 238) + 'px';
  pop.style.display = 'block';
}
function closeColFilter() {
  document.getElementById('col-filter-pop').style.display = 'none';
  _cfTab = null; _cfCol = null;
}
function toggleCFV(val, checked) {
  if (!_cfTab || !_cfCol) return;
  if (!filterState[_cfTab].colFilters[_cfCol])
    filterState[_cfTab].colFilters[_cfCol] = { mode: 'include', values: new Set() };
  const cf = filterState[_cfTab].colFilters[_cfCol];
  if (checked) cf.values.add(String(val)); else cf.values.delete(String(val));
  renderTab(_cfTab);
}
function setCFMode(mode) {
  if (!_cfTab || !_cfCol) return;
  if (!filterState[_cfTab].colFilters[_cfCol])
    filterState[_cfTab].colFilters[_cfCol] = { mode, values: new Set() };
  else filterState[_cfTab].colFilters[_cfCol].mode = mode;
  renderTab(_cfTab);
}
function clearCF() {
  if (!_cfTab || !_cfCol) return;
  delete filterState[_cfTab].colFilters[_cfCol];
  closeColFilter();
  renderTab(_cfTab);
}
function cfpSearch(q) {
  const ql = q.toLowerCase();
  document.querySelectorAll('#cfp-list .cfv-row').forEach(el => {
    el.style.display = el.querySelector('span').textContent.toLowerCase().includes(ql) ? '' : 'none';
  });
}
document.addEventListener('click', e => {
  const pop = document.getElementById('col-filter-pop');
  if (pop && pop.style.display !== 'none' && !pop.contains(e.target)) closeColFilter();
});

// ── th element builder ────────────────────────────────────────────────────────
function thEl(tabId, col, label) {
  const active = hasColFilter(tabId, col);
  return `<th class="th-sort" onclick="sortClick('${tabId}','${col}')">` +
    `<div class="th-inner"><span>${label}</span>${sortIcon(tabId, col)}` +
    `<span class="cf-btn${active ? ' cf-active' : ''}" ` +
    `onclick="event.stopPropagation();openColFilter(event,'${tabId}','${col}')" title="Filter">≡</span>` +
    `</div></th>`;
}

// ── Autocomplete helper ────────────────────────────────────────────────────────
function updateAC(tabId, cells) {
  const dl = document.getElementById(tabId + '-ac');
  if (!dl) return;
  const uniq = [...new Set(cells.filter(Boolean).map(String).map(s => s.trim()).filter(s => s.length > 1))]
    .sort().slice(0, 200);
  dl.innerHTML = uniq.map(v => `<option value="${esc(v)}">`).join('');
}

// ── Active column filter chips ────────────────────────────────────────────────
function renderColFilterChips(tabId) {
  const cf = filterState[tabId].colFilters || {};
  const active = Object.entries(cf).filter(([, v]) => v.values && v.values.size > 0);
  if (!active.length) return '';
  const chips = active.map(([col, v]) => {
    const vals = [...v.values].join(', ');
    const modeLabel = v.mode === 'exclude' ? '≠' : '=';
    return `<span class="cf-chip">` +
      `<span class="cf-chip-col">${esc(col)}</span> ` +
      `<span class="cf-chip-mode">${modeLabel}</span> ` +
      `<span class="cf-chip-val" title="${esc(vals)}">${esc(vals)}</span>` +
      `<button class="cf-chip-x" onclick="clearCFByKey('${tabId}','${col}')">✕</button>` +
      `</span>`;
  }).join('');
  return `<div class="cf-chips">${chips}</div>`;
}
function clearCFByKey(tabId, col) {
  delete filterState[tabId].colFilters[col];
  renderTab(tabId);
}
function signalBars(dbm) {
  if (dbm == null) return '—';
  const lvl = dbm > -55 ? 4 : dbm > -65 ? 3 : dbm > -75 ? 2 : 1;
  const col  = dbm > -65 ? 'var(--green)' : dbm > -75 ? 'var(--yellow)' : 'var(--red)';
  const bars = [4, 8, 11, 14].map((h, i) =>
    `<div class="sig-bar" style="height:${h}px;background:${i < lvl ? col : 'var(--border)'}"></div>`
  ).join('');
  return `<span class="sig">${bars}</span> <span style="font-size:11px;color:var(--muted)">${dbm} dBm</span>`;
}
function tBar(val, max, dir) {
  const pct = max > 0 ? Math.min((val / max) * 100, 100) : 0;
  return `<div class="tbar-wrap">
    <div class="tbar-bg"><div class="tbar-fill ${dir === 'upload' ? 'up' : ''}" style="width:${pct.toFixed(1)}%"></div></div>
    <span style="color:var(--text);min-width:68px;text-align:right">${fmtBps(val)}</span>
  </div>`;
}

// ── Sidebar navigation ──────���─────────────────────────────────────────────────
document.querySelectorAll('.nav-item[data-tab]').forEach(el => {
  el.addEventListener('click', () => {
    document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
    document.querySelectorAll('.section').forEach(s => s.classList.remove('active'));
    el.classList.add('active');
    activeTab = el.dataset.tab;
    document.getElementById(activeTab).classList.add('active');
    renderTab(activeTab);
  });
});

// ── Toolbar ───────────────────────────────────────────────────────────────────
function buildNetOptions(tabId) {
  const cur = filterState[tabId].network;
  return `<option value="all"${cur === 'all' ? ' selected' : ''}>All Networks</option>` +
    networks.map(n =>
      `<option value="${n.UUID}"${cur === n.UUID ? ' selected' : ''}>${esc(n.label)}</option>`
    ).join('');
}

function initToolbars() {
  if (!toolbarsReady) {
    TABS.forEach(t => {
      document.getElementById(t + '-toolbar').innerHTML = `
        <div class="toolbar">
          <select id="${t}-net-sel" onchange="filterNetwork('${t}',this.value)">
            ${buildNetOptions(t)}
          </select>
          <input id="${t}-search-inp" type="text" placeholder="Search…" list="${t}-ac"
                 value="${esc(filterState[t].search)}"
                 oninput="filterSearch('${t}',this.value)">
          <datalist id="${t}-ac"></datalist>
          <button class="btn-refresh" id="${t}-refresh-btn" onclick="triggerRefresh()">
            <span class="ri">↺</span> Refresh
          </button>
        </div>`;
    });
    toolbarsReady = true;
  } else {
    // Update select options without replacing the whole toolbar (keeps input focus)
    TABS.forEach(t => {
      const sel = document.getElementById(t + '-net-sel');
      if (sel) sel.innerHTML = buildNetOptions(t);
    });
  }
}

function filterNetwork(tabId, val) {
  filterState[tabId].network = val;
  renderTab(tabId);
}
function filterSearch(tabId, val) {
  filterState[tabId].search = val;
  renderTab(tabId);
}

// ── Refresh ───────────────────────────────────────────────────────────────────
async function triggerRefresh() {
  TABS.forEach(t => {
    const btn = document.getElementById(t + '-refresh-btn');
    if (btn) btn.classList.add('spinning');
  });
  document.getElementById('status-text').textContent = 'Refreshing…';

  try { await fetch('/api/refresh', { method: 'POST' }); } catch(e) {}

  const prev = lastUpdated;
  let attempts = 0;
  const timer = setInterval(async () => {
    attempts++;
    try {
      const res  = await fetch('/api/data');
      const json = await res.json();
      if (json.last_updated && json.last_updated !== prev) {
        clearInterval(timer);
        onData(json);
        TABS.forEach(t => {
          const btn = document.getElementById(t + '-refresh-btn');
          if (btn) btn.classList.remove('spinning');
        });
      }
    } catch(e) {}
    if (attempts >= 60) {
      clearInterval(timer);
      TABS.forEach(t => {
        const btn = document.getElementById(t + '-refresh-btn');
        if (btn) btn.classList.remove('spinning');
      });
      document.getElementById('status-text').textContent = 'Refresh timed out';
    }
  }, 2000);
}

// ── Data reception ────────────────────────────────────────────────────────────
function onData(json) {
  appData     = json.data || {};
  lastUpdated = json.last_updated;
  networks    = appData.networks || [];

  const t = json.last_updated ? new Date(json.last_updated).toLocaleTimeString() : '—';
  document.getElementById('status-text').textContent = 'Updated ' + t;
  document.getElementById('net-count').textContent =
    networks.length ? networks.length + ' network' + (networks.length > 1 ? 's' : '') : '';

  initToolbars();
  renderTab(activeTab);
}

// ── Render dispatcher ─────────────────────────────────────────────────────────
function renderTab(tabId) {
  const state = filterState[tabId];
  let html = '';

  const errs = appData.fetchErrors || [];
  if (errs.length) {
    html += `<div class="err-banner">⚠ ${errs.slice(0, 3).map(esc).join(' &nbsp;·&nbsp; ')}</div>`;
  }

  html += renderColFilterChips(tabId);

  switch (tabId) {
    case 'uplink-quality': html += renderUplinkQuality(state, tabId); break;
    case 'throughput':     html += renderThroughput(state, tabId);    break;
    case 'clients':        html += renderClients(state, tabId);       break;
    case 'phy-ifaces':     html += renderPhyIfaces(state, tabId);     break;
    case 'switch-ports':   html += renderSwitchPorts(state, tabId);   break;
    case 'events':         html += renderEvents(state, tabId);        break;
  }
  document.getElementById(tabId + '-body').innerHTML = html;
}

// ── Network filter helper ─────────────────────────────────────────────────────
function selectedNets(nf) {
  if (nf === 'all') return networks;
  const n = networks.find(x => x.UUID === nf);
  return n ? [n] : [];
}
function multiNet(nf) { return nf === 'all' && networks.length > 1; }

// Build per-network lookup maps:
//   devMap[nid][deviceUUID]  = label
//   ifaceMap[nid][ifaceUUID] = { label, virtualDeviceUUID }
function buildLookupMaps() {
  const devMap   = {};
  const ifaceMap = {};
  networks.forEach(net => {
    const nid = net.UUID;
    devMap[nid]   = {};
    ifaceMap[nid] = {};
    ((appData.virtualDevices || {})[nid] || []).forEach(d => {
      devMap[nid][d.UUID] = d.label || d.UUID;
    });
    ((appData.uplinkPhyIfaces || {})[nid] || []).forEach(i => {
      ifaceMap[nid][i.UUID] = { label: i.label, virtualDeviceUUID: i.virtualDeviceUUID };
    });
  });
  return { devMap, ifaceMap };
}

// Parse port range search: "1-4" → range, "3" → single, else text
function parsePortSearch(q) {
  if (!q) return null;
  const range = /^(\d+)-(\d+)$/.exec(q.trim());
  if (range) return { type: 'range', lo: parseInt(range[1]), hi: parseInt(range[2]) };
  const num = /^\d+$/.exec(q.trim());
  if (num) return { type: 'num', n: parseInt(num[0]) };
  return { type: 'text', q: q.toLowerCase() };
}
function portMatches(p, sw, net, parsed) {
  if (!parsed) return true;
  if (parsed.type === 'range') return p.portNumber >= parsed.lo && p.portNumber <= parsed.hi;
  if (parsed.type === 'num')   return p.portNumber === parsed.n;
  return [sw.label, net.label, String(p.portNumber)].some(s => s && s.toLowerCase().includes(parsed.q));
}

// ── Uplink Quality ────────────────────────────────────────────────────────────
function renderUplinkQuality({ network: nf, search: q }, tabId) {
  const nets              = selectedNets(nf);
  const showNt            = multiNet(nf);
  const { devMap, ifaceMap } = buildLookupMaps();
  const rows   = [];

  for (const net of nets) {
    const entry = (appData.uplinkQuality || {})[net.UUID];
    if (!entry) continue;
    const byIface = {};
    (entry.values || []).forEach(v => {
      const k = v.phyInterfaceUUID || 'unknown';
      if (!byIface[k]) byIface[k] = [];
      byIface[k].push(v);
    });
    for (const [ifaceUUID, pts] of Object.entries(byIface)) {
      const latest   = pts[pts.length - 1];
      const v        = latest?.value;
      const nums     = pts.map(p => p.value).filter(x => x != null);
      const avg      = nums.length ? nums.reduce((a,b) => a+b, 0) / nums.length : null;
      const ifInfo   = (ifaceMap[net.UUID] || {})[ifaceUUID] || {};
      const ifLabel  = ifInfo.label || ('…' + ifaceUUID.slice(-8));
      const devLabel = (devMap[net.UUID] || {})[ifInfo.virtualDeviceUUID] || '—';
      const ts       = pts[pts.length-1]?.timestamp ? new Date(pts[pts.length-1].timestamp).toLocaleTimeString() : '—';
      const qualLabel = v == null ? 'No Data' : v > .8 ? 'Good' : v > .5 ? 'Fair' : 'Poor';
      rows.push({ net, ifaceUUID, ifLabel, devLabel, pts, v, avg, ts, qualLabel });
    }
  }

  if (!rows.length) return empty('No uplink quality data', 'No data for the selected network(s).');

  // Store column values for filter dropdowns
  storeColVals(tabId, 'Device',   rows.map(r => r.devLabel));
  storeColVals(tabId, 'Interface', rows.map(r => r.ifLabel));
  if (showNt) storeColVals(tabId, 'Network', rows.map(r => r.net.label));
  storeColVals(tabId, 'Latest Quality', rows.map(r => r.qualLabel));
  storeColVals(tabId, 'Samples', rows.map(r => String(r.pts.length)));

  function getCV(row, col) {
    switch(col) {
      case 'Device':         return row.devLabel;
      case 'Interface':      return row.ifLabel;
      case 'Network':        return row.net.label;
      case 'Latest Quality': return row.qualLabel;
      case 'Average':        return row.avg;
      case 'Samples':        return row.pts.length;
      case 'Last Sample':    return row.ts;
      default: return null;
    }
  }

  let filtered = rows.filter(r => hits([r.ifLabel, r.devLabel, r.net.label,
    r.v != null ? (r.v * 100).toFixed(1) + '%' : '', r.qualLabel], q));
  filtered = applyColFilters(filtered, tabId, getCV);
  if (!filtered.length) return empty('No results', 'No rows match your search.');
  filtered = applySort(filtered, tabId, getCV);

  updateAC(tabId, filtered.flatMap(r => [r.ifLabel, r.devLabel, r.net.label, r.qualLabel]));

  const allNums = filtered.flatMap(r => r.pts.map(p => p.value)).filter(v => v != null);
  const gavg    = allNums.length ? allNums.reduce((a,b) => a+b, 0) / allNums.length : null;

  const stats = `<div class="stats">
    <div class="stat"><div class="stat-lbl">Interfaces</div><div class="stat-val">${filtered.length}</div></div>
    <div class="stat"><div class="stat-lbl">Data Points</div><div class="stat-val">${allNums.length}</div></div>
    <div class="stat"><div class="stat-lbl">Avg Quality</div><div class="stat-val">${gavg != null ? (gavg*100).toFixed(1)+'%' : '—'}</div></div>
  </div>`;

  const ntTh = showNt ? thEl(tabId, 'Network', 'Network') : '';
  const trs  = filtered.map(({ net, ifLabel, devLabel, pts, v, avg, ts, qualLabel }) => {
    const cls  = v == null ? 'dim' : v > .8 ? 'green' : v > .5 ? 'yellow' : 'red';
    const pct  = v != null ? (v*100).toFixed(1)+'%' : '—';
    const barW = v != null ? (v*100).toFixed(1) : 0;
    const col  = v == null ? 'var(--border)' : v > .8 ? 'var(--green)' : v > .5 ? 'var(--yellow)' : 'var(--red)';
    const ntTd = showNt ? `<td><span class="badge dim">${esc(net.label)}</span></td>` : '';
    return `<tr>
      <td style="font-weight:500">${esc(devLabel)}</td>
      <td><span style="color:var(--text)">${esc(ifLabel)}</span></td>
      ${ntTd}
      <td><div class="tbar-wrap"><div class="tbar-bg"><div class="tbar-fill" style="width:${barW}%;background:${col}"></div></div>
          <span class="badge ${cls}" style="min-width:52px;justify-content:center">${pct}</span></div></td>
      <td style="color:var(--muted)">${avg != null ? (avg*100).toFixed(1)+'%' : '—'}</td>
      <td style="color:var(--faint)">${pts.length}</td>
      <td style="color:var(--muted)">${ts}</td>
    </tr>`;
  }).join('');

  return stats + `<div class="tbl-wrap"><table>
    <thead><tr>${thEl(tabId,'Device','Device')}${thEl(tabId,'Interface','Interface')}${ntTh}${thEl(tabId,'Latest Quality','Latest Quality')}${thEl(tabId,'Average','Average')}${thEl(tabId,'Samples','Samples')}${thEl(tabId,'Last Sample','Last Sample')}</tr></thead>
    <tbody>${trs}</tbody></table></div>`;
}

// ── Throughput ────────────────────────────────────────────────────────────────
function renderThroughput({ network: nf, search: q }, tabId) {
  const nets              = selectedNets(nf);
  const showNt            = multiNet(nf);
  const { devMap, ifaceMap } = buildLookupMaps();
  const rows   = [];
  let globalMax = 1;

  for (const net of nets) {
    const entry = (appData.uplinkThroughput || {})[net.UUID];
    if (!entry) continue;
    if ((entry.metadata?.maxValue || 0) > globalMax) globalMax = entry.metadata.maxValue;
    const byKey = {};
    (entry.values || []).forEach(v => {
      const k = `${v.phyInterfaceUUID}||${v.direction}`;
      if (!byKey[k]) byKey[k] = { ifaceUUID: v.phyInterfaceUUID, dir: v.direction, pts: [] };
      byKey[k].pts.push(v);
    });
    Object.values(byKey).forEach(g => {
      const ifInfo   = (ifaceMap[net.UUID] || {})[g.ifaceUUID] || {};
      const ifLabel  = ifInfo.label || ('…' + (g.ifaceUUID || '').slice(-8));
      const devLabel = (devMap[net.UUID] || {})[ifInfo.virtualDeviceUUID] || '—';
      const nums     = g.pts.map(p => p.value).filter(v => v != null);
      const avg      = nums.length ? nums.reduce((a,b) => a+b, 0) / nums.length : 0;
      const peak     = nums.length ? Math.max(...nums) : 0;
      const latest   = g.pts[g.pts.length-1]?.value ?? 0;
      rows.push({ ...g, net, ifLabel, devLabel, avg, peak, latest });
    });
  }

  if (!rows.length) return empty('No throughput data', 'No data for the selected network(s).');

  storeColVals(tabId, 'Device',    rows.map(r => r.devLabel));
  storeColVals(tabId, 'Interface', rows.map(r => r.ifLabel));
  if (showNt) storeColVals(tabId, 'Network', rows.map(r => r.net.label));
  storeColVals(tabId, 'Direction', rows.map(r => r.dir === 'upload' ? 'Upload' : 'Download'));

  function getCV(row, col) {
    switch(col) {
      case 'Device':    return row.devLabel;
      case 'Interface': return row.ifLabel;
      case 'Network':   return row.net.label;
      case 'Direction': return row.dir === 'upload' ? 'Upload' : 'Download';
      case 'Latest':    return row.latest;
      case 'Average':   return row.avg;
      case 'Peak':      return row.peak;
      case 'Samples':   return row.pts.length;
      default: return null;
    }
  }

  let filtered = rows.filter(r => hits([r.ifLabel, r.devLabel, r.dir, r.net.label], q));
  filtered = applyColFilters(filtered, tabId, getCV);
  if (!filtered.length) return empty('No results', 'No rows match your search.');
  filtered = applySort(filtered, tabId, getCV);

  updateAC(tabId, filtered.flatMap(r => [r.ifLabel, r.devLabel, r.net.label,
    r.dir === 'upload' ? 'Upload' : 'Download']));

  const allPts  = filtered.flatMap(r => r.pts.map(p => p.value)).filter(v => v != null);
  const gPeak   = allPts.length ? Math.max(...allPts) : 0;

  const stats = `<div class="stats">
    <div class="stat"><div class="stat-lbl">Series</div><div class="stat-val">${filtered.length}</div></div>
    <div class="stat"><div class="stat-lbl">Peak</div><div class="stat-val">${fmtBps(gPeak)}</div></div>
    <div class="stat"><div class="stat-lbl">Data Points</div><div class="stat-val">${allPts.length}</div></div>
  </div>`;

  const ntTh = showNt ? thEl(tabId, 'Network', 'Network') : '';
  const trs  = filtered.map(r => {
    const ntTd = showNt ? `<td><span class="badge dim">${esc(r.net.label)}</span></td>` : '';
    return `<tr>
      <td style="font-weight:500">${esc(r.devLabel)}</td>
      <td><span style="color:var(--text)">${esc(r.ifLabel)}</span></td>
      ${ntTd}
      <td>${r.dir === 'upload' ? badge('↑ Upload','blue') : badge('↓ Download','green')}</td>
      <td>${tBar(r.latest, globalMax, r.dir)}</td>
      <td style="color:var(--muted)">${fmtBps(r.avg)}</td>
      <td style="color:var(--muted)">${fmtBps(r.peak)}</td>
      <td style="color:var(--faint)">${r.pts.length}</td>
    </tr>`;
  }).join('');

  return stats + `<div class="tbl-wrap"><table>
    <thead><tr>${thEl(tabId,'Device','Device')}${thEl(tabId,'Interface','Interface')}${ntTh}${thEl(tabId,'Direction','Direction')}${thEl(tabId,'Latest','Latest')}${thEl(tabId,'Average','Average')}${thEl(tabId,'Peak','Peak')}${thEl(tabId,'Samples','Samples')}</tr></thead>
    <tbody>${trs}</tbody></table></div>`;
}

// ── Network Clients ───────────────────────────────────────────────────────────
function renderClients({ network: nf, search: q }, tabId) {
  const nets   = selectedNets(nf);
  const showNt = multiNet(nf);
  const all    = [];

  for (const net of nets) {
    ((appData.networkClients || {})[net.UUID] || []).forEach(c => {
      const nid    = net.UUID;
      const swInfo = (!c.isWireless && c.macAddress)
                     ? ((appData.switchClientMap || {})[nid] || {})[c.macAddress]
                     : null;
      const deviceName = c.isWireless ? (c.accessPoint?.label || '') : (swInfo?.switchLabel || '');
      const typeLabel  = c.isWireless ? 'Wi-Fi' : 'Wired';
      const vlanName   = c.connectedVLAN?.name || '';
      all.push({ ...c, _net: net, _deviceName: deviceName, _typeLabel: typeLabel, _vlanName: vlanName });
    });
  }

  if (!all.length) return empty('No clients', 'No active clients on the selected network(s).');

  storeColVals(tabId, 'Name',    all.map(c => c.clientName).filter(Boolean));
  storeColVals(tabId, 'IP',      all.map(c => c.ip).filter(Boolean));
  storeColVals(tabId, 'Type',    all.map(c => c._typeLabel));
  storeColVals(tabId, 'VLAN',    all.map(c => c._vlanName).filter(Boolean));
  storeColVals(tabId, 'SSID',    all.map(c => c.connectedSSID?.ssid).filter(Boolean));
  storeColVals(tabId, 'Device',  all.map(c => c._deviceName).filter(Boolean));
  if (showNt) storeColVals(tabId, 'Network', all.map(c => c._net.label));

  function getCV(c, col) {
    switch(col) {
      case 'Name':     return c.clientName;
      case 'IP':       return c.ip;
      case 'MAC':      return c.macAddress;
      case 'Type':     return c._typeLabel;
      case 'Signal':   return c.signal;
      case 'VLAN':     return c._vlanName;
      case 'SSID':     return c.connectedSSID?.ssid;
      case 'Device':   return c._deviceName;
      case 'Network':  return c._net.label;
      case 'Last Seen': return c.lastSeen;
      default: return null;
    }
  }

  let filtered = all.filter(c => hits([c.clientName, c.ip, c.macAddress,
    c.connectedSSID?.ssid, c._vlanName, c._net.label, c._deviceName], q));
  filtered = applyColFilters(filtered, tabId, getCV);
  if (!filtered.length) return empty('No results', 'No rows match your search.');
  filtered = applySort(filtered, tabId, getCV);

  updateAC(tabId, filtered.flatMap(c => [c.clientName, c.ip, c._vlanName,
    c.connectedSSID?.ssid, c._deviceName, c._net.label, c._typeLabel].filter(Boolean)));

  const wireless = filtered.filter(c => c.isWireless).length;
  const stats = `<div class="stats">
    <div class="stat"><div class="stat-lbl">Total</div><div class="stat-val">${filtered.length}</div></div>
    <div class="stat"><div class="stat-lbl">Wireless</div><div class="stat-val">${wireless}</div></div>
    <div class="stat"><div class="stat-lbl">Wired</div><div class="stat-val">${filtered.length - wireless}</div></div>
  </div>`;

  const ntTh = showNt ? thEl(tabId, 'Network', 'Network') : '';
  const trs  = filtered.map(c => {
    const deviceName = c.isWireless
      ? (c.accessPoint?.label || '<span style="color:var(--faint)">—</span>')
      : (c._deviceName        || '<span style="color:var(--faint)">—</span>');
    const vlan = c.connectedVLAN
      ? `<span class="badge dim">${esc(c.connectedVLAN.name)} <span style="color:var(--faint)">${c.connectedVLAN.vlanID}</span></span>`
      : '<span style="color:var(--faint)">—</span>';
    const ntTd = showNt ? `<td><span class="badge dim">${esc(c._net.label)}</span></td>` : '';
    return `<tr>
      <td><span style="color:#fff;font-weight:500">${esc(c.clientName) || '<span style="color:var(--faint)">—</span>'}</span></td>
      <td><code class="mono">${esc(c.ip) || '—'}</code></td>
      <td><code class="mono" style="font-size:11px;color:var(--muted)">${esc(c.macAddress)}</code></td>
      <td>${c.isWireless ? badge('Wi-Fi','blue') : badge('Wired','dim')}</td>
      <td>${signalBars(c.signal)}</td>
      <td>${vlan}</td>
      <td style="color:var(--muted)">${esc(c.connectedSSID?.ssid) || '—'}</td>
      <td style="color:var(--text)">${deviceName}</td>
      ${ntTd}
      <td style="color:var(--muted);white-space:nowrap">${timeAgo(c.lastSeen)}</td>
    </tr>`;
  }).join('');

  return stats + `<div class="tbl-wrap"><table>
    <thead><tr>${thEl(tabId,'Name','Name')}${thEl(tabId,'IP','IP')}${thEl(tabId,'MAC','MAC')}${thEl(tabId,'Type','Type')}${thEl(tabId,'Signal','Signal')}${thEl(tabId,'VLAN','VLAN')}${thEl(tabId,'SSID','SSID')}${thEl(tabId,'Device','Device')}${ntTh}${thEl(tabId,'Last Seen','Last Seen')}</tr></thead>
    <tbody>${trs}</tbody></table></div>`;
}

// ── Physical Interfaces ───────────────────────────────────────────────────────
function renderPhyIfaces({ network: nf, search: q }, tabId) {
  const nets              = selectedNets(nf);
  const showNt            = multiNet(nf);
  const { devMap }        = buildLookupMaps();
  const all    = [];

  for (const net of nets) {
    ((appData.uplinkPhyIfaces || {})[net.UUID] || []).forEach(i => {
      const devLabel  = (devMap[net.UUID] || {})[i.virtualDeviceUUID] || '—';
      const statusLbl = i.isUplinkActive ? 'Active' : i.isEnabled ? 'Standby' : 'Disabled';
      const speedLbl  = i.portSpeedMbps ? i.portSpeedMbps + ' Mbps' : '';
      const vlanName  = i.nativeVLAN?.name || '';
      all.push({ ...i, _net: net, _devLabel: devLabel, _statusLbl: statusLbl, _speedLbl: speedLbl, _vlanName: vlanName });
    });
  }

  if (!all.length) return empty('No interfaces', 'No uplink interfaces for the selected network(s).');

  storeColVals(tabId, 'Device',      all.map(i => i._devLabel));
  storeColVals(tabId, 'Interface',   all.map(i => i.label).filter(Boolean));
  storeColVals(tabId, 'Status',      all.map(i => i._statusLbl));
  storeColVals(tabId, 'Enabled',     all.map(i => i.isEnabled ? 'Yes' : 'No'));
  storeColVals(tabId, 'Speed',       all.map(i => i._speedLbl).filter(Boolean));
  storeColVals(tabId, 'Native VLAN', all.map(i => i._vlanName).filter(Boolean));
  if (showNt) storeColVals(tabId, 'Network', all.map(i => i._net.label));

  function getCV(i, col) {
    switch(col) {
      case 'Device':      return i._devLabel;
      case 'Interface':   return i.label;
      case 'Port #':      return i.portNumber;
      case 'Status':      return i._statusLbl;
      case 'Enabled':     return i.isEnabled ? 'Yes' : 'No';
      case 'Speed':       return i.portSpeedMbps;
      case 'Native VLAN': return i._vlanName;
      case 'Network':     return i._net.label;
      case 'UUID':        return i.UUID;
      default: return null;
    }
  }

  let filtered = all.filter(i => hits([i.label, i.portNumber, i._net.label,
    i._vlanName, i._devLabel, i._statusLbl], q));
  filtered = applyColFilters(filtered, tabId, getCV);
  if (!filtered.length) return empty('No results', 'No rows match your search.');
  filtered = applySort(filtered, tabId, getCV);

  updateAC(tabId, filtered.flatMap(i => [i._devLabel, i.label, i._statusLbl,
    i._vlanName, i._net.label, i._speedLbl].filter(Boolean)));

  const active  = filtered.filter(i => i.isUplinkActive).length;
  const enabled = filtered.filter(i => i.isEnabled).length;
  const stats   = `<div class="stats">
    <div class="stat"><div class="stat-lbl">Interfaces</div><div class="stat-val">${filtered.length}</div></div>
    <div class="stat"><div class="stat-lbl">Active Uplinks</div><div class="stat-val" style="color:var(--green)">${active}</div></div>
    <div class="stat"><div class="stat-lbl">Enabled</div><div class="stat-val">${enabled}</div></div>
  </div>`;

  const ntTh = showNt ? thEl(tabId, 'Network', 'Network') : '';
  const trs  = filtered.map(i => {
    const stDot   = i.isUplinkActive ? dot('green') : i.isEnabled ? dot('yellow') : dot('gray');
    const stBadge = i.isUplinkActive ? badge('Active','green') : i.isEnabled ? badge('Standby','yellow') : badge('Disabled','dim');
    const vlan    = i.nativeVLAN
      ? `<span class="badge dim">${esc(i.nativeVLAN.name)} <span style="color:var(--faint)">${i.nativeVLAN.vlanID}</span></span>`
      : '—';
    const ntTd  = showNt ? `<td><span class="badge dim">${esc(i._net.label)}</span></td>` : '';
    return `<tr>
      <td style="font-weight:500">${esc(i._devLabel)}</td>
      <td><span style="color:var(--text)">${esc(i.label) || 'Port ' + i.portNumber}</span></td>
      <td style="color:var(--muted)">${i.portNumber}</td>
      <td><div style="display:flex;align-items:center;gap:7px">${stDot}${stBadge}</div></td>
      <td>${i.isEnabled ? dot('green') + ' Yes' : dot('gray') + ' No'}</td>
      <td style="color:var(--muted)">${i._speedLbl || '—'}</td>
      <td>${vlan}</td>
      ${ntTd}
      <td><code class="mono" style="color:var(--faint);font-size:10.5px">${esc(i.UUID)}</code></td>
    </tr>`;
  }).join('');

  return stats + `<div class="tbl-wrap"><table>
    <thead><tr>${thEl(tabId,'Device','Device')}${thEl(tabId,'Interface','Interface')}${thEl(tabId,'Port #','Port #')}${thEl(tabId,'Status','Status')}${thEl(tabId,'Enabled','Enabled')}${thEl(tabId,'Speed','Speed')}${thEl(tabId,'Native VLAN','Native VLAN')}${ntTh}${thEl(tabId,'UUID','UUID')}</tr></thead>
    <tbody>${trs}</tbody></table></div>`;
}

// ── Switch Ports ──────────────────────────────────────────────────────────────
function renderSwitchPorts({ network: nf, search: q }, tabId) {
  const nets    = selectedNets(nf);
  const showNt  = multiNet(nf);
  const parsed  = parsePortSearch(q);

  // Collect all port rows (before filtering)
  const allRows = [];
  for (const net of nets) {
    const switches = (appData.switches || {})[net.UUID] || [];
    for (const sw of switches) {
      const ports = sw.ports || [];
      if (!ports.length) continue;
      ports.forEach(p => allRows.push({ p, sw, net,
        _errLabel: ((p.errorRxPackets||0) + (p.errorTxPackets||0)) > 0 ? 'Yes' : 'No' }));
    }
  }

  if (!allRows.length) {
    const hasSwitches = nets.some(net => ((appData.switches || {})[net.UUID] || []).some(sw => (sw.ports || []).length));
    if (!hasSwitches) return empty('No switch data', 'No switches with port data found for the selected network(s).');
    return empty('No results', 'No ports match your search.');
  }

  storeColVals(tabId, 'Switch',  allRows.map(r => r.sw.label));
  if (showNt) storeColVals(tabId, 'Network', allRows.map(r => r.net.label));
  storeColVals(tabId, 'Errors',  allRows.map(r => r._errLabel));

  function getCV(row, col) {
    switch(col) {
      case 'Network':    return row.net.label;
      case 'Switch':     return row.sw.label;
      case 'Port':       return row.p.portNumber;
      case 'RX Bytes':   return row.p.totalRxBytes || 0;
      case 'TX Bytes':   return row.p.totalTxBytes || 0;
      case 'RX Packets': return row.p.totalRxPackets || 0;
      case 'TX Packets': return row.p.totalTxPackets || 0;
      case 'Errors':     return row._errLabel;
      default: return null;
    }
  }

  // Apply text search (port range / number / text) then column filters then sort
  let portRows = allRows.filter(({ p, sw, net }) => portMatches(p, sw, net, parsed));
  portRows = applyColFilters(portRows, tabId, getCV);

  if (!portRows.length) return empty('No results', 'No ports match your search.');

  portRows = applySort(portRows, tabId, getCV);

  updateAC(tabId, portRows.flatMap(r => [r.sw.label, r.net.label, String(r.p.portNumber)].filter(Boolean)));

  // Compute global maxima on visible rows
  let totalRx = 0, totalTx = 0, errPorts = 0, maxRx = 1, maxTx = 1;
  portRows.forEach(({ p }) => {
    totalRx += (p.totalRxBytes || 0);
    totalTx += (p.totalTxBytes || 0);
    if ((p.errorRxPackets || 0) + (p.errorTxPackets || 0) > 0) errPorts++;
    if ((p.totalRxBytes || 0) > maxRx) maxRx = p.totalRxBytes;
    if ((p.totalTxBytes || 0) > maxTx) maxTx = p.totalTxBytes;
  });

  const stats = `<div class="stats">
    <div class="stat"><div class="stat-lbl">Ports Shown</div><div class="stat-val">${portRows.length}</div></div>
    <div class="stat"><div class="stat-lbl">Total RX</div><div class="stat-val">${fmtBytes(totalRx)}</div></div>
    <div class="stat"><div class="stat-lbl">Total TX</div><div class="stat-val">${fmtBytes(totalTx)}</div></div>
    <div class="stat"><div class="stat-lbl">Ports w/ Errors</div>
      <div class="stat-val" style="color:${errPorts > 0 ? 'var(--red)' : 'var(--green)'}">${errPorts}</div></div>
  </div>`;

  const ntTh = showNt ? thEl(tabId, 'Network', 'Network') : '';
  const trs  = portRows.map(({ p, sw, net, _errLabel }) => {
    const hasErr  = _errLabel === 'Yes';
    const errCell = hasErr
      ? `<span class="badge red">RX ${p.errorRxPackets || 0} / TX ${p.errorTxPackets || 0}</span>`
      : `<span style="color:var(--faint)">—</span>`;
    const ntTd = showNt ? `<td><span class="badge dim">${esc(net.label)}</span></td>` : '';
    return `<tr>
      ${ntTd}
      <td style="font-weight:500;color:var(--text)">${esc(sw.label)}</td>
      <td><span style="font-weight:600;color:#fff">${p.portNumber}</span></td>
      <td><div class="tbar-wrap"><div class="tbar-bg"><div class="tbar-fill" style="width:${((p.totalRxBytes||0)/maxRx*100).toFixed(1)}%"></div></div>
          <span style="min-width:68px;text-align:right;color:var(--text)">${fmtBytes(p.totalRxBytes||0)}</span></div></td>
      <td><div class="tbar-wrap"><div class="tbar-bg"><div class="tbar-fill up" style="width:${((p.totalTxBytes||0)/maxTx*100).toFixed(1)}%"></div></div>
          <span style="min-width:68px;text-align:right;color:var(--text)">${fmtBytes(p.totalTxBytes||0)}</span></div></td>
      <td><code class="mono" style="color:var(--muted)">${(p.totalRxPackets||0).toLocaleString()}</code></td>
      <td><code class="mono" style="color:var(--muted)">${(p.totalTxPackets||0).toLocaleString()}</code></td>
      <td>${errCell}</td>
    </tr>`;
  }).join('');

  return stats + `<div class="tbl-wrap"><table>
    <thead><tr>${ntTh}${thEl(tabId,'Switch','Switch')}${thEl(tabId,'Port','Port')}${thEl(tabId,'RX Bytes','RX Bytes')}${thEl(tabId,'TX Bytes','TX Bytes')}${thEl(tabId,'RX Packets','RX Packets')}${thEl(tabId,'TX Packets','TX Packets')}${thEl(tabId,'Errors','Errors')}</tr></thead>
    <tbody>${trs}</tbody></table></div>`;
}

// ── Event Log ─────────────────────────────────────────────────────────────────
function renderEvents({ network: nf, search: q }, tabId) {
  const nets   = selectedNets(nf);
  const showNt = multiNet(nf);
  const all    = [];

  for (const net of nets) {
    const entry = (appData.eventLog || {})[net.UUID] || {};
    (entry.events || []).forEach(e => all.push({ ...e, _net: net }));
  }

  if (!all.length) return empty('No events', 'No recent events for the selected network(s).');

  storeColVals(tabId, 'Event Type', all.map(e => e.eventType).filter(Boolean));
  storeColVals(tabId, 'API Name',   all.map(e => e.eventTypeAPIName).filter(Boolean));
  if (showNt) storeColVals(tabId, 'Network', all.map(e => e._net.label));

  function getCV(e, col) {
    switch(col) {
      case 'Event Type':   return e.eventType;
      case 'API Name':     return e.eventTypeAPIName;
      case 'Generated At': return e.generatedAt;
      case 'Network':      return e._net.label;
      default: return null;
    }
  }

  let filtered = all.filter(e => hits([e.eventType, e.eventTypeAPIName, e._net.label], q));
  filtered = applyColFilters(filtered, tabId, getCV);
  if (!filtered.length) return empty('No results', 'No rows match your search.');

  // Default sort: newest first; user sort overrides
  if (!filterState[tabId].sortCol) {
    filtered.sort((a, b) => new Date(b.generatedAt) - new Date(a.generatedAt));
  } else {
    filtered = applySort(filtered, tabId, getCV);
  }

  updateAC(tabId, filtered.flatMap(e => [e.eventType, e.eventTypeAPIName, e._net.label].filter(Boolean)));

  const stats = `<div class="stats">
    <div class="stat"><div class="stat-lbl">Events Shown</div><div class="stat-val">${filtered.length}</div></div>
    <div class="stat"><div class="stat-lbl">Networks</div><div class="stat-val">${nets.length}</div></div>
  </div>`;

  const ntTh = showNt ? thEl(tabId, 'Network', 'Network') : '';
  const trs  = filtered.map(e => {
    const t   = (e.eventType || '').toLowerCase();
    const cls = (t.includes('error') || t.includes('fail') || t.includes('down')) ? 'red'
              : (t.includes('warn') || t.includes('disconnect')) ? 'yellow' : 'green';
    const ts  = e.generatedAt ? new Date(e.generatedAt) : null;
    const ntTd = showNt ? `<td><span class="badge dim">${esc(e._net.label)}</span></td>` : '';
    return `<tr>
      <td>${badge(e.eventType || '—', cls)}</td>
      <td><code class="mono" style="color:var(--muted)">${esc(e.eventTypeAPIName) || '—'}</code></td>
      <td style="white-space:nowrap;color:var(--muted)">${ts ? ts.toLocaleString() : '—'}</td>
      <td style="white-space:nowrap;color:var(--muted)">${ts ? timeAgo(e.generatedAt) : '—'}</td>
      ${ntTd}
    </tr>`;
  }).join('');

  return stats + `<div class="tbl-wrap"><table>
    <thead><tr>${thEl(tabId,'Event Type','Event Type')}${thEl(tabId,'API Name','API Name')}${thEl(tabId,'Generated At','Generated At')}${thEl(tabId,'Age','Age')}${ntTh}</tr></thead>
    <tbody>${trs}</tbody></table></div>`;
}

// ── Polling ───────────────────────────────────────────────────────────────────
async function poll() {
  try {
    const res  = await fetch('/api/data');
    const json = await res.json();
    if (json.data && Object.keys(json.data).length) {
      onData(json);
    }
  } catch(e) {
    document.getElementById('status-text').textContent = 'Connection error';
  }
}

poll();
setInterval(poll, 30_000);
</script>
</body>
</html>"""


# ── Entry point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    t = threading.Thread(target=background_loop, daemon=True)
    t.start()
    port = int(os.environ.get("PORT", 8080))
    print(f"Meter Dashboard running at http://localhost:{port}")
    app.run(host="0.0.0.0", port=port, debug=False)
