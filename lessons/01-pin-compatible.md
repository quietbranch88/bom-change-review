# Lesson 1: matching pins do not guarantee a replacement

Read a real difference, separate component facts from design requirements, and change a requirement to observe the decision.

## Start with the manufacturer

The [TI LM5155 product page](https://www.ti.com/product/LM5155) lists LM51551 as a pin-for-pin, same-function alternative while noting additional hiccup protection. Both statements matter.

| Part | Hiccup overload protection |
|---|---|
| LM5155 | Disabled |
| LM51551 | Enabled |

Source: [SNVSB75E, section 6](https://www.ti.com/document-viewer/LM5155/datasheet/GUID-1164172C-0470-43FB-9472-A372BB56068B). This is documented, not guessed from the name.

Hiccup temporarily stops switching after sustained current limiting and restarts after a delay. Soft-start settings and severe load transients can affect unintended activation. Check startup and load behavior, not just pins. See [section 9.3.10](https://www.ti.com/document-viewer/LM5155/datasheet/GUID-4477DCCC-41CD-4FDB-963C-08389EF4A315). This lesson performs no circuit simulation or product-specific prediction.

Backend analogy: matching API routes and schemas do not guarantee matching timeout or retry behavior.

## State the requirement

| Synthetic requirement | LM51551 result | Reason |
|---|---|---|
| Built-in hiccup required | pass | Documented feature |
| Built-in hiccup forbidden | fail | Explicit conflict |
| No requirement supplied | unknown | Feature known, design acceptance unknown |

The third row is an unknown requirement, not an unknown component specification.

## The first rule

The core in [lesson1.py](../lesson1.py):

```python
if requirement == "unspecified" or actual is None:
    return "unknown"
expected = requirement == "required"
return "pass" if actual == expected else "fail"
```

`actual` comes from a sourced catalog; the designer supplies `requirement`. Upstream validation permits only `required`, `forbidden`, or `unspecified`. Do not use `if not actual` to detect missing data: `False` means known absence; `None` means unknown.

```powershell
python lesson1.py --original LM5155 --candidate LM51551
```

Expected excerpt; the complete JSON also includes reasons, gaps, sources, and unverified checks:

```json
{
  "catalog_pin_for_pin": "pass",
  "design_behavior_acceptance": "unknown",
  "overall": "needs_review"
}
```

Change only the requirement:

```powershell
python lesson1.py --candidate LM51551 --hiccup required
python lesson1.py --candidate LM51551 --hiccup forbidden
```

The first feature check passes, but the overall result remains `needs_review`. The second is `blocked_for_requested_scope`. No hardware operation occurs.

## Counterexamples

```powershell
python lesson1.py --original LM5155 --candidate LM5156H
```

[LM5156H](https://www.ti.com/product/LM5156H) is related, but its pinout category differs: WSON 12-pin versus HTSSOP 14-pin. The fixed scope is replacement without PCB changes, so this check fails. Redesign would be another scope.

Predict before running:

```powershell
python lesson1.py --candidate LM5155 --hiccup forbidden
python lesson1.py --candidate LM51551-Q1 --hiccup required
```

The first feature check passes; the overall result still needs review. The second is unknown because that exact suffix is absent from this catalog. Request evidence rather than guessing family specifications.

## Where GraphRAG and MCP fit

RAG could retrieve classifications and datasheet sections, a graph could connect design versions to evidence, and MCP could expose the checker to an Agent. The decision rules and independent test expectations must remain unchanged.

The demonstrated claim is separation of classification, functional differences, and requirement gaps. Whether graph retrieval improves existing queries requires a separate experiment.
