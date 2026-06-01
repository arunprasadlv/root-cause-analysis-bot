# ESGA Pattern 8 Runbook: Certificate Expiration Alerts

## Document Information

| Field | Value |
|--------|--------|
| **Pattern ID** | Pattern_8 |
| **Platform** | ESGA |
| **Category** | SSL/TLS Connectivity |
| **Severity** | P1 - Critical (if expired) / P3 - Medium (if advance warning) |
| **Last Updated** | 2026-05-15 |
| **Author** | Platform Engineering Team |

---

## 1. Overview

This runbook covers certificate expiration events on the ESGA DataPower gateway. Certificate expiration is one of the most common and preventable causes of production outages. DataPower uses certificates for mTLS connections to backends, client authentication, and front-end HTTPS listeners. This runbook covers both proactive handling of expiry alerts (before expiry) and reactive recovery when a certificate has already expired and is causing active failures.

---

## 2. Error Signatures

### 2.1 Common Error Messages

| Error Code | Message | Likely Cause |
|------------|---------|--------------|
| `CERTIFICATE_EXPIRED` | Certificate has expired | Cert past its Not After date |
| `CERTIFICATE_EXPIRY_WARNING` | Certificate expires in N days | Proactive alert from monitoring |
| `0x80090326` | SSL handshake failed | Expired cert causing handshake failure |
| `CERTIFICATE_VERIFY_FAILED` | Unable to verify certificate | Expired cert in trust chain |
| `CERT_NOT_YET_VALID` | Certificate not yet valid | Clock skew or cert issued with future start date |

### 2.2 Splunk Query for Detection

```
index=datapower sourcetype=dp:logs
| search "certificate expired" OR "expiry" OR "CERTIFICATE_EXPIRY" OR "not valid after"
| stats count by cert_subject, service_name, backend_host, expiry_date
| sort expiry_date asc
```

**Proactive Expiry Query (Next 30 Days):**
```
index=datapower sourcetype=dp:cert_inventory
| where expiry_date < relative_time(now(), "+30d")
| table cert_name, subject, service_name, expiry_date, days_to_expiry
| sort days_to_expiry asc
```

---

## 3. Architecture Context

```
Certificates in DataPower — Three Distinct Roles:

1. Frontend (Inbound)         2. Backend (Outbound mTLS)    3. Trust / Validation
   ┌──────────────┐              ┌──────────────┐              ┌──────────────┐
   │ HTTPS Server │              │ Client Cert  │              │ CA / Root    │
   │ Certificate  │              │ for mTLS to  │              │ Certificates │
   │ (presented   │              │ backend      │              │ (validates   │
   │ to clients)  │              │              │              │ backend cert)│
   └──────────────┘              └──────────────┘              └──────────────┘

Each has independent expiry dates and must be tracked separately.
```

---

## 4. Triage Decision Tree

```
Certificate Expiration Alert
          │
          ▼
  Is the certificate already expired?
          │
     ┌────┴────┐
     ▼         ▼
    YES         NO (advance warning)
     │           │
     ▼           ▼
Immediate      What cert type?
Renewal           │
Go to 5.1    ┌────┼────┐
             ▼    ▼    ▼
          Frontend Backend  CA/Trust
            5.2    5.3      5.4
```

---

## 5. Troubleshooting Steps

### 5.1 Emergency: Certificate Already Expired

**Symptoms:**
- Active production failures: SSL handshake errors returning to clients
- Error code `CERTIFICATE_EXPIRED` or `0x80090326` in logs
- All connections through affected service failing

**Immediate Resolution — P1 Response:**

| Step | Action | Expected Result | Time Target |
|------|--------|----------------|-------------|
| 1 | Engage PKI/Security Team immediately via incident bridge | Renewal request opened | 0–5 min |
| 2 | Identify which certificate is expired from DataPower logs | Cert subject / alias known | 5 min |
| 3 | Check ServiceNow CMDB for certificate renewal record | Existing renewal ticket found | 5 min |
| 4 | PKI team generates new certificate | New cert file delivered | 30–60 min |
| 5 | Upload new certificate to DataPower **Crypto Certificate** object | New cert uploaded | 5 min |
| 6 | Update all Crypto Profile / SSL Profile references | References updated | 5 min |
| 7 | Save and **Apply** configuration | Config persisted | 2 min |
| 8 | Test via **Probe** tool and curl from external client | Successful handshake | 2 min |
| 9 | Confirm resolution with monitoring team | Alert cleared | 5 min |

---

