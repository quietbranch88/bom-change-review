# Lesson 3: a real model smoke test with a budget limit

Historical latest result, 2026-09-25: removing `uniqueItems` from the API schema while retaining local duplicate checks allowed the fourth full E01 request to create an 11-field pending review. A separate process read it back; 112 local tests passed at that checkpoint. This was not human review or engineering approval. The chronology below preserves the failures and subsequent correction.

## Responsibilities and data flow

Public-source E01 paraphrase → key/endpoint preflight → single-attempt claim → OpenRouter → JSON validation → all-fields-pending snapshot.

The input is [evaluation/inputs.json](../evaluation/inputs.json), never reference answers. PDF parsing is not part of this lesson.

- The LLM proposes extracted fields.
- Python checks schema, types, citations, and budget, not engineering truth.
- A person accepts, corrects, or requests more evidence. Reviewed extraction is still not circuit approval.

## Historical budget policy

The fixed route was `mistralai/ministral-3b-2512`, `mistral/zdr`, temperature 0, maximum 2,048 output tokens, no automatic retry or provider fallback. Observed input/output pricing was US$0.10 per million tokens, also used as the request's price ceiling. This is historical endpoint information, not a current quotation or quality recommendation.

The dedicated key had a US$0.10 non-resetting limit and was stored in the Windows User environment variable `BOM_REVIEW_OPENROUTER_API_KEY`, not Git. Environment-variable storage is not an encrypted credential vault. That historical key expired on 2026-09-26 at 13:03 Taipei time.

The full context capacity plus output cap and 10% margin yielded a conservative token-cost bound of US$0.0146432. It was not an expected invoice or guarantee about external BYOK fees. Unknown cost remains reserved rather than being recorded as zero.

## Inspect without another inference request

```powershell
python openrouter_smoke.py status
```

This reads the original saved failure: `status=http_400`, `post_attempts=1`, `review_created=false`, `cost_usd=null`. It does not show later attempts. `plan` reads endpoint/key metadata, not inference; unlike a fully offline command, it uses the network. Expired credentials block it.

Do not delete `output/openrouter-e01-20260925/claim.json` or change paths to bypass the used authorization. Another inference requires explicit authorization.

## First failure: local tests did not prove the provider contract

At the first checkpoint, 75 local tests passed, but the real provider returned HTTP 400. The UI supplied no detailed reason; the implementation retained only a status code to avoid leaking raw errors. No particular schema keyword could yet be blamed.

A key-usage snapshot showed US$0 used and US$0.10 remaining. That was not a final per-request invoice. Check the contract and design safe diagnostics before authorizing another request.

