#!/usr/bin/env python3
"""
meter_sdk.py
============
Python SDK for the Meter GraphQL Public API.

Covers the complete public schema:
  • All scalar types       (12 scalars)
  • All enum types         (10 enums, 57 total values)
  • All input types        (12 input types)
  • All 43 query methods   (full argument documentation)
  • Typed error handling   (custom exceptions per error category)
  • Rate-limit utilities   (Retry-After parser, proactive monitor)

QUICK START
-----------
    from meter_sdk import MeterClient

    client = MeterClient(token="YOUR_API_TOKEN")

    # Fetch company info
    company = client.get_company(slug="acme")

    # Fetch active clients
    clients = client.get_network_clients(network_uuid="018a3e00-...")

    # Fetch uplink metrics (last 4 hours, 5-minute buckets)
    from meter_sdk import MetricsFilter
    metrics = client.get_networks_uplink_qualities(
        network_uuids=["018a3e00-..."],
        filter=MetricsFilter(duration_seconds=14400, step_seconds=300),
    )

API REFERENCE
-------------
Full schema documentation:
    https://docs.meter.com/reference/api/schema/queries
    https://docs.meter.com/reference/api/schema/types
    https://docs.meter.com/reference/api/schema/enums
    https://docs.meter.com/reference/api/schema/inputs
    https://docs.meter.com/reference/api/schema/scalars
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from enum import Enum
from typing import Optional

import requests


# ══════════════════════════════════════════════════════════════════════════════
# SCALARS
# ══════════════════════════════════════════════════════════════════════════════
"""
Meter API scalar types.

Scalars are the leaf value types in GraphQL. The Meter API uses both
built-in GraphQL scalars and custom scalars:

Built-in scalars:
  Boolean   — True or False
  Float     — Signed double-precision floating-point
  ID        — Unique identifier (opaque string)
  Int       — Signed 32-bit integer
  String    — UTF-8 text

Custom scalars:
  DateTime  — RFC 3339 date/time string, e.g. "2026-03-07T15:30:00Z"
  IP        — IPv4 or IPv6 address string, e.g. "192.168.1.1"
  IPV4      — IPv4 address string, e.g. "10.0.0.1"
  IPV6      — IPv6 address string, e.g. "2001:db8::1"
  JSONObject — Arbitrary JSON object (returned as a Python dict)
  MacAddress — Colon-separated hex MAC, e.g. "AA:BB:CC:DD:EE:FF"
  UUID      — Standard UUID string, e.g. "550e8400-e29b-41d4-a716-446655440000"
