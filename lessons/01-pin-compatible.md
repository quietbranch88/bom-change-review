# 第一課：同腳位，為什麼還不能直接換？

本課目標：讀懂一個真實差異，把「資料事實」與「設計需求」分開，再親手改參數看判定改變。

## 1. 從原廠分類開始

[TI LM5155 產品頁](https://www.ti.com/product/LM5155)把 LM51551 放在同腳位、相同功能類別，同時註明多了 hiccup 保護。這兩句需要一起讀。

| 型號 | Hiccup 過載保護 |
|---|---|
| LM5155 | Disabled |
| LM51551 | Enabled |

來源：[SNVSB75E §6 Device Comparison Table](https://www.ti.com/document-viewer/LM5155/datasheet/GUID-1164172C-0470-43FB-9472-A372BB56068B)。這是已讀文件中的事實，不是我們從型號猜出來的。

## 2. Hiccup 改變了什麼？

Hiccup 在此可理解為「遇到持續限流，暫停切換，等一段時間再重新啟動」。資料表描述 LM51551 的計時機制，也提醒 soft-start 設定與嚴苛負載暫態可能影響是否意外觸發。這表示替換時要核查原設計的启动／負載行為，不能只看腳位。

來源：[SNVSB75E §9.3.10](https://www.ti.com/document-viewer/LM5155/datasheet/GUID-4477DCCC-41CD-4FDB-963C-08389EF4A315)。本課不模擬電路，也不判斷具體產品是否會誤觸發。

可以用你熟悉的後端經驗理解：兩個 API 的路徑與 request schema 一樣，不代表 timeout／retry 行為也一樣。腳位分類與功能行為應分開核查。

## 3. 先把設計需求講清楚

| 合成設計需求 | LM51551 這項判定 | 原因 |
|---|---|---|
| 必須有內建 hiccup | pass | 文件明示具有該功能 |
| 必須沒有內建 hiccup | fail | 功能與明確要求相反 |
| 沒有人提供這項需求 | unknown | 我們知道元件功能，但不知道設計是否接受 |

第三列不是「元件規格未知」，是「需求未知」。正式報告要讓工程師知道該補哪一種資料。

## 4. 第一條 Python 規則

[lesson1.py](../lesson1.py) 中的核心很小：

```python
if requirement == "unspecified" or actual is None:
    return "unknown"
expected = requirement == "required"
return "pass" if actual == expected else "fail"
```

其中 `actual` 從有來源的 catalog 讀取；`requirement` 由設計者提供。上游已驗證 requirement 只能是 required、forbidden、unspecified。不要用 `if not actual` 判斷缺資料，因為 `False`（確定沒有）和 `None`（不知道）意思不同。

到專案目錄，先執行：

```powershell
python lesson1.py --original LM5155 --candidate LM51551
```

預期看到的局部結果：

```json
{
  "catalog_pin_for_pin": "pass",
  "design_behavior_acceptance": "unknown",
  "overall": "needs_review"
}
```

這是完整 JSON 的摘要；完整輸出也包含理由、缺漏欄位、來源與未驗證項目。

然後只改需求，觀察結果：

```powershell
python lesson1.py --candidate LM51551 --hiccup required
python lesson1.py --candidate LM51551 --hiccup forbidden
```

前者的 feature check 通過，整體仍 needs_review；後者因明確需求衝突而 blocked_for_requested_scope。程式沒有作任何硬體操作。

## 5. 做一個反例

把候選改成 LM5156H：

```powershell
python lesson1.py --original LM5155 --candidate LM5156H
```

它是原廠列出的相近產品，但 pinout 類別不同，封裝從 WSON 12-pin 變成 HTSSOP 14-pin。此課固定檢查「不改 PCB 的替換」，因此這項不通過。若改成重新設計，就是另一個評估範圍。[LM5156H 來源](https://www.ti.com/product/LM5156H)

## 6. 換你先預測，再跑

```powershell
python lesson1.py --candidate LM5155 --hiccup forbidden
python lesson1.py --candidate LM51551-Q1 --hiccup required
```

先想：第一個需求是否與文件吻合？第二個型號在我們的精確 catalog 裡有沒有？這一課不去猜未收錄後綴的規格。

答案：第一個局部 feature check 通過，整體仍需審核；第二個 unknown，要求補候選的 feature 證據。未知不是不支援，也不是已支援。

## 7. 這跟 GraphRAG 有什麼關係？

未來 RAG 負責找出分類與資料表段落，圖資料可以連到設計版本和證據，MCP 可以讓 Agent 呼叫檢查器。無論接哪個工具，這一課的判定規則與測試答案都應保持一致。

文章可以記錄這個可驗證的主張：系統分別呈現 catalog 分類、功能差異與需求缺口。至於圖檢索是否比現有查詢更好，還需要後续實驗。
