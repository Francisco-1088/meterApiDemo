import requests

def baseRequest(method='POST', url='', headers={}, payload=''):
    response = requests.request(method, url, headers=headers, data=payload)
    return response

def companyBySlug(company_slug):
    """Fetch basic company details by slug
    Args:
        company_slug (str): The slug identifier for the company (e.g., "meter")
    Returns:    
        payload: GraphQL query string to fetch company details based on the provided slug
    """
    payload = '{"query": "' + '{ companyBySlug(slug: \\"' + company_slug + '\\") { uuid slug name isCustomer websiteDomain } }' + '"}'
    return payload

def networksUplinkQualities(network_uuid, duration_seconds=14400, step_seconds=300):
    """Get uplink quality metrics across multiple networks
    Args:        
        network_uuid (str): UUID of the network to query
        duration_seconds (int): Time range to query in seconds (default: 4 hours)
        step_seconds (int): Time step for metrics aggregation in seconds (default: 5 minutes)
    Returns:
        payload: GraphQL query string to fetch uplink quality metrics for the specified network and time range
    """
    payload = '{"query": "' + '{ networksUplinkQualities(networkUUIDs: [\\"' + network_uuid + '\\"], filter: { durationSeconds: ' + str(duration_seconds) + ', stepSeconds: ' + str(step_seconds) + ' }) { metadata { minValue maxValue } values { timestamp value phyInterfaceUUID networkUUID } } }' + '"}'
    return payload

def networkClients(network_uuid):
    """List active clients on a network with connection details
    Args:        
        network_uuid (str): UUID of the network to query
    Returns:
        payload: GraphQL query string to fetch active clients on the specified network along with their connection details
    """
    payload = '{"query": "' + '{ networkClients(networkUUID: \\"' + network_uuid + '\\") { macAddress ip clientName isWireless signal lastSeen connectedVLAN { name vlanID } connectedSSID { ssid } } }' + '"}'
    return payload

def companyClients(company_uuid, network_uuid):
    """List clients across multiple networks for a company
    Args:
        company_uuid (str): UUID of the company to query
        network_uuid (str): UUID of the network to query
    Returns:
        payload: GraphQL query string to fetch clients across multiple networks for the specified company
    """
    payload = '{"query": "' + '{ networksClients(companyUUID: \\"' + company_uuid + '\\", networkUUIDs: [\\"' + network_uuid + '\\"]) { macAddress ip clientName isWireless lastSeen } }' + '"}'
    return payload

def uplinkPhyInterfacesForNetwork(network_uuid):
    """List uplink interfaces for a network (WAN connections)
    Args:
        network_uuid (str): UUID of the network to query
    Returns:
        payload: GraphQL query string to fetch uplink interfaces for the specified network
    """
    payload = '{"query": "' + '{ uplinkPhyInterfacesForNetwork(networkUUID: \\"' + network_uuid + '\\") { UUID label portNumber isEnabled isUplink isUplinkActive portSpeedMbps portType nativeVLAN { name vlanID } } }' + '"}'
    return payload

def bssidsForNetwork(network_uuid):
    """List wireless BSSIDs (access point radios) on a network
    Args:
        network_uuid (str): UUID of the network to query
    Returns:
        payload: GraphQL query string to fetch wireless BSSIDs on the specified network
    """
    payload = '{"query": "' + '{ bssidsForNetwork(networkUUID: \\"' + network_uuid + '\\") { BSSID isActive radioBand accessPointSerialNumber SSID { ssid isEnabled } } }' + '"}'
    return payload

def activeClients(network_uuid, duration_seconds=14400, step_seconds=300):
    """Get active client counts (wired vs wireless) over the last 4 hours
    Args:
        network_uuid (str): UUID of the network to query
        duration_seconds (int): Time range to query in seconds (default: 4 hours)
        step_seconds (int): Time step for metrics aggregation in seconds (default: 5 minutes)
    Returns:
        payload: GraphQL query string to fetch active client counts for the specified network and time range
    """
    payload = '{"query": "' + '{ activeClients(networkUUID: \\"' + network_uuid + '\\", filter: { durationSeconds: ' + str(duration_seconds) + ', stepSeconds: ' + str(step_seconds) + ' }) { wired { timestamp value } wireless { timestamp value } } }' + '"}'
    return payload

def networkUplinkThroughput(network_uuid, duration_seconds=14400, step_seconds=300):
    """Get WAN throughput metrics for a network over the last 4 hours
    Args:
        network_uuid (str): UUID of the network to query
        duration_seconds (int): Time range to query in seconds (default: 4 hours)
        step_seconds (int): Time step for metrics aggregation in seconds (default: 5 minutes)
    Returns:
        payload: GraphQL query string to fetch WAN throughput metrics for the specified network and time range
    """
    payload = '{"query": "' + '{ networkUplinkThroughput(networkUUID: \\"' + network_uuid + '\\", filter: { durationSeconds: ' + str(duration_seconds) + ', stepSeconds: ' + str(step_seconds) + ' }) { metadata { minValue maxValue } values { timestamp value direction phyInterfaceUUID } } }' + '"}'
    return payload

def networkUplinkQuality(network_uuid, duration_seconds=14400, step_seconds=300):
    """Get WAN quality (latency/jitter/packet loss) metrics over the last 4 hours
    Args:
        network_uuid (str): UUID of the network to query
        duration_seconds (int): Time range to query in seconds (default: 4 hours)
        step_seconds (int): Time step for metrics aggregation in seconds (default: 5 minutes)
    Returns:
        payload: GraphQL query string to fetch WAN quality metrics for the specified network and time range
    """
    payload = '{"query": "' + '{ networkUplinkQuality(networkUUID: \\"' + network_uuid + '\\", filter: { durationSeconds: ' + str(duration_seconds) + ', stepSeconds: ' + str(step_seconds) + ' }) { metadata { minValue maxValue } values { timestamp value phyInterfaceUUID } } }' + '"}'
    return payload

def recentEventLogEventsPage(network_uuid, limit=20):
    """Fetch recent network events (last 20)
    Args:
        network_uuid (str): UUID of the network to query
        limit (int): Maximum number of events to return (default: 20)
    Returns:
        payload: GraphQL query string to fetch recent network events for the specified network
    """
    payload = '{"query": "' + '{ recentEventLogEventsPage(networkUUID: \\"' + network_uuid + '\\", limit: ' + str(limit) + ') { total events { eventType eventTypeAPIName generatedAt networkUUID } } }' + '"}'
    return payload

def phyInterfacesForVirtualDevice(virtual_device_uuid):
    """List all physical ports on a specific device (switch/AP)
    Args:
        virtual_device_uuid (str): UUID of the virtual device to query
    Returns:
        payload: GraphQL query string to fetch physical interfaces for the specified virtual device
    """
    payload = '{"query": "' + '{ phyInterfacesForVirtualDevice(virtualDeviceUUID: \\"' + virtual_device_uuid + '\\") { UUID label portNumber portType isEnabled isConnected isTrunkPort isUplink portSpeedMbps nativeVLAN { name vlanID } } }' + '"}'
    return payload

def switchPortStats(virtual_device_uuid):
    """Get traffic statistics for all ports on a switch
    Args:
        virtual_device_uuid (str): UUID of the virtual device to query
    Returns:
        payload: GraphQL query string to fetch traffic statistics for all ports on the specified switch
    """
    payload = '{"query": "' + '{ switchPortStats(virtualDeviceUUID: \\"' + virtual_device_uuid + '\\") { portNumber totalRxBytes totalTxBytes totalRxPackets totalTxPackets errorRxPackets errorTxPackets } }' + '"}'
    return payload