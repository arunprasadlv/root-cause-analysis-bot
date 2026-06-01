# ESGA Pattern 1 Runbook: Backend Connection Timeout

## Document Information

| Field | Value |
|--------|--------|
| **Pattern ID** | Pattern_1 |
| **Platform** | ESGA |
| **Category** | Backend Connectivity |
| **Severity** | P2 - High |
| **Last Updated** | 2026-05-15 |
| **Author** | Platform Engineering Team |

---

## 1. Overview

This runbook covers troubleshooting steps for backend connection timeout errors on the ESGA DataPower gateway. These errors occur when the gateway successfully establishes a TCP connection to a backend service but the service fails to respond within the configured timeout window, or when the TCP handshake itself cannot complete. Timeouts may affect a single backend or multiple services simultaneously depending on root cause.

---

## 2. Error Signatures

### 2.1 Common Error Messages

| Error Code | Message | Likely Cause |
|------------|---------|--------------|
| `0x00d30003` | Connection timed out | Backend unresponsive within timeout threshold |
| `ECONNREFUSED` | Connection refused | Backend port not listening |
| `ETIMEDOUT` | TCP connect timeout | Network path blocked or backend overloaded |
| `0x00d30033` | Read timeout | Backend accepted connection but stalled response |
| `Backend unavailable` | All nodes in LB group unhealthy | Load balancer group exhausted |

### 2.2 Splunk Query for Detection

```
index=datapower sourcetype=dp:logs
| search "connection timed out" OR "ETIMEDOUT" OR "backend unavailable" OR "read timeout"
| stats count by error_code, backend_host, service_name
| timechart span=5m count by backend_host
| where count > 5
```

---

## 3. Architecture Context

```
Client          HTTP/HTTPS            TCP/HTTPS
(App)    ──────────────►  Datapower  ───────────────►  Backend
                          (ESGA)         ↑               Service
                                         │
                                   Timeout Check:
                                   - Connect Timeout
                                   - Read Timeout
                                   - LB Health Check
                                   - Retry Policy
```

---

## 4. Triage Decision Tree

```
Backend Connection Timeout
          │
          ▼
  Is issue affecting ALL backends?
          │
     ┌────┴────┐
     ▼         ▼
    YES         NO
     │           │
     ▼           ▼
Go to 5.1   Is backend health check passing?
(Network/       │
Infra)     ┌────┴────┐
           ▼         ▼
          YES         NO
           │           │
           ▼           ▼
      Go to 5.2    Go to 5.3
   (Timeout Config) (Backend Down)
```

---

## 5. Troubleshooting Steps

### 5.1 All Backends Timing Out (Network / Infrastructure Issue)

**Symptoms:**
- Multiple unrelated backends failing simultaneously
- Errors started at a specific timestamp
- Network team may have change activity logged

**Step-by-Step Resolution:**

| Step | Action | Expected Result |
|------|--------|----------------|
| 1 | Check DataPower system logs for network interface errors | Identify NIC or route issue |
| 2 | Navigate to **Status → Network → Interface Status** | Confirm interfaces are UP |
| 3 | Run `ping` from DataPower CLI to known backend IPs | Verify basic reachability |
| 4 | Check for recent network change records in ServiceNow | Identify change causing issue |
| 5 | Engage Network team with timestamp of first failure | Route / firewall investigation |
| 6 | Verify DataPower default gateway is correct | Confirm routing table |
| 7 | Monitor DataPower CPU/memory under **Status → System** | Rule out resource exhaustion |

**Validation Command:**
```bash
# From DataPower CLI
show route
ping count 10 host <backend-ip>
```

---

### 5.2 Timeout Config Mismatch

**Symptoms:**
- Specific backend times out intermittently under load
- Errors increase during peak traffic windows
- Backend team confirms processing time exceeds DataPower timeout

**Resolution:**

| Parameter | Current Default | Recommended | Location |
|-----------|----------------|-------------|----------|
| Connect Timeout | 30s | 60s | Backend Service Object |
| Read Timeout | 60s | 120s | Backend Service Object |
| Persistent Connections | Disabled | Enabled | HTTP Front Side Handler |
| Retry Count | 1 | 3 | Load Balancer Group |
| Retry Interval | 0s | 5s | Load Balancer Group |

**Step-by-Step Resolution:**

1. **Update Backend Service Timeout**
   - Navigate to **Objects → Multi-Protocol Gateway → Backend Service**
   - Locate the affected service
   - Increase **Connect Timeout** and **Read Timeout** values
   - Click **Apply**

2. **Configure Retry Policy**
   - Navigate to **Objects → Load Balancer Group**
   - Set **Retry Policy** to `On Error`
   - Set **Retry Count** to `3`
   - Enable **Health Check** polling

3. **Enable Persistent Connections**
   - Navigate to **Objects → HTTP Front Side Handler**
   - Set **Persistent Connections** to `On`
   - Set **Max Persistent Reuse** to `1000`

---

### 5.3 Backend Service Down

**Symptoms:**
- All requests to one specific backend failing
- Health checks from DataPower load balancer failing
- Other consumers of the backend also reporting errors

**Resolution Checklist:**

- [ ] Confirm backend is down via direct health check from ops workstation
- [ ] Check backend application logs for crash or OOM errors
- [ ] Verify backend pod/process is running (Kubernetes / VM)
- [ ] Escalate to Application Team owning the backend service
- [ ] Temporarily remove the failed node from LB group in DataPower
- [ ] Re-add node once backend team confirms recovery
- [ ] Validate via DataPower **Probe** tool after recovery

**Navigate to LB Group to remove failed node:**
- **Objects → Load Balancer Group → [Group Name] → Members**
- Set failed member to **Disabled**

---

## 6. Common Mistakes to Avoid

| Mistake | Impact | Prevention |
|---------|--------|------------|
| Setting timeout too low for slow backends | Premature timeout failures | Profile backend response time first |
| Not enabling health checks on LB group | Failed node stays in rotation | Always configure active health checks |
| Increasing timeout without backend fix | Masks real performance issue | Fix backend, then tune timeout |
| Disabling retries entirely | Single transient failure causes P1 | Keep retry count ≥ 2 for critical services |
| Not alerting on timeout rate trends | Issue escalates silently | Set Splunk alert at > 5% timeout rate |

---

## 7. Escalation Matrix

| Condition | Escalate To | SLA |
|-----------|------------|-----|
| All backends timing out | Network Infrastructure Team | 15 min response |
| Backend application confirmed down | Application Owning Team | Per service SLA |
| Issue persists > 30 min | DataPower SME Team | 15 min response |
| Firewall blocking egress confirmed | Network Security | 2 hours |
| Persistent performance degradation | Capacity / Performance Team | 4 hours |

---

## 8. Related Runbooks

- [Pattern 2: DNS Resolution Failure](ESGA_Pattern_2_DNS_Resolution_Failure.md)
- [Pattern 7: SSL/TLS Handshake Failure](Sample_Pattern_7_SSL_Handshake_Failure.md)
- [Pattern 9: DataPower System Resource Exhaustion](ESGA_Pattern_9_Resource_Exhaustion.md)
- [Pattern 10: Network & Infrastructure Failures](ESGA_Pattern_10_Network_Infrastructure.md)

---

## 9. Revision History

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0 | 2026-05-15 | Platform Engineering Team | Initial creation |
