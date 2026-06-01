# ESGA Pattern 10 Runbook: Network & Infrastructure Failures

## Document Information

| Field | Value |
|--------|--------|
| **Pattern ID** | Pattern_10 |
| **Platform** | ESGA |
| **Category** | Network & Infrastructure |
| **Severity** | P1 - Critical |
| **Last Updated** | 2026-05-15 |
| **Author** | Platform Engineering Team |

---

## 1. Overview

This runbook covers network and infrastructure failures that impact the ESGA DataPower gateway. These failures occur at the network layer below DataPower's control — including firewall rule changes, routing instability, MTU mismatches, packet loss, and physical or virtual NIC failures. Network failures are distinguished from backend connectivity issues by their broad impact: they typically affect multiple services simultaneously and correlate with infrastructure change activity.

---

## 2. Error Signatures

### 2.1 Common Error Messages

| Error Code | Message | Likely Cause |
|------------|---------|--------------|
| `ECONNREFUSED` | Connection refused | Firewall blocking traffic |
| `ETIMEDOUT` | Network timeout | Route black-holing or packet loss |
| `ENETUNREACH` | Network unreachable | Routing failure |
| `PACKET_LOSS_HIGH` | Elevated packet loss detected | Physical/virtual NIC degradation |
| `MTU_FRAGMENTATION` | IP fragmentation occurring | MTU mismatch in path |
| `NIC_ERROR` | Network interface error | Physical/virtual NIC failure |
| `PROXY_TUNNEL_FAILED` | Forward proxy unreachable | Proxy infrastructure failure |

### 2.2 Splunk Query for Detection

```
index=datapower sourcetype=dp:logs
| search "network unreachable" OR "ETIMEDOUT" OR "packet loss" OR "NIC" OR "route"
| stats count by error_code, backend_host, service_name
| timechart span=1m count by error_code
```

**Broad Impact Detection Query (Multiple Services Failing):**
```
index=datapower sourcetype=dp:logs
| search "connection" AND ("timeout" OR "refused" OR "unreachable")
| stats dc(service_name) as services_affected, count by _time
| where services_affected > 3
| timechart span=5m values(services_affected)
```

---

## 3. Architecture Context

```
External Client
      │
      │ (Internet / WAN)
      ▼
Enterprise Firewall / Load Balancer
      │
      │ (DMZ Network)
      ▼
DataPower (ESGA) ──── NIC ──── Internal Switch
      │
      │ (Internal Network)
      ▼
Backend Services

Key Network Failure Points:
[1] External firewall rule change → blocks inbound client traffic
[2] Internal firewall rule change → blocks DataPower → backend
[3] Route instability → intermittent packet loss
[4] MTU mismatch → fragmentation → silent TCP failures
[5] NIC failure → total or partial connectivity loss
[6] Forward proxy failure → outbound traffic blocked
```

---

## 4. Triage Decision Tree

```
Network / Infrastructure Failure
          │
          ▼
  How many services are affected?
          │
     ┌────┴────┐
     ▼         ▼
  SINGLE     MULTIPLE
  SERVICE    SERVICES
     │           │
     ▼           ▼
Go to 5.2    Was there a recent
(Firewall    network change?
Rule)            │
            ┌────┴────┐
            ▼         ▼
           YES         NO
            │           │
            ▼           ▼
        Go to 5.1    Go to 5.3
       (Change       (Network
       Impact)       Instability)
```

---

## 5. Troubleshooting Steps

### 5.1 Network Change Impact

**Symptoms:**
- Multiple services started failing at the same timestamp
- ServiceNow has network change activity at or just before failure time
- Network team may confirm a recent firewall or routing change

**Step-by-Step Resolution:**

| Step | Action | Expected Result |
|------|--------|----------------|
| 1 | Check ServiceNow change calendar for network changes in last 2 hours | Change record found |
| 2 | Contact network change implementer with failure timestamp | Change acknowledged as cause |
| 3 | Request rollback of the change if reversible | Change rolled back |
| 4 | If rollback not possible: work with network team on forward fix | Fix implemented |
| 5 | Validate all affected services recover after fix | Full service restoration |
| 6 | Conduct post-incident review with Change Management | CAB review scheduled |

**Key Questions for the Network Team:**
- Was a firewall rule added, modified, or deleted?
- Was a routing table updated?
- Was a virtual switch or VLAN changed?
- Was a load balancer VIP or pool modified?

---

### 5.2 Firewall Rule Blocking Traffic

**Symptoms:**
- Specific source → destination traffic failing
- `ECONNREFUSED` or instant timeout (not gradual)
- No backend errors — DataPower cannot even reach the backend IP

**Step-by-Step Resolution:**

1. **Identify the Blocked Traffic Path**
   - Source: DataPower egress IP(s)
   - Destination: Backend IP + Port
   - Protocol: TCP (HTTP/HTTPS)