"""

# Python type aliases for Meter scalar types
DateTime   = str   # RFC 3339 format: "2026-03-07T15:30:00Z"
IP         = str   # IPv4 or IPv6 address string
IPV4       = str   # IPv4 address string
IPV6       = str   # IPv6 address string
JSONObject = dict  # Arbitrary JSON object
MacAddress = str   # e.g. "AA:BB:CC:DD:EE:FF"
UUID       = str   # e.g. "550e8400-e29b-41d4-a716-446655440000"


# ══════════════════════════════════════════════════════════════════════════════
# ENUMS
# ══════════════════════════════════════════════════════════════════════════════

class AlertTargetType(str, Enum):
    """
    Destination type for alert receivers.

    Used in: AlertTarget.type, AlertTargetEmail, AlertTargetSlack,
             AlertTargetSalesforce, AlertTargetWebhook
    """
    EMAIL      = "EMAIL"
    SALESFORCE = "SALESFORCE"
    SLACK      = "SLACK"
    WEBHOOK    = "WEBHOOK"


class ClientAssignmentProtocol(str, Enum):
    """
    IP address assignment protocol for a network client or VLAN.

    Used in: VLAN.ipv4ClientAssignmentProtocol,
             VLAN.ipv6ClientAssignmentProtocol
    """
    DHCP   = "DHCP"
    STATIC = "STATIC"


class DeviceType(str, Enum):
    """
    Physical hardware device type.

    Used in: HardwareDevice.deviceType, HardwareDevicesFilter.deviceType
    Note: VirtualDevice uses VirtualDeviceType which includes OBSERVER.
    """
    ACCESS_POINT          = "ACCESS_POINT"
    CELLULAR_GATEWAY      = "CELLULAR_GATEWAY"
    CONTROLLER            = "CONTROLLER"
    POWER_DISTRIBUTION_UNIT = "POWER_DISTRIBUTION_UNIT"
    SWITCH                = "SWITCH"


class EventType(str, Enum):
    """
    Network event type for the event log.

    Used in: EventLogEvent.eventType, recentEventLogEventsPage.typeFilter
    Filter queries to specific event types using typeFilter=[EventType.WAN_DOWN].
    """
    ACCESS_POINT_CONNECTION              = "ACCESS_POINT_CONNECTION"
    ACCESS_POINT_DFS_CHANNEL_RESET       = "ACCESS_POINT_DFS_CHANNEL_RESET"
    ACCESS_POINT_INVALID_VLAN_CONNECTION = "ACCESS_POINT_INVALID_VLAN_CONNECTION"
    ACCESS_POINT_RADAR_HIT_DETECTED      = "ACCESS_POINT_RADAR_HIT_DETECTED"
    CALIBRATION_JOB_CHANNEL_RESET_FAILED = "CALIBRATION_JOB_CHANNEL_RESET_FAILED"
    CALIBRATION_JOB_COMPLETED            = "CALIBRATION_JOB_COMPLETED"
    COS_HA_FAILOVER                      = "COS_HA_FAILOVER"
    COS_HA_MULTIPLE_ACTIVE               = "COS_HA_MULTIPLE_ACTIVE"
    COS_HA_RECOVERY                      = "COS_HA_RECOVERY"
    DEVICE_BOOT                          = "DEVICE_BOOT"
    DEVICE_OFFLINE                       = "DEVICE_OFFLINE"
    DEVICE_ONLINE                        = "DEVICE_ONLINE"
    FIRMWARE_UPGRADE_COMPLETED           = "FIRMWARE_UPGRADE_COMPLETED"
    FIRMWARE_UPGRADE_PENDING             = "FIRMWARE_UPGRADE_PENDING"
    FIRMWARE_UPGRADE_SCHEDULED           = "FIRMWARE_UPGRADE_SCHEDULED"
    HONEYPOT_ACCESS_POINT_DETECTED       = "HONEYPOT_ACCESS_POINT_DETECTED"
    HONEYPOT_ACCESS_POINT_NO_LONGER_DETECTED = "HONEYPOT_ACCESS_POINT_NO_LONGER_DETECTED"
    IPSEC_TUNNEL_STATUS_CHANGE           = "IPSEC_TUNNEL_STATUS_CHANGE"
    MANAGEMENT_CONNECTION_STATUS_CHANGED = "MANAGEMENT_CONNECTION_STATUS_CHANGED"
    PDU_OUTLET_STATUS_CHANGED            = "PDU_OUTLET_STATUS_CHANGED"
    PORT_BLOCKED                         = "PORT_BLOCKED"
    PORT_UNBLOCKED                       = "PORT_UNBLOCKED"
    ROGUE_ACCESS_POINT_DETECTED          = "ROGUE_ACCESS_POINT_DETECTED"
    ROGUE_ACCESS_POINT_NO_LONGER_DETECTED = "ROGUE_ACCESS_POINT_NO_LONGER_DETECTED"
    STP_ERROR_PORT_BLOCKED               = "STP_ERROR_PORT_BLOCKED"
    STP_ERROR_PORT_UNBLOCKED             = "STP_ERROR_PORT_UNBLOCKED"
    UNSPECIFIED                          = "UNSPECIFIED"
    WAN_DOWN                             = "WAN_DOWN"
    WAN_STATUS_CHANGE                    = "WAN_STATUS_CHANGE"
    WAN_UP                               = "WAN_UP"


class NetworkClientHWMode(str, Enum):
    """
    Wi-Fi hardware mode (802.11 generation) of a wireless client.

    Used in: NetworkClient.hwMode, ClientMetricsTimeseriesValue.hwMode
    """
    NA     = "NA"      # Not applicable (wired client)
    WIFI_2 = "WIFI_2"  # 802.11a/b/g
    WIFI_3 = "WIFI_3"  # 802.11n (Wi-Fi 4 predecessor naming)
    WIFI_4 = "WIFI_4"  # 802.11n
    WIFI_5 = "WIFI_5"  # 802.11ac
    WIFI_6 = "WIFI_6"  # 802.11ax
    WIFI_7 = "WIFI_7"  # 802.11be


class RadioBand(str, Enum):
    """
    Wi-Fi radio frequency band.

    Used in: BSSID.radioBand, NetworkClient.radioBand,
             channelUtilizationByAP.band, wirelessClientMetrics filters
    """
    BAND_2_4G = "BAND_2_4G"  # 2.4 GHz
    BAND_5G   = "BAND_5G"    # 5 GHz
    BAND_6G   = "BAND_6G"    # 6 GHz (Wi-Fi 6E / Wi-Fi 7)


class SSIDEncryptionProtocol(str, Enum):
    """
    Wi-Fi encryption and authentication protocol for an SSID.

    Used in: SSID.encryptionProtocol
    """
    OPEN_MAC_AUTH_RADIUS = "OPEN_MAC_AUTH_RADIUS"  # Open with MAC-based RADIUS
    WPA2                 = "WPA2"                  # WPA2-Personal (PSK)
    WPA2_ENTERPRISE      = "WPA2_ENTERPRISE"        # WPA2 with 802.1X RADIUS
    WPA2_IPSK            = "WPA2_IPSK"              # WPA2 with identity PSK
    WPA2_MPSK            = "WPA2_MPSK"              # WPA2 with multiple PSKs
    WPA3                 = "WPA3"                   # WPA3-Personal (SAE)
    WPA3_ENTERPRISE      = "WPA3_ENTERPRISE"         # WPA3 with 802.1X RADIUS
    WPA3_OWE             = "WPA3_OWE"               # WPA3 Opportunistic Wireless Encryption
    WPA3_TRANSITION      = "WPA3_TRANSITION"         # WPA2/WPA3 mixed mode


class TrafficDirection(str, Enum):
    """
    Direction of network traffic.

    Used in: NetworkUplinkThroughputMetricsValue.direction
    """
    RX = "RX"  # Receive (download / ingress)
    TX = "TX"  # Transmit (upload / egress)


class VirtualDeviceType(str, Enum):
    """
    Logical device type for a VirtualDevice.

    Used in: VirtualDevice.deviceType, DevicesForNetworkFilter.deviceType
    Superset of DeviceType — includes OBSERVER (a monitoring-only device).
    """
    ACCESS_POINT            = "ACCESS_POINT"
    CELLULAR_GATEWAY        = "CELLULAR_GATEWAY"
    CONTROLLER              = "CONTROLLER"
    OBSERVER                = "OBSERVER"
    POWER_DISTRIBUTION_UNIT = "POWER_DISTRIBUTION_UNIT"
    SWITCH                  = "SWITCH"


class WirelessClientConnectionEventType(str, Enum):
    """
    Wireless client connection lifecycle event type.

    Used in: ClientMetricsTimeseriesFilterInput.eventType / eventTypes
    Filter wireless metrics to specific connection events.
    """
    ARP_FAILED    = "ARP_FAILED"
    ARP_LATE      = "ARP_LATE"
    CONNECTED     = "CONNECTED"
    DHCP_FAILED   = "DHCP_FAILED"
    DHCP_OK       = "DHCP_OK"
    DISASSOCIATED = "DISASSOCIATED"
    FAILED        = "FAILED"


# ══════════════════════════════════════════════════════════════════════════════
# INPUT TYPES
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class MetricsFilter:
    """
    Time-range filter for all metrics and time-series queries.

    Required for: activeClients, networkUplinkQuality, networkUplinkThroughput,
                  networksUplinkQualities, channelUtilizationByAP/ByClient/ByNetwork,
                  wirelessClientMetrics, switchPortMetricsRate, controllerPortMetricsRate,
                  controllerDNSRequestRates, apHealthScores, pduMetrics, pdusMetrics

    Fields:
        duration_seconds  (required) — How far back to look. Common values:
                                        3600   = 1 hour
                                        14400  = 4 hours (recommended for most queries)
                                        86400  = 24 hours
        step_seconds      (required) — Aggregation bucket size. Common values:
                                        60   = 1-minute buckets
                                        300  = 5-minute buckets (recommended)
                                        3600 = 1-hour buckets
        end_time          (optional) — RFC 3339 end timestamp.
                                        Defaults to now when omitted.

    Example:
        MetricsFilter(duration_seconds=14400, step_seconds=300)
        # → last 4 hours in 5-minute buckets
    """
    duration_seconds: int
    step_seconds: int
    end_time: Optional[DateTime] = None

    def to_gql(self) -> str:
        """Render as a GraphQL inline input object literal."""
        parts = [
            f"durationSeconds: {self.duration_seconds}",
            f"stepSeconds: {self.step_seconds}",
        ]
        if self.end_time:
            parts.append(f'endTime: "{self.end_time}"')
        return "{ " + ", ".join(parts) + " }"


@dataclass
class ActiveClientsInput:
    """
    Optional configuration for activeClients queries.

    Fields:
        include_meter_hardware (optional) — Include Meter hardware devices
                                            (switches, APs) in the client count.
                                            Default: False.
    """
    include_meter_hardware: Optional[bool] = None

    def to_gql(self) -> str:
        if self.include_meter_hardware is None:
            return "{}"
        val = "true" if self.include_meter_hardware else "false"
        return f"{{ includeMeterHardware: {val} }}"


@dataclass
class IPRangeInput:
    """
    IPv4 or IPv6 address range filter.

    Used in: NetworkClientsFilter.ip_range

    Fields:
        start (required) — First IP address in the range (inclusive).
        end   (required) — Last IP address in the range (inclusive).

    Example:
        IPRangeInput(start="192.168.1.1", end="192.168.1.254")
    """
    start: IP
    end: IP

    def to_gql(self) -> str:
        return f'{{ start: "{self.start}", end: "{self.end}" }}'


@dataclass
class NetworkClientsFilter:
    """
    Filter criteria for networkClients and networksClients queries.

    Required fields (must all be provided together if filter is passed):
        exclude_meter_hardware  — Exclude Meter infrastructure from results.
        include_latency         — Include latency measurements per client.
        include_throughput      — Include throughput measurements per client.
        lookback_minutes        — How many minutes of history to consider
                                  when determining if a client is "active".
                                  Common value: 5 (last 5 minutes).

    Optional fields:
        ap_serial_number   — Filter to clients connected to a specific AP.
        ip_range           — Filter to clients within an IP address range.
        mac_address        — Filter to a specific client MAC address.
        ssid               — Filter to clients on a specific SSID name.
        timestamp          — Filter to clients active at a specific time.
        vlan_id            — Filter to clients on a specific VLAN ID.

    Example:
        NetworkClientsFilter(
            exclude_meter_hardware=True,
            include_latency=False,
            include_throughput=False,
            lookback_minutes=5,
        )
    """
    exclude_meter_hardware: bool
    include_latency: bool
    include_throughput: bool
    lookback_minutes: int
    ap_serial_number: Optional[str]      = None
    ip_range: Optional[IPRangeInput]     = None
    mac_address: Optional[MacAddress]    = None
    ssid: Optional[str]                  = None
    timestamp: Optional[DateTime]        = None
    vlan_id: Optional[int]               = None

    def to_gql(self) -> str:
        em = "true" if self.exclude_meter_hardware else "false"
        il = "true" if self.include_latency else "false"
        it = "true" if self.include_throughput else "false"
        parts = [
            f"excludeMeterHardware: {em}",
            f"includeLatency: {il}",
            f"includeThroughput: {it}",
            f"lookbackMinutes: {self.lookback_minutes}",
        ]
        if self.ap_serial_number:
            parts.append(f'apSerialNumber: "{self.ap_serial_number}"')
        if self.ip_range:
            parts.append(f"ipRange: {self.ip_range.to_gql()}")
        if self.mac_address:
            parts.append(f'macAddress: "{self.mac_address}"')
        if self.ssid:
            parts.append(f'ssid: "{self.ssid}"')
        if self.timestamp:
            parts.append(f'timestamp: "{self.timestamp}"')
        if self.vlan_id is not None:
            parts.append(f"vlanID: {self.vlan_id}")
        return "{ " + ", ".join(parts) + " }"


@dataclass
class DevicesForNetworkFilter:
    """
    Filter virtual devices by type when listing a network's devices.

    Used in: virtualDevicesForNetwork.filter

    Fields:
        device_type (optional) — Restrict to a single VirtualDeviceType.

    Example:
        DevicesForNetworkFilter(device_type=VirtualDeviceType.SWITCH)
    """
    device_type: Optional[VirtualDeviceType] = None

    def to_gql(self) -> str:
        if self.device_type is None:
            return "{}"
        return f"{{ deviceType: {self.device_type.value} }}"


@dataclass
class HardwareDevicesFilter:
    """
    Filter criteria for spareHardwareDevicesForNetwork.

    Fields:
        device_model (optional) — Hardware model string (e.g. "MS12").
        device_type  (optional) — Physical device type (DeviceType enum).
        limit        (optional) — Maximum number of results to return.
        offset       (optional) — Pagination offset.
    """
    device_model: Optional[str]        = None
    device_type: Optional[DeviceType]  = None
    limit: Optional[int]               = None
    offset: Optional[int]              = None

    def to_gql(self) -> str:
        parts = []
        if self.device_model:
            parts.append(f'deviceModel: "{self.device_model}"')
        if self.device_type:
            parts.append(f"deviceType: {self.device_type.value}")
        if self.limit is not None:
            parts.append(f"limit: {self.limit}")
        if self.offset is not None:
            parts.append(f"offset: {self.offset}")
        return ("{ " + ", ".join(parts) + " }") if parts else "{}"


@dataclass
class NumberRangeInput:
    """
    Integer range filter (min/max).

    Used in: ClientMetricsTimeseriesFilterInput.rssi

    Fields:
        min (optional) — Minimum value (inclusive).
        max (optional) — Maximum value (inclusive).

    Example (filter clients with signal between -70 and -40 dBm):
        NumberRangeInput(min=-70, max=-40)
    """
    min: Optional[int] = None
    max: Optional[int] = None

    def to_gql(self) -> str:
        parts = []
        if self.min is not None:
            parts.append(f"min: {self.min}")
        if self.max is not None:
            parts.append(f"max: {self.max}")
        return ("{ " + ", ".join(parts) + " }") if parts else "{}"


@dataclass
class ClientMetricsFilter:
    """
    Filter for wireless client metrics time-series queries.

    Required:
        time_filter — MetricsFilter specifying the time range and resolution.

    Optional filters (all can be combined):
        bands                      — Restrict to specific radio bands.
        channels                   — Restrict to specific Wi-Fi channels.
        client_mac_addresses        — Restrict to specific client MAC addresses.
        event_type                  — Single WirelessClientConnectionEventType.
        event_types                 — Multiple WirelessClientConnectionEventTypes.
        exclude_observers           — Exclude observer-mode devices.
        partial_client_mac_addresses — Substring-match MAC addresses.
        rssi                        — Signal strength range filter (dBm).
        ssid_uuids                  — Restrict to specific SSIDs by UUID.
        virtual_device_uuids        — Restrict to specific APs by UUID.

    Used in: wirelessClientMetrics, wirelessClientMetricsByAP,
             wirelessClientMetricsByClient, channelUtilizationByClient

    Example:
        ClientMetricsFilter(
            time_filter=MetricsFilter(duration_seconds=3600, step_seconds=300),
            bands=[RadioBand.BAND_5G],
            rssi=NumberRangeInput(min=-70),
        )
    """
    time_filter: MetricsFilter
    bands: Optional[list[RadioBand]]                               = None
    channels: Optional[list[int]]                                  = None
    client_mac_addresses: Optional[list[MacAddress]]               = None
    event_type: Optional[WirelessClientConnectionEventType]        = None
    event_types: Optional[list[WirelessClientConnectionEventType]] = None
    exclude_observers: Optional[bool]                              = None
    partial_client_mac_addresses: Optional[list[str]]              = None
    rssi: Optional[NumberRangeInput]                               = None
    ssid_uuids: Optional[list[UUID]]                               = None
    virtual_device_uuids: Optional[list[UUID]]                     = None

    def to_gql(self) -> str:
        parts = [f"timeFilter: {self.time_filter.to_gql()}"]
        if self.bands:
            vals = ", ".join(b.value for b in self.bands)
            parts.append(f"bands: [{vals}]")
        if self.channels:
            parts.append(f"channels: [{', '.join(str(c) for c in self.channels)}]")
        if self.client_mac_addresses:
            macs = ", ".join(f'"{m}"' for m in self.client_mac_addresses)
            parts.append(f"clientMacAddresses: [{macs}]")
        if self.event_type:
            parts.append(f"eventType: {self.event_type.value}")
        if self.event_types:
            vals = ", ".join(e.value for e in self.event_types)
            parts.append(f"eventTypes: [{vals}]")
        if self.exclude_observers is not None:
            parts.append(f"excludeObservers: {'true' if self.exclude_observers else 'false'}")
        if self.rssi:
            parts.append(f"rssi: {self.rssi.to_gql()}")
        if self.ssid_uuids:
            uuids = ", ".join(f'"{u}"' for u in self.ssid_uuids)
            parts.append(f"ssidUUIDs: [{uuids}]")
        if self.virtual_device_uuids:
            uuids = ", ".join(f'"{u}"' for u in self.virtual_device_uuids)
            parts.append(f"virtualDeviceUUIDs: [{uuids}]")
        return "{ " + ", ".join(parts) + " }"


@dataclass
class AllClientMetricsFilter:
    """
    Filter for allClientMetricsByClient and allClientMetricsByVLAN queries.

    Required:
        time_filter — MetricsFilter specifying the time range and resolution.
    """
    time_filter: MetricsFilter

    def to_gql(self) -> str:
        return f"{{ timeFilter: {self.time_filter.to_gql()} }}"


@dataclass
class ChannelUtilizationFilter:
    """
    Filter for channel utilization time-series queries.

    Required:
        time_filter — MetricsFilter specifying the time range and resolution.

    Used in: channelUtilizationByAP, channelUtilizationByClient,
             channelUtilizationByNetwork
    """
    time_filter: MetricsFilter

    def to_gql(self) -> str:
        return f"{{ timeFilter: {self.time_filter.to_gql()} }}"


@dataclass
class CompanyNetworksFilter:
    """
    Filter networks when listing all networks for a company.

    Used in: networksForCompany.filter

    Fields:
        network_uuids (optional) — Restrict to specific network UUIDs.
    """
    network_uuids: Optional[list[UUID]] = None

    def to_gql(self) -> str:
        if not self.network_uuids:
            return "{}"
        uuids = ", ".join(f'"{u}"' for u in self.network_uuids)
        return f"{{ networkUUIDs: [{uuids}] }}"


@dataclass
class SSIDFilter:
    """
    Filter SSIDs when listing a network's wireless networks.

    Used in: ssidsForNetwork.filter

    Fields:
        is_guest  (optional) — True to return only guest SSIDs.
        is_hidden (optional) — True to return only hidden SSIDs.
        ssid      (optional) — Exact SSID name string match.
    """
    is_guest: Optional[bool]  = None
    is_hidden: Optional[bool] = None
    ssid: Optional[str]       = None

    def to_gql(self) -> str:
        parts = []
        if self.is_guest is not None:
            parts.append(f"isGuest: {'true' if self.is_guest else 'false'}")
        if self.is_hidden is not None:
            parts.append(f"isHidden: {'true' if self.is_hidden else 'false'}")
        if self.ssid:
            parts.append(f'ssid: "{self.ssid}"')
        return ("{ " + ", ".join(parts) + " }") if parts else "{}"


# ══════════════════════════════════════════════════════════════════════════════
# EXCEPTIONS
# ══════════════════════════════════════════════════════════════════════════════

class MeterAPIError(Exception):
    """
    Base exception for all Meter API errors.

    All SDK-level exceptions derive from this class, allowing callers to
    catch all API errors with a single `except MeterAPIError` block.
    """


class MeterAuthError(MeterAPIError):
    """
    HTTP 401 Unauthorized.

    The API key is missing, invalid, expired, or revoked.

    Resolution:
        • Verify API_TOKEN is set correctly.
        • Check the Dashboard — the key may have been revoked.
        • Generate a new key under Settings → Integrations → API keys.
    """


class MeterRateLimitError(MeterAPIError):
    """
    HTTP 429 Too Many Requests.

    The 500 requests-per-minute limit has been exceeded.

    Attributes:
        retry_after_dt — datetime when the window resets (from Retry-After header).
        retry_after_str — Raw Retry-After header value string.
        remaining       — X-RateLimit-Remaining at time of 429.
        reset_str       — X-RateLimit-Reset header value string.

    Resolution:
        • Sleep until retry_after_dt before retrying.
        • Bundle multiple queries to reduce request count.
        • Monitor X-RateLimit-Remaining proactively.
    """
    def __init__(
        self,
        retry_after_str: Optional[str] = None,
        remaining: Optional[str] = None,
        reset_str: Optional[str] = None,
    ):
        self.retry_after_str = retry_after_str
        self.remaining       = remaining
        self.reset_str       = reset_str
        self.retry_after_dt  = _parse_rfc1123(retry_after_str)
        msg = f"Rate limit exceeded. Retry after: {retry_after_str or 'unknown'}"
        super().__init__(msg)

    def seconds_to_wait(self) -> float:
        """Return the number of seconds to sleep before retrying."""
        return _seconds_until(self.retry_after_dt)


class MeterValidationError(MeterAPIError):
    """
    HTTP 400 or 422 — Request body or GraphQL query is invalid.

    Raised for:
        HTTP 400 — Request body is not valid JSON.
        HTTP 422 — GraphQL query references a non-existent field or is empty.
                   extension code: GRAPHQL_VALIDATION_FAILED

    Attributes:
        gql_errors — List of GraphQL error objects from the response body.

    Resolution:
        • Check field names against the schema documentation.
        • Ensure the query string is non-empty.
        • Use json.dumps() when serialising the payload.
    """
    def __init__(self, message: str, gql_errors: Optional[list] = None):
        self.gql_errors = gql_errors or []
        super().__init__(message)


class MeterAccessDeniedError(MeterAPIError):
    """
    HTTP 200 with GraphQL extension code UNAUTHORIZED.

    The token is valid but the queried resource is outside this key's scope:
        • UUID belongs to a different company.
        • Feature not enabled for this account.

    Distinct from MeterAuthError (HTTP 401) which means the token itself is bad.

    Attributes:
        gql_errors — List of GraphQL error objects.
    """
    def __init__(self, gql_errors: Optional[list] = None):
        self.gql_errors = gql_errors or []
        super().__init__(
            "Access denied: resource is outside this API key's scope."
        )


class MeterGraphQLError(MeterAPIError):
    """
    HTTP 200 with a non-empty GraphQL errors array (not UNAUTHORIZED).

    Raised when the server returns HTTP 200 but includes errors in the
    response body alongside (possibly partial) data.

    Attributes:
        gql_errors — List of GraphQL error objects.
        data       — Partial data dict, if any was returned alongside errors.
    """
    def __init__(self, gql_errors: list, data: Optional[dict] = None):
        self.gql_errors = gql_errors
        self.data       = data
        messages = "; ".join(e.get("message", "") for e in gql_errors)
        super().__init__(f"GraphQL errors: {messages}")


# ══════════════════════════════════════════════════════════════════════════════
# RATE-LIMIT UTILITIES
# ══════════════════════════════════════════════════════════════════════════════

def _parse_rfc1123(value: Optional[str]) -> Optional[datetime]:
    """
    Parse an RFC 1123 date string into a timezone-aware datetime.

    The Meter API uses RFC 1123 format for rate-limit headers:
        "Fri, 07 Mar 2026 12:01:00 GMT"

    Args:
        value: RFC 1123 string or None.

    Returns:
        timezone-aware datetime, or None if input is None or unparseable.
    """
    if not value:
        return None
    try:
        return parsedate_to_datetime(value)
    except Exception:
        return None


def _seconds_until(dt: Optional[datetime]) -> float:
    """
    Seconds from now until the given datetime, clamped to 0.

    Args:
        dt: timezone-aware datetime or None.

    Returns:
        Non-negative float seconds. 0.0 if dt is None or in the past.
    """
    if dt is None:
        return 0.0
    return max(0.0, (dt - datetime.now(timezone.utc)).total_seconds())


@dataclass
class RateLimitState:
    """
    Current rate-limit window state parsed from response headers.

    Attributes:
        remaining    — Requests remaining in the current 60-second window.
        reset_at     — datetime when the window resets (from X-RateLimit-Reset).
        reset_str    — Raw X-RateLimit-Reset header value.
        retry_after  — datetime from Retry-After header (present only on 429).
    """
    remaining: Optional[int]      = None
    reset_at: Optional[datetime]  = None
    reset_str: Optional[str]      = None
    retry_after: Optional[datetime] = None

    @classmethod
    def from_headers(cls, headers: dict) -> "RateLimitState":
        """
        Parse rate-limit state from a response headers dict.

        Args:
            headers: HTTP response headers from the Meter API.

        Returns:
            RateLimitState populated from the available headers.
        """
        remaining_str = headers.get("X-RateLimit-Remaining")
        reset_str     = headers.get("X-RateLimit-Reset")
        retry_str     = headers.get("Retry-After")

        remaining = None
        if remaining_str is not None:
            try:
                remaining = int(remaining_str)
            except ValueError:
                pass

        return cls(
            remaining=remaining,
            reset_at=_parse_rfc1123(reset_str),
            reset_str=reset_str,
            retry_after=_parse_rfc1123(retry_str),
        )

    def seconds_until_reset(self) -> float:
        """Seconds until the rate-limit window resets."""
        return _seconds_until(self.reset_at)

    def seconds_until_retry(self) -> float:
        """Seconds until the Retry-After time (0 if not set)."""
        return _seconds_until(self.retry_after)

    def is_exhausted(self) -> bool:
        """True if the remaining count is 0."""
        return self.remaining is not None and self.remaining == 0


# ══════════════════════════════════════════════════════════════════════════════
# METER CLIENT
# ══════════════════════════════════════════════════════════════════════════════

DEFAULT_API_URL = "https://api.meter.com/api/v1/graphql"


class MeterClient:
    """
    Synchronous client for the Meter GraphQL Public API.

    Provides one method per API query. All methods return the raw `data` dict
    from the API response. Raises typed exceptions for all documented errors.

    Usage:
        client = MeterClient(token="YOUR_API_KEY")
        company = client.get_company(slug="acme")
        clients = client.get_network_clients(network_uuid="018a...")

    Error handling:
        try:
            data = client.get_network_clients(network_uuid="...")
        except MeterAuthError:
            # Invalid or revoked API key
        except MeterRateLimitError as e:
            time.sleep(e.seconds_to_wait())
            # retry
        except MeterValidationError as e:
            # Bad query or field name
        except MeterAccessDeniedError:
            # UUID not accessible to this key
        except MeterAPIError:
            # Catch-all for other API errors

    Attributes:
        token        — Bearer token used for all requests.
        api_url      — GraphQL endpoint URL.
        rate_limit   — RateLimitState updated from the last response headers.
        timeout      — HTTP request timeout in seconds (default: 60).
    """

    def __init__(
        self,
        token: str,
        api_url: str = DEFAULT_API_URL,
        timeout: int = 60,
    ):
        self.token      = token
        self.api_url    = api_url
        self.timeout    = timeout
        self.rate_limit = RateLimitState()

    # ── Internal request engine ──────────────────────────────────────────────

    def _headers(self) -> dict[str, str]:
        return {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.token}",
        }

    def _execute(self, query: str) -> dict:
        """
        Execute a GraphQL query string and return the `data` dict.

        Parses all documented error types and raises typed exceptions.
        Updates self.rate_limit from response headers on every call.

        Args:
            query: GraphQL query string.

        Returns:
            The `data` dict from a successful response.

        Raises:
            MeterAuthError:        HTTP 401
            MeterRateLimitError:   HTTP 429
            MeterValidationError:  HTTP 400 or 422
            MeterAccessDeniedError: HTTP 200 + UNAUTHORIZED
            MeterGraphQLError:     HTTP 200 + other GraphQL errors
            requests.ConnectionError, requests.Timeout: Network errors
        """
        try:
            response = requests.post(
                self.api_url,
                headers=self._headers(),
                json={"query": query},
                timeout=self.timeout,
            )
        except (requests.Timeout, requests.ConnectionError):
            raise

        # Update rate-limit state from every response
        self.rate_limit = RateLimitState.from_headers(dict(response.headers))

        status = response.status_code

        # ── HTTP 401 ──────────────────────────────────────────────────────
        if status == 401:
            raise MeterAuthError(
                "API key is invalid, expired, or revoked (HTTP 401)."
            )

        # ── HTTP 429 ──────────────────────────────────────────────────────
        if status == 429:
            hdrs = dict(response.headers)
            raise MeterRateLimitError(
                retry_after_str=hdrs.get("Retry-After"),
                remaining=hdrs.get("X-RateLimit-Remaining"),
                reset_str=hdrs.get("X-RateLimit-Reset"),
            )

        # ── HTTP 400 ──────────────────────────────────────────────────────
        if status == 400:
            try:
                gql_errors = response.json().get("errors", [])
            except Exception:
                gql_errors = []
            raise MeterValidationError(
                f"Malformed JSON in request body (HTTP 400).",
                gql_errors=gql_errors,
            )

        # ── HTTP 422 ──────────────────────────────────────────────────────
        if status == 422:
            try:
                gql_errors = response.json().get("errors", [])
            except Exception:
                gql_errors = []
            messages = "; ".join(e.get("message", "") for e in gql_errors)
            raise MeterValidationError(
                f"GraphQL validation failed (HTTP 422): {messages}",
                gql_errors=gql_errors,
            )

        # ── Unexpected non-200 ────────────────────────────────────────────
        if status != 200:
            response.raise_for_status()

        # ── HTTP 200 — check for GraphQL-level errors ─────────────────────
        body = response.json()
        gql_errors = body.get("errors", [])

        if gql_errors:
            codes = {e.get("extensions", {}).get("code", "") for e in gql_errors}
            if "UNAUTHORIZED" in codes:
                raise MeterAccessDeniedError(gql_errors=gql_errors)
            raise MeterGraphQLError(gql_errors=gql_errors, data=body.get("data"))

        return body.get("data", {})

    # ── Company / Network queries ────────────────────────────────────────────

    def get_company(self, slug: str) -> dict:
        """
        Fetch company information by its URL slug.

        Query: companyBySlug(slug: String!) -> Company!

        Returns fields:
            uuid, slug, name, isCustomer, websiteDomain

        Args:
            slug: Company slug (e.g. "acme", "meter").

        Returns:
            Company dict under key "companyBySlug".
        """
        query = f"""
        {{
          companyBySlug(slug: "{slug}") {{
            uuid slug name isCustomer websiteDomain
          }}
        }}
        """
        return self._execute(query)

    def get_networks(
        self,
        company_slug: str,
        filter: Optional[CompanyNetworksFilter] = None,
    ) -> dict:
        """
        List all networks for a company.

        Query: networksForCompany(companySlug: String!, filter: CompanyNetworksFilterInput)
               -> [Network!]!

        Returns fields per network:
            UUID, label, slug

        Args:
            company_slug: Company slug.
            filter:       Optional filter to restrict by specific network UUIDs.

        Returns:
            Dict with key "networksForCompany" containing a list of networks.
        """
        filter_arg = f", filter: {filter.to_gql()}" if filter else ""
        query = f"""
        {{
          networksForCompany(companySlug: "{company_slug}"{filter_arg}) {{
            UUID label slug
          }}
        }}
        """
        return self._execute(query)

    def get_network(self, uuid: UUID) -> dict:
        """
        Fetch a single network by its UUID.

        Query: network(UUID: UUID!) -> Network!

        Returns fields:
            UUID, label, slug, isActive, companySlug

        Args:
            uuid: Network UUID.

        Returns:
            Dict with key "network".
        """
        query = f"""
        {{
          network(UUID: "{uuid}") {{
            UUID label slug isActive companySlug
          }}
        }}
        """
        return self._execute(query)

    def get_network_by_slug(self, company_slug: str, network_slug: str) -> dict:
        """
        Fetch a network by company slug and network slug.

        Query: networkBySlug(companySlug: String!, networkSlug: String!) -> Network!

        Args:
            company_slug: Company slug.
            network_slug: Network slug.

        Returns:
            Dict with key "networkBySlug".
        """
        query = f"""
        {{
          networkBySlug(companySlug: "{company_slug}", networkSlug: "{network_slug}") {{
            UUID label slug isActive
          }}
        }}
        """
        return self._execute(query)

    # ── Client queries ───────────────────────────────────────────────────────

    def get_network_clients(
        self,
        network_uuid: UUID,
        filter: Optional[NetworkClientsFilter] = None,
    ) -> dict:
        """
        List active clients on a network.

        Query: networkClients(networkUUID: UUID!, filter: NetworkClientsFilter)
               -> [NetworkClient!]!

        Returns fields per client:
            macAddress, ip, clientName, isWireless, signal, lastSeen,
            connectedVLAN { name vlanID },
            connectedSSID { ssid }

        Args:
            network_uuid: Network UUID.
            filter:       Optional NetworkClientsFilter. If omitted, returns all
                          active clients with default lookback.

        Returns:
            Dict with key "networkClients" containing a list of clients.
        """
        filter_arg = f", filter: {filter.to_gql()}" if filter else ""
        query = f"""
        {{
          networkClients(networkUUID: "{network_uuid}"{filter_arg}) {{
            macAddress ip clientName isWireless signal lastSeen
            connectedVLAN {{ name vlanID }}
            connectedSSID {{ ssid }}
          }}
        }}
        """
        return self._execute(query)

    def get_networks_clients(
        self,
        company_uuid: UUID,
        network_uuids: list[UUID],
        filter: Optional[NetworkClientsFilter] = None,
    ) -> dict:
        """
        List clients across multiple networks for a company.

        Query: networksClients(companyUUID: UUID!, networkUUIDs: [UUID!]!,
                               filter: NetworkClientsFilter)
               -> [NetworkClient!]!

        Args:
            company_uuid:  Company UUID.
            network_uuids: List of network UUIDs to include.
            filter:        Optional NetworkClientsFilter.

        Returns:
            Dict with key "networksClients".
        """
        uuids_gql  = ", ".join(f'"{u}"' for u in network_uuids)
        filter_arg = f", filter: {filter.to_gql()}" if filter else ""
        query = f"""
        {{
          networksClients(companyUUID: "{company_uuid}", networkUUIDs: [{uuids_gql}]{filter_arg}) {{
            macAddress ip clientName isWireless lastSeen networkUUID
          }}
        }}
        """
        return self._execute(query)

    def get_blocked_clients(self, network_uuid: UUID) -> dict:
        """
        List blocked clients on a network.

        Query: blockedClientsForNetwork(networkUUID: UUID!)
               -> [NetworkClientBlocklistEntry!]!

        Returns fields:
            UUID, macAddress, createdAt, networkUUID

        Args:
            network_uuid: Network UUID.

        Returns:
            Dict with key "blockedClientsForNetwork".
        """
        query = f"""
        {{
          blockedClientsForNetwork(networkUUID: "{network_uuid}") {{
            UUID macAddress createdAt networkUUID
          }}
        }}
        """
        return self._execute(query)

    def get_active_clients(
        self,
        filter: MetricsFilter,
        network_uuid: Optional[UUID] = None,
        network_uuids: Optional[list[UUID]] = None,
        input: Optional[ActiveClientsInput] = None,
    ) -> dict:
        """
        Get wired and wireless client counts over time.

        Query: activeClients(filter: MetricsFilterInput!, networkUUID: UUID,
                             networkUUIDs: [UUID!], input: ActiveClientsInput)
               -> ActiveClientsMetricsResponse!

        Returns:
            wired and wireless lists of { timestamp, value } data points.

        Args:
            filter:        Time range and resolution (required).
            network_uuid:  Single network UUID (use this or network_uuids).
            network_uuids: Multiple network UUIDs.
            input:         Optional ActiveClientsInput configuration.

        Returns:
            Dict with key "activeClients" containing wired and wireless arrays.
        """
        args = [f"filter: {filter.to_gql()}"]
        if network_uuid:
            args.append(f'networkUUID: "{network_uuid}"')
        if network_uuids:
            uuids_gql = ", ".join(f'"{u}"' for u in network_uuids)
            args.append(f"networkUUIDs: [{uuids_gql}]")
        if input:
            args.append(f"input: {input.to_gql()}")
        query = f"""
        {{
          activeClients({", ".join(args)}) {{
            wired    {{ timestamp value }}
            wireless {{ timestamp value }}
          }}
        }}
        """
        return self._execute(query)

    # ── Device queries ───────────────────────────────────────────────────────

    def get_virtual_device(self, uuid: UUID) -> dict:
        """
        Fetch a single virtual device by UUID.

        Query: virtualDevice(UUID: UUID!) -> VirtualDevice!

        Returns fields:
            UUID, label, deviceType, deviceModel, isOnline, networkUUID

        Args:
            uuid: Virtual device UUID.

        Returns:
            Dict with key "virtualDevice".
        """
        query = f"""
        {{
          virtualDevice(UUID: "{uuid}") {{
            UUID label deviceType deviceModel isOnline networkUUID
          }}
        }}
        """
        return self._execute(query)

    def get_virtual_devices(
        self,
        network_uuid: UUID,
        filter: Optional[DevicesForNetworkFilter] = None,
    ) -> dict:
        """
        List all virtual devices on a network.

        Query: virtualDevicesForNetwork(networkUUID: UUID!,
                                        filter: DevicesForNetworkFilter)
               -> [VirtualDevice!]!

        Returns fields per device:
            UUID, label, deviceType, deviceModel, isOnline

        Args:
            network_uuid: Network UUID.
            filter:       Optional DevicesForNetworkFilter to restrict by type.

        Returns:
            Dict with key "virtualDevicesForNetwork".
        """
        filter_arg = f", filter: {filter.to_gql()}" if filter else ""
        query = f"""
        {{
          virtualDevicesForNetwork(networkUUID: "{network_uuid}"{filter_arg}) {{
            UUID label deviceType deviceModel isOnline
          }}
        }}
        """
        return self._execute(query)

    def get_hardware_device(self, serial_number: str) -> dict:
        """
        Look up a physical hardware device by serial number.

        Query: hardwareDevice(serialNumber: String!) -> HardwareDevice!

        Returns fields:
            serialNumber, deviceType, deviceModel, isConnectedToBackend,
            macAddress, networkUUID, virtualDeviceUUID

        Args:
            serial_number: Device serial number.

        Returns:
            Dict with key "hardwareDevice".
        """
        query = f"""
        {{
          hardwareDevice(serialNumber: "{serial_number}") {{
            serialNumber deviceType deviceModel isConnectedToBackend
            macAddress networkUUID virtualDeviceUUID
          }}
        }}
        """
        return self._execute(query)

    def get_spare_hardware_devices(
        self,
        network_uuid: UUID,
        filter: Optional[HardwareDevicesFilter] = None,
    ) -> dict:
        """
        List undeployed spare hardware devices associated with a network.

        Query: spareHardwareDevicesForNetwork(networkUUID: UUID!,
                                              filter: HardwareDevicesFilter)
               -> [HardwareDevice!]!

        Args:
            network_uuid: Network UUID.
            filter:       Optional HardwareDevicesFilter (model, type, pagination).

        Returns:
            Dict with key "spareHardwareDevicesForNetwork".
        """
        filter_arg = f", filter: {filter.to_gql()}" if filter else ""
        query = f"""
        {{
          spareHardwareDevicesForNetwork(networkUUID: "{network_uuid}"{filter_arg}) {{
            serialNumber deviceType deviceModel isConnectedToBackend macAddress
          }}
        }}
        """
        return self._execute(query)

    # ── Interface queries ────────────────────────────────────────────────────

    def get_phy_interfaces(self, virtual_device_uuid: UUID) -> dict:
        """
        List all physical ports on a specific device.

        Query: phyInterfacesForVirtualDevice(virtualDeviceUUID: UUID!)
               -> [PhyInterface!]!

        Returns fields per interface:
            UUID, label, portNumber, portSpeedMbps, isEnabled, isConnected,
            isUplink, isTrunkPort, nativeVLAN { name vlanID }

        Args:
            virtual_device_uuid: UUID of the virtual device (switch or AP).

        Returns:
            Dict with key "phyInterfacesForVirtualDevice".
        """
        query = f"""
        {{
          phyInterfacesForVirtualDevice(virtualDeviceUUID: "{virtual_device_uuid}") {{
            UUID label portNumber portSpeedMbps isEnabled isConnected
            isUplink isTrunkPort nativeVLAN {{ name vlanID }}
          }}
        }}
        """
        return self._execute(query)

    def get_uplink_interfaces(self, network_uuid: UUID) -> dict:
        """
        List WAN uplink physical interfaces for a network.

        Query: uplinkPhyInterfacesForNetwork(networkUUID: UUID!) -> [PhyInterface!]!

        Returns fields per interface:
            UUID, label, portNumber, isEnabled, isUplinkActive, portSpeedMbps,
            nativeVLAN { name vlanID }

        Args:
            network_uuid: Network UUID.

        Returns:
            Dict with key "uplinkPhyInterfacesForNetwork".
        """
        query = f"""
        {{
          uplinkPhyInterfacesForNetwork(networkUUID: "{network_uuid}") {{
            UUID label portNumber isEnabled isUplink isUplinkActive portSpeedMbps
            nativeVLAN {{ name vlanID }}
          }}
        }}
        """
        return self._execute(query)

    # ── Switch queries ───────────────────────────────────────────────────────

    def get_switch_port_stats(
        self,
        virtual_device_uuid: UUID,
        port_number: Optional[int] = None,
        lookback_hours: Optional[int] = None,
    ) -> dict:
        """
        Get cumulative traffic and error counters for switch ports.

        Query: switchPortStats(virtualDeviceUUID: UUID!, portNumber: Int,
                               lookbackHours: Int)
               -> [SwitchPortStat!]!

        Returns fields per port:
            portNumber, totalRxBytes, totalTxBytes, totalRxPackets,
            totalTxPackets, errorRxPackets, errorTxPackets

        Args:
            virtual_device_uuid: Switch virtual device UUID.
            port_number:         Restrict to a single port (optional).
            lookback_hours:      How many hours of history to aggregate (optional).

        Returns:
            Dict with key "switchPortStats".
        """
        args = [f'virtualDeviceUUID: "{virtual_device_uuid}"']
        if port_number is not None:
            args.append(f"portNumber: {port_number}")
        if lookback_hours is not None:
            args.append(f"lookbackHours: {lookback_hours}")
        query = f"""
        {{
          switchPortStats({", ".join(args)}) {{
            portNumber totalRxBytes totalTxBytes totalRxPackets
            totalTxPackets errorRxPackets errorTxPackets
          }}
        }}
        """
        return self._execute(query)

    def get_switch_mac_table(self, virtual_device_uuid: UUID) -> dict:
        """
        Get the MAC address forwarding table for a switch.

        Query: switchMACTable(virtualDeviceUUID: UUID!) -> [SwitchMACTableEntry!]!

        Returns fields per entry:
            virtualDeviceUUID, vlanID, updatedAt

        Args:
            virtual_device_uuid: Switch virtual device UUID.

        Returns:
            Dict with key "switchMACTable".
        """
        query = f"""
        {{
          switchMACTable(virtualDeviceUUID: "{virtual_device_uuid}") {{
            virtualDeviceUUID vlanID updatedAt
          }}
        }}
        """
        return self._execute(query)

    def get_switch_port_metrics_rate(
        self,
        virtual_device_uuid: UUID,
        filter: MetricsFilter,
        port_number: Optional[int] = None,
    ) -> dict:
        """
        Get time-series traffic rate metrics for switch ports.

        Query: switchPortMetricsRate(virtualDeviceUUID: UUID!,
                                     filter: MetricsFilterInput!,
                                     portNumber: Int)
               -> SwitchPortMetricsRateResponse!

        Args:
            virtual_device_uuid: Switch UUID.
            filter:              Time range and resolution.
            port_number:         Restrict to a single port (optional).

        Returns:
            Dict with key "switchPortMetricsRate".
        """
        args = [
            f'virtualDeviceUUID: "{virtual_device_uuid}"',
            f"filter: {filter.to_gql()}",
        ]
        if port_number is not None:
            args.append(f"portNumber: {port_number}")
        query = f"""
        {{
          switchPortMetricsRate({", ".join(args)}) {{
            values {{
              portNumber timestamp
              totalRxBytes totalTxBytes
            }}
          }}
        }}
        """
        return self._execute(query)

    # ── Controller queries ───────────────────────────────────────────────────

    def get_controller_port_stats(
        self,
        virtual_device_uuid: UUID,
        port_number: Optional[int] = None,
        lookback_hours: Optional[int] = None,
    ) -> dict:
        """
        Get cumulative traffic statistics for controller (firewall) ports.

        Query: controllerPortStats(virtualDeviceUUID: UUID!, portNumber: Int,
                                   lookbackHours: Int)
               -> [ControllerPortStat!]!

        Args:
            virtual_device_uuid: Controller virtual device UUID.
            port_number:         Restrict to a single port (optional).
            lookback_hours:      Hours of history to aggregate (optional).

        Returns:
            Dict with key "controllerPortStats".
        """
        args = [f'virtualDeviceUUID: "{virtual_device_uuid}"']
        if port_number is not None:
            args.append(f"portNumber: {port_number}")
        if lookback_hours is not None:
            args.append(f"lookbackHours: {lookback_hours}")
        query = f"""
        {{
          controllerPortStats({", ".join(args)}) {{
            portNumber totalRxBytes totalTxBytes
            totalRxPackets totalTxPackets
          }}
        }}
        """
        return self._execute(query)

    def get_controller_port_metrics_rate(
        self,
        virtual_device_uuid: UUID,
        filter: MetricsFilter,
        port_number: Optional[int] = None,
    ) -> dict:
        """
        Get time-series traffic rate metrics for controller ports.

        Query: controllerPortMetricsRate(virtualDeviceUUID: UUID!,
                                         filter: MetricsFilterInput!,
                                         portNumber: Int)
               -> ControllerPortMetricsRateResponse!

        Args:
            virtual_device_uuid: Controller UUID.
            filter:              Time range and resolution.
            port_number:         Restrict to a single port (optional).

        Returns:
            Dict with key "controllerPortMetricsRate".
        """
        args = [
            f'virtualDeviceUUID: "{virtual_device_uuid}"',
            f"filter: {filter.to_gql()}",
        ]
        if port_number is not None:
            args.append(f"portNumber: {port_number}")
        query = f"""
        {{
          controllerPortMetricsRate({", ".join(args)}) {{
            values {{
              portNumber timestamp totalRxBytes totalTxBytes
            }}
          }}
        }}
        """
        return self._execute(query)

    def get_controller_dns_request_rates(
        self,
        virtual_device_uuid: UUID,
        filter: MetricsFilter,
    ) -> dict:
        """
        Get DNS request rate metrics for a controller.

        Query: controllerDNSRequestRates(virtualDeviceUUID: UUID!,
                                         filter: MetricsFilterInput!)
               -> ControllerDNSRequestRatesResponse!

        Args:
            virtual_device_uuid: Controller UUID.
            filter:              Time range and resolution.

        Returns:
            Dict with key "controllerDNSRequestRates".
        """
        query = f"""
        {{
          controllerDNSRequestRates(
            virtualDeviceUUID: "{virtual_device_uuid}",
            filter: {filter.to_gql()}
          ) {{
            values {{ timestamp value UUID }}
          }}
        }}
        """
        return self._execute(query)

    # ── SSID / VLAN / BSSID queries ──────────────────────────────────────────

    def get_ssid(self, uuid: UUID) -> dict:
        """
        Fetch a single SSID by UUID.

        Query: ssid(UUID: UUID!) -> SSID!

        Returns fields:
            UUID, ssid, isEnabled, isGuest, isHidden, encryptionProtocol,
            networkUUID

        Args:
            uuid: SSID UUID.

        Returns:
            Dict with key "ssid".
        """
        query = f"""
        {{
          ssid(UUID: "{uuid}") {{
            UUID ssid isEnabled isGuest isHidden encryptionProtocol networkUUID
          }}
        }}
        """
        return self._execute(query)

    def get_ssids(
        self,
        network_uuid: UUID,
        filter: Optional[SSIDFilter] = None,
    ) -> dict:
        """
        List SSIDs configured on a network.

        Query: ssidsForNetwork(networkUUID: UUID!, filter: SSIDFilterInput)
               -> [SSID!]!

        Returns fields per SSID:
            UUID, ssid, isEnabled, isGuest, isHidden, encryptionProtocol

        Args:
            network_uuid: Network UUID.
            filter:       Optional SSIDFilter (by guest/hidden status or name).

        Returns:
            Dict with key "ssidsForNetwork".
        """
        filter_arg = f", filter: {filter.to_gql()}" if filter else ""
        query = f"""
        {{
          ssidsForNetwork(networkUUID: "{network_uuid}"{filter_arg}) {{
            UUID ssid isEnabled isGuest isHidden encryptionProtocol
          }}
        }}
        """
        return self._execute(query)

    def get_vlan(self, uuid: UUID) -> dict:
        """
        Fetch a single VLAN by UUID.

        Query: vlan(UUID: UUID!) -> VLAN!

        Returns fields:
            UUID, name, vlanID, isEnabled, isDefault, networkUUID

        Args:
            uuid: VLAN UUID.

        Returns:
            Dict with key "vlan".
        """
        query = f"""
        {{
          vlan(UUID: "{uuid}") {{
            UUID name vlanID isEnabled isDefault networkUUID
          }}
        }}
        """
        return self._execute(query)

    def get_vlans(self, network_uuid: UUID) -> dict:
        """
        List all VLANs configured on a network.

        Query: vlans(networkUUID: UUID!) -> [VLAN!]!

        Returns fields per VLAN:
            UUID, name, vlanID, isEnabled, isDefault

        Args:
            network_uuid: Network UUID.

        Returns:
            Dict with key "vlans".
        """
        query = f"""
        {{
          vlans(networkUUID: "{network_uuid}") {{
            UUID name vlanID isEnabled isDefault
          }}
        }}
        """
        return self._execute(query)

    def get_bssids(
        self,
        network_uuid: UUID,
        include_inactive: Optional[bool] = None,
    ) -> dict:
        """
        List wireless BSSIDs (access point radios) on a network.

        Query: bssidsForNetwork(networkUUID: UUID!, includeInactive: Boolean)
               -> [BSSID!]!

        Returns fields per BSSID:
            BSSID, isActive, radioBand, accessPointSerialNumber,
            SSID { ssid isEnabled }

        A BSSID corresponds to a single radio on an access point.
        Each AP typically has one BSSID per band per SSID.

        Args:
            network_uuid:     Network UUID.
            include_inactive: If True, include BSSIDs that are currently inactive.

        Returns:
            Dict with key "bssidsForNetwork".
        """
        args = [f'networkUUID: "{network_uuid}"']
        if include_inactive is not None:
            args.append(f"includeInactive: {'true' if include_inactive else 'false'}")
        query = f"""
        {{
          bssidsForNetwork({", ".join(args)}) {{
            BSSID isActive radioBand accessPointSerialNumber
            SSID {{ ssid isEnabled }}
          }}
        }}
        """
        return self._execute(query)

    def get_inter_vlan_pairs(self, network_uuid: UUID) -> dict:
        """
        List permitted inter-VLAN communication pairs.

        Query: interVLANCommunicationPermittedPairs(networkUUID: UUID!)
               -> [InterVLANCommunicationPermittedPair!]!

        Returns fields per pair:
            UUID, networkUUID

        Args:
            network_uuid: Network UUID.

        Returns:
            Dict with key "interVLANCommunicationPermittedPairs".
        """
        query = f"""
        {{
          interVLANCommunicationPermittedPairs(networkUUID: "{network_uuid}") {{
            UUID networkUUID
          }}
        }}
        """
        return self._execute(query)

    # ── Uplink / Throughput metrics ──────────────────────────────────────────

    def get_uplink_quality(
        self,
        filter: MetricsFilter,
        network_uuid: Optional[UUID] = None,
        network_uuids: Optional[list[UUID]] = None,
        phy_interface_uuid: Optional[UUID] = None,
        virtual_device_uuid: Optional[UUID] = None,
    ) -> dict:
        """
        Get WAN uplink quality metrics (latency/jitter/packet-loss composite).

        Query: networkUplinkQuality(filter: MetricsFilterInput!, networkUUID: UUID,
                                    networkUUIDs: [UUID!], phyInterfaceUUID: UUID,
                                    virtualDeviceUUID: UUID)
               -> NetworkUplinkQualityResponse!

        Returns:
            metadata { minValue maxValue } and values list with:
            timestamp, value (0.0–1.0 quality score), phyInterfaceUUID

        Args:
            filter:              Time range and resolution (required).
            network_uuid:        Single network UUID.
            network_uuids:       Multiple network UUIDs.
            phy_interface_uuid:  Restrict to a specific uplink interface.
            virtual_device_uuid: Restrict to a specific device's uplinks.

        Returns:
            Dict with key "networkUplinkQuality".
        """
        args = [f"filter: {filter.to_gql()}"]
        if network_uuid:
            args.append(f'networkUUID: "{network_uuid}"')
        if network_uuids:
            uuids_gql = ", ".join(f'"{u}"' for u in network_uuids)
            args.append(f"networkUUIDs: [{uuids_gql}]")
        if phy_interface_uuid:
            args.append(f'phyInterfaceUUID: "{phy_interface_uuid}"')
        if virtual_device_uuid:
            args.append(f'virtualDeviceUUID: "{virtual_device_uuid}"')
        query = f"""
        {{
          networkUplinkQuality({", ".join(args)}) {{
            metadata {{ minValue maxValue }}
            values {{ timestamp value phyInterfaceUUID networkUUID }}
          }}
        }}
        """
        return self._execute(query)

    def get_uplink_throughput(
        self,
        filter: MetricsFilter,
        network_uuid: Optional[UUID] = None,
        network_uuids: Optional[list[UUID]] = None,
        phy_interface_uuid: Optional[UUID] = None,
        virtual_device_uuid: Optional[UUID] = None,
    ) -> dict:
        """
        Get WAN uplink throughput (bandwidth) metrics.

        Query: networkUplinkThroughput(filter: MetricsFilterInput!, ...)
               -> NetworkUplinkThroughputMetricsResponse!

        Returns:
            metadata { minValue maxValue } and values list with:
            timestamp, value (bits per second), direction (RX or TX),
            phyInterfaceUUID

        Args:
            filter:              Time range and resolution (required).
            network_uuid:        Single network UUID.
            network_uuids:       Multiple network UUIDs.
            phy_interface_uuid:  Restrict to a specific interface.
            virtual_device_uuid: Restrict to a specific device.

        Returns:
            Dict with key "networkUplinkThroughput".
        """
        args = [f"filter: {filter.to_gql()}"]
        if network_uuid:
            args.append(f'networkUUID: "{network_uuid}"')
        if network_uuids:
            uuids_gql = ", ".join(f'"{u}"' for u in network_uuids)
            args.append(f"networkUUIDs: [{uuids_gql}]")
        if phy_interface_uuid:
            args.append(f'phyInterfaceUUID: "{phy_interface_uuid}"')
        if virtual_device_uuid:
            args.append(f'virtualDeviceUUID: "{virtual_device_uuid}"')
        query = f"""
        {{
          networkUplinkThroughput({", ".join(args)}) {{
            metadata {{ minValue maxValue }}
            values {{ timestamp value direction phyInterfaceUUID networkUUID }}
          }}
        }}
        """
        return self._execute(query)

    def get_networks_uplink_qualities(
        self,
        network_uuids: list[UUID],
        filter: MetricsFilter,
    ) -> dict:
        """
        Get uplink quality metrics across multiple networks simultaneously.

        Query: networksUplinkQualities(networkUUIDs: [UUID!]!,
                                       filter: MetricsFilterInput!)
               -> [NetworkUplinkQualityResponse!]!

        Returns one response object per network, each with:
            metadata { minValue maxValue },
            values [ { timestamp value phyInterfaceUUID networkUUID } ]

        Args:
            network_uuids: List of network UUIDs to query.
            filter:        Time range and resolution.

        Returns:
            Dict with key "networksUplinkQualities".
        """
        uuids_gql = ", ".join(f'"{u}"' for u in network_uuids)
        query = f"""
        {{
          networksUplinkQualities(
            networkUUIDs: [{uuids_gql}],
            filter: {filter.to_gql()}
          ) {{
            metadata {{ minValue maxValue }}
            values {{ timestamp value phyInterfaceUUID networkUUID }}
          }}
        }}
        """
        return self._execute(query)

    # ── Wireless metrics ─────────────────────────────────────────────────────

    def get_wireless_client_metrics(
        self,
        network_uuid: UUID,
        filter: ClientMetricsFilter,
    ) -> dict:
        """
        Get wireless client connection metrics for a network.

        Query: wirelessClientMetrics(networkUUID: UUID!,
                                     filter: ClientMetricsTimeseriesFilterInput!)
               -> [WirelessClientMetricsResponse!]!

        Returns per-MAC metrics including signal, SNR, throughput, and retry rates.

        Args:
            network_uuid: Network UUID.
            filter:       ClientMetricsFilter with time range and optional filters.

        Returns:
            Dict with key "wirelessClientMetrics".
        """
        query = f"""
        {{
          wirelessClientMetrics(
            networkUUID: "{network_uuid}",
            filter: {filter.to_gql()}
          ) {{
            response {{
              metadata {{
                clientCount signal snr
                rxBytes txBytes rxRate txRate
              }}
            }}
          }}
        }}
        """
        return self._execute(query)

    def get_wireless_client_metrics_by_ap(
        self,
        network_uuid: UUID,
        ap_virtual_device_uuid: UUID,
        filter: ClientMetricsFilter,
    ) -> dict:
        """
        Get wireless client metrics aggregated by access point.

        Query: wirelessClientMetricsByAP(networkUUID: UUID!,
                                          apVirtualDeviceUUID: UUID!,
                                          filter: ClientMetricsTimeseriesFilterInput!)
               -> ClientMetricsTimeseriesResponse!

        Args:
            network_uuid:           Network UUID.
            ap_virtual_device_uuid: AP virtual device UUID.
            filter:                 ClientMetricsFilter.

        Returns:
            Dict with key "wirelessClientMetricsByAP".
        """
        query = f"""
        {{
          wirelessClientMetricsByAP(
            networkUUID: "{network_uuid}",
            apVirtualDeviceUUID: "{ap_virtual_device_uuid}",
            filter: {filter.to_gql()}
          ) {{
            metadata {{ clientCount signal snr }}
            values   {{ timestamp clientCount signal snr }}
          }}
        }}
        """
        return self._execute(query)

    def get_wireless_client_metrics_by_client(
        self,
        network_uuid: UUID,
        mac_address: MacAddress,
        filter: ClientMetricsFilter,
    ) -> dict:
        """
        Get wireless metrics for a single client identified by MAC address.

        Query: wirelessClientMetricsByClient(networkUUID: UUID!,
                                              macAddress: MacAddress!,
                                              filter: ClientMetricsTimeseriesFilterInput!)
               -> ClientMetricsTimeseriesResponse!

        Args:
            network_uuid: Network UUID.
            mac_address:  Client MAC address (e.g. "AA:BB:CC:DD:EE:FF").
            filter:       ClientMetricsFilter.

        Returns:
            Dict with key "wirelessClientMetricsByClient".
        """
        query = f"""
        {{
          wirelessClientMetricsByClient(
            networkUUID: "{network_uuid}",
            macAddress: "{mac_address}",
            filter: {filter.to_gql()}
          ) {{
            metadata {{ clientCount signal snr rxBytes txBytes }}
            values   {{ timestamp clientCount signal snr }}
          }}
        }}
        """
        return self._execute(query)

    def get_channel_utilization_by_network(
        self,
        network_uuid: UUID,
        filter: ChannelUtilizationFilter,
        band: Optional[RadioBand] = None,
    ) -> dict:
        """
        Get Wi-Fi channel utilization across all APs on a network.

        Query: channelUtilizationByNetwork(networkUUID: UUID!,
                                           filter: ChannelUtilizationTimeseriesFilterInput!,
                                           band: RadioBand)
               -> [ChannelUtilizationResponseV2!]!

        Returns per-AP utilization breakdown:
            rx, tx, self, OBSS, non-802.11, total utilization percentages.

        Args:
            network_uuid: Network UUID.
            filter:       ChannelUtilizationFilter.
            band:         Restrict to a specific RadioBand (optional).

        Returns:
            Dict with key "channelUtilizationByNetwork".
        """
        args = [
            f'networkUUID: "{network_uuid}"',
            f"filter: {filter.to_gql()}",
        ]
        if band:
            args.append(f"band: {band.value}")
        query = f"""
        {{
          channelUtilizationByNetwork({", ".join(args)}) {{
            band virtualDeviceUUID
            values {{ timestamp totalUtilization rxUtilization txUtilization }}
          }}
        }}
        """
        return self._execute(query)

    def get_channel_utilization_by_ap(
        self,
        network_uuid: UUID,
        ap_virtual_device_uuid: UUID,
        filter: ChannelUtilizationFilter,
        band: Optional[RadioBand] = None,
    ) -> dict:
        """
        Get channel utilization for a specific access point.

        Query: channelUtilizationByAP(networkUUID: UUID!,
                                       apVirtualDeviceUUID: UUID!,
                                       filter: ChannelUtilizationTimeseriesFilterInput!,
                                       band: RadioBand)
               -> [ChannelUtilizationResponseV2!]!

        Args:
            network_uuid:           Network UUID.
            ap_virtual_device_uuid: AP virtual device UUID.
            filter:                 ChannelUtilizationFilter.
            band:                   Restrict to a specific band (optional).

        Returns:
            Dict with key "channelUtilizationByAP".
        """
        args = [
            f'networkUUID: "{network_uuid}"',
            f'apVirtualDeviceUUID: "{ap_virtual_device_uuid}"',
            f"filter: {filter.to_gql()}",
        ]
        if band:
            args.append(f"band: {band.value}")
        query = f"""
        {{
          channelUtilizationByAP({", ".join(args)}) {{
            band virtualDeviceUUID
            values {{ timestamp totalUtilization rxUtilization txUtilization }}
          }}
        }}
        """
        return self._execute(query)

    def get_channel_utilization_by_client(
        self,
        network_uuid: UUID,
        mac_address: MacAddress,
        filter: ChannelUtilizationFilter,
    ) -> dict:
        """
        Get channel utilization from the perspective of a single wireless client.

        Query: channelUtilizationByClient(networkUUID: UUID!,
                                           macAddress: MacAddress!,
                                           filter: ChannelUtilizationTimeseriesFilterInput!)
               -> [ClientChannelUtilizationTimeseriesValue!]!

        Args:
            network_uuid: Network UUID.
            mac_address:  Client MAC address.
            filter:       ChannelUtilizationFilter.

        Returns:
            Dict with key "channelUtilizationByClient".
        """
        query = f"""
        {{
          channelUtilizationByClient(
            networkUUID: "{network_uuid}",
            macAddress: "{mac_address}",
            filter: {filter.to_gql()}
          ) {{
            timestamp totalUtilization rxUtilization txUtilization selfUtilization
          }}
        }}
        """
        return self._execute(query)

    def get_all_client_metrics_by_client(
        self,
        network_uuid: UUID,
        mac_address: MacAddress,
        filter: AllClientMetricsFilter,
    ) -> dict:
        """
        Get all available metrics for a single client by MAC address.

        Query: allClientMetricsByClient(networkUUID: UUID!, macAddress: MacAddress!,
                                         filter: AllClientMetricsTimeseriesFilterInput!)
               -> AllClientMetricsTimeseriesResponse!

        Returns rxBytes and txBytes time-series data.

        Args:
            network_uuid: Network UUID.
            mac_address:  Client MAC address.
            filter:       AllClientMetricsFilter with time range.

        Returns:
            Dict with key "allClientMetricsByClient".
        """
        query = f"""
        {{
          allClientMetricsByClient(
            networkUUID: "{network_uuid}",
            macAddress: "{mac_address}",
            filter: {filter.to_gql()}
          ) {{
            rxBytes {{ metadata {{ minValue maxValue }} values {{ timestamp value }} }}
            txBytes {{ metadata {{ minValue maxValue }} values {{ timestamp value }} }}
          }}
        }}
        """
        return self._execute(query)

    def get_all_client_metrics_by_vlan(
        self,
        network_uuid: UUID,
        vlan_uuid: UUID,
        filter: AllClientMetricsFilter,
    ) -> dict:
        """
        Get aggregated client metrics for all clients on a VLAN.

        Query: allClientMetricsByVLAN(networkUUID: UUID!, vlanUUID: UUID!,
                                       filter: AllClientMetricsTimeseriesFilterInput!)
               -> AllClientMetricsTimeseriesResponse!

        Args:
            network_uuid: Network UUID.
            vlan_uuid:    VLAN UUID.
            filter:       AllClientMetricsFilter.

        Returns:
            Dict with key "allClientMetricsByVLAN".
        """
        query = f"""
        {{
          allClientMetricsByVLAN(
            networkUUID: "{network_uuid}",
            vlanUUID: "{vlan_uuid}",
            filter: {filter.to_gql()}
          ) {{
            rxBytes {{ metadata {{ minValue maxValue }} values {{ timestamp value }} }}
            txBytes {{ metadata {{ minValue maxValue }} values {{ timestamp value }} }}
          }}
        }}
        """
        return self._execute(query)

    # ── AP health ────────────────────────────────────────────────────────────

    def get_ap_health_scores(
        self,
        serial_number: str,
        filter: MetricsFilter,
    ) -> dict:
        """
        Get health score time-series data for a specific access point.

        Query: apHealthScores(serialNumber: String!, filter: MetricsFilterInput!)
               -> [AccessPointHealthScores!]!

        Returns per-timestamp:
            overallScore, performanceScore, rfScore, systemScore,
            cpuPercent, memoryPercent, clientCount (by band)

        Args:
            serial_number: AP hardware serial number.
            filter:        Time range and resolution.

        Returns:
            Dict with key "apHealthScores".
        """
        query = f"""
        {{
          apHealthScores(
            serialNumber: "{serial_number}",
            filter: {filter.to_gql()}
          ) {{
            timestamp overallScore performanceScore rfScore systemScore
            cpuPercent memoryPercent
          }}
        }}
        """
        return self._execute(query)

    # ── Event log ────────────────────────────────────────────────────────────

    def get_event_log(
        self,
        network_uuid: UUID,
        limit: int = 20,
        offset: Optional[int] = None,
        start_time: Optional[DateTime] = None,
        end_time: Optional[DateTime] = None,
        type_filter: Optional[list[EventType]] = None,
        virtual_device_uuid_filter: Optional[list[UUID]] = None,
    ) -> dict:
        """
        Fetch paginated network event log entries.

        Query: recentEventLogEventsPage(networkUUID: UUID!, limit: Int!,
                                        offset: Int, startTime: DateTime,
                                        endTime: DateTime,
                                        typeFilter: [EventType!],
                                        virtualDeviceUUIDFilter: [UUID!])
               -> EventLogEventsPage!

        Returns:
            total — Total matching event count.
            events — List of: eventType, eventTypeAPIName, generatedAt, networkUUID

        Args:
            network_uuid:               Network UUID.
            limit:                      Maximum events to return (required).
            offset:                     Pagination offset.
            start_time:                 RFC 3339 start timestamp.
            end_time:                   RFC 3339 end timestamp.
            type_filter:                Restrict to specific EventType values.
            virtual_device_uuid_filter: Restrict to events from specific devices.

        Example (fetch only WAN up/down events):
            client.get_event_log(
                network_uuid="...",
                limit=50,
                type_filter=[EventType.WAN_UP, EventType.WAN_DOWN],
            )

        Returns:
            Dict with key "recentEventLogEventsPage".
        """
        args = [f'networkUUID: "{network_uuid}"', f"limit: {limit}"]
        if offset is not None:
            args.append(f"offset: {offset}")
        if start_time:
            args.append(f'startTime: "{start_time}"')
        if end_time:
            args.append(f'endTime: "{end_time}"')
        if type_filter:
            types_gql = ", ".join(e.value for e in type_filter)
            args.append(f"typeFilter: [{types_gql}]")
        if virtual_device_uuid_filter:
            uuids_gql = ", ".join(f'"{u}"' for u in virtual_device_uuid_filter)
            args.append(f"virtualDeviceUUIDFilter: [{uuids_gql}]")
        query = f"""
        {{
          recentEventLogEventsPage({", ".join(args)}) {{
            total
            events {{
              eventType eventTypeAPIName generatedAt networkUUID
            }}
          }}
        }}
        """
        return self._execute(query)

    # ── Alert receivers ──────────────────────────────────────────────────────

    def get_alert_receiver(self, uuid: UUID) -> dict:
        """
        Fetch a single alert receiver configuration by UUID.

        Query: alertReceiver(UUID: UUID!) -> AlertReceiver!

        Returns fields:
            UUID, companyUUID, label, createdAt, updatedAt,
            targets [ { UUID type } ]

        Args:
            uuid: Alert receiver UUID.

        Returns:
            Dict with key "alertReceiver".
        """
        query = f"""
        {{
          alertReceiver(UUID: "{uuid}") {{
            UUID companyUUID label createdAt updatedAt
            targets {{ UUID type }}
          }}
        }}
        """
        return self._execute(query)

    def get_alert_receivers(self, company_uuid: UUID) -> dict:
        """
        List all alert receivers for a company.

        Query: alertReceiversForCompany(companyUUID: UUID!) -> [AlertReceiver!]!

        Returns fields per receiver:
            UUID, label, createdAt, targets [ { UUID type } ]

        Args:
            company_uuid: Company UUID.

        Returns:
            Dict with key "alertReceiversForCompany".
        """
        query = f"""
        {{
          alertReceiversForCompany(companyUUID: "{company_uuid}") {{
            UUID label createdAt
            targets {{ UUID type }}
          }}
        }}
        """
        return self._execute(query)

    # ── PDU metrics ──────────────────────────────────────────────────────────

    def get_pdu_metrics(
        self,
        virtual_device_uuid: UUID,
        filter: MetricsFilter,
    ) -> dict:
        """
        Get power metrics for a single Power Distribution Unit (PDU).

        Query: pduMetrics(virtualDeviceUUID: UUID!, filter: MetricsFilterInput!)
               -> PDUMetricsTimeseriesResponse!

        Returns per-outlet metrics: currentAmps, powerWatts, voltageVolts,
        plus device-level temperatureCelsius.

        Args:
            virtual_device_uuid: PDU virtual device UUID.
            filter:              Time range and resolution.

        Returns:
            Dict with key "pduMetrics".
        """
        query = f"""
        {{
          pduMetrics(
            virtualDeviceUUID: "{virtual_device_uuid}",
            filter: {filter.to_gql()}
          ) {{
            virtualDeviceUUID
            temperatureCelsius {{ values {{ timestamp value }} }}
          }}
        }}
        """
        return self._execute(query)

    def get_pdus_metrics(
        self,
        virtual_device_uuids: list[UUID],
        filter: MetricsFilter,
    ) -> dict:
        """
        Get power metrics for multiple PDUs simultaneously.

        Query: pdusMetrics(virtualDeviceUUIDs: [UUID!]!, filter: MetricsFilterInput!)
               -> [PDUMetricsTimeseriesResponse!]!

        Args:
            virtual_device_uuids: List of PDU virtual device UUIDs.
            filter:               Time range and resolution.

        Returns:
            Dict with key "pdusMetrics".
        """
        uuids_gql = ", ".join(f'"{u}"' for u in virtual_device_uuids)
        query = f"""
        {{
          pdusMetrics(
            virtualDeviceUUIDs: [{uuids_gql}],
            filter: {filter.to_gql()}
          ) {{
            virtualDeviceUUID
            temperatureCelsius {{ values {{ timestamp value }} }}
          }}
        }}
        """
        return self._execute(query)

    # ── Convenience method: raw query ────────────────────────────────────────

    def execute_raw(self, query: str) -> dict:
        """
        Execute an arbitrary GraphQL query string.

        Use this for queries not yet covered by named methods, or for
        bundling custom combinations of fields using GraphQL aliases.

        Example:
            data = client.execute_raw('''
            {
              companyInfo: companyBySlug(slug: "acme") { name }
              clients: networkClients(networkUUID: "...") { macAddress ip }
            }
            ''')

        Args:
            query: Raw GraphQL query string.

        Returns:
            The `data` dict from a successful response.
        """
        return self._execute(query)


# ══════════════════════════════════════════════════════════════════════════════
# USAGE EXAMPLE
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import config

    print("Meter SDK — Usage Demo")
    print(f"API URL : {config.API_URL}")
    print()

    # Instantiate the client
    client = MeterClient(token=config.API_TOKEN, api_url=config.API_URL)

    # ── Example 1: Company info ───────────────────────────────────────────────
    print("[1] get_company()")
    try:
        data    = client.get_company(slug=config.COMPANY_SLUG)
        company = data.get("companyBySlug", {})
        print(f"    Name    : {company.get('name')}")
        print(f"    UUID    : {company.get('uuid')}")
        print(f"    Customer: {company.get('isCustomer')}")
        rl = client.rate_limit
        print(f"    Rate limit remaining: {rl.remaining}  resets: {rl.reset_str}")
    except MeterAuthError as e:
        print(f"    ✗ Auth error: {e}")
    except MeterRateLimitError as e:
        print(f"    ✗ Rate limited. Retry in {e.seconds_to_wait():.1f}s")
    except MeterAPIError as e:
        print(f"    ✗ API error: {e}")

    print()

    # ── Example 2: Network clients ────────────────────────────────────────────
    print("[2] get_network_clients()")
    try:
        data    = client.get_network_clients(network_uuid=config.NETWORK_UUID)
        clients = data.get("networkClients", [])
        print(f"    Total clients: {len(clients)}")
        wireless = sum(1 for c in clients if c.get("isWireless"))
        print(f"    Wireless: {wireless}  Wired: {len(clients) - wireless}")
    except MeterAPIError as e:
        print(f"    ✗ {type(e).__name__}: {e}")

    print()

    # ── Example 3: Uplink quality metrics ────────────────────────────────────
    print("[3] get_networks_uplink_qualities() — last 4 hours, 5-min buckets")
    try:
        f    = MetricsFilter(duration_seconds=14400, step_seconds=300)
        data = client.get_networks_uplink_qualities(
            network_uuids=[config.NETWORK_UUID],
            filter=f,
        )
        results = data.get("networksUplinkQualities", [])
        for r in results:
            pts = r.get("values", [])
            meta = r.get("metadata", {})
            print(f"    Data points: {len(pts)}  "
                  f"min={meta.get('minValue')}  max={meta.get('maxValue')}")
    except MeterAPIError as e:
        print(f"    ✗ {type(e).__name__}: {e}")

    print()

    # ── Example 4: Filter events by type ─────────────────────────────────────
    print("[4] get_event_log() — WAN events only")
    try:
        data = client.get_event_log(
            network_uuid=config.NETWORK_UUID,
            limit=10,
            type_filter=[EventType.WAN_UP, EventType.WAN_DOWN, EventType.WAN_STATUS_CHANGE],
        )
        page   = data.get("recentEventLogEventsPage", {})
        events = page.get("events", [])
        print(f"    Total matching: {page.get('total')}  Showing: {len(events)}")
        for evt in events[:3]:
            print(f"    • {evt.get('eventType'):<35} {evt.get('generatedAt')}")
    except MeterAPIError as e:
        print(f"    ✗ {type(e).__name__}: {e}")

    print()

    # ── Example 5: Error handling demonstration ───────────────────────────────
    print("[5] Error handling — deliberate invalid token")
    bad_client = MeterClient(token="INVALID_TOKEN", api_url=config.API_URL)
    try:
        bad_client.get_company(slug="meter")
    except MeterAuthError as e:
        print(f"    ✓ Caught MeterAuthError: {e}")
    except MeterAPIError as e:
        print(f"    ✓ Caught MeterAPIError: {e}")

    print("\nDone.")
