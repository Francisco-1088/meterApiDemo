#!/usr/bin/env python3
"""
basic_query.py
==============
Demonstrates how to authenticate and perform simple queries against the
Meter GraphQL API.

AUTHENTICATION
--------------
The Meter API uses Bearer token authentication. Every request must include
an Authorization header:

    Authorization: Bearer YOUR_API_KEY

API keys are created in the Dashboard under:
    Settings > Integrations > API keys

Keys are scoped to the company where they were created and are displayed
only once at creation time. Each key can be revoked from the Dashboard
instantly.

REQUEST FORMAT
--------------
All API calls are HTTP POST requests to a single endpoint:

    https://api.meter.com/api/v1/graphql

The request body is a JSON object with a single `query` field containing
a GraphQL query string:

    { "query": "{ companyBySlug(slug: \"acme\") { name } }" }

RESPONSE FORMAT
---------------
Successful responses return HTTP 200 with a JSON body. The structure
mirrors the query — only the fields you asked for are returned:

    { "data": { "companyBySlug": { "name": "Acme Corp" } } }

GraphQL errors are returned inside an `errors` array (sometimes alongside
HTTP 200), with an `extensions.code` field describing the error type.
"""

import json
import requests
import config

# ── API configuration ──────────────────────────────────────────────────────────

API_URL      = config.API_URL
API_TOKEN    = config.API_TOKEN
COMPANY_SLUG = config.COMPANY_SLUG
NETWORK_UUID = config.NETWORK_UUID


# ── Authentication helper ──────────────────────────────────────────────────────

def make_headers(token: str) -> dict[str, str]:
    """
    Build the HTTP headers required for every Meter API request.

    The Authorization header uses the Bearer scheme. The token value is your
    API key exactly as shown in the Dashboard — do not add any extra encoding
    or prefix beyond "Bearer ".

    The Content-Type header tells the server the body is JSON-encoded GraphQL.

    Args:
        token: Your Meter API key (the full Bearer token string).

    Returns:
        Dict containing Content-Type and Authorization headers.

    Example:
        >>> headers = make_headers("v2.public.eyJ...")
        >>> headers["Authorization"]
        'Bearer v2.public.eyJ...'
    """
    return {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token}",
    }


# ── Core request function ──────────────────────────────────────────────────────

def run_query(
    query: str,
    token: str = API_TOKEN,
    url: str = API_URL,
) -> dict:
    """
    Execute a GraphQL query against the Meter API and return the parsed response.

    Sends an HTTP POST to the GraphQL endpoint with the query embedded in a
    JSON body. The Meter API always returns JSON — on success the `data` key
    holds your results; on error the `errors` array contains details.

    Rate-limit headers are present on every response:
        X-RateLimit-Remaining  — requests left in the current 60-second window
        X-RateLimit-Reset      — RFC 1123 timestamp when the window resets

    Args:
        query: A valid GraphQL query string.
               Example: '{ companyBySlug(slug: "acme") { name uuid } }'
        token: Bearer token for authentication. Defaults to config.API_TOKEN.
        url:   Meter GraphQL endpoint. Defaults to config.API_URL.

    Returns:
        Parsed JSON response dict. May contain:
            - "data":   dict with the requested fields on success
            - "errors": list of error objects on failure (or partial failure)

    Raises:
        requests.HTTPError:   On 4xx/5xx HTTP responses (401, 400, 422, 429).
        requests.Timeout:     If the server does not respond within 60 seconds.
        requests.ConnectionError: If the network is unreachable.
    """
    headers = make_headers(token)
    payload = {"query": query}
    response = requests.post(url, headers=headers, json=payload, timeout=60)
    response.raise_for_status()
    return response.json()


# ── Query functions ────────────────────────────────────────────────────────────

def get_company(slug: str) -> dict:
    """
    Fetch basic company information by its slug identifier.

    GraphQL query: companyBySlug(slug: String!) -> Company!

    Retrieves:
        uuid           — Unique company identifier
        slug           — URL-friendly identifier (e.g. "acme")
        name           — Display name of the company
        isCustomer     — Whether this is a paying customer
        websiteDomain  — Company's website domain

    Args:
        slug: The company's slug identifier (e.g. "meter", "acme-corp").

    Returns:
        API response dict. Access company data at result["data"]["companyBySlug"].

    Example:
        >>> result = get_company("meter")
        >>> company = result["data"]["companyBySlug"]
        >>> print(company["name"])
        Meter
    """
    query = f"""
    {{
      companyBySlug(slug: "{slug}") {{
        uuid
        slug
        name
        isCustomer
        websiteDomain
      }}
    }}
    """
    return run_query(query)


