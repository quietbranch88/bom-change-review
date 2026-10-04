# 第二課：讓覆核留下紀錄

這一課做本機命令列工具，不是網頁。**程式保存人的決定，不代替人判斷原文支持什麼。** 沒有 OpenRouter 呼叫，也沒有實際工程師簽核。

## 先看一個情境

我們手動製作一份錯誤草稿，把 E01 的最高輸入電壓寫成 300 V。這不是任何模型的實際回答，不能拿來比較模型品質。

| 動作 | 最新欄位 | 保留的紀錄 |
|---|---|---|
| 匯入草稿 | input_max = 300 V，待審 | 原始草稿、來源內容、各自的 hash |
| 對照 Q2 修正 | input_max = 30 V，已修正 | 300 → 30、理由、覆核者標籤、UTC 時間 |
| 詢問電流條件 | continuous_current = null，待補問 | 「1.5 A 是連續還是峰值？」 |

因此案件仍是 `needs_input`，不是已完成，更不是可安全替料。其他未看過的欄位仍保留 `pending`。

這裡 Q1/Q2 是 [評測改寫資料](../evaluation/inputs.json) 的區塊 ID，不是原始節錄檔 Q1–Q6 的行號；紀錄會嵌入完整 case context，避免把兩套定位混用。

## 三種決定

- `accept`：接受目前的擷取值；不改值。可以接受 null，意思是確認「現有來源沒交代」，不是確認需求不存在。
- `correct`：提供完整替換欄位，包括 value、unit、evidence_ids，且必須真的改變內容。不能只換值卻丟失單位或證據。
- `needs_input`：值保持不變，理由寫清楚需要問什麼。不能因其他欄位通過就略過。

每次都必須明確提供 reviewer 與 reason。之後可重新覆核同欄位，最新動作決定當前狀態，舊動作仍在 history。全部欄位都 accepted/corrected 才標記 `reviewed`；任何 needs_input 阻止完成。工程適用性永遠是 `not_evaluated`。

**不要把這個狀態當成權威簽核。** reviewer 只是操作者自填文字，不驗證身分；示範一律使用 `demo-reviewer`，不代表 Zoe 已審。

## 跟著跑一次

在專案目錄，以 Python 3.11+ 執行。需要 uv 時，將 `python` 換成 `uv run --no-project --python 3.13 python`。先建立新的練習資料夾；若名字已存在，換一個新名字，不刪舊紀錄。

```powershell
New-Item -ItemType Directory output/review-practice
python local_review.py init --case E01 --draft examples/local-review/draft.simulated.json --origin demo --out output/review-practice/review-0.json
python local_review.py decide --review output/review-practice/review-0.json --field input_max --action correct --replacement examples/local-review/input-max.corrected.json --reviewer demo-reviewer --reason "Demo: Q2 states 30 V, not 300 V." --out output/review-practice/review-1.json
python local_review.py decide --review output/review-practice/review-1.json --field continuous_current --action needs_input --reviewer demo-reviewer --reason "Demo: is 1.5 A continuous or peak?" --out output/review-practice/review-2.json
python local_review.py show --review output/review-practice/review-2.json
```

預期：revision=2、input_max=30、continuous_current=null、extraction_status=needs_input、engineering_suitability=not_evaluated；history 有兩筆。review-0.json 的 300 V 仍在，沒有被覆寫。

若你要練習「接受」，仍用示範身分，不要把例子當真人審核：

```powershell
python local_review.py decide --review output/review-practice/review-2.json --field input_min --action accept --reviewer demo-reviewer --reason "Demo: checked the explicit 14 V requirement in Q2." --out output/review-practice/review-3.json
```

接受 input_min 不會解決 continuous_current 的待補問，整案仍 needs_input。

## 為什麼另存版本？

每次寫入使用新檔名；目標存在就拒絕，不能覆寫原稿或先前覆核。讀取時從原始 draft 重播 events，檢查順序、before/after、欄位型別及時間。來源內容或欄位契約變更後，舊紀錄的 show 會顯示 `stale`，不允許繼續 decide，必須為新來源重新 init。

若只是修正抄錯，可在現有來源上 correct。若客戶補充原本沒有的條件，應先保存新的來源版本再開始新覆核，不能用原來沒說該條件的 Q2 作證。任意新文件的匯入介面尚未實作，目前只支援 inputs.json 裡的 case 與既有回應格式。

## 邊界與尚未完成的事

- 匯入接受 [evaluation 定義的 case_id／fields 回應格式](../evaluation/README.md)，不是早期 request.reference-draft.json 的另一套結構。沒有偷偷把參考答案當模型輸入或語意檢查器。
- `--origin` 的 demo/manual_draft/model_output 都是自填來源聲明；程式沒有連到模型驗證。這次所有示範都是 demo。
- 自填 actor、時間與 hash 不等於不可竄改稽核。持有本機檔案的人可以重寫整份紀錄；沒有登入、電子簽章或權限隔離。
- 另存 JSON 不是有交易保證的資料庫。寫到一半中斷可能留下損壞的新檔，舊檔不被覆寫；損壞紀錄須檢查並改用新檔名重做，不能聲稱 crash-safe。
- 每次需自己指定要延續哪個快照。可以從同一版本分叉，工具不決定哪個是唯一最新版，也不處理多人合併。
- `show` exit 0 只表示成功讀取／產生報告，即使狀態 needs_input/stale 也是 0；請讀 extraction_status。驗證／I/O 錯誤 exit 2。
- 本機紀錄保留到使用者決定刪除；本工具無刪除指令、無外傳。示範輸出放在已忽略的 output/；不要放公司私人 BOM 或真實個資到公開專案。
- 沒有 UI、OpenRouter、Neo4j、MCP、EDA、實測或自動替料核准。

[實作](../local_review.py) · [測試](../tests/test_local_review.py) · 驗證與限制（本機稽核檔未公開）
