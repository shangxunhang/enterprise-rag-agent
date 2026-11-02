# RAG-Agent Project Instructions

## Mandatory issue recording

Any issue discovered during requirements, architecture, audit, implementation, testing, regression, E2E, or closure work must be recorded in the repository, regardless of severity or whether it is immediately fixed.

This includes BLOCKER / MAJOR / MINOR / NOTE findings, code bugs, contract wording gaps, test fixture gaps, pre-existing failures, verification gaps, compatibility debt, deferred backlog items, and deterministic fixes.

Do not leave an issue only in chat context or terminal output.

Follow:

`docs/PROJECT_ISSUE_RECORDING_RULE_20260815.md`

When an issue changes the current phase contract, implementation plan, acceptance criteria, verification matrix, or Definition of Done, also synchronize the current authoritative plan / Source of Truth. Preserve historical audit documents as historical evidence; do not rewrite them to manufacture a pass.

For deterministic issues with no architectural choice or user trade-off, record the issue and solution, synchronize the authority, implement, verify, and perform only the minimum closure check required. Do not create unnecessary repeated full audits.

For issues requiring architecture, ownership, schema/interface, scope, compatibility, or business-semantic trade-offs, record them and stop for an explicit user decision before changing the frozen authority.
