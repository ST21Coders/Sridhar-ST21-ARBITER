# Production Security Checklist

Securing a production environment requires strict environment isolation, the principle of least privilege, and robust monitoring. Key practices include restricting access to authorized personnel, enforcing multi-factor authentication (MFA), encrypting data both at rest and in transit, and maintaining comprehensive logging for rapid incident response.

---

## 1. Access Control & Identity
* **Principle of Least Privilege:** Grant users and services only the permissions necessary to perform their specific functions. Avoid using shared accounts or reusing administrative credentials.
* **Multi-Factor Authentication (MFA):** Require MFA for all personnel accessing production servers, databases, and administrative consoles.
* **Just-In-Time (JIT) Access:** Implement JIT access or Privileged Access Management (PAM) tools to temporarily elevate privileges for administrative tasks, rather than keeping accounts permanently highly privileged.

## 2. Infrastructure & Network Hardening
* **Environment Separation:** Strictly segregate development, testing, and production environments. Developers should not have access to production databases or systems unless strictly necessary.
* **Network Segmentation:** Place production resources in private subnets and restrict inbound/outbound traffic using Network Security Groups or firewalls.
* **Web Application Firewalls (WAF):** Deploy a WAF to filter, monitor, and block malicious web traffic (e.g., SQL injections, cross-site scripting) targeting public-facing applications.

## 3. Data Protection
* **Encryption:** Encrypt sensitive data both at rest (e.g., using AES-256 for databases and backups) and in transit (e.g., enforcing TLS).
* **Secret Management:** Never hardcode API keys, passwords, or certificates in source code. Utilize dedicated secret management tools to inject them securely at runtime.
* **Remove Unnecessary Services:** Uninstall any unnecessary software, sample applications, or development tools from production servers to minimize your attack surface.

## 4. Monitoring & Auditing
* **Comprehensive Logging:** Aggregate access, error, and system logs across your entire infrastructure using centralized log management. Ensure no sensitive PII is accidentally logged.
* **Unified Auditing:** Leverage endpoint detection and response (EDR) or security agents to monitor file activity, process elevations, and network operations.
* **Vulnerability & Penetration Testing:** Regularly conduct security audits, vulnerability scanning, and penetration testing to proactively identify flaws before malicious actors do.

## 5. Incident Response & Backups
* **Disaster Recovery:** Establish and regularly test an automated backup strategy, including off-site storage and a clearly defined restoration plan.
* **Incident Response Playbook:** Have a documented incident response plan so your team knows exactly who to contact, how to isolate compromised systems, and how to safely restore services.
