#!/usr/bin/env bash
set -euo pipefail

API_URL="https://api.meter.com/api/v1/graphql"

API_TOKEN="v2.public.eyJpYXQiOiIyMDI2LTAyLTIxVDE5OjIyOjU4WiIsImlzcyI6Im1ldGVyYXBpLmNvbSIsImp0aSI6IjAxOWM4MWE3LWI5ZWQtZWFlNS03M2E0LTYwMmMxMjBiNzI2MyIsInBlcm1pc3Npb25zIjoiYXBpIiwicmVhbG0iOiJwcm9kIiwic3ViIjoibWV0ZXI6Ly9wdWJsaWNfYXBpLzAxOWM4MWE3LWI5ZTUtOWYyMi0zYmI3LTZjOWY1NmEwYzJhYyIsInRva2VuVHlwZSI6ImJlYXJlciJ95FDbpd_Sw1-PoD5f4pjuoCyjVrLED2V0Aeml0K0ZuzXCjiF2k-D6GXexpt7PsmgCKqOauyEq-9iBWSk6mETBCA.eyJraWQiOiJiZWFyZXItdjAiLCJlcG9jaCI6IjAifQ"

# Production meter customer
COMPANY_SLUG="meter"

# The 'primary' network
NETWORK_UUID="018a3e00-4b13-84cc-4e10-4b5d5f3b0f67"

# Production meter customer
COMPANY_UUID="0190273c-e082-6b73-0159-8c213602bcc1"

# https://dashboard.meter.com/org/meter/network/primary/hardware/switches/018bdfb7-1e34-74ba-ca68-9d4dd83dcc1f/insights
VIRTUAL_DEVICE_UUID="018bdfb7-1e34-74ba-ca68-9d4dd83dcc1f"

END_TIME=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
START_TIME=$(date -u -v-24H +"%Y-%m-%dT%H:%M:%SZ" 2>/dev/null \
  || date -u -d "24 hours ago" +"%Y-%m-%dT%H:%M:%SZ")

if [[ -z "${NO_COLOR:-}" ]]; then
  BOLD='\033[1m'
  GREEN='\033[0;32m'
  CYAN='\033[0;36m'
  YELLOW='\033[0;33m'
  RED='\033[0;31m'
  DIM='\033[2m'
  RESET='\033[0m'
else
  BOLD='' GREEN='' CYAN='' YELLOW='' RED='' DIM='' RESET=''
fi

QUERIES=(
  "Company Info"
  "Fetch basic company details by slug"
  '{ companyBySlug(slug: "'"$COMPANY_SLUG"'") { uuid slug name isCustomer websiteDomain } }'

  "Multi-Network Uplink Quality"
  "Get uplink quality metrics across multiple networks"
  '{ networksUplinkQualities(networkUUIDs: ["'"$NETWORK_UUID"'"], filter: { durationSeconds: 14400, stepSeconds: 300 }) { metadata { minValue maxValue } values { timestamp value phyInterfaceUUID networkUUID } } }'

  "Network Clients"
  "List active clients on a network with connection details"
  '{ networkClients(networkUUID: "'"$NETWORK_UUID"'") { macAddress ip clientName isWireless signal lastSeen connectedVLAN { name vlanID } connectedSSID { ssid } } }'

  "Multi-Network Clients"
  "List clients across multiple networks for a company"
  '{ networksClients(companyUUID: "'"$COMPANY_UUID"'", networkUUIDs: ["'"$NETWORK_UUID"'"]) { macAddress ip clientName isWireless lastSeen } }'

  "Uplink Physical Interfaces"
  "List uplink interfaces for a network (WAN connections)"
  '{ uplinkPhyInterfacesForNetwork(networkUUID: "'"$NETWORK_UUID"'") { UUID label portNumber isEnabled isUplink isUplinkActive portSpeedMbps portType nativeVLAN { name vlanID } } }'

  "BSSIDs"
  "List wireless BSSIDs (access point radios) on a network"
  '{ bssidsForNetwork(networkUUID: "'"$NETWORK_UUID"'") { BSSID isActive radioBand accessPointSerialNumber SSID { ssid isEnabled } } }'

  "Active Clients Count"
  "Get active client counts (wired vs wireless) over the last 4 hours"
  '{ activeClients(networkUUID: "'"$NETWORK_UUID"'", filter: { durationSeconds: 14400, stepSeconds: 300 }) { wired { timestamp value } wireless { timestamp value } } }'

  "Uplink Throughput Metrics"
  "Get WAN throughput metrics for a network over the last 4 hours"
  '{ networkUplinkThroughput(networkUUID: "'"$NETWORK_UUID"'", filter: { durationSeconds: 14400, stepSeconds: 300 }) { metadata { minValue maxValue } values { timestamp value direction phyInterfaceUUID } } }'

  "Uplink Quality Metrics"
  "Get WAN quality (latency/jitter/packet loss) metrics over the last 4 hours"
  '{ networkUplinkQuality(networkUUID: "'"$NETWORK_UUID"'", filter: { durationSeconds: 14400, stepSeconds: 300 }) { metadata { minValue maxValue } values { timestamp value phyInterfaceUUID } } }'

  "Event Log"
  "Fetch recent network events (last 20)"
  '{ recentEventLogEventsPage(networkUUID: "'"$NETWORK_UUID"'", limit: 20) { total events { eventType eventTypeAPIName generatedAt networkUUID } } }'

  "Physical Interfaces for Device"
  "List all physical ports on a specific device (switch/AP)"
  '{ phyInterfacesForVirtualDevice(virtualDeviceUUID: "'"$VIRTUAL_DEVICE_UUID"'") { UUID label portNumber portType isEnabled isConnected isTrunkPort isUplink portSpeedMbps nativeVLAN { name vlanID } } }'

  "Switch Port Stats"
  "Get traffic statistics for all ports on a switch"
  '{ switchPortStats(virtualDeviceUUID: "'"$VIRTUAL_DEVICE_UUID"'") { portNumber totalRxBytes totalTxBytes totalRxPackets totalTxPackets errorRxPackets errorTxPackets } }'
)

