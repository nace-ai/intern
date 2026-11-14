---
name: credential-access-host
title: Credential access on a compromised host — scaffold, steps to be written by the team
triggers: Load when you hold a shell or administrative foothold on a host (state_query what=access) and the next step is credential material stored on that host. The technique steps are not written yet; the harness wiring below applies regardless.
attack: credential-access
---

> **Status: scaffold.** The technique steps in §2 have not been written yet. Until they are, don't
> improvise here: record the opportunity (§3) and continue with the rest of the engagement.

## 1. Before you start (harness rules)
- **A recorded foothold.** It must be recorded with `state_update action=access`, and the host must
  be in scope.
- **Operator approval.**
  - In the TUI, ask the operator before extracting credential material from a host.
  - In an unattended run (`intern run`), go ahead only if `APPROVE` includes `host-credentials`.
    Otherwise call `needs_operator kind=host-credentials` with the host and what you would collect,
    and move on.
- **Rules of engagement:** read-only, minimal footprint, no persistence, nothing left behind. Clean
  up any file you created and record that you did.

## 2. Steps (to be written by the team)
<!-- TEAM: write the approved method for each platform: tools, commands, prerequisites, footprint,
     and how to verify the result without touching other systems. Keep each step read-only. -->

### Windows hosts
<!-- TEAM -->

### Linux and macOS hosts
<!-- TEAM -->

### Credential stores, configuration files and secrets in use by applications
<!-- TEAM -->

## 3. Recording
- **Each credential:** `state_update action=cred username=… secret=… source="host <name> (<where it
  was found>)"`. Secrets stay in the engagement folder (0600) and are masked in evidence. Never put
  them in notes, captions or the report narrative.
- **The weakness:** record what made the credential reachable as a finding
  (`state_update action=vuln`), for example plaintext secrets on disk, an over-privileged service
  account, or cached credentials of a privileged user.
  - Tag it with the Credential Access techniques it demonstrates. `attack_lookup
    tactic=credential-access` lists them; use the most specific sub-technique.
- **Where it could be reused:** `state_query what=pivots` shows where a new credential could be
  tried. Trying one is lateral movement: see the `lateral-movement` playbook, and `cred-reuse`
  approval applies.
- **Nothing recovered:** record the coverage anyway: `state_update action=coverage
  attack=[credential-access] coverage_status=tested notes=<what was checked on which host>`.

## 4. Evidence
- Capture proof with `screenshot mode=terminal`, running the command that shows the credential
  material exists. The harness masks secrets in the image and transcript; check the caption says
  what was found, not the value.
- Link the evidence to the finding (`vuln_id`).

## 5. Stop conditions
- **Approval withdrawn:** the operator declines, or the approval doesn't cover this host.
- **Instability:** the step would risk the host's stability or availability.
- **Out of scope:** the material belongs to a system or person outside the scope. Record it as
  found, don't use it, and tell the operator.
