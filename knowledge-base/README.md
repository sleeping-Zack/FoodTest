# Food knowledge base

Local PostgreSQL registry for source files, immutable parsing/chunking runs, candidate knowledge, classification, and version/reference evidence. This addition contains the code required by KB-00–10; it does not change the existing classifier service.

KB-09 adds source-bound business-date policies, historical version selection, typed references, bounded expansion with cycle detection, an evidence gap ledger, and incremental impact tracing. Directory status/date declarations are unverified claims. Real candidates remain `review_pending`, `scope_complete=false`, and `executable=false`.

## Install and configure

Python 3.11+ and local PostgreSQL are required. Install from this directory with `python -m pip install -e ".[test]"`. Copy `config/example.json` to an ignored local configuration, set its source roots and database, and provide the password through the environment variable named by `password_env`. No credentials or source standards are included.

Run `food-kb --config config/local.json validate-config`, then `doctor` and `migrate`. The registry supports explicit local source allowlists; files outside them are rejected. Existing applied migrations must remain unchanged.

## KB-09 commands

Use `versions-build` after source registration, then `versions-report SNAPSHOT_ID`. Query `resolve-versions SNAPSHOT_ID --context DATES_JSON` with optional `--document-id` or `--version-id`. Date records preserve `value`, `raw_value`, `role`, `source_ref`, and `verification_state`. Production, sampling, testing, and task dates are distinct; export dates never substitute for them. Explicit sourced policy bounds include whether the boundary day is included.

Use `expand-references SNAPSHOT_ID --version-id VERSION_ID` to trace verified normative dependencies, or explicitly select `--mode candidates` to explore unverified candidates. `--max-depth`, `--max-nodes`, and `--max-edges` bound traversal. Dated references must match the exact version; undated references require a resolved business context. Mentions and replacement text do not establish normative dependencies.

`versions-impact OLD_SNAPSHOT NEW_SNAPSHOT` compares fixed candidates and traces evidence-bearing old/new dependencies. Optional node/edge budgets bound this calculation. Its output is a recheck list and never changes old snapshots, approves knowledge, or publishes rules. Missing body text, amendments, drafts, unreadable extraction pages, conflicts, and unresolved conditions remain explicit gaps.

## Validation

Tests require a dedicated PostgreSQL database named `food_kb_test_*`; business databases are rejected. Set `FOOD_KB_TEST_DBNAME`, `FOOD_KB_TEST_PORT`, `FOOD_KB_TEST_USER`, and `FOOD_KB_PASSWORD`, then run `python -m pytest`. Each database test creates an isolated schema; it does not reset user data. Tests using locally retained standards skip explicitly when those excluded fixtures are absent. Browser tests may skip when the documented Windows Edge/Node runtime is unavailable.

Local evidence replay scripts are under `scripts/`. KB-09 replay requires a prepared database/source baseline and writes only a new evidence directory. Original standards, manual transcriptions, business input, local configurations, database backups, and result artifacts are intentionally excluded from this repository. Engineering validation does not establish overall food classification accuracy, legal applicability, or expert approval. See [KB-09 engineering acceptance](../docs/knowledge-base/KB-09-engineering-acceptance.md).


## KB-10 retrieval commands

Run `retrieval-build --context SOURCE_IDS_JSON` with explicit `chunk_run_id`, `knowledge_run_id`, `taxonomy_snapshot_id`, and `version_snapshot_id`. `retrieval-report GENERATION_ID` returns the frozen corpus. `query-exact GENERATION_ID --filters FILTERS_JSON` supports standard/year/version/node/project/clause/type/review and separate method/judgment fields. `query-lexical GENERATION_ID --query TEXT` defaults to `zh_ngram_phrase`; `literal_raw` and `normalized_phrase` remain comparison paths. Queries accept `--limit`, `--offset`, optional filters and source-preserving confirmed condition facts. All commands require the global `--config` argument.

The Chinese index uses explicit character unigrams/bigrams and a GIN candidate index, followed by whole-phrase verification against original fields. It does not use an English stemmer or unreviewed synonym expansion. GB versus GB/T, years, and arbitrary text/unit case stay distinct. Unknown applicability stays visible; a related phrase hit never establishes the same food entity or a complete requirement list. Missing/corrupt indexes or database faults produce explicit degraded/failed states.

Only accepted manual body text is full-text indexed; catalog/classification/inspection records preserve their own roles and unavailable-body gaps. Generation membership and derived index fingerprints are fixed. See [KB-10 engineering acceptance](../docs/knowledge-base/KB-10-engineering-acceptance.md).
