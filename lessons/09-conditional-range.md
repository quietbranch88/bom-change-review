# 第九課：條件式電壓範圍比較器

2026-09-27。已實作 [range_assessment.py](../range_assessment.py)，只讀本機 JSON，不呼叫 LLM。它回答的是「在已聲明且已覆核的條件下，BIAS 建議工作電壓範圍是否涵蓋需求」，不是「這顆可以直接替料嗎」。

## 三個分支已跑過

| 資料組合 | 本次結果 | 證據身分 |
| --- | --- | --- |
| 實際保存的需求與候選草稿，尚無事實覆核／接法條件紀錄 | unknown | 實際本機案件，不能假裝資料齊全 |
| 假設輸入直接加到 BIAS、VCC 由內部穩壓器供電、接面溫度 −20～85°C，並放入明確標示的模擬覆核紀錄 | matches_requirement | synthetic_fixture，只驗證程式分支 |
| 上述模擬條件改成 VCC 直連 BIAS | violates_requirement | synthetic_fixture，只驗證程式分支 |

數值與條件的來源仍是[第八課的候選事實草稿](../examples/ti-1222180/candidate-facts-v1.json)及已檢查的資料表。這裡的模擬接法、溫度與覆核者並非 Zoe 或工程師提供的案件證據。

## 誰決定什麼

1. 使用者覆核需求與候選事實，另提供可回查的接法、電壓節點對應及接面溫度範圍。環境溫度不會自動當成接面溫度。
2. Context JSON 綁定整份需求與候選資料指紋，分開保存事實覆核紀錄與案例條件紀錄。沒有提供就保持缺資料，不補造接受紀錄。
3. 程式只選出符合精確候選型號、規格類別與 VCC 條件的 BIAS 建議工作列；需唯一適用列，且溫度範圍落在規格適用範圍內。
4. 最後用含邊界的範圍包含關係比較。條件適用但電壓不涵蓋才是 violates_requirement；缺少或不適用的條件是 unknown。

重要防護：

- `absolute_maximum` 永遠不當作正常工作通過證據。
- 型號不做尾碼合併；此版是產品型號層級，不是完整訂購料號資格認證。
- 未覆核輸入上下限、過期需求、缺條件、未確認電壓映射、相互競爭的適用列，都不會自動通過。
- false 映射表示不能套用這個直接映射規則，不等於候選電路一定失敗。
- 候選事實的額外未知條件不默默忽略；目前支援的條件結構不符合時回固定錯誤。
- 無論 matches、violates 或 unknown，整案仍為 needs_engineering_review；輸出能力、資格、腳位、佈局與實測未評估。

## Context 格式

Context 不是由模型猜出來的。最外層必須有以下欄位：

- `schema_version: 1`
- `origin`: `user_declared` 或 `synthetic_fixture`
- `requirements_sha256`、`facts_sha256`: local_review.fingerprint 的標準化 JSON 指紋，不是檔案位元組雜湊。
- `candidate_model`: 精確模型名。
- `fact_review`: null，或含 status／reviewer／reference 的接受紀錄。
- `application`: null，或含 input_maps_to_bias、vcc_supply、junction_temperature、reviewer、reference 的條件聲明。溫度格式為 min／max／unit=degC；缺少條件可用 null。

`human_fact_review=pending` 的原始候選草稿不會被修改。新的接受紀錄由獨立 context 提供，綁定同一份草稿內容。此本機工具不驗證人員身分或紀錄真偽；非空 reviewer/reference 只是聲明，不是登入權限、數位簽章或證明。有人偽造所有輸入檔時，工具不能保障真實性。

## 查看實際案例

以下輸出已保存，直接 show 即可，沒有網路呼叫：

```powershell
rtk uv run --no-project --python 3.13 python range_assessment.py show --requirements output/requirements-review-20260927/review-11.json --facts examples/ti-1222180/candidate-facts-v1.json --assessment output/range-assessment-20260927/actual-unknown.json
```

預期 status=unknown、report_currency=current，原因是 fact_review_missing、application_conditions_missing。current 只代表相對於目前提供的本機資料仍有效，不代表已判符合或原廠網站從未更新。

一般 assess 用相同 --requirements／--facts，可選擇 --context；--out 必須是新的檔案。現有輸出禁止覆寫。全新 clone 沒有 output 下的私人／生成紀錄，請先跑教學案例或執行測試，不要偽造接受紀錄來跳過門檻。

保存的報告有輸入指紋與 rule_version。show 會用當前輸入重算，比對報告；輸入／規則變動時標 stale/unknown，不能把舊 pass 當現行結果。即使輸入檔本身沒變，底層原始案例來源改變也會偵測過期。這不是跨程序交易鎖或自動遠端文件刷新；來源文件的重新取得與人工覆核仍由人負責。

## 驗證範圍

新增12項測試，全專案176項通過；兩個隔離故障注入分別移除規格類別篩選與覆核門檻，測試都抓到錯誤，已還原。實際案件 unknown 以及两個獨立 synthetic 分支均跑過 CLI → 新檔 → 另一程序 show。這是本機軟體驗證，不是新模型品質、硬體或工程認證。驗證紀錄（本機稽核檔未公開）。
