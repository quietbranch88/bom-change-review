# Schema 相容性對照：先離線，再決定是否送出

這些是合成題目的比較材料，不是 E01 修正。原三次 E01 呼叫都失敗，第三次確認供應商回覆 `Invalid structured output syntax`。後來另經明確授權，以獨立 schema_probe.py 執行五次對照，僅加入 uniqueItems 的 M02 失敗；詳見 [實際結果](results-20260925.md)。原 E01 schema 未覆寫，下列內容保留實驗設計。

[probes.json](probes.json) 的 base_schema 為一個物件，只有 value、unit、evidence_ids。每次 deep-copy base_schema，再將該 variant 的 property_overrides **整個替換**到 properties；不要將多個 variant 疊加。messages 全部相同，因此差別只在下列單一規則。

| 版本 | 相對 M00 唯一改動 | 要檢查什麼 |
|---|---|---|
| M00 | 無 | 最小的結構化輸出是否能被接受 |
| M01 | unit 只有 enum，省略 type | 是否不接受未顯式宣告型別的 enum |
| M02 | evidence_ids 加 uniqueItems | 是否不接受陣列唯一性限制 |
| M03 | value 改成 number 或 null 的 type 陣列 | 是否不接受這種可空寫法 |
| M04 | value 用 anyOf 表示 number 或 null | 與 M03 語意相同的另一種寫法是否不同 |

所有版本的提示都要求從 Q1 擷取 14 V；共同合理結果是 `{"value":14,"unit":"V","evidence_ids":["Q1"]}`。JSON Schema 接受 null 並不表示此題回答 null 正確；本地 cases 只檢查格式語意，不是模型評分。M00 刻意未要求陣列去重，不能直接拿來替換正式規則；正式的 Python 覆核仍應拒絕重複證據。

## 官方標準與供應商相容性要分開

- [JSON Schema enum](https://json-schema.org/understanding-json-schema/reference/enum) 允許省略 type，包括限制成 null；不能直接把原本 enum-only 判定為標準語法錯誤。
- [JSON Schema type](https://json-schema.org/understanding-json-schema/reference/type) 允許 type 陣列，也定義 uniqueItems。因此本機檢查通過，仍可能與供應商的支援子集不同。
- [OpenRouter 結構化輸出](https://openrouter.ai/docs/guides/features/structured-outputs) 提醒嚴格模式可能限制可用 Schema 特性，且支援依端點而異。這不證明本案是某一特性造成。

## 後續實驗的停止條件

先只安排 M00；若它仍回相同錯誤，就停止猜上述三個特性，轉查最小請求／路由／供應商的相容性。若 M00 成功，再按已授權的次數比較各版本，每個版本最多一次、不自動重試。比較時模型、端點、提示、temperature、strict、輸出上限與隱私路由一致；價格與金鑰有效期每次重新檢查。

成功至少要確認 HTTP 成功、模型／供應商符合、finish_reason=stop，且答案通過該 Schema 與本題三欄預期值；僅 HTTP200 不算成功。失敗保留安全摘要與加密證據。此最小格式不相容於現有 E01 覆核器，不應硬接 run_once 或冒充完整覆核成功；真正執行前需建立受相同預算／單次鎖保護的獨立診斷入口。

最小題成功不能單獨歸因原 E01 的故障，因為原題的提示、欄位與巢狀結構都不同。找到可疑特性後，仍須回到完整 E01，只更改該特性做確認；也可能同時有多個不相容點。probes.json 的 live_status=not_run 保留準備時的固定 fixture，不作為即時狀態；實測以獨立結果紀錄為準。五次授權已用完；本文件不授權新的 API 請求。
