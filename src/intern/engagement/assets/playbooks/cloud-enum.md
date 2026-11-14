---
name: cloud-enum
title: Cloud credential enumeration & privilege mapping
triggers: Load when the engagement touches a cloud account, or the moment you obtain any cloud credential — an AWS access key, Azure/GCP token, an SSRF that reaches a metadata endpoint, or secrets from CI/config. For mapping what a credential can do and which privilege-escalation paths exist, read-only.
attack: discovery, privilege-escalation, credential-access
owasp: A02, API8
payloads: Methodology and Resources/Cloud - AWS Pentest; Methodology and Resources/Cloud - Azure Pentest; Server Side Request Forgery
---

Cloud work here is **reconnaissance and privilege mapping**, not takeover. Identify what a credential is, what it can reach, and which escalation paths *exist* — demonstrate them to the minimum needed for proof. No persistence, no exfiltration of data, no destructive or account-modifying actions.

## 1. Where credentials come from
When you get access to a host or request, check the usual stores:
- **Instance metadata (IMDS):** `http://169.254.169.254/...` (AWS), `http://169.254.169.254/metadata/...` with `Metadata: true` (Azure), `metadata.google.internal` with `Metadata-Flavor: Google` (GCP). An SSRF that reaches these is a credential-theft primitive — note it as such.
- **On-disk:** `~/.aws/credentials`, `~/.azure/`, `~/.config/gcloud/`, env vars (`AWS_*`, `AZURE_*`, `GOOGLE_*`), container env, CI secrets, Terraform state.
- **ECS/IRSA:** `169.254.170.2$AWS_CONTAINER_CREDENTIALS_RELATIVE_URI`.

## 2. Identify the principal (read-only, first thing)
Never act before you know *who you are* and how noisy you can be:
- AWS: `aws sts get-caller-identity` — account, principal, path. 
- Azure: token introspection / `az account show`.
- GCP: `gcloud auth list`, token info endpoint.
Record the credential (`state_update action=cred`) and the foothold (`action=access`).

## 3. Map permissions, don't guess
- Prefer the account's own policy/role read APIs (IAM get/list on self) over blind probing.
- Where you must probe, do it **read-only and sparse** — `list`/`describe`/`get`, never create/delete. Dry-run flags where they exist.
- Build the picture: what services, what resources, what the identity is allowed to assume.

## 4. Privilege-escalation *paths* (identify, prove minimally)
Map known escalation shapes and show one works — without actually seizing the account:
- **AWS:** `iam:PassRole` + a compute/launch permission, `sts:AssumeRole` chains, policy-version/attach-user-policy, Lambda/Glue role flips.
- **Azure:** Owner/Contributor-on-self, managed-identity abuse, automation runbooks.
- **GCP:** `iam.serviceAccountTokenCreator`/`actAs`, `setIamPolicy`, deploy-as-service-account.
Proof = demonstrating the *reachable* path (e.g. you can `PassRole` a more-privileged role to a function you can invoke) — not persisting a new admin. Stop at the demonstration.

## 5. Reachable data & lateral surface (note, don't take)
- Identify readable stores (S3/Blob/GCS buckets, snapshots, RDS/Cosmos/Cloud SQL) and cross-account/tenant/project reach — as *findings*, with a bounded proof of read access (one object listing, not a bulk pull).
- Metadata/SSRF → internal services and further creds: map the chain.

## Out of scope here (ROE)
Do **not**: create users/keys/app-registrations, plant backdoors or event triggers, disable/mute logging or detection, snapshot-share or bulk-exfiltrate data, or run anything destructive. Those conflict with the engagement's read-only, no-persistence rules — the finding is that the path *exists*, proven minimally.

## Record & validate
- Each credential, foothold, and escalation path → `state_update` (cred/access/vuln) with a bounded proof saved via `evidence`.
- Severity = the access actually demonstrated (and the blast radius it implies), judged by `drex_triage`. A "could escalate" with no demonstrated path stays `suspected`.
