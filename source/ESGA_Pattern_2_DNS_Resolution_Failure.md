# ESGA Pattern 2 Runbook: DNS Resolution Failure

## Document Information

| Field | Value |
|--------|--------|
| **Pattern ID** | Pattern_2 |
| **Platform** | ESGA |
| **Category** | Backend Connectivity |
| **Severity** | P2 - High |
| **Last Updated** | 2026-05-15 |
| **Author** | Platform Engineering Team |

---

## 1. Overview

This runbook covers DNS resolution failures on the ESGA DataPower gateway. These errors occur when DataPower cannot resolve a backend hostname to an IP address before establishing a connection. DNS failures are often silent — they surface as generic connection errors and are frequently misdiagnosed as backend outages. They commonly follow infrastructure changes such as backend migrations, hostname renames, or DNS server maintenance.

---

## 2. Error Signatures

### 2.1 Common Error Messages

| Error Code | Message | Likely Cause |
|------------|---------|--------------|
| `DNS_RESOLVE_FAILED` | Failed to resolve hostname | DNS server unreachable or record missing |
| `NXDOMAIN` | Non-existent domain | Hostname deleted or misspelled |
| `SERVFAIL` | DNS server failure | Upstream DNS infrastructure issue |
| `TIMEOUT` | DNS query timed out | DNS server overloaded or unreachable |
| `0x00d30001` | Unknown host | Backend hostname not resolvable |

### 2.2 Splunk Query for Detection

```
index=datapower sourcetype=dp:logs
| search "DNS" OR "resolve" OR "unknown host" OR "NXDOMAIN"
| stats count by error_code, backend_host, service_name
| where count > 3
| sort -count
```

---

## 3. Architecture Context

```
Client               Datapower (ESGA)                  DNS Server
(App)   ──────────►  1. Extract backend hostname   ──►  (Enterprise DNS)
                     2. DNS Query → resolve IP           │
                     3. TCP Connect → backend IP   ◄─────┘
                                                    Resolved IP
                     If DNS fails:
                     - Connection aborted at step 2
                     - Error returned to client
```

---

## 4. Triage Decision Tree

```
DNS Resolution Failure
          │
          ▼
  Is it a single hostname or multiple?
          │
     ┌────┴────┐
     ▼         ▼
  SINGLE     MULTIPLE
     │           │
     ▼           ▼
Does nslookup   Go to 5.3
resolve it?    (DNS Server Issue)
     │
┌────┴────┐
▼         ▼
YES        NO
 │          │
 ▼          ▼
Go to 5.2  Go to 5.1
(DataPower  (Record
DNS Config) Missing)
```

---

## 5. Troubleshooting Steps

### 5.1 DNS Record Missing or Incorrect

**Symptoms:**
- Specific hostname fails to resolve
- `nslookup` from external systems also fails
- Error started after a backend migration or rename

**Step-by-Step Resolution:**

| Step | Action | Expected Result |
|------|--------|----------------|
| 1 | Run `nslookup <hostname>` from an ops workstation | Confirm if record exists externally |
| 2 | Check ServiceNow for recent backend migration change records | Identify hostname rename |
| 3 | Contact DNS / Infrastructure team to verify A/CNAME record | Confirm record status |
| 4 | If hostname changed: update DataPower backend service URL | Correct hostname in use |
| 5 | If record missing: raise DNS provisioning request | New record created |
| 6 | After DNS fix: test via DataPower **Probe** tool | Successful resolution and connection |

**Validation Command:**
```bash
# From ops workstation
nslookup <backend-hostname> <dns-server-ip>
dig <backend-hostname> @<dns-server-ip>
```

---

### 5.2 DataPower DNS Configuration Issue

**Symptoms:**
- Hostname resolves fine from external workstations
- DataPower-specific failures only
- May follow DataPower config change or domain restore

**Step-by-Step Resolution:**

1. **Verify DataPower DNS Server Config**
   - Navigate to **Network → DNS Settings**
   - Confirm Primary and Secondary DNS server IPs are correct
   - Confirm **Search Domains** include the correct domain suffix

2. **Check Static Host Entries**
   - Navigate to **Network → Host Aliases**
   - Look for stale or conflicting static entries overriding DNS
   - Remove any incorrect static entries

3. **Flush DNS Cache**
   - Navigate to **Status → DNS Cache**
   - Click **Flush** to clear cached records
   - Re-test backend connection

4. **Verify DNS Reachability from DataPower**
   - DataPower CLI: `ping count 3 host <dns-server-ip>`
   - Confirm DNS servers respond

| Parameter | Check | Location |
|-----------|-------|----------|
| Primary DNS | Correct enterprise DNS IP | Network → DNS Settings |
| Secondary DNS | Failover DNS IP configured | Network → DNS Settings |
| Search Domain | Matches backend domain | Network → DNS Settings |
| DNS Cache TTL | Not stale (flush if needed) | Status → DNS Cache |

---

### 5.3 DNS Server Infrastructure Issue

**Symptoms:**
- Multiple unrelated backends failing simultaneously
- Errors start at a specific timestamp
- Network / infrastructure team may have DNS maintenance activity

**Resolution Checklist:**

- [ ] Confirm DNS server health with Infrastructure team
- [ ] Check for planned DNS maintenance windows in ServiceNow
- [ ] Temporarily add static host entries for critical backends as workaround
- [ ] Monitor until DNS infrastructure is restored
- [ ] Remove static entries after DNS service confirmed stable
- [ ] Review and update secondary DNS server config in DataPower

**Adding a Temporary Static Entry:**
- Navigate to **Network → Host Aliases**
- Add: Hostname → Resolved IP (obtained from ops workstation nslookup)
- Remove entry once DNS is restored

---

## 6. Common Mistakes to Avoid

| Mistake | Impact | Prevention |
|---------|--------|------------|
| Leaving static host entries in permanently | Stale IP after backend migration | Remove static entries once DNS is stable |
| Not configuring secondary DNS | Single point of DNS failure | Always configure a secondary DNS server |
| Hardcoding IP addresses in backend services | Breaks on infrastructure change | Always use hostnames with proper DNS |
| Forgetting to flush DNS cache after fix | Old failed record persists | Always flush cache after DNS record update |
| Not verifying DNS after DataPower restore | Restored config may have wrong DNS | Include DNS check in DR runbook |

---

## 7. Escalation Matrix

| Condition | Escalate To | SLA |
|-----------|------------|-----|
| DNS record missing or incorrect | DNS / Infrastructure Team | 2 hours |
| DNS server infrastructure failure | Network Infrastructure Team | 15 min response |
| Multiple services impacted | Incident Bridge — P1 escalation | Immediate |
| Issue persists > 30 min | DataPower SME Team | 15 min response |

---

## 8. Related Runbooks

- [Pattern 1: Backend Connection Timeout](ESGA_Pattern_1_Backend_Connection_Timeout.md)
- [Pattern 10: Network & Infrastructure Failures](ESGA_Pattern_10_Network_Infrastructure.md)

---

## 9. Revision History

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0 | 2026-05-15 | Platform Engineering Team | Initial creation |
