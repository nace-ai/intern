---
name: lateral-movement
title: Lateral movement — scaffold, steps to be written by the team
triggers: Load when a credential, token, key or foothold you hold could reach another host, account or cloud environment in scope (state_query what=pivots lists the candidates). The technique steps are not written yet; the harness wiring below applies regardless.
attack: lateral-movement
---

> **Status: scaffold.** The technique steps in §2 have not been written yet. Until they are, don't
> improvise here: record each candidate path (§3) and continue with the rest of the engagement.

## 1. Before you start (harness rules)
- **Scope.** The destination must be in scope. A reachable system outside scope is recorded, not
  touched.
- **Operator approval.**
  - In the TUI, ask before trying a credential or token anywhere other than where it came from.
  - In an unattended run, go ahead only if `APPROVE` includes `cred-reuse`. Otherwise call
    `needs_operator kind=cred-reuse` with the credential (by id, never the secret), the destination
    and what it would prove.
- **One attempt per credential per destination.** Never spray, and watch for lockout policies.
- **Rules of engagement:** read-only on the destination, no persistence, nothing left behind.

## 2. Steps (to be written by the team)
<!-- TEAM: write the approved method for each environment: prerequisites, tools, commands,
     footprint, how to confirm access with the least privilege needed, and how to clean up. -->

### Windows and Active Directory networks
<!-- TEAM -->

### Linux and SSH estates
<!-- TEAM -->

### Cloud accounts, roles and tenants
<!-- TEAM -->

### Containers and clusters
<!-- TEAM -->

## 3. Recording
- **Each attempt:** record the outcome against its pivot opportunity: `state_update action=pivot
  id=<opportunity> pivot_result=success|failed|skipped notes=…`. Failed and skipped attempts count;
  the report lists unexplored paths.
- **A success:** record the new foothold with `state_update action=access` (type, target, via,
  cred_ref, level). Record the weakness that allowed it with `state_update action=vuln`.
  - Typical weaknesses: credential reuse across systems, over-broad trust, missing segmentation.
  - Tag the finding with the Lateral Movement techniques it demonstrates (`attack_lookup
    tactic=lateral-movement`).
- **Attack path.** Add the step to `report_content attack_path` with its `techniques`.
- **Nothing to try:** record coverage: `state_update action=coverage attack=[lateral-movement]
  coverage_status=tested|na notes=…` (n/a only when no credential or foothold could reach anything
  in scope).

## 4. Evidence
- Capture the proof of access on the destination with `screenshot mode=terminal`, running an
  identity and host check such as `whoami` and `hostname`. Link it to the finding.
- The caption names the source credential or token by id, never its value.

## 5. Stop conditions
- **Approval withdrawn:** the operator declines, or the approval doesn't cover this destination.
- **Out of scope:** the destination turns out to be out of scope, or belongs to a third party.
- **Lockout risk:** a lockout policy would be triggered.
