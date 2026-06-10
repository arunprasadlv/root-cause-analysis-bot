# Pattern 9 Runbook: DataPower System Resource Exhaustion

## Document Information

| Field | Value |
|--------|--------|
| **Pattern ID** | Pattern_9 |
| **Platform** | DataPower Gateway |
| **Category** | System Performance |
| **Severity** | P1 - Critical |
| **Last Updated** | 2026-05-15 |
| **Author** | Platform Engineering Team |

---

## 1. Overview

This runbook covers DataPower system resource exhaustion on the DataPower gateway. These events occur when DataPower's CPU, memory, or thread pool reaches capacity, causing the gateway to throttle or drop requests. Resource exhaustion is often triggered by traffic surges, memory leaks in processing policies, inefficient transformations, or connection storms from misbehaving clients. Unlike backend errors, resource exhaustion affects all services running on the DataPower instance simultaneously.

---

## 2. Error Signatures

### 2.1 Common Error Messages

| Error Code | Message | Likely Cause |
|------------|---------|--------------|
| `HTTP 503` | Service unavailable | DataPower thread pool full |
| `MEMORY_ALLOC_FAILED` | Memory allocation failure | JVM / heap exhaustion |
| `THREAD_POOL_EXHAUSTED` | No available worker threads | Concurrent request overload |
| `CPU_THROTTLE_ACTIVE` | Processing throttled | Sustained CPU > 90% |
| `DOCUMENT_CACHE_FULL` | Document cache exhausted | Large payload volume |
| `CONNECTION_LIMIT_REACHED` | Max connections exceeded | Connection storm |
| `SYSTEM_OVERLOAD` | System overload detected | Multiple resource constraints simultaneously |

### 2.2 Splunk Query for Detection

```
index=datapower sourcetype=dp:logs
| search "memory" OR "thread pool" OR "overload" OR "throttle" OR "connection limit"
| stats count by error_code, service_name
| timechart span=1m count by error_code
```

**System Metrics Query:**
```
index=datapower sourcetype=dp:metrics
| stats avg(cpu_usage) as avg_cpu, max(memory_usage) as peak_mem,
  avg(active_threads) as avg_threads by datapower_host
| where avg_cpu > 80 OR peak_mem > 85
```

---

## 3. Architecture Context

```
DataPower Gateway — Resource Stack

┌────────────────────────────────────────────────┐
│  CPU                                           │
│  [████████████████░░░░]  80% ← Warning         │
│  [████████████████████]  95% ← Critical        │
│                                                │
│  Memory (Heap)                                 │
│  [██████████████░░░░░░]  70% ← Monitor         │
│  [████████████████████]  90% ← Critical        │
│                                                │
│  Worker Thread Pool                            │
│  Active/Max: 95/100 ← Near Exhaustion         │
│  Active/Max: 100/100 ← Exhausted → 503s        │
│                                                │
│  Inbound Connections                           │
│  Active/Limit: 980/1000 ← Near Limit           │
└────────────────────────────────────────────────┘
```

---

## 4. Triage Decision Tree

```
Resource Exhaustion Symptoms
(all services degraded / 503s)
          │
          ▼
  Check DataPower System Dashboard
          │
   ┌──────┼──────┐
   ▼      ▼      ▼
 CPU    Memory Thread Pool
 High    High    Full
  │       │       │
  ▼       ▼       ▼
 5.1     5.2     5.3
```

---

## 5. Troubleshooting Steps

### 5.1 High CPU

**Symptoms:**
- All services responding slowly
- DataPower CPU > 85% sustained in monitoring dashboard
- Latency increasing across all services, not one specific backend

**Step-by-Step Resolution:**

| Step | Action | Expected Result |
|------|--------|----------------|
| 1 | Navigate to **Status → System → CPU Usage** | Current CPU % confirmed |
| 2 | Navigate to **Status → Services → Service Statistics** | Identify highest-traffic service |
| 3 | Check for unusual spike in request rate on one service | Anomalous traffic identified |
| 4 | Review XSLT / GatewayScript policies on high-traffic service | Inefficient transform found |
| 5 | Check if a batch job / load test is running unexpectedly | Source of spike identified |
| 6 | If runaway client: apply temporary rate limit to offending consumer | CPU pressure reduced |
| 7 | If inefficient policy: schedule optimization in next change window | Performance improvement planned |
| 8 | Engage Capacity team if baseline CPU is trending up week-over-week | Scaling review initiated |

**CPU Monitoring Thresholds:**

| Level | CPU % | Action |
|-------|-------|--------|
| Monitor | 70–80% | Watch trend, no action yet |
| Warning | 80–90% | Investigate traffic source |
| Critical | > 90% | Immediate action — rate limit offenders |

---

