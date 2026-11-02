# Public snapshot authority

Approved 2026-11-02 (Asia/Shanghai).

Publish an independent current engineering snapshot on the existing remote main
ancestry, preserving the original development checkout, bytes, index and history.
Do not upload the 168 development-history commits, which include local operational
inventories and business metadata. No history rewrite or force push is authorized.

Include production source, required scripts, configuration templates, frozen
dependencies, test source, reviewed synthetic examples and selected technical docs.
Exclude identifiable enterprise material, real historical inputs, raw model
requests/replies, runtime databases/checkpoints and large audit artifacts. Every
excluded original remains at its existing local location.

Historical replay tests may require a maintainer's local archive. Preserve their
assertions and explicitly document prerequisites; do not create substitute historical
inputs or claim the entire test tree is portable. Private inline filenames and
project identities in three test files are read from opt-in local environment
values; missing local prerequisites cause an explicit skip. Configuration examples
use documented path/identity placeholders, not historical data.

Acceptance for this maintenance task: verify selected content and ancestry for
disclosure; verify source/test completeness with documented differences; install
the existing frozen dependencies; check imports/CLI help and the documented offline
subset in the assembled public tree; record all findings and source preservation.
CI must use this same public subset. No new E2E, paid model call, business-logic
repair or acceptance of current quality Gates / stage-8 Release is authorized.

After those checks pass, organize local commits by production, tests and documentation,
push only `codex/publication-v3-20261102`, and open a Draft PR targeting main.
Do not automatically merge or modify main. PR approval remains a later human step.

Source baseline: `41aba4ab03670173ef316c122c4747811a70ab5a` plus its existing dirty
engineering files. Public base: `07dda2e80645cac67f8b5f5e30002d21c2ab01eb`.
Validation, local-data requirements and known problems are separate documents in
this directory; successful publication does not close business acceptance.
