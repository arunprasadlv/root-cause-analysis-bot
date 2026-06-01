# ESGA Pattern 3 Runbook: Authentication & Authorization Failures

## Document Information

| Field | Value |
|--------|--------|
| **Pattern ID** | Pattern_3 |
| **Platform** | ESGA |
| **Category** | Authentication & Authorization |
| **Severity** | P2 - High |
| **Last Updated** | 2026-05-15 |
| **Author** | Platform Engineering Team |

---

## 1. Overview

This runbook provides troubleshooting steps for authentication and authorization failures on the ESGA DataPower gateway. These errors occur when a client request fails identity verification (authentication) or lacks sufficient permissions to access a resource (authorization). Common causes include expired OAuth tokens, invalid API keys, misconfigured AAA policies, JWT signature mismatches, and LDAP connectivity issues.

---

## 2. Error Signatures

### 2.1 Common Error Messages

| Error Code | Message | Likely Cause |
|------------|---------|--------------|
| `HTTP 401` | Unauthorized | Missing or invalid credentials |
| `HTTP 403` | Forbidden | Valid identity but insufficient permissions |
| `0x00d30010` | AAA authentication failed | AAA policy rejection |
| `JWT_SIGNATURE_INVALID` | JWT verification failed | Wrong signing key or tampered token |
| `TOKEN_EXPIRED` | OAuth token expired | Client using stale access token |
| `LDAP_BIND_FAILED` | LDAP authentication failed | LDAP server unreachable or wrong credentials |
| `API_KEY_NOT_FOUND` | API key invalid | Key revoked, deleted, or not provisioned |

### 2.2 Splunk Query for Detection

```
index=datapower sourcetype=dp:logs
| search "authentication failed" OR "401" OR "403" OR "JWT" OR "AAA" OR "API key"
| stats count by error_code, client_ip, service_name, uri
| where count > 5
| sort -count
```

---

## 3. Architecture Context

```
Client              Datapower (ESGA)                  Auth Backend
(App)   ──────────► AAA Policy Evaluation  ──────────► LDAP / OAuth Server
                         │                              API Key Store
                    ┌────┴─────────────────┐
                    │  Authentication       │
                    │  - OAuth Token Check  │
                    │  - JWT Validation     │
                    │  - API Key Lookup     │
                    │  - LDAP Bind          │
                    │                       │
                    │  Authorization        │
                    │  - Role/Scope Check   │
                    │  - IP Allowlist       │
                    └───────────────────────┘
```

---

## 4. Triage Decision Tree

```
Auth Failure (401/403)
          │
          ▼
  Check HTTP Status Code
          │
     ┌────┴────┐
     ▼         ▼
   HTTP 401  HTTP 403
     │           │
     ▼           ▼
What auth     Go to 5.4
method?      (Authorization
     │         Policy)
  ┌──┼──┐
  ▼  ▼  ▼
JWT OAuth LDAP/API Key
 │    │       │
 ▼    ▼       ▼
5.1  5.2     5.3
```

---

## 5. Troubleshooting Steps

### 5.1 JWT Signature / Validation Failure

**Symptoms:**
- All requests from a specific client application failing with 401
- Error started after a key rotation event
- `JWT_SIGNATURE_INVALID` or `JWT_DECODE_FAILED` in logs

**Step-by-Step Resolution:**

| Step | Action | Expected Result |
|------|--------|----------------|
| 1 | Capture failing JWT token from request logs | Raw token available |
| 2 | Decode JWT payload at jwt.io (non-production only) | Inspect claims and algorithm |
| 3 | Navigate to **Objects → Crypto → Crypto Key** | View current signing keys |
| 4 | Confirm key alias in DataPower matches `kid` field in JWT header | Key ID alignment |
| 5 | If key rotated: upload new public key to **Crypto Certificate** | Updated key in place |
| 6 | Update **AAA Policy → JWT Validation** to reference new key | Policy updated |
| 7 | Apply changes and test with a valid fresh token | 200 OK response |

**Validation Command:**
```bash
# Decode JWT to inspect claims (base64 decode the payload segment)
echo "<JWT_PAYLOAD_SEGMENT>" | base64 -d | python3 -m json.tool
```

---

### 5.2 OAuth Token Failure (Expired / Invalid)

**Symptoms:**
- Client receiving 401 with `TOKEN_EXPIRED` message
- Issue affects specific client apps, not all
- Errors correlate with long-running sessions

**Step-by-Step Resolution:**

