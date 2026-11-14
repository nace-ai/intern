---
name: identity-cloud
title: Cloud identity providers and SaaS sign-on — configuration review
triggers: Load when the client uses a cloud identity provider (Microsoft Entra ID, Okta, Google Workspace) for workforce or customer sign-in, or an in-scope application signs users in through one (OIDC, SAML).
owasp: A07, A01
attack: initial-access, credential-access, privilege-escalation, persistence
payloads: OAuth Misconfiguration; SAML Injection
---

A configuration review of the identity provider, plus checks on how in-scope applications trust it.
It is read-only throughout.

## 1. Before you start
- **Access.** Ask the client for a read-only assessment role:
  - Entra ID: Global Reader plus Security Reader;
  - Okta: Read-Only Administrator;
  - Google Workspace: a read-only admin role.

  Don't try to obtain access any other way.
- **No changes.** Make no changes to tenant settings, users, groups, applications or policies.
  Exports and screenshots only.
- **Test accounts.** Use the test accounts the client provides for anything that involves signing
  in.

## 2. Review: record each area as tested or as a finding
- **Sign-in strength:**
  - MFA is enforced for every user and every application, including admins and service accounts;
  - privileged roles use phishing-resistant methods;
  - legacy and basic authentication protocols are off.
- **Access policies (conditional access, Okta sign-on policies):**
  - exclusions (users, groups, applications, locations) that leave gaps;
  - policies left in report-only mode;
  - trusted locations that are too broad.
- **Privileged access:**
  - how many accounts hold standing top-level admin roles;
  - whether break-glass accounts are protected and monitored;
  - whether roles are assigned permanently or just-in-time;
  - whether admins use separate accounts.
- **Applications and consent:**
  - whether ordinary users can consent to applications asking for broad permissions;
  - applications and service principals holding high-privilege permissions;
  - credentials on applications: who owns them, and when they expire;
  - multi-tenant applications.
- **External and guest access:** invitation settings, guest permissions, and cross-tenant defaults.
- **Self-service:** password reset, MFA registration and account recovery, and how strong each one
  is.
- **Logging and alerting:** sign-in log retention, and risk-based policies. These also count toward
  A09 coverage.
- **Applications that sign in through the provider (in scope):**
  - **User identity:** they should key users on the provider's immutable identifier, not a mutable
    or unverified email claim. Trusting the email claim is an account-takeover class. Verify it with
    the client's test accounts only.
  - **SAML and OIDC:** check signature validation, audience and issuer checks, and exact
    redirect-URI matching, with your own test accounts. The `auth-session` playbook covers the web
    side.

## 3. Recording
- **Each weakness:** record a finding with `state_update action=vuln owasp=[A07]` (or `A01` for
  authorization gaps), a CVSS v4.0 vector, and the ATT&CK techniques it enables. `attack_lookup`
  finds them; see the Identity Provider platform.
- **Evidence:**
  - screenshots of the setting pages, taken with `browser_session` as the assessment user; or
  - exported settings saved with `evidence`.

  Mask user names that the finding doesn't need.
- **Persistence opportunities are observe-only.** For example, who can add credentials to
  applications. Report them; never add credentials or accounts.
- **Coverage:** account for the tactics reviewed with `state_update action=coverage attack=[…]`.

## 4. Stop conditions
- **A change would be needed to verify something.** Stop and record it as a lead for the client to
  confirm.
- **Signs of an active compromise** (admins nobody recognises, unexpected application credentials,
  unexplained consent grants). Stop and tell the operator at once.
