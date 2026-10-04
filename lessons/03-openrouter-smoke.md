# 第三課：有預算限制的真實模型試跑

2026-09-25 最新狀態：API Schema 移除 uniqueItems、保留本機去重後，第四次完整 E01 成功建立 11 欄待審紀錄，另一程序已讀取驗證。112 項本機測試通過。這不是人工覆核或工程核准；以下保留失敗到修正的教學脈絡，最新成果在末段。

## 資料怎麼走

E01 公開來源改寫題 → 檢查金鑰限額與端點價格 → 寫入單次請求鎖定紀錄 → OpenRouter → 驗證 JSON → 建立全部欄位「待審」的快照。

最後兩步只在模擬回應測試通過；真實呼叫目前停在 HTTP 400。輸入來自 evaluation/inputs.json，不把 references.json 的答案送給模型，也不把 PDF 解析當成本課已完成的功能。

- LLM 負責把來源整理成指定欄位。
- Python 負責欄位、型別、來源與預算檢查；不能證明工程事實正確。
- 人負責接受、修正或要求補資料；即使所有擷取欄位通過，也不是替料或電路核准。

## 預算如何限制

固定 mistralai/ministral-3b-2512、mistral/zdr、temperature=0、最多輸出 2,048 tokens；不自動重試或切換供應商。當次端點輸入／輸出報價各 US$0.10／百萬 tokens，並把此價格上限放入請求。這是當時端點資訊，不是永久報價或品質推薦。

專用金鑰總限額 US$0.10、不重置，儲存在 Windows User 環境變數 BOM_REVIEW_OPENROUTER_API_KEY；不使用共用金鑰，不寫入 Git。此儲存方式不是加密密碼庫。當前金鑰於台北時間 2026-09-26 13:03 到期。

試跑前以完整 context 容量加輸出上限、再加 10% 餘裕算出 US$0.0146432 的保守 token 費用上界。這不是預期帳單，也不是外部 BYOK 費用保證。真正費用應取供應商回傳與帳務紀錄；未知就保留預算，不當作零元。

## 現在可以安全查看什麼

在專案目錄執行以下命令，只讀取已保存結果，不呼叫模型：

```powershell
python openrouter_smoke.py status
```

結果為 status=http_400、post_attempts=1、review_created=false、cost_usd=null。`plan`（也是預設命令）只查端點與金鑰 metadata，不做推理；金鑰到期後會阻擋。

本輪已執行過 `run`。不要刪除 output/openrouter-e01-20260925/claim.json 或換路徑繞過單次限制；再次推理需重新取得授權。

## 為什麼 75 個測試通過，API 還是失敗？

本機測試證明的是程式在給定回應下會怎麼處理，不是外部服務必然接受這份請求。OpenRouter 顯示 Mistral 上游一次失敗、HTTP 400，沒有顯示更詳細原因；本機為避免洩漏原始錯誤內容只保存狀態码，因此目前不能斷言是哪個欄位不相容。

後續應先檢查請求契約，設計有範圍且遮蔽敏感內容的診斷，再在新授權下測試。不能藉「修一下再試」無限消耗預算。成功後仍須人工覆核，才能討論擷取品質。

查核後的金鑰用量快照為 US$0，剩餘 US$0.10；它不是逐次最終帳單，所以本輪紀錄仍為待核對。詳見 驗證與限制（本機稽核檔未公開）。

