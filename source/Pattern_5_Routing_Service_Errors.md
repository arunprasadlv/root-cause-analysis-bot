# Pattern 5 Runbook: Routing & Service Configuration Errors

## Document Information

| Field | Value |
|--------|--------|
| **Pattern ID** | Pattern_5 |
| **Platform** | DataPower Gateway |
| **Category** | Service Configuration |
| **Severity** | P2 - High |
| **Last Updated** | 2026-05-15 |
| **Author** | Platform Engineering Team |

---

## 1. Overview

This runbook covers troubleshooting steps for routing and service configuration errors on the DataPower gateway. These errors occur when DataPower cannot match an incoming request to a service definition, or when the service is incorrectly configured to route to the backend. Routing errors are often caused by deployment mistakes, incorrect URL mappings, Multi-Protocol Gateway (MPGW) misconfiguration, or a service being disabled without notification.

---

## 2. Error Signatures

### 2.1 Common Error Messages

| Error Code | Message | Likely Cause |
|------------|---------|--------------|
| `HTTP 404` | Service not found | No matching service or URL mapping |
| `HTTP 503` | Service unavailable | Service disabled or backend group empty |
| `HTTP 502` | Bad gateway | Backend returned unexpected response |
| `0x00d30022` | No route matched | Request URL/verb not mapped to any handler |
| `SERVICE_DISABLED` | Service is disabled | MPGW or FSH disabled in DataPower |
| `FSH_NOT_LISTENING` | Front side handler not active | Port not bound or handler stopped |
| `BACKEND_URL_INVALID` | Malformed backend URL | Incorrect backend URL in service config |

### 2.2 Splunk Query for Detection

```
index=datapower sourcetype=dp:logs
| search "no route" OR "service not found" OR "503" OR "502" OR "service disabled"
| stats count by error_code, uri, service_name, http_method
| where count > 3
| sort -count
```

---

## 3. Architecture Context

```
Incoming Request
      │
      ▼
Front Side Handler (FSH)      ← Listens on port/protocol
      │
      ▼
Multi-Protocol Gateway (MPGW) ← Matches URL pattern to service
      │
      ▼
Processing Policy             ← Applies rules (auth, transform, etc.)
      │
      ▼
Backend URL / Load Balancer Group  ← Routes to backend
      │
      ▼
Backend Service
```

---

## 4. Triage Decision Tree

```
Routing / Service Error
          │
          ▼
  What HTTP status is returned?
          │
   ┌──────┼──────┐
   ▼      ▼      ▼
 404    503    502
  │      │      │
  ▼      ▼      ▼
5.1    5.2    5.3
(No    (Service (Bad
Route) Disabled) Gateway)
```

---

## 5. Troubleshooting Steps

### 5.1 No Route Matched (HTTP 404)

**Symptoms:**
- All requests to a specific URL return 404
- Error present in DataPower logs before reaching backend
- May follow a URL path change in a client application or API contract update

**Step-by-Step Resolution:**

| Step | Action | Expected Result |
|------|--------|----------------|
| 1 | Capture the exact request URI and HTTP method from logs | Full request path available |
| 2 | Navigate to **Objects → Multi-Protocol Gateway → [Service]** | Service config open |
| 3 | Check **URL Map** — confirm the request path matches a defined pattern | URL pattern identified |
| 4 | If path mismatch: update URL Map to include new path pattern | Route added |
| 5 | Check **HTTP Method** filter — confirm GET/POST/etc. is allowed | Method allowed |
| 6 | Verify the Front Side Handler port matches the incoming request port | Port confirmed |
| 7 | Save and **Apply** changes | Config persisted |
| 8 | Test via **Probe** or curl | HTTP 200 received |

**Validation Command:**
```bash
curl -v -X <METHOD> https://<datapower-host>:<port>/<uri>
```

---

### 5.2 Service Disabled (HTTP 503)

**Symptoms:**
- Requests return 503 immediately without reaching backend
- DataPower logs show `SERVICE_DISABLED` or `FSH_NOT_LISTENING`
- May follow a maintenance window, config deployment, or accidental disable

**Step-by-Step Resolution:**

