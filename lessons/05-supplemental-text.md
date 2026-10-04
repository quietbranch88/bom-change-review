# 第五課：先保存補充原文，再檢查判讀草稿

後續：[第六課](06-supplemental-model.md)增加模型串接與 model_output 待審來源；目前只完成本機替代回覆驗證，真實推論被金鑰／預算前置條件阻擋。本課 capture/attach-demo 行為與既有示範不變。

本課是本機、模擬資料的程式實作，沒有呼叫 LLM。AI 撰寫的示範草稿以 `demo_not_model_output` 標記，不能當成實際模型回覆或人工核准。

## 用一句话走完

模擬文字：[reply.txt](../examples/supplemental-text/reply.txt)

> 可以改 PCB，但接頭位置不能動；費用要先給主管確認。

先保存整句及換行，再附上一份分開記錄許可、限制、待確認事項的草稿。這句的示範解讀不是工程核准：

| 草稿內容 | 引用 | 仍不能宣稱 |
|---|---|---|
| 提出允許改板 | 可以改 PCB | 封裝／腳位／位置完全不限 |
| 接頭位置固定 | 接頭位置不能動 | 已驗證某候選能符合 |
| 費用待確認 | 費用要先給主管確認 | 主管已同意 |

## 三個分開的動作

1. `capture`：讀取既有「等待確認」教學快照與 UTF-8 文字檔。另存案件、SIM- 來源 ID、取得時間、原文、來源指紋及原補問快照。此時 `draft=null`，不需要模型才能保存資料。
2. `attach-demo`：讀取另外提供的示範 JSON，驗證來源 ID／指紋、分類、資料結構，以及引用是否真的存在原文；成功也只另存 `pending_review`。
3. `show`：重新讀檔，完整顯示原文、草稿、未驗證警告與原補問狀態。不寫回 PCB follow-up 或 E01 review。

所有操作的結果都以 `synthetic=true` 標示。`--synthetic` 是操作者的聲明，不能自動辨識輸入是否真的為模擬資料；本課只准用模擬文字，不匯入真實私人資料。

## 看本次已保存的結果

在專案目錄執行：

```powershell
rtk uv run --no-project --python 3.13 python supplemental_intake.py show --record output/supplemental-text-20260927/pending.json
```

這個 output 檔只在本機，未提交 Git。原文與兩項註記已保存；`value_proposal=true` 只是草稿判讀，`followup_state_unchanged=waiting_for_confirmation` 說明原流程並未放行。

## 自己重做

先依[第四課](04-pcb-followup.md)建立到 `r1.json` 的已問／等待快照。以下沿用本機已存在的那份；全新 checkout 要替換成自己建立的路徑。

```powershell
rtk pwsh -NoProfile -Command "New-Item -ItemType Directory -Path output/text-first-run -ErrorAction Stop"
rtk uv run --no-project --python 3.13 python supplemental_intake.py capture --followup output/pcb-followup-20260925/r1.json --text-file examples/supplemental-text/reply.txt --source-id SIM-TEXT-1 --synthetic --out output/text-first-run/source.json
```

接著將[草稿模板](../examples/supplemental-text/draft-template.json)另存為 `output/text-first-run/draft-input.json`，在編輯器將 `REPLACE_WITH_CAPTURED_SOURCE_SHA256` 換成剛才輸出的 `source_sha256`。其餘欄位就是人／日後模型需提出的待審判讀。保留模板，不把某次執行的指紋寫回共用模板。

```powershell
rtk uv run --no-project --python 3.13 python supplemental_intake.py attach-demo --record output/text-first-run/source.json --draft-file output/text-first-run/draft-input.json --out output/text-first-run/pending.json
rtk uv run --no-project --python 3.13 python supplemental_intake.py show --record output/text-first-run/pending.json
```

若故意把引用改成原文沒有的句子，草稿會被拒收；先前的 source.json 仍在。目的檔已存在則拒絕覆寫；可從原始 capture 另存新的草稿版本，但本課沒有選定「最新正式版」的版本登錄系統。

## 程式能檢查什麼，不能檢查什麼

| 檢查 | 目前能力 |
|---|---|
| 型別、欄位、來源 ID／指紋 | 可以拒絕不符契約的草稿 |
| 引用是否出現在原文 | 可以拒絕不存在的引用 |
| 已宣告 conditional/question/unknown | 提案值保持 null，不轉 true |
| 句子是否其實是疑問／條件句 | 沒有自動語意判讀 |
| 是否漏掉所有限制 | 沒有完整性保證，需人對照完整原文 |
| 判讀正確後更新主流程 | 尚未實作，不自動更新 |

例如「可以改 PCB 嗎？接頭位置不能動。」被故意寫成 allowed，且 constraints=[]，仍可能通過格式與引用檢查。測試刻意保留這個反例：它只能待審，`semantic_review` 與 `constraint_completeness` 都是 unverified，不能宣稱通過檢查就理解正確。

程式不讀文字中的指令來執行工具，但這不是 LLM prompt-injection 防禦效果的實測；本課根本沒有模型呼叫。

## 大小、保存與隱私邊界

- 原文最多 16 KiB、JSON 輸入／輸出最多 256 KiB。限量以 UTF-8 位元組計，超量拒收，不截斷後冒充完整資料。
- 原文與草稿以本機明文 JSON 保存；output 被 Git 忽略不等於加密或存取控制。
- 沒有第三方分享、上傳、付費模型呼叫或自動清理；檔案保留到使用者決定刪除。沒有在本次刪除任何紀錄。
- 指紋用來偵測內容不一致，不是身分驗證或防竄改簽章。不同 capture 之間沒有全域 SIM- ID 去重或去識別化功能。
- 未實作真實補充來源匯入政策、模型抽取、人工覆核推進與工程驗證。這些都是後续範圍，不能由本課的本機測試推定完成。

[程式](../supplemental_intake.py)／[測試](../tests/test_supplemental_intake.py)／本次驗證（本機稽核檔未公開）
