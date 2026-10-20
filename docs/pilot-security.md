# Rivet pilot: data handling and access

**Pilot scope.** Start with a redacted package in a separate customer workspace. The demonstration uses fictional equipment and documents; it is not evidence of validated performance on a manufacturer's CAD system.

| Area | Pilot practice |
| --- | --- |
| Customer separation | Records, source documents and jobs are scoped to the customer's workspace. Rivet is an early product; workspace isolation is application enforced, not a claim of independently certified infrastructure. |
| Microsoft access | Agree the exact mailbox and SharePoint site before connecting. Use the selected resources only, with the minimum permissions required. Verify both Microsoft tenant restrictions and Rivet's server allowlist before importing confidential data. |
| Email | Rivet prepares unsent drafts. A person reviews and sends from Outlook. The demo does not grant Rivet Mail.Send or authorize engineering release. |
| AI processing | Relevant document text and page images are processed through the OpenAI API. API data is not used to train OpenAI models by default unless the account explicitly opts in. Rivet's Responses requests use `store: false`. This is not a zero-retention claim: provider abuse-monitoring retention can still apply. |
| Commercial confidentiality | Execute an NDA and agree permitted use, access, retention and deletion before receiving confidential pilot documents. Do not claim an NDA is in place until signed. |
| Redaction | Remove customer and personal names, addresses, confidential commercial terms and unnecessary identifiers. Keep device tags, electrical ratings, revision identifiers, reviewer comments and the layout needed to test extraction. Check PDF annotations, layers, metadata and spreadsheet hidden sheets as well as visible text. |
| Retention and deletion | Agree a pilot retention period. On request, the operator removes the customer's stored originals, derived artifacts and application records, and confirms completion. Explain any backup or provider retention that cannot be removed immediately. No automated deletion or instant erasure guarantee is claimed. |
| Human responsibility | Outputs are drafts. Manufacturer engineers retain responsibility for technical correctness, protection coordination, compliance, pricing, approval and production release. |
| Certifications | Rivet does not claim SOC 2, ISO 27001 or other security certification. Customer security review and a documented access check precede a confidential deployment. |

**Before upload:** identify the authorized customer contact; sign the NDA; confirm the workspace, selected mailbox/site and redaction; agree retention and deletion; import a small sample first. External upload access stays closed until the contact is known.

Provider reference: [OpenAI API data controls](https://developers.openai.com/api/docs/guides/your-data) (reviewed 19 October 2026).
