#!/usr/bin/env python3
"""Meter Network Dashboard — Flask server that polls the Meter GraphQL API every 5 minutes."""

import os
import threading
import time
from datetime import datetime, timezone
import config

import requests
from flask import Flask, jsonify, render_template_string

app = Flask(__name__)

# ── Configuration (mirrors public-api-demo.sh) ────────────────────────────────

API_URL = config.API_URL

API_TOKEN = config.API_TOKEN

NETWORK_UUID = config.NETWORK_UUID
COMPANY_UUID = config.COMPANY_UUID
VIRTUAL_DEVICE_UUID = config.VIRTUAL_DEVICE_UUID

REFRESH_INTERVAL = 300  # 5 minutes

# ── Data cache ────────────────────────────────────────────────────────────────

_cache: dict = {}
_last_updated: str | None = None
_lock = threading.Lock()


# ── GraphQL helper ────────────────────────────────────────────────────────────

def gql(query: str) -> dict:
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {API_TOKEN}",
    }
    try:
        resp = requests.post(API_URL, json={"query": query}, headers=headers, timeout=30)
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:
        return {"error": str(exc)}


# ── Queries ───────────────────────────────────────────────────────────────────

def build_queries() -> dict[str, str]:
    n = NETWORK_UUID
    v = VIRTUAL_DEVICE_UUID
    return {
        "uplinkQualities": (
            f'{{ networksUplinkQualities(networkUUIDs: ["{n}"], '
            f'filter: {{ durationSeconds: 14400, stepSeconds: 300 }}) {{'
            f' metadata {{ minValue maxValue }}'
            f' values {{ timestamp value phyInterfaceUUID networkUUID }} }} }}'
        ),
        "networkClients": (
            f'{{ networkClients(networkUUID: "{n}") {{'
            f' macAddress ip clientName isWireless signal lastSeen'
            f' connectedVLAN {{ name vlanID }} connectedSSID {{ ssid }} }} }}'
        ),
        "uplinkPhyInterfaces": (
            f'{{ uplinkPhyInterfacesForNetwork(networkUUID: "{n}") {{'
            f' UUID label portNumber isEnabled isUplink isUplinkActive'
            f' portSpeedMbps nativeVLAN {{ name vlanID }} }} }}'
        ),
        "uplinkThroughput": (
            f'{{ networkUplinkThroughput(networkUUID: "{n}", '
            f'filter: {{ durationSeconds: 14400, stepSeconds: 300 }}) {{'
            f' metadata {{ minValue maxValue }}'
            f' values {{ timestamp value direction phyInterfaceUUID }} }} }}'
        ),
        "eventLog": (
            f'{{ recentEventLogEventsPage(networkUUID: "{n}", limit: 20) {{'
            f' total events {{ eventType eventTypeAPIName generatedAt networkUUID }} }} }}'
        ),
        "switchPortStats": (
            f'{{ switchPortStats(virtualDeviceUUID: "{v}") {{'
            f' portNumber totalRxBytes totalTxBytes totalRxPackets totalTxPackets'
            f' errorRxPackets errorTxPackets }} }}'
        ),
    }


def fetch_all() -> None:
    global _last_updated
    queries = build_queries()
    new_data: dict = {}
    for key, query in queries.items():
        print(f"  Fetching {key}…", flush=True)
        new_data[key] = gql(query)
    with _lock:
        _cache.update(new_data)
        _last_updated = datetime.now(timezone.utc).isoformat()
    print(f"  Done — {_last_updated}", flush=True)


def background_loop() -> None:
    while True:
        print(f"[{datetime.now().strftime('%H:%M:%S')}] Polling Meter API…", flush=True)
        try:
            fetch_all()
        except Exception as exc:
            print(f"  ERROR: {exc}", flush=True)
        time.sleep(REFRESH_INTERVAL)


# ── Flask routes ──────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template_string(HTML_TEMPLATE)


@app.route("/api/data")
def api_data():
    with _lock:
        return jsonify({"data": dict(_cache), "last_updated": _last_updated})


# ── HTML template ─────────────────────────────────────────────────────────────

HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Meter — Network Dashboard</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600&family=Roboto+Mono:wght@400;500&display=swap">
<style>
/*
 * Color palette sourced from dashboard.meter.com production CSS
 * Body bg #282a3d, surfaces #1e202e / #343647 / #4e5161, brand #5461c8
 */
:root {
  /* Backgrounds — dark indigo/navy */
  --bg:        #282a3d;   /* page body (exact from <body style>) */
  --bg1:       #1e202e;   /* darker panels, table headers */
  --bg2:       #343647;   /* card surfaces */
  --bg3:       #4e5161;   /* hover / raised elements */

  /* Borders */
  --border:    #4e5161;   /* primary border */
  --border2:   #343647;   /* subtle row dividers */

  /* Text — from rgb() color classes in production CSS */
  --text:      #e4e6f0;   /* rgb(228 230 240) */
  --muted:     #9799ad;   /* rgb(151 153 173) */
  --faint:     #66687a;   /* rgb(102 104 122) */

  /* Brand accent — Meter indigo */
  --brand:     #5461c8;   /* rgb(84 97 200) */
  --brand-bg:  rgba(84,97,200,.15);
  --brand-dim: rgba(84,97,200,.08);

  /* Semantic status colors */
  --green:     #22c55e;
  --green-bg:  rgba(34,197,94,.12);
  --red:       #f45757;
  --red-bg:    rgba(244,87,87,.12);
  --yellow:    #f59e0b;
  --yellow-bg: rgba(245,158,11,.12);

  --radius:    6px;
}

*{box-sizing:border-box;margin:0;padding:0;}
body{
  background:var(--bg);color:var(--text);
  font-family:'Inter',-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
  font-size:13px;line-height:1.5;min-height:100vh;
}