def get_networks(company_slug: str) -> dict:
    """
    List all networks associated with a company.

    GraphQL query: networksForCompany(companySlug: String!) -> [Network!]!

    Retrieves:
        UUID   — Unique network identifier (used as networkUUID in other queries)
        label  — Human-readable network name
        slug   — URL-friendly identifier

    Args:
        company_slug: The company's slug identifier.

    Returns:
        API response dict. Access network list at result["data"]["networksForCompany"].

    Example:
        >>> result = get_networks("meter")
        >>> for net in result["data"]["networksForCompany"]:
        ...     print(net["UUID"], net["label"])
    """
    query = f"""
    {{
      networksForCompany(companySlug: "{company_slug}") {{
        UUID
        label
        slug
      }}
    }}
    """
    return run_query(query)


def get_virtual_devices(network_uuid: str) -> dict:
    """
    List all virtual devices (switches, APs, controllers) on a network.

    GraphQL query: virtualDevicesForNetwork(networkUUID: UUID!) -> [VirtualDevice!]!

    A VirtualDevice is the logical representation of a physical device.
    It pairs with a HardwareDevice that holds serial number and connectivity state.

    Retrieves:
        UUID         — Virtual device UUID (used in device-specific queries)
        label        — Human-readable device name
        deviceType   — One of: ACCESS_POINT, SWITCH, CONTROLLER,
                       CELLULAR_GATEWAY, POWER_DISTRIBUTION_UNIT, OBSERVER
        deviceModel  — Hardware model string (e.g. "MS12")
        isOnline     — Whether the device is currently connected to Meter's backend

    Args:
        network_uuid: UUID of the network to query.

    Returns:
        API response dict. Access devices at result["data"]["virtualDevicesForNetwork"].
    """
    query = f"""
    {{
      virtualDevicesForNetwork(networkUUID: "{network_uuid}") {{
        UUID
        label
        deviceType
        deviceModel
        isOnline
      }}
    }}
    """
    return run_query(query)


def get_network_clients(network_uuid: str) -> dict:
    """
    Retrieve all currently active clients connected to a network.

    GraphQL query: networkClients(networkUUID: UUID!) -> [NetworkClient!]!

    Each NetworkClient represents a device connected to the network, either
    wired or wireless. The `lastSeen` timestamp indicates when the client was
    last observed by Meter hardware.

    Retrieves per client:
        macAddress      — Hardware MAC address (e.g. "AA:BB:CC:DD:EE:FF")
        ip              — Assigned IP address
        clientName      — Device hostname or alias if known
        isWireless      — True for Wi-Fi clients, False for wired
        signal          — Wi-Fi signal strength in dBm (null for wired)
        lastSeen        — ISO 8601 timestamp of last observed activity
        connectedVLAN   — VLAN the client is assigned to (name + vlanID)
        connectedSSID   — SSID the client is connected to (wireless only)

    Args:
        network_uuid: UUID of the network to query.

    Returns:
        API response dict. Access clients at result["data"]["networkClients"].
    """
    query = f"""
    {{
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
    }}
    """
    return run_query(query)


def get_hardware_device(serial_number: str) -> dict:
    """
    Look up a specific physical hardware device by its serial number.

    GraphQL query: hardwareDevice(serialNumber: String!) -> HardwareDevice!

    HardwareDevice represents the physical unit — its serial number, model,
    and real-time backend connectivity status. Use the virtualDeviceUUID field
    to link to its logical counterpart for configuration queries.

    Retrieves:
        serialNumber            — Device serial number
        deviceType              — Hardware type (ACCESS_POINT, SWITCH, etc.)
        deviceModel             — Model identifier
        isConnectedToBackend    — Whether the device is currently online
        macAddress              — Device MAC address
        networkUUID             — The network this device belongs to
        virtualDeviceUUID       — UUID of the paired VirtualDevice

    Args:
        serial_number: The device serial number printed on the hardware.

    Returns:
        API response dict. Access device at result["data"]["hardwareDevice"].
    """
    query = f"""
    {{
      hardwareDevice(serialNumber: "{serial_number}") {{
        serialNumber
        deviceType
        deviceModel
        isConnectedToBackend
        macAddress
        networkUUID
        virtualDeviceUUID
      }}
    }}
    """
    return run_query(query)


