# Rivet evaluation data handling

Prepared for South Coast Controls | 8 November 2026

## Evaluation scope

One redacted historical drawing package, operated by Rivet and reviewed with SCC's engineers. The first evaluation does not require SCC Outlook access, a desktop agent, or a production-system connection. This document describes the evaluation path and proposed handling terms; it is not a certification or a production security attestation.

## Current environment and access

- **Processing location:** the currently verified evaluation environment is a founder-managed Mac, with a local application database and file storage. FileVault was verified enabled on 8 November 2026. A hosted production environment has not been verified for SCC; any move to one requires a separate review of region, storage, access and backups.
- **Operator access:** Srikanth Samy operates the evaluation. Authorized operators and infrastructure administrators can technically access stored files; Rivet does not promise that no person can see them. Agree any additional personnel access with SCC before processing.
- **Workspace separation:** the private intake API scopes source files and manifests to the authenticated workspace. Another workspace cannot download them. Files use generated storage names, owner-only filesystem permissions and recorded SHA-256 hashes. This is application-enforced separation, not independently certified isolation.
- **Originals and output:** preserve received originals, identify each file's revision and role, and make proposed changes in copies. Results remain drafts for SCC engineer review. No engineering approval, customer sending or production release is granted by the evaluation.

## AI processing and learning

Relevant text and page images can be sent to the OpenAI API for reading and drafting. The private intake records external AI permission separately from redaction; the first-day runner blocks remote reading unless a recorded authorization covers the exact source files. The upload itself starts no model processing.

OpenAI states API data is not used to train its models by default unless the account opts in. Rivet's relevant Responses requests use `store: false`; this does not mean zero retention. Default abuse-monitoring retention can include content for up to 30 days, with policy exceptions. Zero Data Retention is not claimed for this evaluation. Confirm that SCC's customer obligations permit this processing before enabling it.

The proposed evaluation does not use SCC documents for cross-company learning or fine-tuning. Customer drawing conventions are explicit, source-cited rules attached to selected orders; they are not an automatic guarantee of learned drafting expertise.

Reference: https://developers.openai.com/api/docs/guides/your-data (checked 8 November 2026).

## Agree before sharing

- Use an SCC-approved, named-recipient transfer method, such as SCC's own SharePoint location. Do not send confidential files to the public website or an anonymous link. Rivet's private intake endpoint is implemented, but a hosted SCC upload link has not been verified or issued.
- Confirm SCC is authorized to share the redacted material and, if applicable, execute an NDA before transfer. Please send the required NDA or vendor-security questionnaire for review.
- Agree the operator, storage location, permitted processing and retention. Proposed active-copy retention is 30 days after evaluation delivery, or earlier on request; this is a proposed term, not an automated deletion guarantee.
- Deletion is operator-run. Agree which originals, derived files, transcripts, exports and records are covered; confirm active-copy removal in writing and disclose any backup or provider-retention exceptions. No instant or universal erasure guarantee is made.

## IT review before a connected pilot

Outlook and SharePoint access are optional for the initial evaluation. A later integration requires SCC administrator approval, exact mailbox/site scope, verified provider-side restrictions and a test that other resources are denied. Rivet's draft workflow does not require Mail.Send. Any other write action needs its own agreed scope and review.

Project-relevance filtering is not phishing protection, malware scanning or ransomware prevention. File validation and engineering checks do not replace SCC's endpoint and Microsoft security controls. Rivet does not claim SOC 2, ISO 27001, a completed independent penetration test or guaranteed handwriting/CAD accuracy.

Before live use, verify hosting, identity controls, TLS, storage encryption, administrator access, logging, backup/restore, deletion and incident contacts with SCC IT. The evaluation does not certify these as a completed production deployment.

Contact: Srikanth Samy | ssamy@berkeley.edu
