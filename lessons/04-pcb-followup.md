# 第四課：不知道，不等於還沒問過

後續：[第五課](05-supplemental-text.md)新增完整補充文字保存與示範草稿檢查，仍不做模型語意抽取或自動核准；本課固定三選一介面保持不變。

這一課實作單一欄位 `pcb_changes_allowed` 的本機補問狀態。所有補充回答都是模擬，不是真實需求方回覆；不呼叫模型、不傳送外部訊息、不修改原 E01 待審結果。

## 看三個結果

以下三份檔案從同一個「已問、等待」教學快照分支，並不是同一需求方依序給了三個答案。

| 模擬回答 | 新提案 | 流程狀態 |
|---|---|---|
| 可以修改 PCB | true | pending_review：提案待審 |
| 不能修改 PCB | false | pending_review：提案待審，不是缺資料 |
| 我不知道，要再確認 | null | waiting_for_confirmation：等待，不再次提問 |

這是程式處理明確選項的結果，不是測試 LLM 理解自然語言。`reply --answer` 只接受 allow、deny、unknown，文字固定；它沒有把真實回覆自動轉成 JSON 的能力。

本次保存結果在忽略提交的 `output/pcb-followup-20260925/`。在專案目錄查看：

```powershell
rtk uv run --no-project --python 3.13 python pcb_followup.py show --record output/pcb-followup-20260925/deny.json
```

注意三個不同資料：`value_proposal=false` 是模擬提案；`base_value_unchanged=null` 是原值；`base_field_status=needs_input` 是原覆核狀態。工具沒有默默把原本的待補問變成核准。

## 從新目錄重做

以下使用既有實測 review-2 作為唯讀起點；沒有該本機檔案時，先依[第二課](02-local-review.md)建立自己的教學 review，再替換 --review 路徑。first-run 目錄與檔名必須尚不存在。

```powershell
rtk pwsh -NoProfile -Command "New-Item -ItemType Directory -Path output/pcb-first-run -ErrorAction Stop"
rtk uv run --no-project --python 3.13 python pcb_followup.py init --review output/openrouter-e01-20260925-fixed-04/review-2.json --out output/pcb-first-run/r0.json
rtk uv run --no-project --python 3.13 python pcb_followup.py ask --record output/pcb-first-run/r0.json --out output/pcb-first-run/r1.json
rtk uv run --no-project --python 3.13 python pcb_followup.py reply --record output/pcb-first-run/r1.json --answer unknown --source-id SIM-1 --out output/pcb-first-run/r2.json
rtk uv run --no-project --python 3.13 python pcb_followup.py reply --record output/pcb-first-run/r2.json --answer deny --source-id SIM-2 --out output/pcb-first-run/r3.json
rtk uv run --no-project --python 3.13 python pcb_followup.py show --record output/pcb-first-run/r3.json
```

這條示範路徑是：先不知道，後來補充不能改板。兩次回覆都保留，不必重新問一次。`ask` 僅記錄教學中的提問動作，沒有對外發訊息。CLI 處理一次就結束；新輸入到來再執行下一次，不是背景輪詢。

## 為什麼不直接修改原 JSON？

原模型結果、補充來源、補問進度是不同證據。教學快照內保留原 review 副本與指紋，另存事件的時間、順序、SIM- 來源、固定模擬文字與選項。這不是原抽取 schema 的新欄位，也不是帶 SIM 引用直接寫回原需求。

- 已問或已有值時再 ask：拒絕，沒有新快照。
- 重複 SIM- 來源 ID：拒絕；同一檔名再次寫入：拒絕，保留原檔。
- 先 allow 後 deny：保留兩筆並顯示 conflict，提案值為 null；不自動採最後一筆。
- 先 deny 後 unknown：保留已提出的 false 與未知回覆，仍待審；unknown 不是撤回聲明。
- 原始案例來源／契約改變：顯示 stale，拒絕繼續寫入；另建新案例流程，不在本課自動合併。

## 邊界

此工具只綁定使用者提供的那份 review，沒有追蹤另一份較新的 review 是不是已成為「最新核准版」；沒有中央版本登錄、登入身分或防竄改簽章。指紋與結構檢查不是授權機制。

還沒有：真實補充來源匯入、LLM 語意抽取、人工核准回寫、衝突裁決、跨 11 欄的任務規則、自動通知。true 也不代表封裝／腳位不限；所有結果的工程適用性仍為 not_evaluated。

本機軟體行為的驗證見驗證紀錄（本機稽核檔未公開）；規則背景見11 欄草案（本機稽核檔未公開）。軟體測試通過不等於硬體規則完整或元件可替換。
