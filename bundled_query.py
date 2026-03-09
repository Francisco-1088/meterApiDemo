#!/usr/bin/env python3
"""
bundled_query.py
================
Demonstrates GraphQL query bundling: combining multiple resource types
into a single HTTP request to minimise API calls and stay within the
500 requests-per-minute rate limit.

WHY BUNDLE?
-----------
GraphQL's key advantage over REST APIs is that you can request multiple
independent resource types in a single round-trip. The Meter API
documentation explicitly recommends this approach:

    "Batch related data into a single query to reduce the number of
     requests you make and avoid hitting rate limits."

HOW BUNDLING WORKS
------------------
A GraphQL operation can contain multiple top-level fields. When two
fields share the same name (e.g., two networkClients calls for different
networks) you use an alias to distinguish them:

    {
      primaryClients:   networkClients(networkUUID: "uuid-1") { macAddress }
      secondaryClients: networkClients(networkUUID: "uuid-2") { macAddress }
    }

This results in ONE HTTP request returning BOTH datasets under their
aliased keys.

REQUEST COUNT COMPARISON
------------------------
Demo                     REST requests needed    GraphQL requests used
──────────────────────── ──────────────────────  ─────────────────────
Network Overview Bundle  4 (company + clients    1
                           + uplinks + events)
Metrics Bundle           3 (quality + thruput    1
                           + switch ports)
Inventory Bundle         4 (devices + clients    1
                           + SSIDs + VLANs)
──────────────────────── ──────────────────────  ─────────────────────
Total                    11                      3
"""

import json
import time
import requests
import config

# ── Configuration ──────────────────────────────────────────────────────────────

API_URL             = config.API_URL
API_TOKEN           = config.API_TOKEN
COMPANY_SLUG        = config.COMPANY_SLUG
NETWORK_UUID        = config.NETWORK_UUID
VIRTUAL_DEVICE_UUID = config.VIRTUAL_DEVICE_UUID

HEADERS = {
    "Content-Type": "application/json",
    "Authorization": f"Bearer {API_TOKEN}",
}


# ── Core request helper ────────────────────────────────────────────────────────

def run_query(query: str) -> tuple[dict, dict]:
    """
    Execute a GraphQL query and return (parsed_response, response_headers).

    Args:
        query: GraphQL query string (may contain multiple top-level fields).

    Returns:
        Tuple of (response_dict, headers_dict).

    Raises:
        requests.HTTPError: On 4xx/5xx HTTP responses.
    """
    response = requests.post(
        API_URL, headers=HEADERS, json={"query": query}, timeout=60
    )
    response.raise_for_status()
    return response.json(), dict(response.headers)


# ── Bundle 1: Network Overview ─────────────────────────────────────────────────

def network_overview_bundle(company_slug: str, network_uuid: str) -> dict:
    """
    Fetch a full network snapshot in a single HTTP request.

    Bundles four independent resource types using GraphQL aliases:

        companyInfo      — Basic company details (companyBySlug)
        networkClients   — All currently active clients (networkClients)
        uplinkInterfaces — WAN uplink port status (uplinkPhyInterfacesForNetwork)
        eventLog         — Most recent 10 network events (recentEventLogEventsPage)

    Without bundling this would require 4 separate HTTP requests.
    With bundling: 1 request returns all four datasets simultaneously.

    GraphQL aliases used:
        companyInfo     : companyBySlug(slug: "...")
        uplinkInterfaces: uplinkPhyInterfacesForNetwork(...)
        eventLog        : recentEventLogEventsPage(...)

    (networkClients has a unique name so no alias is needed for it.)

    Args:
        company_slug: Company slug identifier.
        network_uuid: UUID of the network to inspect.

    Returns:
        API response dict with a `data` key containing all four datasets.
    """
    query = f"""
    {{
      companyInfo: companyBySlug(slug: "{company_slug}") {{
        uuid
        name
        slug
        isCustomer
        websiteDomain
      }}

      networkClients(networkUUID: "{network_uuid}") {{
        macAddress
        ip
        clientName
        isWireless
        signal
        lastSeen
        connectedVLAN {{
          name
          vlanID
        }}
        connectedSSID {{
          ssid
        }}
      }}

      uplinkInterfaces: uplinkPhyInterfacesForNetwork(networkUUID: "{network_uuid}") {{
        UUID
        label
        portNumber
        isEnabled
        isUplinkActive
        portSpeedMbps
        nativeVLAN {{
          name
          vlanID
        }}
      }}

      eventLog: recentEventLogEventsPage(networkUUID: "{network_uuid}", limit: 10) {{
        total
        events {{
          eventType
          eventTypeAPIName
          generatedAt
          networkUUID
        }}
      }}
    }}
    """
    result, headers = run_query(query)
    return result


# ── Bundle 2: Metrics ──────────────────────────────────────────────────────────

