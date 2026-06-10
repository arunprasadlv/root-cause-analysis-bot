"""One-off: rewrite escalation ground-truth answers in golden_test_set.csv to the
full-matrix enumeration format (matches SYSTEM_PROMPT rule 8). Safe to delete after use."""

import csv

CSV_PATH = "eval/golden_test_set.csv"

GT = {
    "p1_escalation_1": (
        "Escalate according to the Backend Connection Timeout escalation matrix: "
        "if all backends are timing out, escalate to the Network Infrastructure Team (SLA: 15 min response); "
        "if the backend application is confirmed down, escalate to the Application Owning Team (SLA: per service SLA); "
        "if the issue persists more than 30 minutes, escalate to the DataPower SME Team (SLA: 15 min response); "
        "if a firewall blocking egress is confirmed, escalate to Network Security (SLA: 2 hours); "
        "for persistent performance degradation, escalate to the Capacity / Performance Team (SLA: 4 hours)."
    ),
    "p2_escalation_1": (
        "Escalate according to the DNS Resolution Failure escalation matrix: "
        "if the DNS record is missing or incorrect, escalate to the DNS / Infrastructure Team (SLA: 2 hours); "
        "for a DNS server infrastructure failure, escalate to the Network Infrastructure Team (SLA: 15 min response); "
        "if multiple services are impacted, escalate to the Incident Bridge as a P1 escalation (SLA: immediate); "
        "if the issue persists more than 30 minutes, escalate to the DataPower SME Team (SLA: 15 min response)."
    ),
    "p3_escalation_1": (
        "Escalate according to the Authentication & Authorization Failures escalation matrix: "
        "if the LDAP server is down affecting all users, escalate to the Directory Services Team (SLA: 15 min response); "
        "if the OAuth server is unreachable, escalate to the Identity Platform Team (SLA: 15 min response); "
        "if JWT key rotation coordination is needed, escalate to the Security / PKI Team (SLA: 4 hours); "
        "if the issue persists more than 30 minutes, escalate to the DataPower SME Team (SLA: 15 min response); "
        "for a suspected security breach or token theft, escalate to Security Operations (SOC) (SLA: immediate)."
    ),
    "p4_escalation_1": (
        "Escalate according to the Message Transformation Errors escalation matrix: "
        "if an XSLT / GWScript fix requires deployment, escalate to the DataPower SME Team (SLA: 4 hours); "
        "if a schema change requires an API contract update, escalate to the API Governance Team (SLA: per release cycle); "
        "if a client payload is consistently malformed, escalate to the Client Application Team (SLA: per service SLA); "
        "if the issue persists more than 30 minutes affecting production, escalate to the DataPower SME Team (SLA: 15 min response)."
    ),
    "p5_escalation_1": (
        "Escalate according to the Routing & Service Configuration Errors escalation matrix: "
        "if a service was disabled by an unknown change, escalate to Change Management Investigation (SLA: 1 hour); "
        "if a backend URL migration was not communicated, escalate to the Application Team and Gateway Team (SLA: 2 hours); "
        "if the issue persists more than 30 minutes, escalate to the DataPower SME Team (SLA: 15 min response); "
        "if multiple services are impacted, escalate to the Incident Bridge as a P1 escalation (SLA: immediate)."
    ),
    "p6_escalation_1": (
        "Escalate according to the Security Policy Violations escalation matrix: "
        "if a genuine threat or attack pattern is detected, escalate to the Security Operations Center (SOC) (SLA: immediate); "
        "if an IP allowlist change requires approval, escalate to the Network Security Team (SLA: 2 hours); "
        "if a rate limit adjustment requires governance approval, escalate to the API Governance Team (SLA: 4 hours); "
        "if the issue persists more than 30 minutes, escalate to the DataPower SME Team (SLA: 15 min response); "
        "if multiple services are under coordinated attack, escalate to the SOC and Incident Commander (SLA: immediate)."
    ),
    "p8_escalation_1": (
        "Escalate according to the Certificate Expiration escalation matrix: "
        "if a certificate has already expired in production, escalate to the PKI Team and DataPower SME via the P1 bridge (SLA: immediate); "
        "if a certificate expires in under 7 days, escalate to the PKI/Security Team (SLA: 4 hours); "
        "if a certificate expires in under 30 days, escalate to the PKI/Security Team (SLA: 1 business day); "
        "if the backend team is unresponsive to mTLS renewal coordination, escalate to the Application Team Manager (SLA: 4 hours); "
        "if the issue persists after certificate upload, escalate to the DataPower SME Team (SLA: 15 min response)."
    ),
    "p9_escalation_1": (
        "Escalate according to the DataPower System Resource Exhaustion escalation matrix: "
        "if DataPower is restarting or crashing, escalate to DataPower SME and Infrastructure (SLA: immediate); "
        "if a memory leak is suspected, escalate to the DataPower SME Team (SLA: 15 min response); "
        "if CPU stays above 90% and is not reducing after mitigation, escalate to the Capacity / Performance Team (SLA: 30 min); "
        "for a connection storm from an external source, escalate to Network Security and the Client Team (SLA: 15 min); "
        "if the issue persists more than 15 minutes affecting all services, escalate to the Incident Bridge as a P1 escalation (SLA: immediate)."
    ),
    "p10_escalation_1": (
        "Escalate according to the Network & Infrastructure Failures escalation matrix: "
        "if multiple services are failing due to a network change, escalate to the Network Change Manager and P1 Bridge (SLA: immediate); "
        "if a firewall rule is blocking production traffic, escalate to the Network Security Team (SLA: 2 hours for P2, immediate for P1); "
        "for route instability or packet loss, escalate to the Network Infrastructure Team (SLA: 15 min response); "
        "if an MTU mismatch is causing data transfer failures, escalate to the Network Infrastructure Team (SLA: 2 hours); "
        "for a physical or virtual NIC failure, escalate to the Data Center / Virtualization Team (SLA: 15 min response); "
        "if the issue persists more than 15 minutes affecting multiple services, escalate to the Incident Commander (SLA: immediate)."
    ),
}


def main() -> None:
    with open(CSV_PATH, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames
        rows = list(reader)

    updated = 0
    for row in rows:
        if row["id"] in GT:
            row["ground_truth_answer"] = GT[row["id"]]
            updated += 1

    with open(CSV_PATH, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print(f"Updated {updated} escalation ground-truth answers")
    assert updated == len(GT), f"expected {len(GT)} updates, got {updated}"


if __name__ == "__main__":
    main()
