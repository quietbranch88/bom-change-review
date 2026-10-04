# Lesson 2: record human review

This is a local CLI, not a website. It records human decisions; it does not decide what the source supports. No OpenRouter request or engineering sign-off occurs.

## A deliberately wrong draft

The hand-created E01 draft says 300 V instead of 30 V. It is not a real model answer or a model-quality benchmark.

| Action | Current field | Retained evidence |
|---|---|---|
| Import | `input_max = 300 V`, pending | Draft, source context, hashes |
| Correct against Q2 | `input_max = 30 V`, corrected | Before/after, reason, reviewer label, UTC time |
| Ask about current | `continuous_current = null`, needs input | Is 1.5 A continuous or peak? |

The case remains `needs_input`, not complete or safe for replacement. Other fields remain pending. Q1/Q2 are block IDs in the [evaluation input](../evaluation/inputs.json), not the original excerpt's Q1–Q6 locators; the record embeds its case context.

## Decisions

- `accept`: retain the value. Accepting null confirms that the source does not specify it, not that the requirement is absent.
- `correct`: provide a complete changed field: `value`, `unit`, and `evidence_ids`.
- `needs_input`: retain the value and explain what to ask.

Reviewer and reason are mandatory. The latest action sets the field's current state; history retains earlier actions. All fields must be accepted/corrected to become `reviewed`; any `needs_input` blocks completion. Engineering suitability stays `not_evaluated`.

Reviewer labels are self-declared, not authenticated signatures. Use `demo-reviewer`, not a claim that Zoe reviewed the example.

## Run it

Use Python 3.11+. If needed, substitute `uv run --no-project --python 3.13 python`. Choose a new directory if the destination exists; preserve earlier records.

```powershell
New-Item -ItemType Directory output/review-practice
python local_review.py init --case E01 --draft examples/local-review/draft.simulated.json --origin demo --out output/review-practice/review-0.json
python local_review.py decide --review output/review-practice/review-0.json --field input_max --action correct --replacement examples/local-review/input-max.corrected.json --reviewer demo-reviewer --reason "Demo: Q2 states 30 V, not 300 V." --out output/review-practice/review-1.json
python local_review.py decide --review output/review-practice/review-1.json --field continuous_current --action needs_input --reviewer demo-reviewer --reason "Demo: is 1.5 A continuous or peak?" --out output/review-practice/review-2.json
python local_review.py show --review output/review-practice/review-2.json
```

Expected: revision 2, input maximum 30, continuous current null, `needs_input`, `not_evaluated`, two history events. The original 300 V file remains unchanged.

```powershell
python local_review.py decide --review output/review-practice/review-2.json --field input_min --action accept --reviewer demo-reviewer --reason "Demo: checked the explicit 14 V requirement in Q2." --out output/review-practice/review-3.json
```

Accepting the minimum does not resolve the current question.

## Versioning

Writes require new filenames and refuse existing destinations. Reads replay events, checking sequence, before/after, types, and times. Changed source or field contracts make old records `stale`; further decisions require a new initialization.

Correct copying mistakes against existing evidence. New customer information needs a new source version, not a citation to a block that never contained it. Arbitrary document import is not implemented; supported cases come from `inputs.json`.

## Limits

- The [evaluation response contract](../evaluation/README.md) differs from `request.reference-draft.json`. Reference answers are not model input or a semantic checker.
- `demo`, `manual_draft`, and `model_output` are provenance declarations, not verified provider origins.
- Actors, timestamps, and hashes are not tamper-proof audit or access control. File owners can rewrite records; there is no login or signature.
- JSON files are not database transactions. An interrupted write may leave a corrupt new file, while the previous file remains intact. Inspect failures and use a new destination; do not claim crash safety.
- Users choose the snapshot to continue. There is no canonical latest version or multi-user merge.
- `show` exits 0 even for `needs_input` or `stale`; read the status. Validation/I/O errors exit 2.
- Records remain until the operator deletes them. There is no deletion command or outbound transfer. Ignored `output/` is not encryption; do not commit private BOMs or personal data.
- This lesson adds no UI, model request, Neo4j, MCP, EDA, hardware test, or automatic replacement approval.

[Implementation](../local_review.py) · [Tests](../tests/test_local_review.py). Verification records remain local.