def metrics_bundle(network_uuid: str, virtual_device_uuid: str) -> dict:
    """
    Fetch three time-series metrics datasets in a single HTTP request.

    Bundles metrics queries using aliases and a shared time filter
    (last 4 hours, 5-minute resolution):

        uplinkQuality    — WAN link quality scores per uplink interface
                           (networksUplinkQualities)
        uplinkThroughput — WAN upload/download bandwidth per interface
                           (networkUplinkThroughput)
        switchPortStats  — Cumulative per-port traffic counters for a switch
                           (switchPortStats)

    The MetricsFilterInput applied to time-series queries:
        durationSeconds: 14400  — look back 4 hours
        stepSeconds: 300        — aggregate into 5-minute buckets

    Without bundling: 3 separate HTTP requests.
    With bundling   : 1 request returns all three metrics simultaneously.

    Args:
        network_uuid:        UUID of the network.
        virtual_device_uuid: UUID of a switch virtual device.

    Returns:
        API response dict with uplinkQuality, uplinkThroughput, switchPortStats.
    """
    query = f"""
    {{
      uplinkQuality: networksUplinkQualities(
        networkUUIDs: ["{network_uuid}"],
        filter: {{ durationSeconds: 14400, stepSeconds: 300 }}
      ) {{
        metadata {{
          minValue
          maxValue
        }}
        values {{
          timestamp
          value
          phyInterfaceUUID
          networkUUID
        }}
      }}

      uplinkThroughput: networkUplinkThroughput(
        networkUUID: "{network_uuid}",
        filter: {{ durationSeconds: 14400, stepSeconds: 300 }}
      ) {{
        metadata {{
          minValue
          maxValue
        }}
        values {{
          timestamp
          value
          direction
          phyInterfaceUUID
        }}
      }}

      switchPortStats(virtualDeviceUUID: "{virtual_device_uuid}") {{
        portNumber
        totalRxBytes
        totalTxBytes
        totalRxPackets
        totalTxPackets
        errorRxPackets
        errorTxPackets
      }}
    }}
    """
    result, _ = run_query(query)
    return result


# ── Bundle 3: Network Inventory ────────────────────────────────────────────────

def inventory_bundle(network_uuid: str) -> dict:
    """
    Fetch the complete network inventory in a single HTTP request.

    Bundles four list resources:

        devices  — All virtual devices on the network (virtualDevicesForNetwork)
                   Device types: ACCESS_POINT, SWITCH, CONTROLLER,
                   CELLULAR_GATEWAY, POWER_DISTRIBUTION_UNIT, OBSERVER

        clients  — All active clients (networkClients)
                   Includes wired and wireless devices

        ssids    — All configured SSIDs (ssidsForNetwork)
                   Encryption protocols: WPA2, WPA3, WPA2_ENTERPRISE, etc.

        vlans    — All configured VLANs (vlans)
                   Each VLAN has a numeric ID, name, and gateway info

    Without bundling: 4 separate HTTP requests.
    With bundling   : 1 request, 4 complete datasets.

    Args:
        network_uuid: UUID of the network to inspect.

    Returns:
        API response dict with devices, clients, ssids, vlans.
    """
    query = f"""
    {{
      devices: virtualDevicesForNetwork(networkUUID: "{network_uuid}") {{
        UUID
        label
        deviceType
        deviceModel
        isOnline
      }}

      clients: networkClients(networkUUID: "{network_uuid}") {{
        macAddress
        ip
        clientName
        isWireless
        lastSeen
      }}

      ssids: ssidsForNetwork(networkUUID: "{network_uuid}") {{
        UUID
        ssid
        isEnabled
        isGuest
        isHidden
        encryptionProtocol
      }}

      vlans: vlans(networkUUID: "{network_uuid}") {{
        UUID
        name
        vlanID
        isEnabled
        isDefault
      }}
    }}
    """
    result, _ = run_query(query)
    return result


# ── Bundle 4: Multi-network clients (same query, multiple networks via alias) ──

def multi_network_clients_bundle(
    network_uuid_a: str,
    network_uuid_b: str,
    company_uuid: str,
) -> dict:
    """
    Fetch clients from two separate networks plus company-wide client totals
    in a single HTTP request.

    Demonstrates aliasing the same query field twice with different arguments,
    alongside a different query (networksClients) in the same operation.

    Without bundling: 3 requests.
    With bundling   : 1 request.

    Args:
        network_uuid_a: UUID of the first network.
        network_uuid_b: UUID of the second network (can equal network_uuid_a
                        to demonstrate the alias syntax even on the same network).
        company_uuid:   UUID of the company (for the networksClients query).

    Returns:
        API response dict with primaryNetworkClients, secondaryNetworkClients,
        and companyClients.
    """
    # Deduplicate so the API receives each UUID at most once
    unique_uuids = list(dict.fromkeys([network_uuid_a, network_uuid_b]))
    uuids_gql = '", "'.join(unique_uuids)

    query = f"""
    {{
      primaryNetworkClients: networkClients(networkUUID: "{network_uuid_a}") {{
        macAddress
        ip
        clientName
        isWireless
      }}

      secondaryNetworkClients: networkClients(networkUUID: "{network_uuid_b}") {{
        macAddress
        ip
        clientName
        isWireless
      }}

      companyClients: networksClients(
        companyUUID: "{company_uuid}",
        networkUUIDs: ["{uuids_gql}"]
      ) {{
        macAddress
        ip
        clientName
        isWireless
        lastSeen
      }}
    }}
    """
    result, _ = run_query(query)
    return result


