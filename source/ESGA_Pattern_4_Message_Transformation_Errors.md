# ESGA Pattern 4 Runbook: Message Transformation & Parsing Errors

## Document Information

| Field | Value |
|--------|--------|
| **Pattern ID** | Pattern_4 |
| **Platform** | ESGA |
| **Category** | Message Processing |
| **Severity** | P3 - Medium |
| **Last Updated** | 2026-05-15 |
| **Author** | Platform Engineering Team |

---

## 1. Overview

This runbook covers troubleshooting steps for message transformation and parsing errors on the ESGA DataPower gateway. These errors occur during the processing phase — after the connection is established but while DataPower is reading, validating, or transforming the request or response payload. Common triggers include schema validation failures, malformed JSON/XML, XSLT stylesheet errors, GatewayScript exceptions, and encoding mismatches between clients and backend services.

---

## 2. Error Signatures

### 2.1 Common Error Messages

| Error Code | Message | Likely Cause |
|------------|---------|--------------|
| `HTTP 400` | Bad Request | Malformed or schema-invalid payload |
| `HTTP 500` | Internal Server Error | XSLT or GatewayScript runtime exception |
| `XML_PARSE_ERROR` | Failed to parse XML | Malformed XML in request/response |
| `JSON_PARSE_ERROR` | JSON parse failure | Invalid JSON syntax |
| `SCHEMA_VALIDATION_FAILED` | Payload does not match schema | Contract violation between client and service |
| `XSLT_TRANSFORM_FAILED` | Stylesheet runtime error | Logic error or null node in transformation |
| `GWSCRIPT_EXCEPTION` | GatewayScript unhandled exception | Runtime error in custom JS policy |
| `ENCODING_ERROR` | Character encoding mismatch | UTF-8 vs ISO-8859-1 conflict |

### 2.2 Splunk Query for Detection

```
index=datapower sourcetype=dp:logs
| search "parse error" OR "transform failed" OR "schema validation" OR "GatewayScript" OR "XSLT"
| stats count by error_code, service_name, uri
| where count > 3
| sort -count
```

---

## 3. Architecture Context

```
Client Request
     │
     ▼
DataPower (ESGA) — Processing Pipeline
┌────────────────────────────────────────┐
│  1. Input Validation                   │
│     - Schema / WSDL validation         │
│     - JSON / XML parse check           │
│                                        │
│  2. Transformation                     │
│     - XSLT stylesheet execution        │
│     - GatewayScript policy execution   │
│     - Header manipulation              │
│                                        │
│  3. Output Validation                  │
│     - Response schema check            │
│     - Encoding normalization           │
└────────────────────────────────────────┘
     │
     ▼
Backend Service
```

---

## 4. Triage Decision Tree

```
Transformation / Parse Error
          │
          ▼
  Is error on Request or Response?
          │
     ┌────┴────┐
     ▼         ▼
 REQUEST     RESPONSE
     │           │
     ▼           ▼
Is it JSON  Is it XSLT
or XML?     or GWScript?
     │           │
  ┌──┴──┐    ┌───┴───┐
  ▼     ▼    ▼       ▼
JSON   XML  XSLT  GWScript
 5.1   5.1   5.2    5.3
```

---

## 5. Troubleshooting Steps

### 5.1 JSON / XML Parse Error

**Symptoms:**
- Specific service returning HTTP 400 to clients
- `JSON_PARSE_ERROR` or `XML_PARSE_ERROR` in DataPower logs
- Error started after a client application deployment

**Step-by-Step Resolution:**

| Step | Action | Expected Result |
|------|--------|----------------|
| 1 | Enable DataPower **Probe** on the failing service | Capture raw request payload |
| 2 | Extract the raw payload from the probe capture | Payload available for inspection |
| 3 | Run payload through a JSON/XML validator tool | Identify syntax error location |
| 4 | Confirm whether error is in request (client bug) or response (backend bug) | Scope confirmed |
| 5 | If client payload: notify client application team with error line/column | Client fix issued |
| 6 | If backend response: raise defect with backend application team | Backend fix issued |
| 7 | As interim: consider adding a **Validate** action to return clear 400 to client | Cleaner error handling |

**Validation Tools:**
```bash
# Validate JSON
echo '<payload>' | python3 -m json.tool

# Validate XML
xmllint --noout payload.xml
```

---

### 5.2 XSLT Transformation Failure