### 5.2 Memory Exhaustion

**Symptoms:**
- Increasing response latency followed by 503 errors
- `MEMORY_ALLOC_FAILED` errors in system logs
- GC activity increasing (if visible in DataPower metrics)

**Step-by-Step Resolution:**

1. **Check Memory Usage**
   - Navigate to **Status → System → Memory Usage**
   - Note: heap usage and document cache usage separately

2. **Identify Memory Consumers**
   - Navigate to **Status → Document Cache**
   - Check for unusually large cached documents
   - Clear document cache if it is consuming excessive memory:
     - **Status → Document Cache → Flush**

3. **Check for Memory Leak Indicators**
   - Is memory increasing monotonically without releasing?
   - Does a DataPower restart resolve the issue temporarily?
   - If yes: this indicates a memory leak — escalate to DataPower SME

4. **Adjust Memory Settings**

| Parameter | Default | Recommended | Location |
|-----------|---------|-------------|----------|
| Document Cache Size | 256 MB | Tune per traffic | System Settings |
| Max In-Memory Message Size | 10 MB | Reduce if large payloads | MPGW Settings |
| Metadata Cache Size | 64 MB | Monitor and tune | XML Manager |

---

### 5.3 Thread Pool Exhaustion

**Symptoms:**
- DataPower returns 503 with `THREAD_POOL_EXHAUSTED`
- Requests queueing — latency increasing before outright rejection
- Correlates with a spike in concurrent connections

**Step-by-Step Resolution:**

| Step | Action | Expected Result |
|------|--------|----------------|
| 1 | Navigate to **Status → System → Thread Pool Status** | Active vs max threads visible |
| 2 | Identify which service or client is holding threads open | Connection held too long |
| 3 | Check backend response times — slow backend holding DataPower threads | Backend latency root cause |
| 4 | Reduce backend timeout to free stalled threads faster | Thread release accelerated |
| 5 | Enable connection limits per client in the FSH | Per-client connection cap |
| 6 | Review **Workload Management** policy — enable backpressure | Graceful load shedding |
| 7 | If all backends are slow: refer to Pattern 1 (Connection Timeout) | Backend recovery path |

**Thread Pool Configuration:**

| Parameter | Default | Recommended | Location |
|-----------|---------|-------------|----------|
| Max Worker Threads | 100 | Scale with load | System Settings |
| Thread Backlog Queue | 50 | Monitor — tune with capacity | System Settings |
| Max Connections per Client | Unlimited | Set per-service limit | HTTP FSH |

---

### 5.4 Connection Storm (Connection Limit Reached)

**Symptoms:**
- Sudden spike in inbound connections
- `CONNECTION_LIMIT_REACHED` in logs
- Often caused by a misbehaving client retrying aggressively

**Resolution Checklist:**

- [ ] Identify the source IP(s) driving the connection storm from access logs
- [ ] Check if a client retry loop is in effect — exponential backoff not implemented
- [ ] Apply emergency rate limit on the offending client IP or consumer key
- [ ] Confirm DataPower connection limit config in **HTTP Front Side Handler**
- [ ] Contact the client application team to fix retry logic
- [ ] Remove emergency rate limit after client fix is deployed

---

## 6. Common Mistakes to Avoid

| Mistake | Impact | Prevention |
|---------|--------|------------|
| Restarting DataPower without capturing diagnostics | Root cause lost | Always collect must-gather before restart |
| Not monitoring memory trend over time | Memory leak missed until crash | Set weekly memory trend review |
| No per-client connection limits | Single bad client exhausts gateway | Always configure per-client limits |
| Inefficient XSLT on high-volume services | Sustained high CPU | Profile XSLT performance in load testing |
| Ignoring 70–80% CPU warnings | Hits 100% during next traffic spike | Treat warnings as action items, not noise |

---

## 7. Escalation Matrix

| Condition | Escalate To | SLA |
|-----------|------------|-----|
| DataPower restarting / crashing | DataPower SME + Infrastructure | Immediate |
| Memory leak suspected | DataPower SME Team | 15 min response |
| CPU > 90% not reducing after mitigation | Capacity / Performance Team | 30 min |
| Connection storm from external source | Network Security + Client Team | 15 min |
| Issue persists > 15 min affecting all services | Incident Bridge — P1 escalation | Immediate |

---

## 8. Related Runbooks

- [Pattern 1: Backend Connection Timeout](Pattern_1_Backend_Connection_Timeout.md)
- [Pattern 5: Routing & Service Configuration Errors](Pattern_5_Routing_Service_Errors.md)
- [Pattern 10: Network & Infrastructure Failures](Pattern_10_Network_Infrastructure.md)

---

## 9. Revision History

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0 | 2026-05-15 | Platform Engineering Team | Initial creation |