# ── Output helpers ─────────────────────────────────────────────────────────────

def print_section(title: str) -> None:
    """Print a formatted section header."""
    print(f"\n{'=' * 60}")
    print(f"  {title}")
    print(f"{'=' * 60}")


def print_result(data: dict) -> None:
    """Pretty-print a parsed API response."""
    print(json.dumps(data, indent=2, default=str))


def print_rate_limit_info(response_headers: dict) -> None:
    """
    Display the rate-limit headers returned by the API.

    The Meter API enforces 500 requests per minute per API key.
    Monitor these headers to avoid exceeding the limit.

    Args:
        response_headers: The HTTP response headers dict.
    """
    remaining = response_headers.get("X-RateLimit-Remaining", "N/A")
    reset_time = response_headers.get("X-RateLimit-Reset", "N/A")
    print(f"  Rate limit remaining : {remaining} requests")
    print(f"  Rate limit resets at : {reset_time}")


# ── Main ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("Meter API — Basic Query Demo")
    print(f"Endpoint : {API_URL}")
    print(f"Company  : {COMPANY_SLUG}")
    print(f"Network  : {NETWORK_UUID}")

    # ── Step 1: Authenticate by fetching company info ─────────────────────────
    # The simplest way to verify your token is valid is to query companyBySlug.
    # A 401 response means your API_TOKEN in config.py is wrong or revoked.
    print_section("Step 1 — Verify Authentication (companyBySlug)")
    print(f"  Querying company '{COMPANY_SLUG}'...")

    try:
        result = get_company(COMPANY_SLUG)
        company = result.get("data", {}).get("companyBySlug", {})
        print(f"  ✓ Authenticated successfully")
        print(f"  Company name : {company.get('name')}")
        print(f"  UUID         : {company.get('uuid')}")
        print(f"  Is customer  : {company.get('isCustomer')}")
        print(f"  Website      : {company.get('websiteDomain')}")
    except requests.HTTPError as e:
        print(f"  ✗ HTTP {e.response.status_code} — check your API_TOKEN in config.py")
        raise SystemExit(1)

    # ── Step 2: Discover networks ─────────────────────────────────────────────
    print_section("Step 2 — List Networks (networksForCompany)")

    result = get_networks(COMPANY_SLUG)
    networks = result.get("data", {}).get("networksForCompany", [])
    print(f"  Found {len(networks)} network(s):")
    for net in networks:
        print(f"    • {net.get('label'):<30} UUID: {net.get('UUID')}  slug: {net.get('slug')}")

    # ── Step 3: List virtual devices ──────────────────────────────────────────
    print_section("Step 3 — List Virtual Devices (virtualDevicesForNetwork)")

    result = get_virtual_devices(NETWORK_UUID)
    devices = result.get("data", {}).get("virtualDevicesForNetwork", [])
    print(f"  Found {len(devices)} device(s) on network {NETWORK_UUID}:")
    for dev in devices:
        status = "online" if dev.get("isOnline") else "offline"
        print(f"    • [{status:>7}] {dev.get('label'):<25} {dev.get('deviceType'):<20} {dev.get('deviceModel')}")

    # ── Step 4: List active clients ───────────────────────────────────────────
    print_section("Step 4 — List Network Clients (networkClients)")

    result = get_network_clients(NETWORK_UUID)
    clients = result.get("data", {}).get("networkClients", [])
    wireless = [c for c in clients if c.get("isWireless")]
    wired    = [c for c in clients if not c.get("isWireless")]
    print(f"  Total clients  : {len(clients)}")
    print(f"  Wireless       : {len(wireless)}")
    print(f"  Wired          : {len(wired)}")

    if clients:
        print(f"\n  First 5 clients:")
        for client in clients[:5]:
            conn_type = "Wi-Fi" if client.get("isWireless") else "Wired"
            name = client.get("clientName") or client.get("macAddress")
            ip   = client.get("ip") or "—"
            print(f"    • {name:<30} {ip:<18} {conn_type}")

    # ── Step 5: Full raw response example ────────────────────────────────────
    print_section("Step 5 — Full Raw Response (companyBySlug)")
    print("  Raw JSON response from the API:\n")
    print_result(get_company(COMPANY_SLUG))

    print("\nDone.")
