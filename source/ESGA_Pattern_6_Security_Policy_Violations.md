# ESGA Pattern 6 Runbook: Security Policy Violations

## Document Information

| Field | Value |
|--------|--------|
| **Pattern ID** | Pattern_6 |
| **Platform** | ESGA |
| **Category** | Security |
| **Severity** | P2 - High |
| **Last Updated** | 2026-05-15 |
| **Author** | Platform Engineering Team |

---

## 1. Overview

This runbook covers security policy violations on the ESGA DataPower gateway. These events occur when DataPower enforces gateway-level security controls and blocks a request. This includes rate limiting enforcement, IP allowlist rejections, threat protection triggers (SQL injection, XSS, oversized payloads), CORS policy violations, and WS-Security failures. Unlike authentication failures, these errors are enforced by DataPower policy before the request reaches any backend service.

---

## 2. Error Signatures

### 2.1 Common Error Messages

| Error Code | Message | Likely Cause |
|------------|---------|--------------|
| `HTTP 429` | Too Many Requests | Rate limit exceeded |
| `HTTP 403` | Forbidden | IP not in allowlist |
| `HTTP 400` | Bad Request | Threat protection pattern detected |
| `HTTP 413` | Payload Too Large | Request body exceeds size limit |
| `RATE_LIMIT_EXCEEDED` | Client exceeded request quota | Token bucket exhausted |
| `IP_NOT_WHITELISTED` | Source IP blocked | IP allowlist enforcement |
| `THREAT_DETECTED` | Malicious pattern found | SQL injection / XSS pattern matched |
| `CORS_REJECTED` | Origin not permitted | CORS policy violation |
| `WS_SECURITY_FAILED` | WS-Security validation failed | Invalid SOAP security header |

### 2.2 Splunk Query for Detection

```
index=datapower sourcetype=dp:logs
| search "rate limit" OR "IP not whitelisted" OR "threat detected" OR "CORS" OR "429" OR "413"
| stats count by error_code, client_ip, service_name, uri
| timechart span=5m count by error_code
| where count > 10
```

---

## 3. Architecture Context

```
Incoming Request
      │
      ▼
DataPower Security Enforcement Layer
┌──────────────────────────────────────────┐
│  1. IP Allowlist Check                   │
│     → Block if source IP not permitted   │
│                                          │
│  2. Rate Limit Check                     │
│     → Block if quota exceeded            │
│                                          │
│  3. Payload / Threat Inspection          │
│     → Block if malicious pattern found   │
│     → Block if payload too large         │
│                                          │
│  4. CORS Policy Check                    │
│     → Block if Origin not in allow-list  │
│                                          │
│  5. WS-Security Validation (SOAP only)   │
│     → Block if signature invalid         │
└──────────────────────────────────────────┘
      │
      ▼
Processing continues to auth → transform → backend
```

---

## 4. Triage Decision Tree

```
Security Policy Violation
          │
          ▼
  What error / HTTP status?
          │
   ┌──────┼──────┬──────┐
   ▼      ▼      ▼      ▼
 429    403    400    413
  │      │      │      │
  ▼      ▼      ▼      ▼
5.1    5.2    5.3    5.4
(Rate  (IP    (Threat (Size
Limit) Allowlist) Detect) Limit)
```

---

## 5. Troubleshooting Steps

### 5.1 Rate Limit Exceeded (HTTP 429)

**Symptoms:**
- Client receiving 429 responses, especially at peak traffic
- Rate limit errors spike at regular intervals
- Affects a specific client app or consumer group

**Step-by-Step Resolution:**

| Step | Action | Expected Result |
|------|--------|----------------|
| 1 | Identify the consumer identity from request logs (API key / client ID) | Consumer identified |
| 2 | Navigate to **Objects → Rate Limit Policy → [Policy Name]** | View current limits |
| 3 | Review current limit vs. client's legitimate traffic volume | Determine if limit is too low |
| 4 | If limit is appropriate: client must reduce request frequency | Client team notified |
| 5 | If limit needs adjustment: raise change request with API governance | Limit updated per approval |
| 6 | Check if burst traffic is legitimate (batch job / event-driven spike) | Context understood |
| 7 | Consider adding burst allowance for scheduled jobs | Burst policy configured |

**Rate Limit Configuration:**

| Parameter | Location | Description |
|-----------|----------|-------------|
| Requests per Second | Rate Limit Policy | Sustained rate limit |
| Burst Limit | Rate Limit Policy | Short-burst allowance |
| Window Size | Rate Limit Policy | Rolling window duration |
| Consumer Key | API Key Plan | Per-consumer limit |

---

### 5.2 IP Not Whitelisted (HTTP 403)

**Symptoms:**
- All requests from a specific IP or IP range return 403
- Error message references IP allowlist
- Started after a client environment change or new integration

