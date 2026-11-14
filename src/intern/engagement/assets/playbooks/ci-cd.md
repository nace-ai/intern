---
name: ci-cd
title: CI/CD and software supply chain
triggers: Load when build or source infrastructure is reachable — Jenkins, GitLab, Gitea, GitHub Enterprise, Argo CD, TeamCity, Drone, Nexus/Artifactory, container or package registries — or when public repos/pipelines of the target turn up in OSINT.
attack: initial-access, credential-access
owasp: A03, A08
payloads: Insecure Source Code Management; Dependency Confusion; API Key Leaks
---

The build system holds the keys to production: deploy credentials, signing keys, cloud roles. Look for anonymous access, readable secrets and ways to make the pipeline run your code. Prove with reads; never trigger builds, register runners or publish packages unless the operator explicitly allows it.

## 1. Find and fingerprint
- Hosts/paths: `jenkins.`, `ci.`, `gitlab.`, `argocd.`, `/jenkins`, `/job/`, `/-/` (GitLab), `/api/v4/version`, `/api/json`, `/artifactory/api/`, `/service/rest/v1/` (Nexus), `/v2/_catalog` (registries).
- Version → advisories with trigger checks (doctrine: applicability, not version strings).

## 2. Anonymous and low-priv access
- Jenkins: `/api/json?tree=jobs[name]`, `/asynchPeople/`, build logs (`/job/<j>/lastBuild/consoleText`), `/script` (script console — reachable = code execution; prove only that it's reachable/authorized, run `println "pwn7x3q"` only with approval).
- GitLab: public/internal projects (`/api/v4/projects?visibility=internal`), open registration, CI/CD variables visible to your role, runner registration token exposure.
- Argo CD / Drone / TeamCity: anonymous UI or API, readable application manifests and repo credentials.
- Artifact/package registries: anonymous read of private packages, anonymous **write** (check with a non-destructive permission query, not by publishing).

## 3. Secrets in the pipeline
- Build logs with echoed secrets, `set -x` output, failed-step dumps; artifacts containing `.env`, kubeconfigs, keys; pipeline definitions (`Jenkinsfile`, `.gitlab-ci.yml`, `.github/workflows/*.yml`) naming secrets and deploy targets.
- Verify each secret with one read-only call, then follow it (cloud → `cloud-enum`, cluster → `container-k8s`).

## 4. Pipeline injection (prove the path, run nothing without approval)
- Public repos whose workflows run on `pull_request_target` / `workflow_run` with checkout of untrusted code, or interpolate `${{ github.event.* }}` into `run:` — a classic injection. Document the exact vulnerable expression; demonstrating it requires opening a PR, so get approval first.
- Writable repos/branches without protection that feed a deploy pipeline; self-hosted runners shared between trust levels.

## 5. Dependency confusion
- Internal package names exposed (`package.json`, `requirements.txt`, lockfiles, JS bundles, error messages) that are **unclaimed** on the public registry → record the names and the resolver behaviour; never publish a package to prove it.

## Record & validate
- Exposure/secret → `state_update action=vuln owasp=[A08]` (pipeline integrity) or `A03` (dependency/supply-chain); secrets → `action=cred`; script-console or runner access → `action=access`.
- Chain every live credential into the system it unlocks.
