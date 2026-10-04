# Lesson 9: conditional voltage-range assessment

Historical checkpoint: 2026-09-27. [range_assessment.py](../range_assessment.py) reads local JSON without an LLM. It asks whether an applicable, reviewed BIAS recommended operating range covers the requirement—not whether the component is a direct replacement.

## Three executed branches

| Inputs | Result | Evidence identity |
|---|---|---|
| Actual requirements and pending candidate draft, no fact review/application context | `unknown` | Actual local case |
| Assumed direct input-to-BIAS mapping, internal VCC regulator, Tj −20–85°C, explicitly simulated review receipts | `matches_requirement` | `synthetic_fixture`: software branch only |
| Same synthetic setup, VCC tied to BIAS | `violates_requirement` | `synthetic_fixture`: software branch only |

Specifications come from the [saved candidate draft](../examples/ti-1222180/candidate-facts-v1.json). Synthetic wiring, temperature, and reviewers are not case evidence supplied by Zoe or an engineer.

## Decision sequence

1. A person reviews requirements/facts and provides traceable wiring, voltage-node mapping, and junction-temperature bounds. Ambient temperature is not automatically junction temperature.
2. A context binds the complete requirement/fact fingerprints and separates fact-review receipts from application declarations. Missing receipts are not fabricated.
3. The program selects a unique applicable recommended BIAS row for the exact candidate, VCC arrangement, and covered temperature range.
4. An inclusive containment comparison returns a violation only when applicable evidence does not cover the voltage requirement. Missing/inapplicable conditions remain unknown.

Guards:

- `absolute_maximum` never supports normal-operation success.
- Model suffixes are not merged. Product-model matching is not full orderable-part qualification.
- Unreviewed bounds, stale sources, missing conditions, unconfirmed mappings, or competing applicable rows cannot pass.
- A false mapping means this direct-mapping rule is inapplicable, not that the entire candidate circuit fails.
- Unsupported extra fact conditions produce a fixed error, not silent omission.
- Every result retains `needs_engineering_review`; output capability, qualification, pins, layout, and measurements remain unassessed.

## Context contract

- `schema_version: 1`.
- `origin`: `user_declared` or `synthetic_fixture`.
- `requirements_sha256` / `facts_sha256`: canonical JSON fingerprints, not byte-level file hashes.
- `candidate_model`: exact model.
- `fact_review`: null or an accepted receipt with status/reviewer/reference.
- `application`: null or a declaration containing input-to-BIAS mapping, VCC supply, junction-temperature min/max/unit `degC`, reviewer, and reference. Missing conditions may remain null.

The original candidate draft's pending status is preserved. A separate receipt binds its exact content. Reviewer/reference text is not authenticated identity, authorization, a signature, or proof of truth; forged inputs can defeat that trust assumption.

## Inspect the actual case

The historical output is local and absent from a fresh clone:

```powershell
python range_assessment.py show --requirements output/requirements-review-20260927/review-11.json --facts examples/ti-1222180/candidate-facts-v1.json --assessment output/range-assessment-20260927/actual-unknown.json
```

Expected: `status=unknown`, `report_currency=current`, gaps `fact_review_missing` and `application_conditions_missing`. Current means consistent with supplied local inputs, not compliant or synchronized with the vendor website.

`assess` uses the same requirements/facts flags, optional context, and a new output filename. Do not invent accepted receipts to bypass missing generated files.

Saved reports carry input fingerprints and rule version. `show` recomputes against current inputs; changed input/rules or underlying case sources make old results stale/unknown. This is not a distributed lock or automatic remote-document refresh.

## Verification scope

Historical evidence: 12 new / 176 total tests passed. Isolated mutations removed specification-class filtering and the review gate; relevant tests failed, originals were restored. Actual unknown and two synthetic branches ran through CLI → new file → fresh-process show. This proves local software behavior, not model quality, hardware operation, or engineering certification. Session records remain local.