# ── Output helpers ─────────────────────────────────────────────────────────────

def print_section(title: str, subtitle: str = "") -> None:
    """Print a formatted section header."""
    print(f"\n{'━' * 65}")
    print(f"  {title}")
    if subtitle:
        print(f"  {subtitle}")
    print(f"{'━' * 65}")


def summarise(label: str, value) -> None:
    """Print a concise summary line for a single result dataset."""
    if isinstance(value, list):
        print(f"  {label:<30} {len(value):>4} item(s)")
    elif isinstance(value, dict):
        scalars = {k: v for k, v in value.items() if not isinstance(v, (dict, list))}
        print(f"  {label:<30} {scalars}")
    elif value is None:
        print(f"  {label:<30} (null)")
    else:
        print(f"  {label:<30} {value}")


def print_bundle_summary(label: str, result: dict) -> None:
    """Print a structured summary of a bundled query response."""
    print_section(label)
    if "errors" in result:
        print(f"  GraphQL errors:")
        for err in result["errors"]:
            print(f"    ✗ [{err.get('extensions', {}).get('code', '?')}] {err.get('message')}")
        return

    data = result.get("data", {})
    if not data:
        print("  (no data returned)")
        return

    for key, value in data.items():
        summarise(key, value)


# ── Main ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("Meter API — Bundled Query Demo")
    print(f"Endpoint : {API_URL}")
    print(f"Network  : {NETWORK_UUID}")
    print()
    print("GraphQL lets you combine multiple resource types into ONE HTTP request.")
    print("This demo makes 3 requests instead of the 11 a REST API would require.")

    t_total_start = time.monotonic()

    # ── Bundle 1: Network Overview ────────────────────────────────────────────
    print("\n[1/3] Network Overview Bundle")
    print("      companyInfo  +  networkClients  +  uplinkInterfaces  +  eventLog")
    t0 = time.monotonic()
    result1 = network_overview_bundle(COMPANY_SLUG, NETWORK_UUID)
    elapsed1 = time.monotonic() - t0
    print(f"      → 1 HTTP request completed in {elapsed1 * 1000:.0f} ms")
    print_bundle_summary("Network Overview Bundle Results", result1)

    # Show a detail from the bundle
    data1 = result1.get("data", {})
    company = data1.get("companyInfo", {})
    events  = data1.get("eventLog", {})
    if company:
        print(f"\n  Company   : {company.get('name')} ({company.get('slug')})")
    if events:
        print(f"  Events    : {events.get('total')} total, last {len(events.get('events', []))} shown")

    # ── Bundle 2: Metrics ─────────────────────────────────────────────────────
    print("\n[2/3] Metrics Bundle")
    print("      uplinkQuality  +  uplinkThroughput  +  switchPortStats")
    t0 = time.monotonic()
    result2 = metrics_bundle(NETWORK_UUID, VIRTUAL_DEVICE_UUID)
    elapsed2 = time.monotonic() - t0
    print(f"      → 1 HTTP request completed in {elapsed2 * 1000:.0f} ms")
    print_bundle_summary("Metrics Bundle Results", result2)

    data2 = result2.get("data", {})
    quality_data = data2.get("uplinkQuality", [])
    if quality_data:
        all_values = sum(len(q.get("values", [])) for q in quality_data)
        print(f"\n  Uplink quality data points : {all_values}")

    # ── Bundle 3: Network Inventory ──────────────────���────────────────────────
    print("\n[3/3] Inventory Bundle")
    print("      devices  +  clients  +  ssids  +  vlans")
    t0 = time.monotonic()
    result3 = inventory_bundle(NETWORK_UUID)
    elapsed3 = time.monotonic() - t0
    print(f"      → 1 HTTP request completed in {elapsed3 * 1000:.0f} ms")
    print_bundle_summary("Inventory Bundle Results", result3)

    data3 = result3.get("data", {})
    ssids = data3.get("ssids", [])
    if ssids:
        print(f"\n  SSIDs:")
        for ssid in ssids:
            status = "enabled" if ssid.get("isEnabled") else "disabled"
            guest  = " [guest]" if ssid.get("isGuest") else ""
            print(f"    • {ssid.get('ssid') or '':<30} {ssid.get('encryptionProtocol') or '':<20} {status}{guest}")

    # ── Summary ───────────────────────────────────────────────────────────────
    t_total = time.monotonic() - t_total_start
    print(f"\n{'═' * 65}")
    print(f"  Summary")
    print(f"{'═' * 65}")
    print(f"  Total HTTP requests  : 3")
    print(f"  Equivalent REST calls: 11+")
    print(f"  Total elapsed time   : {t_total * 1000:.0f} ms")
    print(f"\n  Each bundle used GraphQL aliases to pack multiple queries into")
    print(f"  one request — no extra round-trips, no extra rate-limit cost.")
    print()