### 5.2 Proactive: Frontend Server Certificate Renewal

**Symptoms:**
- Monitoring alert: frontend HTTPS certificate expires in < 30 days
- No active failures yet

**Step-by-Step Resolution:**

1. **Identify the Certificate**
   - Navigate to **Objects → Crypto → Crypto Certificate**
   - Filter by expiry date — identify certificates expiring within 30 days

2. **Generate Renewal Request**
   - Obtain CSR (Certificate Signing Request) from the existing certificate
   - Submit CSR to PKI team via ServiceNow ticket
   - Track ticket to completion (SLA: 4 hours for standard certs)

3. **Upload and Replace**
   - Navigate to **Objects → Crypto → Crypto Certificate → [Cert Name]**
   - Upload the new certificate PEM file
   - Do **not** change the object name — all dependent references will automatically use the updated cert

4. **Validate**
   - Check certificate details — confirm new expiry date
   - Navigate to **Objects → Crypto → SSL Server Profile**
   - Confirm the profile still references the correct cert object
   - Test via `openssl s_client` from an external host

**Validation Command:**
```bash
openssl s_client -connect <datapower-host>:<port> -showcerts < /dev/null 2>/dev/null \
  | openssl x509 -noout -dates
```

---

### 5.3 Proactive: Backend Client Certificate Renewal (mTLS)

**Symptoms:**
- Alert: backend mTLS client certificate expiring within 30 days
- If not renewed: backend service will reject DataPower's mTLS connection

**Resolution Checklist:**

- [ ] Identify the mTLS client certificate used for the affected backend
- [ ] Contact backend service team — confirm they expect a new client cert
- [ ] Generate new key pair and CSR
- [ ] Submit CSR to enterprise PKI via ServiceNow
- [ ] Provide new client certificate to backend team for their allowlist update
- [ ] Upload new certificate and private key to DataPower **Crypto Key** and **Crypto Certificate**
- [ ] Update **SSL Client Profile** to reference the new key and cert objects
- [ ] Coordinate cutover timing with backend team
- [ ] Apply change in DataPower and test via Probe
- [ ] Confirm backend team sees successful mTLS handshake from new cert

---

### 5.4 Proactive: CA / Intermediate Certificate Renewal

**Symptoms:**
- Alert: CA or intermediate certificate in DataPower trust store expiring
- If not renewed: validation of backend certificates signed by this CA will fail

**Resolution Checklist:**

- [ ] Identify which CA certificate is expiring
- [ ] Determine which backend services use certificates signed by this CA
- [ ] Obtain renewed CA certificate from PKI team
- [ ] Navigate to **Objects → Crypto → Crypto Certificate → [CA Cert Name]**
- [ ] Upload new CA certificate
- [ ] Navigate to **Objects → Crypto → Crypto Validation Credential**
- [ ] Confirm all Validation Credentials that used the old CA now reference the updated cert
- [ ] Test each affected backend service via Probe

---

## 6. Common Mistakes to Avoid

| Mistake | Impact | Prevention |
|---------|--------|------------|
| No proactive monitoring of cert expiry | Surprise production outage | Set alerts at 60, 30, and 7 days |
| Renewing only leaf cert and not updating CA trust | Validation still fails after renewal | Always check full chain is current |
| Uploading cert to wrong DataPower object alias | Old cert still in use | Confirm object name, not just file name |
| Not coordinating mTLS cert change with backend team | Backend still expects old cert | Always coordinate both sides of mTLS renewal |
| Not including DataPower in enterprise cert inventory | Cert renewal missed | Register all DataPower certs in ServiceNow CMDB |

---

## 7. Escalation Matrix

| Condition | Escalate To | SLA |
|-----------|------------|-----|
| Certificate already expired in production | PKI Team + DataPower SME (P1 bridge) | Immediate |
| Certificate expiring in < 7 days | PKI/Security Team | 4 hours |
| Certificate expiring in < 30 days | PKI/Security Team | 1 business day |
| Backend team unresponsive to mTLS renewal coordination | Application Team Manager | 4 hours |
| Issue persists after certificate upload | DataPower SME Team | 15 min response |

---

## 8. Related Runbooks

- [Pattern 7: SSL/TLS Handshake Failure](Sample_Pattern_7_SSL_Handshake_Failure.md)
- [Pattern 3: Authentication & Authorization Failures](ESGA_Pattern_3_Authentication_Authorization_Failures.md)

---

## 9. Revision History

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0 | 2026-05-15 | Platform Engineering Team | Initial creation |