2. **Verify Firewall is the Cause**
   - From DataPower CLI: `ping count 5 host <backend-ip>`
   - If ping succeeds but TCP fails: firewall is blocking the specific port
   - From an ops workstation: `telnet <backend-ip> <port>` — should connect

3. **Check DataPower Egress IP**
   - Navigate to **Status → Network → Interface Status**
   - Note the DataPower egress IP (may differ from management IP)
   - Confirm this IP is what backends see (may go through NAT)

4. **Submit Firewall Exception Request**
   - Raise ServiceNow change request for firewall rule update
   - Specify: Source IP, Destination IP, Destination Port, Protocol
   - Include business justification and approver
   - Network Security SLA: 2 hours for P2, immediate for P1

**Validation Command:**
```bash
# From DataPower CLI
ping count 5 host <backend-ip>

# From ops workstation
nc -zv <backend-ip> <port>
```

---

### 5.3 Route Instability / Packet Loss

**Symptoms:**
- Intermittent failures that don't follow a clear pattern
- `SSL_ERROR_SYSCALL` errors correlate with latency spikes
- Some requests succeed, others timeout
- Network monitoring shows packet loss on specific paths

**Step-by-Step Resolution:**

| Step | Action | Expected Result |
|------|--------|----------------|
| 1 | Check DataPower system logs for intermittent vs. sustained failure | Failure pattern characterized |
| 2 | Run `traceroute` from DataPower CLI to failing backend | Hop-by-hop path visible |
| 3 | Identify the hop with high latency or packet loss | Problem router/switch identified |
| 4 | Request packet capture between DataPower and backend from Network team | Packet loss evidence collected |
| 5 | Escalate to Network Infrastructure team with traceroute output | Route investigation initiated |
| 6 | As interim: enable retry policy on affected services in DataPower | Transient failures auto-retried |
| 7 | Monitor until Network team resolves route instability | Full recovery confirmed |

**Traceroute from DataPower CLI:**
```bash
traceroute host <backend-ip>
```

---

### 5.4 MTU Mismatch

**Symptoms:**
- Large payloads fail while small payloads succeed
- Failures are consistent above a specific payload size (~1400 bytes)
- `MTU_FRAGMENTATION` events in network monitoring
- TCP connections established but data transfer stalls

**Resolution Checklist:**

- [ ] Confirm symptoms match MTU profile: small requests succeed, large fail
- [ ] Engage Network team to run MTU path discovery test
- [ ] Check MTU configuration on DataPower network interface
- [ ] Check MTU configuration on all intermediate network hops
- [ ] Standard Ethernet MTU: 1500 bytes; VXLAN / tunnel overhead reduces effective MTU
- [ ] Set DataPower interface MTU to match network path MTU
- [ ] Alternatively: enable TCP MSS clamping on the gateway
- [ ] Test with a range of payload sizes after fix

**Navigate to MTU setting:**
- **Network → Interface → [Interface Name] → MTU**
- Default: 1500 bytes
- Adjust to match network path (typically 1400–1500 bytes)

---

## 6. Common Mistakes to Avoid

| Mistake | Impact | Prevention |
|---------|--------|------------|
| Not checking change calendar when multiple services fail | Root cause investigation delayed | Always correlate failure time with change records |
| Using management IP instead of egress IP for firewall rules | Firewall rule has no effect | Always confirm the actual DataPower egress IP |
| Not retaining traceroute output before network change | Evidence lost for post-incident review | Always save diagnostic output |
| Assuming backend is down when firewall is blocking | Wrong team engaged first | Test connectivity before escalating to app team |
| Not enabling retries during intermittent packet loss | Transient failures impact clients | Enable retry policy as interim mitigation |

---

## 7. Escalation Matrix

| Condition | Escalate To | SLA |
|-----------|------------|-----|
| Multiple services failing due to network change | Network Change Manager + P1 Bridge | Immediate |
| Firewall rule blocking production traffic | Network Security Team | 2 hours (P2) / Immediate (P1) |
| Route instability / packet loss | Network Infrastructure Team | 15 min response |
| MTU mismatch causing data transfer failures | Network Infrastructure Team | 2 hours |
| Physical / virtual NIC failure | Data Center / Virtualization Team | 15 min response |
| Issue persists > 15 min affecting multiple services | Incident Commander | Immediate |

---

## 8. Related Runbooks

- [Pattern 1: Backend Connection Timeout](ESGA_Pattern_1_Backend_Connection_Timeout.md)
- [Pattern 2: DNS Resolution Failure](ESGA_Pattern_2_DNS_Resolution_Failure.md)
- [Pattern 7: SSL/TLS Handshake Failure](Sample_Pattern_7_SSL_Handshake_Failure.md)
- [Pattern 9: DataPower System Resource Exhaustion](ESGA_Pattern_9_Resource_Exhaustion.md)

---

## 9. Revision History

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0 | 2026-05-15 | Platform Engineering Team | Initial creation |
