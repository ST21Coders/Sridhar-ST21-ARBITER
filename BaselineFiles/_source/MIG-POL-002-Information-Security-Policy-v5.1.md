# MIG-POL-002 — Information Security Policy

**Document ID:** MIG-POL-002  
**Version:** v5.1  
**Effective:** February 2026  
**Owner:** CISO Office  

---

## §1 Purpose

Establishes mandatory information-security controls covering web traffic inspection, identity, IoT, and API rate-limiting.

## §2.2 SSL/TLS Inspection

SSL/TLS inspection is mandatory on ALL web traffic. Exceptions only with documented CISO approval in the SSL Inspection Exception Register, 90-day max. PCI DSS 4.0 Requirement 4.1 applies.

## §4.1 MFA Enforcement

MFA is required for ALL users — employees, contractors, vendors — regardless of privilege level. PCI DSS 8.4 applies. Coverage gaps must be closed within 30 days of detection.

## §5.1 IoT External Communication

Monitoring-only mode is NOT acceptable for IoT external communication. Active blocking is required. Internal firmware-update proxying must be enforced for all IoT devices.

## §API01 API Rate Limiting

All production API resources must enforce a baseline of 2,000 requests per IP per 5 minutes. WAF logs must reach SIEM within 60 seconds.
