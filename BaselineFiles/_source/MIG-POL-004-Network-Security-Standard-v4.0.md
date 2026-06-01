# MIG-POL-004 — Network Security Standard

**Document ID:** MIG-POL-004  
**Version:** v4.0  
**Effective:** December 2025  
**Owner:** VP Network Engineering (with CISO)  

---

## §1 Purpose

Defines minimum network-security controls for production resources, including WAF requirements, segmentation, IoT, and the prohibition on temporary perimeter exceptions.

## §2 WAF Requirements

No production application resource shall be directly accessible from the public internet without AWS WAF + OWASP CRS. Temporary exceptions are not permitted. PCI DSS 6.4 applies to any cardholder-data-touching resource.

## §3 Network Segmentation

VPC peering between production and non-production environments is prohibited. Cross-account routing through approved Transit Gateway segments remains compliant.

## §4 IoT Segmentation

IoT external communication must be blocked. Monitoring-only mode is not acceptable. Internal firmware-update proxying must be enforced.
