---
name: external-exposure
title: External exposure — dangling DNS records and email domain protections
triggers: Load during recon of internet-facing domains, once subdomains are enumerated (osint, certificate transparency), and for every in-scope domain that sends or receives email.
owasp: A02
attack: reconnaissance
---

Two checks that are cheap, read-only and often missed. Both use public DNS and HTTP responses only.

## 1. Dangling DNS records (subdomain takeover candidates)
1. **Resolve every subdomain in scope.** Follow the CNAME chain for each one: `dig +short CNAME
   <sub>`, then resolve the target.
2. **Record each candidate.** A subdomain is a candidate when its record points at a third-party
   resource that no longer exists:
   - the target name returns NXDOMAIN; or
   - the provider answers with its own "no such site / app / bucket" page.

   Note the provider and the resource type.
3. **Look at the less common forms too:**
   - NS delegations to a zone the provider says doesn't exist;
   - MX records pointing at a mail service that no longer hosts the domain;
   - A records pointing at cloud addresses the client may no longer hold. That last one is hard to
     prove, so record it as a lead.
4. **Don't claim the resource.** Registering the dangling name is the takeover itself. It needs the
   client's explicit written approval:
   - in the TUI, ask the operator;
   - in an unattended run, record it with `needs_operator kind=other`.

   Without that approval, the DNS answer plus the provider's not-found response are the proof.
5. **Rate severity by what trusts the subdomain:**
   - cookies scoped to the parent domain;
   - OAuth or SAML redirect URLs;
   - CSP or CORS allow-lists;
   - email sent from it;
   - links in the client's own pages.

## 2. Email domain protections
- **SPF** (`dig +short TXT <domain>`): look for these problems:
  - missing;
  - `+all` or `?all`;
  - more than 10 DNS lookups (the record then fails);
  - very broad includes.
- **DMARC** (`dig +short TXT _dmarc.<domain>`): look for these problems:
  - missing;
  - `p=none` (monitoring only);
  - `pct` below 100;
  - no aggregate reporting (`rua`);
  - a weaker subdomain policy (`sp`).
- **DKIM:** selectors can't be enumerated. If the client shares a sample message, read the selector
  from its headers and check the key. Otherwise record DKIM as not verifiable.
- **MTA-STS and TLS reporting:**
  - `_mta-sts.<domain>` TXT, plus the policy at `https://mta-sts.<domain>/.well-known/mta-sts.txt`;
  - `_smtp._tls.<domain>` TXT.
- **Domains that send no email** should publish `v=spf1 -all` and a DMARC `p=reject`.
- **Don't send spoofed email** to prove a weak policy: the published records are the evidence.
  Sending mail to real people is social engineering, out of scope unless the rules of engagement
  include it.

## 3. DNS hygiene worth a minute
- **CAA records:** which certificate authorities may issue certificates for the domain.
- **DNSSEC:** whether it is enabled.
- **Wildcard records:** whether any exist.
- **Zone transfers:** whether the authoritative servers allow them (`dig AXFR <domain> @<ns>`). An
  allowed transfer is a finding.

## 4. Recording
- **Each weakness:** record a finding with `state_update action=vuln owasp=[A02]`, a CVSS v4.0 vector,
  and the ATT&CK technique that applies (`attack_lookup`).
- **Evidence:** a `screenshot mode=terminal` of the `dig` and `curl -sI` output that shows it.
- **Coverage:** once the domains are checked, record `state_update action=coverage
  attack=[reconnaissance] coverage_status=tested notes=<domains and checks>`.