Official references: [provider routing](https://openrouter.ai/docs/guides/routing/provider-selection), [structured outputs](https://openrouter.ai/docs/guides/features/structured-outputs), [usage accounting](https://openrouter.ai/docs/cookbook/administration/usage-accounting).

## Safe public diagnostics

The subsequent implementation bounded HTTP-error reads at 8 KiB plus one byte to detect overflow. It accepted only a JSON error structure, retained no raw body or headers, and first redacted the known key and recognizable authorization tokens.

A strict allowlist then retained only fixed messages, known schema keywords/error classes, and the Mistral name. Messages were capped at 500 characters; unknown or oversized free text became `[WITHHELD]`, not a supposedly safe truncated excerpt. Invalid JSON, failed reads, and oversized bodies produced fixed statuses. This deliberately loses some diagnostic detail.

Future result files include the safe summary; prior files were not rewritten and claims were not unlocked. Failed requests create no review and do not retry. Twelve new tests brought the historical total to 87. A local HTTP fixture exercised response → urllib → safe summary → file, not the real provider. Its synthetic `Unsupported schema keyword: uniqueItems` message was not evidence of the actual cause.

## Second authorized diagnostic request

At 19:09 Taipei on 2026-09-25, a separately authorized E01 request still returned 400. Adding single-level `metadata.raw` JSON decoding brought the historical total to 89 tests, but the saved real summary was only `Provider returned error`.

This did not establish that the provider had no detailed body, nor that the synthetic keyword failure caused this rejection. No third request was authorized at that checkpoint.

Evidence was saved under `output/openrouter-e01-20260925-diagnostic-02/` with separate approval and claim; the original remained unchanged. Reservations across result files were conservative accounting markers, not permission to spend US$0.20. The key's total limit stayed US$0.10.

## Recoverable private diagnostics

Not safe to publish does not mean not worth retaining. A successfully read POST-error body of at most 8 KiB can be redacted for the known key, then encrypted using Windows DPAPI's current-user scope into `error.private.json`. Public results contain only the safe summary and `private_archive_status`; no headers or credentials are added.

This is encrypted private evidence, not fully anonymized public data. Processes using the same Windows identity may decrypt it; it is not a defense against account compromise or administrators. Non-Windows, encryption failure, oversized bodies, or failed reads produce no plaintext fallback. Save failures are recorded; claims remain locked and no retry occurs. The first two discarded bodies could not be recovered retrospectively.

```powershell
python private_diagnostics.py <path-to-error.private.json>
```

By default, this only verifies decryption. Use `--reveal-private` only in a private local terminal; never paste the output into chat, GitHub, or shared logs. JSON escaping and redaction may make a raw JSON fragment unparsable. Git ignore does not control backups or synchronization. Exact-file deletion needs separate approval; there is no automatic upload or deletion.

Ten new tests brought the total to 99. Real Windows encryption/decryption and cross-process readback were verified; HTTP responses remained fixtures. Cross-account denial, provider content, and model quality were not proven. No paid call occurred in that implementation step. See [Microsoft's DPAPI contract](https://learn.microsoft.com/en-us/windows/win32/api/dpapi/nf-dpapi-cryptprotectdata).

## Third request: a real rejection category

After new authorization, the unchanged E01 request returned HTTP 400 at 19:40 Taipei. Its encrypted archive was readable, and an in-memory comparison confirmed the non-sensitive message `Invalid structured output syntax`. Raw bodies, account identifiers, and ciphertext were not published.

This established a structured-output rejection, not the exact unsupported construct. It did not justify guessing `uniqueItems`, missing `type`, or nullable types. [Mistral's custom structured-output documentation](https://docs.mistral.ai/studio/conversations/structured-output/custom) describes JSON Schema, not universal support for every schema expression.

Evidence was saved under `output/openrouter-e01-20260925-diagnostic-03/`. The key-usage snapshot still was not a final invoice. The diagnostic path now crossed the real provider boundary; successful extraction and review had not yet been demonstrated. A fourth inference needed separate authorization.

## Five single-variable probes

Five newly authorized requests compared minimal, enum-only, `uniqueItems`, nullable type-array, and nullable `anyOf` schemas. Only the `uniqueItems` variant failed, and its request differed from the baseline only by that keyword. Four saved answers were revalidated; their reported costs totaled US$0.0000296. Failed-request cost remained unknown. See the [results and limitations](../examples/schema-probes/results-20260925.md).

The result supported removing the API keyword while preserving Python uniqueness validation. It did not yet prove the full E01 fix or all models' behavior. Ten new tests brought the historical total to 109. All five authorizations were consumed; no automatic rerun followed.

## After the fix: the first real E01 draft

With separate authorization to change and verify E01 once, only the API schema's 13 `uniqueItems` declarations were removed. Offline schema, prompt, model, route, fields, and local duplicate rejection stayed unchanged.

At 20:04 Taipei on 2026-09-25, one request succeeded: 1,065 input tokens, 373 output tokens, reported cost US$0.0001438, no retry.

| Field | Saved extraction | Evidence |
|---|---|---|
| Original part | LT8301ESS | Q1 |
| Requester candidates | Empty array; no suggestions borrowed from replies | None |
| Input | 14–30 V | Q2 |
| Output | 5 V, 1.5 A, 7.5 W | Q2 |
| Topology / qualification requirement | flyback / AEC-Q100 | Q2 |
| Continuous current / PCB changes allowed | null: unspecified | None |

A fresh process confirmed response/draft/review consistency, 11 pending fields, current source binding, and no human decisions. This was one public-paraphrase extraction smoke test, not PDF parsing, a quality statistic, or replacement approval. AEC-Q100 in a requirement does not qualify the candidate.

```powershell
python local_review.py show --review output/openrouter-e01-20260925-fixed-04/review-0.json
```

These output files are local and ignored, not included in a fresh clone. The old `status` command still reads the first failure; do not rerun or delete claims because of that. Authorization was consumed. Human source review and resolution of unknown conditions came next.
