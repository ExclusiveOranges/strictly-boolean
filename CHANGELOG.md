# Changelog

## v1.5.2

- Added concurrent live candidate verification with a bounded worker pool.
- Kept Brave retrieval, adaptive retrieval limits, Boolean truth rules, license adjudication, ranking, and result classification unchanged.
- Kept Courier as the default logo while preserving the optional local 1980s logo toggle.

## v1.5.0

- Added bounded adaptive retrieval.
- Reworded result counts as candidates examined rather than implying exhaustive web totals.
- Added hard limits for retrieval deepening and API requests.

## v1.4.x

- Added license facets and `license:any-open`.
- Hardened license detection to prefer affirmative resource-level evidence.
- Added conservative related-page license probing for thin landing pages.
- Added license-evidence adjudication when conflicting signals are present.

## v1.3.x

- Added implicit AND.
- Added Advanced Search query construction.
- Added visible and locally enforced `site:` constraints.

## v1.2.x

- Added the local Flask interface.
- Added result grouping, provenance, explanations, transparent ranking, query persistence, and Firefox form-history protections.

## v1.0–v1.1

- Separated retrieval from verification.
- Added DNF-based retrieval planning, three-valued indexed evidence, URL deduplication, provenance, and transparent ranking.

## v0.5–v0.9

- Added the recursive-descent Boolean parser, structured explanations, retrieval/verification separation, pagination, and conservative indexed-evidence fallback.
