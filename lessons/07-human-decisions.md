# 第七課：把人工決定套用到流程

2026-09-27。本課已實作並實際套用 Zoe 的三項文字決定；不呼叫模型、不增加 API 費用。

## 現在不是只存一篇筆記

新增 [supplemental_review.py](../supplemental_review.py)，將來源綁定的決定 JSON 套用到現有待審草稿，再另存一份可由程式重播／讀取的覆核快照。

流程：`pending_review + 明確決定 → 驗證來源與逐項決定 → 新 text_reviewed 快照`。

原 pending.json 保留不動。新快照包含原稿、原稿指紋、覆核者聲明、決定依據、套用時間與每項 before/action/after；讀取時由事件重建 reviewed_draft，不把修改後文字偽裝成模型原答。

本次實際決定：

| 目標 | 操作 | 新版結果 |
| --- | --- | --- |
| permission | accept | 允許改 PCB |
| constraints/0 | accept | 接頭位置不可改動 |
| open_questions/0 | correct | 費用要先給主管確認。 |

「主管確認」這句話已經過文字覆核，**不等於主管真的已確認費用**。所以 open_questions 仍保留，原 follow-up 不自動轉成完成；改板何時可開始仍未明確決定。

## 決定 JSON 與程式的分工

- 人：決定接受什麼、改成什麼，明確記錄 reviewer 與 decision_reference。
- 程式：比對整份 pending 的指紋，要求每個既有項目恰有一個決定，檢查替換值的格式與引文，保存修改歷程；不替人判斷語意。
- 此版僅支援 accept/correct，且必須覆核所有既有草稿項目。未決定完就保持原本 pending，不輸出 text_reviewed；尚無逐項暫存 UI、reject、增刪項目或工程簽核。

`pending_sha256` 使用 local_review.fingerprint 的標準化 JSON 指紋，不是檔案位元組的 SHA256。兩者不能混用；在本案例中前者是 e18c…、後者是 71b1…。改變草稿內容後，不能把舊決定套到新版本。

輸出欄位刻意分開：

- `state=text_reviewed`、`review_scope=existing_draft_items_only`：草稿內既有項目已逐項記錄決定。
- `semantic_review=human_decisions_recorded`：記錄人類決定，不保證人類永遠正確。
- `constraint_completeness=unverified`：不能由「全部現有項目已看過」推論「模型沒有漏抽其他條件」。
- `unresolved_questions`：文字已覆核但實際尚待處理的事項。
- `automatic_transition_allowed=false`、`engineering_suitability=not_evaluated`：不自動改原需求、不核准改板或替料。

## 本機使用

以下使用這台電腦既有的 ignored 教學輸出；全新 clone 不含這些資料。套用已實際執行過，請勿覆寫現有結果。若要在新案例使用，先建立自己的 pending，再依其指紋與逐項目標撰寫明確決定，選擇全新的輸出檔名。

本次已執行：

```powershell
rtk uv run --no-project --python 3.13 python supplemental_review.py apply --pending output/openrouter-supplement-20260927/pending.json --decisions output/openrouter-supplement-20260927/human-decisions-1.json --out output/openrouter-supplement-20260927/text-reviewed-1.json
```

可再次只讀查看：

```powershell
rtk uv run --no-project --python 3.13 python supplemental_review.py show --record output/openrouter-supplement-20260927/text-reviewed-1.json --pending output/openrouter-supplement-20260927/pending.json
```

show 要求提供當前 pending；若它與已覆核版本不同，或原案例來源已更新，結果為 stale、value_proposal=null，仍可查歷史。來源被破壞而無法解析時則回固定錯誤，不把歷史判成有效。

## 防護與限制

漏項、重複／不明目標、錯誤指紋、不存在的引用、來源過期、偽造 accept 的 after 值均被拒絕；既有輸出不能覆寫，也不能將 reviewed 快照再次當 pending 套用。

這是本機教學工具：reviewer 是自行聲明，沒有登入權限驗證或數位簽章。指紋不是防偽簽章；可任意改所有檔案的人仍可偽造紀錄。同一輸出檔的 exclusive write 防覆寫，不是跨目錄去重／多程序交易保證。寫入中斷留下的檔案也不會自動刪除重送。未接 Neo4j、MCP、EDA 或真正的工程審批系統。

驗證：12 項新增／全專案 164 項測試通過，兩個隔離故障注入均被測試抓到並還原；實際 apply → 檔案 → 另一程序 show 已通過。這驗證本機文字覆核流程，不是新的模型品質、身分驗證或硬體驗證。完整證據（本機稽核檔未公開）。