/* ── Header ── */
header{
  position:sticky;top:0;z-index:100;
  height:52px;padding:0 28px;
  display:flex;align-items:center;justify-content:space-between;
  background:rgba(30,32,46,.95);backdrop-filter:blur(12px);
  border-bottom:1px solid var(--border);
}
.logo{font-size:16px;font-weight:600;letter-spacing:-.3px;color:#fff;display:flex;align-items:center;gap:1px;}
.logo-dot{color:var(--brand);}
.header-right{display:flex;align-items:center;gap:12px;color:var(--muted);font-size:12px;}
.pulse-wrap{
  display:flex;align-items:center;gap:7px;
  padding:4px 10px;
  background:var(--bg1);border:1px solid var(--border);border-radius:6px;
}
.pulse{width:6px;height:6px;border-radius:50%;background:var(--green);animation:pulse 2.2s ease-in-out infinite;}
@keyframes pulse{0%,100%{opacity:1;}50%{opacity:.2;}}

/* ── Nav ── */
nav{
  display:flex;padding:0 28px;
  border-bottom:1px solid var(--border);
  overflow-x:auto;background:var(--bg1);
}
nav::-webkit-scrollbar{display:none;}
nav button{
  background:none;border:none;border-bottom:2px solid transparent;
  color:var(--muted);padding:11px 16px;cursor:pointer;
  font:400 13px/1 'Inter',sans-serif;white-space:nowrap;
  transition:color .15s,border-color .15s;
}
nav button:hover{color:var(--text);}
nav button.active{color:#fff;border-bottom-color:var(--brand);}

/* ── Main ── */
main{padding:28px;max-width:1440px;}
.section{display:none;animation:fadeIn .18s ease;}
.section.active{display:block;}
@keyframes fadeIn{from{opacity:0;transform:translateY(3px);}to{opacity:1;transform:none;}}

/* ── Section header ── */
.sec-hdr{margin-bottom:20px;}
.sec-hdr h2{font-size:14px;font-weight:600;color:#fff;letter-spacing:-.1px;}
.sec-hdr p{font-size:12px;color:var(--muted);margin-top:3px;}

/* ── Stats row ── */
.stats{display:flex;gap:10px;margin-bottom:20px;flex-wrap:wrap;}
.stat{
  flex:1;min-width:120px;
  background:var(--bg1);border:1px solid var(--border);border-radius:var(--radius);
  padding:14px 16px;
}
.stat-lbl{font-size:10px;text-transform:uppercase;letter-spacing:.6px;color:var(--muted);}
.stat-val{font-size:22px;font-weight:600;color:#fff;margin-top:4px;line-height:1.1;}
.stat-sub{font-size:11px;color:var(--muted);margin-top:3px;}

/* ── Table ── */
.tbl-wrap{border:1px solid var(--border);border-radius:var(--radius);overflow:hidden;}
table{width:100%;border-collapse:collapse;}
thead tr{background:var(--bg1);}
th{
  padding:9px 14px;
  font-size:10px;font-weight:500;text-transform:uppercase;letter-spacing:.55px;
  color:var(--faint);text-align:left;
  border-bottom:1px solid var(--border);white-space:nowrap;
}
td{
  padding:9px 14px;
  border-bottom:1px solid var(--border2);
  vertical-align:middle;
  background:var(--bg2);
}
tr:last-child td{border-bottom:none;}
tbody tr{transition:background .1s;}
tbody tr:hover td{background:var(--bg3);}

/* ── Badges ── */
.badge{
  display:inline-flex;align-items:center;gap:5px;
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
.sig-bar{width:3px;background:var(--border);border-radius:1px;}

/* ── Throughput bar ── */
.tbar-wrap{display:flex;align-items:center;gap:8px;min-width:120px;}
.tbar-bg{flex:1;height:3px;background:var(--border2);border-radius:2px;overflow:hidden;}
.tbar-fill{height:100%;border-radius:2px;background:var(--green);transition:width .5s ease;}
.tbar-fill.up{background:#9ca8e8;}
</style>
</head>
<body style="background-color:#282a3d">

<header>
  <div class="logo">meter<span class="logo-dot">.</span></div>
  <div class="header-right">
    <span id="net-label" style="color:var(--faint);font-size:11px;">network / primary</span>
    <div class="pulse-wrap">
      <div class="pulse"></div>
      <span id="status-text">Loading…</span>
    </div>
  </div>
</header>

<nav id="nav">
  <button class="active" data-tab="uplink-quality">Uplink Quality</button>
  <button data-tab="clients">Network Clients</button>
  <button data-tab="phy-ifaces">Physical Interfaces</button>
  <button data-tab="throughput">Throughput</button>
  <button data-tab="events">Event Log</button>
  <button data-tab="switch-ports">Switch Ports</button>
</nav>

<main>
  <div id="uplink-quality" class="section active">
    <div class="sec-hdr"><h2>Uplink Quality</h2><p>WAN quality metrics across uplink interfaces — last 4 hours</p></div>
    <div id="uplink-quality-body"><div class="loading"><div class="spinner"></div><div class="title">Fetching data…</div></div></div>
  </div>

  <div id="clients" class="section">
    <div class="sec-hdr"><h2>Network Clients</h2><p>Active clients on the network with connection details</p></div>
    <div id="clients-body"><div class="loading"><div class="spinner"></div><div class="title">Fetching data…</div></div></div>
  </div>

  <div id="phy-ifaces" class="section">
    <div class="sec-hdr"><h2>Uplink Physical Interfaces</h2><p>WAN uplink port configuration and status</p></div>
    <div id="phy-ifaces-body"><div class="loading"><div class="spinner"></div><div class="title">Fetching data…</div></div></div>
  </div>

  <div id="throughput" class="section">
    <div class="sec-hdr"><h2>Uplink Throughput Metrics</h2><p>WAN bandwidth utilisation per interface — last 4 hours</p></div>
    <div id="throughput-body"><div class="loading"><div class="spinner"></div><div class="title">Fetching data…</div></div></div>
  </div>

  <div id="events" class="section">
    <div class="sec-hdr"><h2>Event Log</h2><p>Most recent 20 network events</p></div>
    <div id="events-body"><div class="loading"><div class="spinner"></div><div class="title">Fetching data…</div></div></div>
  </div>

  <div id="switch-ports" class="section">
    <div class="sec-hdr"><h2>Switch Port Stats</h2><p>Cumulative traffic and error counters for all switch ports</p></div>
    <div id="switch-ports-body"><div class="loading"><div class="spinner"></div><div class="title">Fetching data…</div></div></div>
  </div>
</main>

<script>
// ── Tab navigation ─────────────────────────────────────────────────────────
document.getElementById('nav').addEventListener('click', e => {
  const btn = e.target.closest('button');
  if (!btn) return;
  document.querySelectorAll('nav button').forEach(b => b.classList.remove('active'));
  document.querySelectorAll('.section').forEach(s => s.classList.remove('active'));
  btn.classList.add('active');
  document.getElementById(btn.dataset.tab).classList.add('active');
});

// ── Helpers ────────────────────────────────────────────────────────────────
function fmtBytes(b) {
  if (b == null || b === 0) return '0 B';
  const u = ['B','KB','MB','GB','TB'];
  const i = Math.min(Math.floor(Math.log(Math.abs(b)) / Math.log(1024)), u.length - 1);
  return (b / Math.pow(1024, i)).toFixed(i ? 1 : 0) + '\u202f' + u[i];
}
function fmtBps(v) {
  if (v == null || v === 0) return '0 bps';
  const u = ['bps','Kbps','Mbps','Gbps'];
  const i = Math.min(Math.floor(Math.log(Math.abs(v)) / Math.log(1000)), u.length - 1);
  return (v / Math.pow(1000, i)).toFixed(i ? 1 : 0) + '\u202f' + u[i];
}
function timeAgo(ts) {
  if (!ts) return '—';
  const s = Math.floor((Date.now() - new Date(ts)) / 1000);
  if (s < 60) return s + 's ago';
  if (s < 3600) return Math.floor(s/60) + 'm ago';
  if (s < 86400) return Math.floor(s/3600) + 'h ago';
  return Math.floor(s/86400) + 'd ago';
}
function shortId(uuid) { return uuid ? uuid.slice(-8) : '—'; }
function esc(s) {
  if (s == null) return '';
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}
function badge(label, cls) { return `<span class="badge ${cls}">${esc(label)}</span>`; }
function dot(cls) { return `<span class="dot ${cls}"></span>`; }
function empty(msg, sub) {
  return `<div class="empty"><div class="title">${msg}</div>${sub ? `<div>${sub}</div>` : ''}</div>`;
}

// ── Signal bars ─────────────────────────────────────────────────────────────
function signalBars(dbm) {
  if (dbm == null) return '—';
  const lvl = dbm > -55 ? 4 : dbm > -65 ? 3 : dbm > -75 ? 2 : 1;
  const c = dbm > -65 ? 'green' : dbm > -75 ? 'yellow' : 'red';
  const bars = [4,8,11,14].map((h,i) =>
    `<div class="sig-bar${i < lvl ? ' on' : ''}" style="height:${h}px;background:${i < lvl ? (c==='green'?'var(--green)':c==='yellow'?'var(--yellow)':'var(--red)') : 'var(--border)'}"></div>`
  ).join('');
  return `<span class="sig">${bars}</span> <span style="font-size:11px;color:var(--muted)">${dbm} dBm</span>`;
}

// ── Throughput mini-bar ──────────────────────────────────────────────────────
function tBar(val, max, dir) {
  const pct = max > 0 ? Math.min((val / max) * 100, 100) : 0;
  return `<div class="tbar-wrap"><div class="tbar-bg"><div class="tbar-fill ${dir==='upload'?'up':''}" style="width:${pct}%"></div></div><span style="color:var(--text);min-width:70px;text-align:right">${fmtBps(val)}</span></div>`;
}

// ── Renderers ──────────────────────────────────────────────────────────────

function renderUplinkQuality(raw) {
  // networksUplinkQualities returns an array (one entry per network)
  const arr = raw?.data?.networksUplinkQualities;
  if (!arr || !Array.isArray(arr) || !arr.length) return empty('No data', 'Could not load uplink quality metrics.');

  // Flatten values and metadata across all network entries
  const vals = arr.flatMap(x => x.values || []);
  if (!vals.length) return empty('No data points', 'No uplink quality data for this time range.');
  const metaMin = Math.min(...arr.map(x => x.metadata?.minValue ?? Infinity).filter(isFinite));
  const metaMax = Math.max(...arr.map(x => x.metadata?.maxValue ?? -Infinity).filter(isFinite));

  const byIface = {};
  vals.forEach(v => {
    const k = v.phyInterfaceUUID || 'unknown';
    if (!byIface[k]) byIface[k] = { pts: [], net: v.networkUUID };
    byIface[k].pts.push(v);
  });

  const numVals = vals.map(v => v.value).filter(v => v != null);
  const avg = numVals.length ? (numVals.reduce((a,b)=>a+b,0)/numVals.length) : null;

  const stats = `<div class="stats">
    <div class="stat"><div class="stat-lbl">Interfaces</div><div class="stat-val">${Object.keys(byIface).length}</div></div>
    <div class="stat"><div class="stat-lbl">Data Points</div><div class="stat-val">${vals.length}</div></div>
    <div class="stat"><div class="stat-lbl">Avg Quality</div><div class="stat-val">${avg != null ? (avg*100).toFixed(1)+'%' : '—'}</div><div class="stat-sub">min ${isFinite(metaMin)?metaMin:'—'} / max ${isFinite(metaMax)?metaMax:'—'}</div></div>
  </div>`;

  const rows = Object.entries(byIface).map(([iface, {pts, net}]) => {
    const latest = pts[pts.length-1];
    const v = latest.value;
    const cls = v > .8 ? 'green' : v > .5 ? 'yellow' : 'red';
    const ifaceNums = pts.map(p => p.value).filter(x => x != null);
    const ifaceAvg = ifaceNums.length ? (ifaceNums.reduce((a,b)=>a+b,0)/ifaceNums.length) : null;
    const pct = v != null ? (v*100).toFixed(1)+'%' : '—';
    const barW = v != null ? (v*100).toFixed(1) : 0;
    // timestamp is an ISO string (e.g. "2026-03-06T11:30:00Z")
    const tsStr = latest.timestamp ? new Date(latest.timestamp).toLocaleTimeString() : '—';
    return `<tr>
      <td><code class="mono">…${esc(shortId(iface))}</code></td>
      <td><div class="tbar-wrap"><div class="tbar-bg"><div class="tbar-fill" style="width:${barW}%;background:${v>.8?'var(--green)':v>.5?'var(--yellow)':'var(--red)'}"></div></div><span class="badge ${cls}" style="min-width:56px;justify-content:center">${pct}</span></div></td>
      <td style="color:var(--muted)">${ifaceAvg != null ? (ifaceAvg*100).toFixed(1)+'%' : '—'}</td>
      <td style="color:var(--muted)">${pts.length}</td>
      <td><code class="mono" style="color:var(--faint)">…${esc(shortId(net))}</code></td>
      <td style="color:var(--muted)">${tsStr}</td>
    </tr>`;
  }).join('');

  return stats + `<div class="tbl-wrap"><table>
    <thead><tr><th>Interface</th><th>Latest Quality</th><th>Average</th><th>Points</th><th>Network</th><th>Last Sample</th></tr></thead>
    <tbody>${rows}</tbody>
  </table></div>`;
}

function renderClients(raw) {
  const clients = raw?.data?.networkClients;
  if (!clients) return empty('No data', 'Could not load network clients.');
  if (!clients.length) return empty('No clients', 'No active clients found on this network.');

  const wireless = clients.filter(c => c.isWireless).length;
  const wired = clients.length - wireless;

  const stats = `<div class="stats">
    <div class="stat"><div class="stat-lbl">Total</div><div class="stat-val">${clients.length}</div></div>
    <div class="stat"><div class="stat-lbl">Wireless</div><div class="stat-val">${wireless}</div></div>
    <div class="stat"><div class="stat-lbl">Wired</div><div class="stat-val">${wired}</div></div>
  </div>`;

  const rows = clients.map(c => {
    const typeBadge = c.isWireless ? badge('Wi-Fi','blue') : badge('Wired','dim');
    const vlan = c.connectedVLAN ? `<span class="badge dim">${esc(c.connectedVLAN.name)} <span style="color:var(--faint)">${c.connectedVLAN.vlanID}</span></span>` : '<span style="color:var(--faint)">—</span>';
    return `<tr>
      <td><span style="color:#fff;font-weight:500">${esc(c.clientName) || '<span style="color:var(--faint)">—</span>'}</span></td>
      <td><code class="mono">${esc(c.ip) || '—'}</code></td>
      <td><code class="mono" style="font-size:11px;color:var(--muted)">${esc(c.macAddress)}</code></td>
      <td>${typeBadge}</td>
      <td>${signalBars(c.signal)}</td>
      <td>${vlan}</td>
      <td style="color:var(--muted)">${esc(c.connectedSSID?.ssid) || '—'}</td>
      <td style="color:var(--muted);white-space:nowrap">${timeAgo(c.lastSeen)}</td>
    </tr>`;
  }).join('');

  return stats + `<div class="tbl-wrap"><table>
    <thead><tr><th>Name</th><th>IP</th><th>MAC Address</th><th>Type</th><th>Signal</th><th>VLAN</th><th>SSID</th><th>Last Seen</th></tr></thead>
    <tbody>${rows}</tbody>
  </table></div>`;
}

function renderPhyIfaces(raw) {
  const ifaces = raw?.data?.uplinkPhyInterfacesForNetwork;
  if (!ifaces) return empty('No data', 'Could not load physical interfaces.');
  if (!ifaces.length) return empty('No interfaces', 'No uplink interfaces found.');

  const active = ifaces.filter(i => i.isUplinkActive).length;
  const enabled = ifaces.filter(i => i.isEnabled).length;

  const stats = `<div class="stats">
    <div class="stat"><div class="stat-lbl">Interfaces</div><div class="stat-val">${ifaces.length}</div></div>
    <div class="stat"><div class="stat-lbl">Active Uplinks</div><div class="stat-val" style="color:var(--green)">${active}</div></div>
    <div class="stat"><div class="stat-lbl">Enabled</div><div class="stat-val">${enabled}</div></div>
  </div>`;

  const rows = ifaces.map(i => {
    const statusDot = i.isUplinkActive ? dot('green') : i.isEnabled ? dot('yellow') : dot('gray');
    const statusBadge = i.isUplinkActive ? badge('Active','green') : i.isEnabled ? badge('Standby','yellow') : badge('Disabled','dim');
    const speed = i.portSpeedMbps ? `${i.portSpeedMbps} Mbps` : '—';
    const vlan = i.nativeVLAN ? `<span class="badge dim">${esc(i.nativeVLAN.name)} <span style="color:var(--faint)">${i.nativeVLAN.vlanID}</span></span>` : '—';
    return `<tr>
      <td><span style="font-weight:500">${esc(i.label) || `Port ${i.portNumber}`}</span></td>
      <td style="color:var(--muted)">${i.portNumber}</td>
      <td><div style="display:flex;align-items:center;gap:7px">${statusDot}${statusBadge}</div></td>
      <td>${i.isEnabled ? dot('green') + ' Yes' : dot('gray') + ' No'}</td>
      <td style="color:var(--muted)">${speed}</td>
      <td>${vlan}</td>
      <td><code class="mono" style="color:var(--faint)">${esc(i.UUID)}</code></td>
    </tr>`;
  }).join('');

  return stats + `<div class="tbl-wrap"><table>
    <thead><tr><th>Label</th><th>Port #</th><th>Status</th><th>Enabled</th><th>Speed</th><th>Native VLAN</th><th>UUID</th></tr></thead>
    <tbody>${rows}</tbody>
  </table></div>`;
}

function renderThroughput(raw) {
  const d = raw?.data?.networkUplinkThroughput;
  if (!d) return empty('No data', 'Could not load throughput metrics.');
  const vals = d.values || [];
  if (!vals.length) return empty('No data points', 'No throughput data for this time range.');

  const byKey = {};
  vals.forEach(v => {
    const k = `${v.phyInterfaceUUID}||${v.direction}`;
    if (!byKey[k]) byKey[k] = { iface: v.phyInterfaceUUID, dir: v.direction, pts: [] };
    byKey[k].pts.push(v);
  });

  const globalMax = d.metadata?.maxValue || 1;
  const stats = `<div class="stats">
    <div class="stat"><div class="stat-lbl">Data Points</div><div class="stat-val">${vals.length}</div></div>
    <div class="stat"><div class="stat-lbl">Peak</div><div class="stat-val">${fmtBps(d.metadata?.maxValue)}</div></div>
    <div class="stat"><div class="stat-lbl">Floor</div><div class="stat-val">${fmtBps(d.metadata?.minValue)}</div></div>
  </div>`;

  const rows = Object.values(byKey).map(g => {
    const numVals = g.pts.map(p => p.value).filter(v => v != null);
    const avg = numVals.length ? numVals.reduce((a,b)=>a+b,0)/numVals.length : 0;
    const peak = numVals.length ? Math.max(...numVals) : 0;
    const latest = g.pts[g.pts.length-1]?.value ?? 0;
    const dirBadge = g.dir === 'upload' ? badge('↑ Upload','blue') : badge('↓ Download','green');
    return `<tr>
      <td><code class="mono">…${esc(shortId(g.iface))}</code></td>
      <td>${dirBadge}</td>
      <td>${tBar(latest, globalMax, g.dir)}</td>
      <td style="color:var(--muted)">${fmtBps(avg)}</td>
      <td style="color:var(--muted)">${fmtBps(peak)}</td>
      <td style="color:var(--faint)">${g.pts.length}</td>
    </tr>`;
  }).join('');

  return stats + `<div class="tbl-wrap"><table>
    <thead><tr><th>Interface</th><th>Direction</th><th>Latest</th><th>Average</th><th>Peak</th><th>Samples</th></tr></thead>
    <tbody>${rows}</tbody>
  </table></div>`;
}

function renderEvents(raw) {
  const d = raw?.data?.recentEventLogEventsPage;
  if (!d) return empty('No data', 'Could not load event log.');
  const events = d.events || [];
  if (!events.length) return empty('No events', 'No recent events found.');

  const stats = `<div class="stats">
    <div class="stat"><div class="stat-lbl">Total Events</div><div class="stat-val">${d.total ?? '—'}</div></div>
    <div class="stat"><div class="stat-lbl">Showing</div><div class="stat-val">${events.length}</div></div>
  </div>`;

  const rows = events.map(e => {
    const t = (e.eventType || '').toLowerCase();
    const cls = (t.includes('error') || t.includes('fail') || t.includes('down')) ? 'red'
               : (t.includes('warn') || t.includes('disconnect')) ? 'yellow' : 'green';
    const ts = e.generatedAt ? new Date(e.generatedAt) : null;
    return `<tr>
      <td>${badge(e.eventType || '—', cls)}</td>
      <td><code class="mono" style="color:var(--muted)">${esc(e.eventTypeAPIName) || '—'}</code></td>
      <td style="white-space:nowrap;color:var(--muted)">${ts ? ts.toLocaleString() : '—'}</td>
      <td style="white-space:nowrap;color:var(--muted)">${ts ? timeAgo(e.generatedAt) : '—'}</td>
      <td><code class="mono" style="color:var(--faint)">…${esc(shortId(e.networkUUID))}</code></td>
    </tr>`;
  }).join('');

  return stats + `<div class="tbl-wrap"><table>
    <thead><tr><th>Event Type</th><th>API Name</th><th>Generated At</th><th>Age</th><th>Network</th></tr></thead>
    <tbody>${rows}</tbody>
  </table></div>`;
}

function renderSwitchPorts(raw) {
  const ports = raw?.data?.switchPortStats;
  if (!ports) return empty('No data', 'Could not load switch port stats.');
  if (!ports.length) return empty('No ports', 'No switch port data found.');

  const totalRx = ports.reduce((s,p) => s+(p.totalRxBytes||0), 0);
  const totalTx = ports.reduce((s,p) => s+(p.totalTxBytes||0), 0);
  const errCount = ports.filter(p => (p.errorRxPackets||0)+(p.errorTxPackets||0) > 0).length;
  const maxRx = Math.max(...ports.map(p => p.totalRxBytes||0), 1);
  const maxTx = Math.max(...ports.map(p => p.totalTxBytes||0), 1);

  const stats = `<div class="stats">
    <div class="stat"><div class="stat-lbl">Ports</div><div class="stat-val">${ports.length}</div></div>
    <div class="stat"><div class="stat-lbl">Total RX</div><div class="stat-val">${fmtBytes(totalRx)}</div></div>
    <div class="stat"><div class="stat-lbl">Total TX</div><div class="stat-val">${fmtBytes(totalTx)}</div></div>
    <div class="stat"><div class="stat-lbl">Ports w/ Errors</div><div class="stat-val" style="color:${errCount>0?'var(--red)':'var(--green)'}">${errCount}</div></div>
  </div>`;

  const rows = ports.map(p => {
    const hasErr = (p.errorRxPackets||0)+(p.errorTxPackets||0) > 0;
    const errCell = hasErr
      ? `<span class="badge red">RX ${p.errorRxPackets||0} / TX ${p.errorTxPackets||0}</span>`
      : `<span style="color:var(--faint)">—</span>`;
    return `<tr>
      <td><span style="font-weight:600;color:#fff">${p.portNumber}</span></td>
      <td><div class="tbar-wrap"><div class="tbar-bg"><div class="tbar-fill" style="width:${((p.totalRxBytes||0)/maxRx*100).toFixed(1)}%"></div></div><span style="min-width:68px;text-align:right;color:var(--text)">${fmtBytes(p.totalRxBytes||0)}</span></div></td>
      <td><div class="tbar-wrap"><div class="tbar-bg"><div class="tbar-fill up" style="width:${((p.totalTxBytes||0)/maxTx*100).toFixed(1)}%"></div></div><span style="min-width:68px;text-align:right;color:var(--text)">${fmtBytes(p.totalTxBytes||0)}</span></div></td>
      <td><code class="mono" style="color:var(--muted)">${(p.totalRxPackets||0).toLocaleString()}</code></td>
      <td><code class="mono" style="color:var(--muted)">${(p.totalTxPackets||0).toLocaleString()}</code></td>
      <td>${errCell}</td>
    </tr>`;
  }).join('');

  return stats + `<div class="tbl-wrap"><table>
    <thead><tr><th>Port</th><th>RX Bytes</th><th>TX Bytes</th><th>RX Packets</th><th>TX Packets</th><th>Errors</th></tr></thead>
    <tbody>${rows}</tbody>
  </table></div>`;
}

// ── Render all tabs ─────────────────────────────────────────────────────────
function renderAll(data) {
  document.getElementById('uplink-quality-body').innerHTML = renderUplinkQuality(data.uplinkQualities);
  document.getElementById('clients-body').innerHTML        = renderClients(data.networkClients);
  document.getElementById('phy-ifaces-body').innerHTML     = renderPhyIfaces(data.uplinkPhyInterfaces);
  document.getElementById('throughput-body').innerHTML     = renderThroughput(data.uplinkThroughput);
  document.getElementById('events-body').innerHTML         = renderEvents(data.eventLog);
  document.getElementById('switch-ports-body').innerHTML   = renderSwitchPorts(data.switchPortStats);
}

// ── Countdown ───────────────────────────────────────────────────────────────
let nextRefresh = 300;
setInterval(() => {
  if (nextRefresh <= 0) return;
  nextRefresh--;
  const m = Math.floor(nextRefresh / 60);
  const s = (nextRefresh % 60).toString().padStart(2,'0');
  document.getElementById('status-text').textContent = `Refresh in ${m}:${s}`;
}, 1000);

// ── Data polling ────────────────────────────────────────────────────────────
async function poll() {
  try {
    const res  = await fetch('/api/data');
    const json = await res.json();
    if (json.data && Object.keys(json.data).length) {
      renderAll(json.data);
      const t = json.last_updated ? new Date(json.last_updated).toLocaleTimeString() : '—';
      document.getElementById('status-text').textContent = `Updated ${t}`;
      nextRefresh = 300;
    }
  } catch (e) {
    document.getElementById('status-text').textContent = 'Connection error';
  }
}

poll();
setInterval(poll, 30_000); // re-poll every 30 s; server refreshes every 5 min
</script>
</body>
</html>"""


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    t = threading.Thread(target=background_loop, daemon=True)
    t.start()
    port = int(os.environ.get("PORT", 8080))
    print(f"Meter Dashboard running at http://localhost:{port}")
    app.run(host="0.0.0.0", port=port, debug=False)