官方契約：[供應商路由](https://openrouter.ai/docs/guides/routing/provider-selection)、[結構化輸出](https://openrouter.ai/docs/guides/features/structured-outputs)、[用量計費欄位](https://openrouter.ai/docs/cookbook/administration/usage-accounting)。

## 後續：安全錯誤診斷已實作，未再試跑

上述「只保留狀態碼」描述的是首次失敗當時的程式。現在遇到 HTTP 錯誤時，會限量讀取回應（8 KiB 上限，多讀 1 byte 判斷超限），只接受 JSON error 結構，不保存原文或標頭。

先遮蔽目前金鑰與可辨識的 token／Authorization，再做嚴格白名單比對：只保留固定錯誤文字、已知 schema keyword、已知錯誤類型及 Mistral 名稱。訊息上限 500 字元；超長或無法辨識的自由文字一律為 `[WITHHELD]`，不是把前 500 字直接當成安全內容。JSON 損壞、讀取失敗或過大也只留固定狀態。這會犧牲部分除錯資訊，不能保證所有上游錯誤都能被解釋。

新診斷會進入未來執行的 result.json；preflight 錯誤也可在 CLI 顯示。費用未知時仍保留預算，HTTP 失敗不產生覆核紀錄、不重試。沒有追補或改寫首次失敗的檔案，也沒有刪除 claim；下一次真實推理仍需新的明確授權。

12 項新增測試及全專案 87 項測試通過。本機 HTTP 伺服器驗證了錯誤回應 → urllib → 安全摘要 → 結果檔案的路徑；服務與金鑰都是測試替身，不是 OpenRouter 端到端成功證據。示範中的 `Unsupported schema keyword: uniqueItems` 只是測試資料，不是本次 400 的已知原因。

## 第二次明確授權的診斷結果

Zoe 後來明確同意再送一次 E01，總限額仍 US$0.10、不自動重試。已補上單層 metadata.raw JSON 解析，同樣只保留白名單固定摘要；新增兩項測試，全專案 89 項通過。2026-09-25 台北時間 19:09 的第二次請求仍回覆 400，實際保存的摘要只有 `Provider returned error`。供應商 UI 亦未顯示詳細原因。

這說明本機診斷測試通過，仍不代表能辨識真實供應商的所有錯誤。不能由摘要缺失判定供應商沒有詳細回應，也不能把 synthetic 的 uniqueItems 錯誤冒充這次原因。尚未修好，不做第三次未授權呼叫。

第二次證據位於 output/openrouter-e01-20260925-diagnostic-02；新增獨立 approval.json 與 claim，第一次檔案保留。原本 `status` 命令仍讀第一次結果，不是第二次狀態。兩份結果中的 reservation 代表各次不確定帳務的保守標記，不是新增 US$0.20 花費授權；平台金鑰總限額從未提高。後續可先安排可安全檢視的完整診斷，再討論最小請求對照試驗，不能盲目重送或宣稱已修復。

## 可還原的私有診斷

前一版把「不能公開」與「不能保留」混在一起：讀到未知錯誤後直接丟棄。現在將兩者分開。POST 的 HTTP 錯誤本文不超過 8 KiB、讀取成功且本機加密可用時，先遮蔽已知金鑰，再用 Windows DPAPI 的目前使用者範圍加密，保存到該輪的 `error.private.json`。不保存標頭或額外附上憑證；一般 `result.json` 只有安全摘要與 `private_archive_status`。

這是加密私有證據，不是「已去除全部個資」的公開資料。與目前 Windows 身分相同的程序可解密；不保證抵抗帳號遭入侵或管理員。非 Windows、加密失敗或本文過大時不保存原文；存檔失敗標記 `save_failed`。兩種失敗都不改成明文、不解除請求鎖，也不重送。

只有未來另行授權的請求可能產生此檔案。前兩次原文已經丟失，沒有檔案可以補救。若將來存在檔案，預設查看只驗證能否解密，不輸出原文：

```powershell
python private_diagnostics.py <該輪的error.private.json完整路徑>
```

確定要在自己的本機終端機閱讀私有內容，才加 `--reveal-private`；不要將輸出貼到聊天、GitHub 或共享終端紀錄。內容以 JSON 跳脫輸出，已知金鑰遮蔽可能使原始 JSON 片段不再可解析。`output/` 被 Git 忽略，但不是備份或同步軟體的存取控制；不要把私有目錄另外上傳。診斷結束後另取得同意刪除這個確切的私有檔案，保留 claim/result；目前沒有自動刪除或上傳。

新增 10 項本機測試，全專案 99 項通過；真實 Windows 加解密與分程序檔案還原已驗證。HTTP 來源及 preflight 都是測試替身，不證明 OpenRouter 實際回應內容、跨帳號拒絕或模型品質。這輪沒有付費呼叫。DPAPI 範圍依據 [Microsoft 官方契約](https://learn.microsoft.com/en-us/windows/win32/api/dpapi/nf-dpapi-cryptprotectdata)。

## 第三次：終於保留到實際拒絕原因的類別

Zoe 回覆「好測試」後，另授權一次相同 E01 請求，沒有修改 schema 或模型。台北時間 2026-09-25 19:40 收到 HTTP400；這次 error.private.json 成功加密保存。預設檢查證實可解密，另以記憶體中的固定非敏感訊息比對，確認供應商訊息為 `Invalid structured output syntax`。沒有把完整錯誤本文、帳號欄位或密文貼到聊天。

已知：供應商拒絕結構化輸出語法。未知：是哪個 schema 寫法、轉換或供應商限制造成。不能直接指控 uniqueItems、缺少 type 或可空型別；也不能說硬體資料本身有問題。[Mistral 官方文件](https://docs.mistral.ai/studio/conversations/structured-output/custom) 說明自訂結構化輸出採用 JSON Schema，但這不保證接受我們送出的每種寫法。下一步應做最小 schema 對照；本次不改程式，不再發送請求。

專用金鑰用量快照仍為零、剩餘 US$0.10，不是最終逐次帳單。證據在 output/openrouter-e01-20260925-diagnostic-03，包含獨立 approval/claim/result、加密錯誤及安全 diagnosis。診斷資料保存路徑已跨真實供應商邊界驗證；成功產生答案及覆核仍未驗證。第四次推理需要另行授權。先前「沒有第三次呼叫」僅描述當時階段，由此結果更新。

## 後續：五次單一變因比較

另獲最多五次明確授權後，實測最小版、enum-only、uniqueItems、可空 type 陣列、可空 anyOf。只有 uniqueItems 版本失敗，且與最小版請求的唯一差異確實是該規則。其餘四次的答案及已保存檔案都重新驗證，費用回報合計 US$0.0000296，失敗費用仍未知。詳見 [結果表與限制](../examples/schema-probes/results-20260925.md)。

這說明「陣列不可重複」可以由本機 Python 保證，不一定能直接交給模型供應商的 Schema 引擎。合理候選修正是移除 API Schema 中的 uniqueItems，保留本機校驗；但本次尚未改動或驗證完整 E01，也不能推廣為所有模型都不支援。新增獨立入口與 10 項測試，全專案 109 項通過。五次授權已用完，不自動重送。

## 修正後：第一份真實 E01 草稿

另取得修正及一次完整 E01 驗證的同意後，只移除 API Schema 的 13 個 uniqueItems；離線 Schema、提示、模型、路由、欄位與本機拒絕重複值的規則不變。2026-09-25 台北時間 20:04，一次請求成功建立待審紀錄；回報 1,065 input tokens、373 output tokens、費用 US$0.0001438，沒有重試。

模型保存的草稿包含：

| 欄位 | 擷取內容 | 來源 |
|---|---|---|
| 原始料號 | LT8301ESS | Q1 |
| 提問者候選 | 空陣列，沒有把回覆建議冒充提問者候選 | 無 |
| 輸入電壓 | 14–30 V | Q2 |
| 輸出 | 5 V、1.5 A、7.5 W | Q2 |
| 拓樸／資格要求 | flyback／AEC-Q100 | Q2 |
| 連續電流、允許改板 | null：未交代 | 無 |

另由新程序確認 response/draft/review 內容一致、11 欄全部 pending、來源未過期、沒有人工決策事件。這是一次公開來源改寫題的成功擷取，不是原始 PDF 解析、模型品質統計或替料核准。需求出現 AEC-Q100 也不等於候選零件已具備資格。

讀取這次紀錄，不會呼叫模型：

```powershell
python local_review.py show --review output/openrouter-e01-20260925-fixed-04/review-0.json
```

注意：舊 `openrouter_smoke.py status` 仍讀第一次失敗目錄；不要因此重送或刪除 claim。這次新同意已用完。接下來應由人逐欄核對來源，特別處理未知條件；不要直接把待審改成通過。完整修正與實測證據見 audit（本機稽核檔未公開）。