**Symptoms:**
- Service returns HTTP 500 with transform error
- Specific field in payload triggers the failure
- Error started after a stylesheet deployment

**Step-by-Step Resolution:**

1. **Identify the Failing Stylesheet**
   - Navigate to **Objects → Multi-Protocol Gateway → [Service] → Request Rule**
   - Locate the **Transform** action that is failing
   - Note the stylesheet filename

2. **Check Stylesheet for Runtime Errors**
   - Navigate to **Objects → Stylesheet → [Stylesheet Name]**
   - Download the stylesheet for review
   - Common issues:
     - Referencing a node that may be null (`select="//node"` with no null check)
     - XPath version mismatch (XPath 1.0 vs 2.0 functions)
     - Missing namespace declarations

3. **Test with Probe**
   - Enable **Probe** on the service
   - Capture the failing message
   - Use **Probe → Test Stylesheet** to run stylesheet against captured payload

4. **Apply Fix**

| Issue | Fix |
|-------|-----|
| Null node reference | Add `if` check before accessing optional nodes |
| Namespace error | Declare missing namespace in stylesheet header |
| XPath 2.0 function in 1.0 context | Use XSLT 2.0 processor or rewrite XPath |
| Hardcoded values changed | Update stylesheet with correct values |

---

### 5.3 GatewayScript Exception

**Symptoms:**
- HTTP 500 returned with `GWSCRIPT_EXCEPTION`
- Error message contains a JavaScript stack trace
- Failure is tied to specific payload structure or header value

**Step-by-Step Resolution:**

| Step | Action | Expected Result |
|------|--------|----------------|
| 1 | Retrieve the full stack trace from DataPower system logs | Error line number identified |
| 2 | Navigate to **Objects → GatewayScript → [Script Name]** | Script file located |
| 3 | Review code at the failing line number | Root cause identified |
| 4 | Common causes: null reference, undefined variable, JSON.parse on non-JSON | Fix applied |
| 5 | Add `try/catch` blocks around risky operations | Resilient error handling |
| 6 | Test fix in non-production first | Regression testing complete |
| 7 | Deploy fix and validate via Probe | Successful execution |

**Safe GatewayScript Pattern:**
```javascript
try {
  var body = session.input.readAsJSON();
  // processing logic
} catch (e) {
  session.output.write({ error: 'Invalid payload', detail: e.message });
  session.reject(400);
}
```

---

### 5.4 Schema Validation Failure

**Symptoms:**
- Client receiving HTTP 400 with schema validation detail
- New version of API contract deployed by client or backend
- Required field missing or field format changed

**Resolution Checklist:**

- [ ] Enable Probe to capture the failing payload
- [ ] Identify which schema the **Validate** action references
- [ ] Compare failing payload against the current schema definition
- [ ] Determine whether schema or payload is out of date
- [ ] If schema needs update: obtain new XSD/JSON Schema from API owner
- [ ] Upload new schema to **Objects → XML Manager → Schema**
- [ ] Test validation with updated schema
- [ ] Communicate schema change to all consuming clients

---

## 6. Common Mistakes to Avoid

| Mistake | Impact | Prevention |
|---------|--------|------------|
| Not using try/catch in GatewayScript | Unhandled exceptions return 500 | Always wrap risky code in try/catch |
| XSLT accessing optional nodes without null check | Sporadic 500 errors | Add `if (node)` guards for optional fields |
| Validating against stale schema version | Legitimate requests rejected | Keep schema objects in sync with API contract |
| Not enabling Probe for transformation debugging | Slow root cause analysis | Enable Probe in non-prod on all services |
| Mixing JSON and XML content types in one service | Parse errors | Validate Content-Type headers early in policy |

---

## 7. Escalation Matrix

| Condition | Escalate To | SLA |
|-----------|------------|-----|
| XSLT / GWScript fix requires deployment | DataPower SME Team | 4 hours |
| Schema change requires API contract update | API Governance Team | Per release cycle |
| Client payload consistently malformed | Client Application Team | Per service SLA |
| Issue persists > 30 min affecting production | DataPower SME Team | 15 min response |

---

## 8. Related Runbooks

- [Pattern 5: Routing & Service Configuration Errors](ESGA_Pattern_5_Routing_Service_Errors.md)
- [Pattern 1: Backend Connection Timeout](ESGA_Pattern_1_Backend_Connection_Timeout.md)

---

## 9. Revision History

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0 | 2026-05-15 | Platform Engineering Team | Initial creation |