**Step-by-Step Resolution:**

1. **Identify the Blocked IP**
   - Extract `client_ip` from DataPower access logs
   - Confirm the IP is the legitimate source (not a NAT IP behind a load balancer)

2. **Review Current IP Allowlist**
   - Navigate to **Objects → IP Allowlist → [Policy Name]**
   - Check whether the client IP or CIDR range is present

3. **Add IP to Allowlist**
   - If the IP is legitimate: raise a firewall/allowlist change request via ServiceNow
   - Obtain approval from Security team
   - Add IP/CIDR to the allowlist object
   - Apply changes

4. **Verify NAT / Proxy IPs**
   - Confirm whether the client traffic passes through a load balancer or proxy
   - The DataPower-visible IP may be the proxy IP, not the original client IP
   - Obtain correct egress IP from the client's network team

---

### 5.3 Threat Protection Triggered (HTTP 400)

**Symptoms:**
- Specific payload fields triggering rejections
- DataPower logs show `THREAT_DETECTED` with pattern details
- May be a legitimate request flagged as a false positive

**Step-by-Step Resolution:**

| Step | Action | Expected Result |
|------|--------|----------------|
| 1 | Capture the rejected payload via Probe | Raw payload available |
| 2 | Identify which pattern triggered the rule (SQL, XSS, command injection) | Pattern identified |
| 3 | Assess if this is a genuine threat or false positive | Classification made |
| 4 | If genuine threat: block and notify Security Operations (SOC) | SOC alerted |
| 5 | If false positive: review pattern specificity in **Threat Protection Policy** | Policy scope reviewed |
| 6 | Narrow the matching pattern to reduce false positives | Refined rule applied |
| 7 | Never disable threat protection entirely for a service | Security posture maintained |

**False Positive Assessment Checklist:**
- [ ] Does the payload contain SQL-like syntax for legitimate reasons? (e.g., database admin APIs)
- [ ] Does the payload contain HTML for legitimate reasons? (e.g., content management APIs)
- [ ] Is the pattern in a field that realistically could carry user-controlled input?
- [ ] Has the Security team reviewed and approved the exception?

---

### 5.4 Payload Size Limit Exceeded (HTTP 413)

**Symptoms:**
- Large file uploads or bulk API requests returning 413
- Issue started after a new use case was onboarded
- Consistent failure above a certain payload size

**Resolution Checklist:**

- [ ] Identify the payload size from request logs
- [ ] Navigate to **Objects → HTTP Front Side Handler → [Handler]**
- [ ] Review **Max Request Size** setting
- [ ] Assess whether the use case legitimately requires a larger payload
- [ ] If approved: raise a change request to increase the limit for the affected service
- [ ] Consider whether chunked transfer or multipart upload is more appropriate
- [ ] Apply change and validate with the client team

| Parameter | Default | Location |
|-----------|---------|----------|
| Max Request Size | 10 MB | HTTP Front Side Handler |
| Max Response Size | 10 MB | HTTP Front Side Handler |
| Buffer Size | 256 KB | Multi-Protocol Gateway |

---

## 6. Common Mistakes to Avoid

| Mistake | Impact | Prevention |
|---------|--------|------------|
| Disabling threat protection to fix false positive | Security posture degraded | Narrow the pattern, never disable entirely |
| Adding overly broad CIDR ranges to IP allowlist | Unintended IP access granted | Use precise /32 IPs where possible |
| Setting rate limits without profiling normal traffic | Legitimate traffic throttled | Baseline traffic before setting limits |
| Not alerting on repeated 429s | Abusive client goes undetected | Set Splunk alert for sustained 429 spikes |
| Approving IP allowlist changes without security review | Unauthorized access | All allowlist changes require Security approval |

---

## 7. Escalation Matrix

| Condition | Escalate To | SLA |
|-----------|------------|-----|
| Genuine threat / attack pattern detected | Security Operations Center (SOC) | Immediate |
| IP allowlist change requires approval | Network Security Team | 2 hours |
| Rate limit adjustment requires governance approval | API Governance Team | 4 hours |
| Issue persists > 30 min | DataPower SME Team | 15 min response |
| Multiple services under coordinated attack | SOC + Incident Commander | Immediate |

---

## 8. Related Runbooks

- [Pattern 3: Authentication & Authorization Failures](ESGA_Pattern_3_Authentication_Authorization_Failures.md)
- [Pattern 7: SSL/TLS Handshake Failure](Sample_Pattern_7_SSL_Handshake_Failure.md)
- [Pattern 10: Network & Infrastructure Failures](ESGA_Pattern_10_Network_Infrastructure.md)

---

## 9. Revision History

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0 | 2026-05-15 | Platform Engineering Team | Initial creation |
