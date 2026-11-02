# Public snapshot validation

Date: 2026-11-02 (Asia/Shanghai). These checks ran in the assembled independent
publication worktree, not the original development checkout. Earlier original-tree
results are not used as acceptance evidence here.

## Identity and scope

- Publication branch: `codex/publication-v3-20261102`.
- Parent baseline: remote main `07dda2e80645cac67f8b5f5e30002d21c2ab01eb`.
- Current engineering source: `41aba4ab03670173ef316c122c4747811a70ab5a` plus its
  existing dirty files. The source HEAD alone does not identify the snapshot bytes.
- Original development history is retained locally; its 168 commits are not ancestors
  added by this snapshot. Production, tests and documentation have separate commits.
- The final Git tree/PR is the authoritative uploaded file set; detailed file hashes,
  path manifests and preservation receipts remain in local review storage.

Formal engineering coverage: every current production Python module, required
runtime script, config/Prompt, dependency declaration/lock and formal test source
from the reviewed engineering manifest is present. The root generated audit JSON
is excluded. Three test files externalize private local values; two ingestion
config examples use explicit placeholders, and the ingestion README omits a private
filename. Two production files have only terminal whitespace cleanup; production
Python ASTs are unchanged. The original checkout bytes are preserved.
Three further test files have formatting-only changes with identical ASTs; historical
Markdown hard breaks are preserved through the documented whitespace attribute.

The paired SRCREV-02 fixture family is retained as originally labeled
`SIMULATED_ACCEPTANCE_FIXTURE`: its README explicitly identifies the city, project,
organization, equipment and quantities as invented. Its expected assertions are
test-only and must not enter model-visible inputs. No paired E2E was executed.

## Actual checks in the publication tree

| Check | Observed result |
| --- | --- |
| Frozen installation | CPython 3.10.20, isolated uv 0.11.32; `uv sync --frozen --extra test --extra providers --extra document-ingestion` installed 54 locked packages on Windows |
| Lock integrity | `uv lock --check --offline` passed; pyproject and uv.lock unchanged |
| Environment versions/imports | `scripts/check_environment.py` passed; optional local-RAG, local-model and host CUDA packages were not installed or claimed |
| Static syntax | All 1,202 current engineering Python files parsed successfully, including the three publication-only test adjustments |
| Core imports | 15 actual modules imported; every module origin verified inside the publication worktree and the source-bootstrap loaded-origin check passed |
| CLI import surfaces | `run_pipeline.py --help`, `workflow_control.py --help`, `run_post_phase1_full8.py --help` exited successfully without business dispatch |
| Final documented offline subset | **159 passed, 1 skipped in 15.50 seconds**, in the publication `.venv` |
| Skipped node | `test_document_ingestion_text_normalizer.py::test_word_full_v2_config_excludes_gold_reference_from_index` requires the original local config and filename |
| Diff/format | `git diff --check` passed; explicit line-ending attributes preserved; no unintended executable-mode changes |
| Security/content | Full candidate-file scan plus independent contextual review: no confirmed real credentials, known private business identities, current `.env` key, database/binary, private `.env`, symlink, file >=1 MiB or long raw Provider JSON field |

Imported modules covered the application, Supervisor/factory, BudgetManager, Writer
context/Review Copy, Post-write composition/Consistency, deterministic report,
context packer/gate, ModelGateway, evaluator, ingestion config and mainline runtime.
Importing them is not evidence that optional native services or model execution work.

The eight tested modules are exactly the README/CI subset: citation association,
context packing, source bootstrap, Artifact, FactSnapshot, SectionPlan, BudgetManager
and text normalizer. CI checks the same subset on Linux and Windows. The local
result above was obtained on Windows; hosted CI results remain separate evidence.

Process-only configuration was selected before application imports. Provider
acceptance, plugin autoload, bytecode/cache generation and tracing were disabled.
No `.env` was copied into the public tree. No full suite, new E2E, paid model,
historical resume, database repair or formal Release was executed.

## Public content and local dependencies

Reviewed public data includes technical evaluator contracts/catalogs and short
synthetic smoke/example records, plus two explicitly synthetic Full8 coverage JSON
files. No real document catalog is included. The test tree retains its original
historical replay code and exact proof boundaries; 17 identified docs fixtures,
historical request/reply archives, raw checkpoints and runtime databases remain local.

[LOCAL_ARCHIVE_TESTS.md](LOCAL_ARCHIVE_TESTS.md) and its JSON inventory identify
30 resources, 37 affected test/helper modules and service/platform prerequisites.
These are static coverage lower bounds, not a claim that every other test is portable.
Missing local resources may cause skip or failure according to the existing node;
the public package does not silently turn all such failures into skips.

All known issues are recorded in [ISSUE_REGISTER.md](ISSUE_REGISTER.md) and
[PACKAGING_ISSUES.md](PACKAGING_ISSUES.md). Current quality Gate and stage-8
RunCompletion/Release acceptance remain open, as described in
[CURRENT_STATUS.md](CURRENT_STATUS.md).