1. **Verify Token Expiry**
   - Check DataPower AAA logs for the exact rejection reason
   - Confirm whether token is expired or truly invalid

2. **Check Clock Skew**
   - Navigate to **Status → System → Date/Time**
   - Verify DataPower system clock matches NTP server
   - Acceptable skew: < 5 minutes

3. **Validate OAuth Server Connectivity**
   - Navigate to **Objects → AAA Policy → Token Validation URL**
   - Use **Probe** tool to confirm OAuth introspection endpoint is reachable
   - Check for HTTP 200 response from OAuth server

4. **Review Token Lifetime Configuration**

| Parameter | Check | Location |
|-----------|-------|----------|
| Access Token TTL | Not shorter than expected | OAuth Provider Config |
| Clock Skew Tolerance | Set to 300s (5 min) | AAA Policy |
| Token Introspection URL | Correct and reachable | AAA Policy → OAuth |

---

### 5.3 LDAP / API Key Authentication Failure

**Symptoms:**
- Specific users or API key consumers failing
- `LDAP_BIND_FAILED` or `API_KEY_NOT_FOUND` in logs
- Issue may affect all users (LDAP server down) or select users (revoked key)

**Step-by-Step Resolution:**

**For LDAP failures:**

| Step | Action | Expected Result |
|------|--------|----------------|
| 1 | Navigate to **Objects → AAA Policy → LDAP Server** | Check server config |
| 2 | Verify LDAP server hostname and port (389 / 636) | Correct endpoint |
| 3 | Use DataPower **Probe** to test LDAP bind | Successful bind response |
| 4 | Confirm bind DN and password are current | Credentials valid |
| 5 | Check LDAP server health with Directory team | Server status confirmed |
| 6 | If LDAP down: engage Directory Services team | Restoration of LDAP service |

**For API Key failures:**

| Step | Action | Expected Result |
|------|--------|----------------|
| 1 | Identify the failing API key from request logs | Key value captured |
| 2 | Look up key in API Management platform (APIM / ServiceNow) | Check key status |
| 3 | If key revoked: client must request a new key | New key provisioned |
| 4 | If key valid: check DataPower key store is synced | Key present in DataPower |
| 5 | Re-sync API key store if out of date | Key store updated |

---

### 5.4 Authorization Policy Denial (HTTP 403)

**Symptoms:**
- Client authenticates successfully (no 401) but gets 403
- Specific endpoints or methods (POST/DELETE) failing
- Access recently changed for a service or user group

**Resolution Checklist:**

- [ ] Confirm client identity from DataPower logs (which user / role authenticated)
- [ ] Review AAA Authorization policy in **Objects → AAA Policy → Authorization**
- [ ] Check role-to-resource mapping for the failing endpoint
- [ ] Verify the client's token/credentials carry the required scope or role claim
- [ ] If scope missing: client application must request correct OAuth scope
- [ ] If role mapping incorrect: update Authorization policy and apply
- [ ] Test with a token carrying the correct scope/role

---

## 6. Common Mistakes to Avoid

| Mistake | Impact | Prevention |
|---------|--------|------------|
| Not updating DataPower key after JWT key rotation | All requests fail with 401 | Include DataPower in key rotation runbook |
| Ignoring clock skew between DataPower and OAuth server | Intermittent token rejections | Monitor NTP sync status |
| Using service account credentials for LDAP bind that expire | LDAP failures after password rotation | Use non-expiring service accounts |
| Hardcoding API keys in client applications | Key compromise is undetectable | Use secrets management vault |
| Not logging auth failure details | Slow triage | Enable verbose AAA logging in non-prod |

---

## 7. Escalation Matrix

| Condition | Escalate To | SLA |
|-----------|------------|-----|
| LDAP server down affecting all users | Directory Services Team | 15 min response |
| OAuth server unreachable | Identity Platform Team | 15 min response |
| JWT key rotation coordination needed | Security / PKI Team | 4 hours |
| Issue persists > 30 min | DataPower SME Team | 15 min response |
| Suspected security breach / token theft | Security Operations (SOC) | Immediate |

---

## 8. Related Runbooks

- [Pattern 7: SSL/TLS Handshake Failure](Sample_Pattern_7_SSL_Handshake_Failure.md)
- [Pattern 6: Security Policy Violations](ESGA_Pattern_6_Security_Policy_Violations.md)
- [Pattern 8: Certificate Expiration Alerts](ESGA_Pattern_8_Certificate_Expiration.md)

---

## 9. Revision History

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0 | 2026-05-15 | Platform Engineering Team | Initial creation |