pretty_print() {
  if command -v python3 &> /dev/null; then
    echo "$1" | python3 -m json.tool 2>/dev/null || echo "$1"
  else
    echo "$1"
  fi
}

run_query() {
  local name="$1"
  local description="$2"
  local query="$3"

  echo -e "\n${BOLD}${CYAN}━━━ ${name} ━━━${RESET}"
  echo -e "${DIM}${description}${RESET}\n"

  if echo "$query" | grep -q "YOUR_.*_HERE"; then
    echo -e "${YELLOW}  SKIPPED: This query requires a UUID that hasn't been configured yet."
    echo -e "  Edit the variables at the top of this script to set it.${RESET}"
    return 0
  fi

  local escaped_query
  escaped_query=$(printf '%s' "$query" | sed 's/\\/\\\\/g; s/"/\\"/g')
  local payload='{"query": "'"$escaped_query"'"}'

  local response http_code body
  response=$(curl -s -w "\n%{http_code}" -X POST "$API_URL" \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer $API_TOKEN" \
    -d "$payload" \
    2>&1) || {
    echo -e "${RED}  ERROR: curl failed${RESET}"
    echo "$response"
    return 0
  }

  http_code=$(echo "$response" | tail -1)
  body=$(echo "$response" | sed '$d')

  if [[ "$http_code" -ge 200 && "$http_code" -lt 300 ]]; then
    if [[ "$body" == *'"errors"'* ]]; then
      echo -e "${YELLOW}  HTTP $http_code (with GraphQL errors):${RESET}"
    else
      echo -e "${GREEN}  HTTP $http_code OK${RESET}"
    fi
    pretty_print "$body"
  else
    echo -e "${RED}  HTTP $http_code${RESET}"
    pretty_print "$body"
  fi
}

print_header() {
  echo -e "${BOLD}"
  echo "╔══════════════════════════════════════════════════════════╗"
  echo "║           Meter Public GraphQL API Demo                 ║"
  echo "╚══════════════════════════════════════════════════════════╝"
  echo -e "${RESET}"
  echo -e "  Endpoint:  ${DIM}$API_URL${RESET}"
  echo -e "  Company:   ${DIM}$COMPANY_SLUG${RESET}"
  echo -e "  Network:   ${DIM}$NETWORK_UUID${RESET}"
  echo -e "  Time range:${DIM} $START_TIME to $END_TIME${RESET}"
}

print_header

filter="${1:-}"
total=$(( ${#QUERIES[@]} / 3 ))
ran=0

for (( i=0; i<${#QUERIES[@]}; i+=3 )); do
  name="${QUERIES[$i]}"
  description="${QUERIES[$i+1]}"
  query="${QUERIES[$i+2]}"

  if [[ -n "$filter" && "$name" != *"$filter"* ]]; then
    continue
  fi

  ran=$((ran + 1))
  echo -e "${DIM}[$ran/$total]${RESET}"
  run_query "$name" "$description" "$query"
done

if [[ $ran -eq 0 && -n "$filter" ]]; then
  echo -e "\n${YELLOW}No queries matched filter: \"$filter\"${RESET}"
  echo "Available queries:"
  for (( i=0; i<${#QUERIES[@]}; i+=3 )); do
    echo "  - ${QUERIES[$i]}"
  done
fi

echo -e "\n${DIM}Done. Ran $ran queries.${RESET}"