1. **Check MPGW Service State**
   - Navigate to **Objects → Multi-Protocol Gateway → [Service Name]**
   - Check **Administrative State** — should be `Enabled`
   - If disabled: set to `Enabled` and click **Apply**

2. **Check Front Side Handler State**
   - Navigate to **Objects → HTTP Front Side Handler → [Handler Name]**
   - Confirm **Administrative State** is `Enabled`
   - Confirm the correct **Port** is configured and bound

3. **Check Domain State**
   - Navigate to **Control Panel → Domain Status**
   - Confirm the application domain is `Up`
   - If domain is down: investigate domain-level error before re-enabling

4. **Verify Load Balancer Group Has Active Members**
   - Navigate to **Objects → Load Balancer Group → [Group Name]**
   - Confirm at least one member is in `Active` state
   - Re-enable disabled members if backend is healthy

| Check | Location | Expected State |
|-------|----------|----------------|
| MPGW State | Objects → MPGW | Enabled |
| FSH State | Objects → HTTP FSH | Enabled |
| Domain State | Control Panel → Domain Status | Up |
| LB Group Members | Objects → Load Balancer Group | ≥1 Active |

---

### 5.3 Bad Gateway (HTTP 502)

**Symptoms:**
- Request reaches DataPower and is forwarded to backend
- Backend returns an unexpected or malformed response
- DataPower cannot parse backend response and returns 502 to client

**Step-by-Step Resolution:**

| Step | Action | Expected Result |
|------|--------|----------------|
| 1 | Enable **Probe** on the failing service | Capture backend response |
| 2 | Inspect the raw backend response in Probe capture | Identify malformed response |
| 3 | Check backend application logs for error context | Root cause in backend |
| 4 | Confirm backend URL in service config is correct | Backend URL validated |
| 5 | Confirm backend protocol matches service config (HTTP vs HTTPS) | Protocol match confirmed |
| 6 | If backend is returning 5xx: escalate to backend application team | Backend team engaged |
| 7 | If response format changed: update transformation to handle new format | DataPower policy updated |

---

### 5.4 Backend URL Misconfiguration

**Symptoms:**
- Service was recently deployed or migrated
- All requests to a service fail immediately
- Logs show `BACKEND_URL_INVALID` or connection refused to wrong host

**Resolution Checklist:**

- [ ] Navigate to **Objects → Multi-Protocol Gateway → [Service] → Backend**
- [ ] Verify backend hostname is correct (not an old/stale value)
- [ ] Verify backend port is correct (80/443/custom)
- [ ] Verify protocol is correct (HTTP vs HTTPS)
- [ ] Verify URL path prefix is correct if service uses a sub-path
- [ ] Test the backend URL directly from an ops workstation
- [ ] Apply corrected URL and re-test via Probe tool

---

## 6. Common Mistakes to Avoid

| Mistake | Impact | Prevention |
|---------|--------|------------|
| Using wildcard URL maps too broadly | Unexpected services matched | Use specific path prefixes in URL maps |
| Not checking service state after config deployment | Service left disabled post-deploy | Include service state check in deployment checklist |
| Hardcoding environment-specific backend URLs | Wrong backend in wrong environment | Use DataPower config variables per environment |
| Not testing 404 path in non-prod before go-live | Production 404 discovered by clients | Include negative path testing in regression suite |
| Forgetting to update URL map after API path change | All requests fail after API versioning | Coordinate API contract changes with gateway team |

---

## 7. Escalation Matrix

| Condition | Escalate To | SLA |
|-----------|------------|-----|
| Service disabled by unknown change | Change Management Investigation | 1 hour |
| Backend URL migration not communicated | Application Team + Gateway Team | 2 hours |
| Issue persists > 30 min | DataPower SME Team | 15 min response |
| Multiple services impacted | Incident Bridge — P1 escalation | Immediate |

---

## 8. Related Runbooks

- [Pattern 1: Backend Connection Timeout](Pattern_1_Backend_Connection_Timeout.md)
- [Pattern 4: Message Transformation Errors](Pattern_4_Message_Transformation_Errors.md)
- [Pattern 9: DataPower System Resource Exhaustion](Pattern_9_Resource_Exhaustion.md)

---

## 9. Revision History

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0 | 2026-05-15 | Platform Engineering Team | Initial creation |
