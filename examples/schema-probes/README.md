# Schema compatibility probes: design offline before sending

These synthetic probes are not the E01 fix. Three earlier E01 requests failed; the third retained the real message `Invalid structured output syntax`. Five later, separately authorized probes found only M02 failing. See [results](results-20260925.md) and the subsequent full-E01 confirmation. This file preserves the experiment design; it authorizes no new request.

[probes.json](probes.json) defines a base object with value, unit, and evidence IDs. Deep-copy it for each variant, replacing the entire overridden property—not accumulating variants. Messages stay identical.

| Variant | Only change from M00 | Question |
|---|---|---|
| M00 | None | Is the minimal structured output accepted? |
| M01 | Unit enum without type | Is an enum with no explicit type accepted? |
| M02 | Add evidence-ID `uniqueItems` | Is array uniqueness accepted? |
| M03 | Value type array: number or null | Is this nullable expression accepted? |
| M04 | Value anyOf: number or null | Does an equivalent expression behave differently? |

Every prompt asks for 14 V from Q1. Expected answer: `{"value":14,"unit":"V","evidence_ids":["Q1"]}`. Schema-valid null is not a correct answer here. Local schema cases are not model-quality grading. M00 deliberately permits duplicates; it must not replace the production Python uniqueness guard.

## Standard validity versus provider support

[JSON Schema enum](https://json-schema.org/understanding-json-schema/reference/enum) can omit type; [type](https://json-schema.org/understanding-json-schema/reference/type) supports type arrays. Array uniqueness is a standard constraint. Standard-valid schemas can still exceed a provider's supported subset.

[OpenRouter structured-output documentation](https://openrouter.ai/docs/guides/features/structured-outputs) describes endpoint-dependent support. It alone does not identify this incident's cause.

## Stop conditions and comparison controls

Start with M00. If it fails, stop blaming the variants and investigate the minimal request/route/provider contract. If it succeeds, compare authorized variants once each without retry. Keep model, endpoint, messages, temperature, strict mode, output cap, and privacy routing identical. Recheck key validity and pricing each time.

Success requires the intended model/provider, `finish_reason=stop`, valid structure, and the expected three values—not just HTTP 200. Failures retain safe summaries and encrypted private evidence. This format is incompatible with the E01 reviewer; use the independent budget/claim-protected diagnostic entrypoint, not a fabricated full-review success.

A minimal success does not prove the original E01 cause: prompts, fields, nesting, and combinations differ. Confirm the suspected feature by changing only that feature in the full E01 request under separate authorization. More than one incompatibility may exist.

Fixture `live_status=not_run` preserves preparation state; the results record contains actual observations. All five authorizations were used. This document grants no additional API access.
