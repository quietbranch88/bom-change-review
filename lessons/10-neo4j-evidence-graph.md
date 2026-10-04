# 第十課：把已知的證據與缺口連成 Neo4j 圖

2026-09-28：第一個本機匯入／查詢切片已在 Neo4j Community 5.26.29 實測。沒有呼叫模型、沒有部署雲端、沒有判定元件可以替換。

## 1. 這次真正新增的是什麼？

以前 JSON 能告訴你一份案件裡有什麼。現在把案件、候選元件、規格及文件連起來，可以沿關係查詢來源，也能反向找出某份文件可能影響的案件。

既有實際案例投影包含 10 個節點、13 條關係（不是 10 個元件）：案件快照 1、需求 1、原元件與候選 2、規格 3、文件版本 1、缺口 2。程式沒有為了達到數量而捏造硬體元件。

```text
E01 案件快照
  ├─ ABOUT_ORIGINAL → LT8301ESS（未替它猜製造商）
  ├─ HAS_REQUIREMENT → 板端輸入需求 14–30 V
  ├─ CONSIDERS → LM5155
  │               └─ HAS_SPEC → 三筆分開的條件規格
  │                                └─ EXTRACTED_FROM → SNVSB75E / Rev E / PDF hash
  └─ HAS_GAP → 候選規格待覆核、實際使用條件待提供
                  └─ BLOCKS → 這項輸入需求的檢查
```

這是可追溯的資料關係，不是「LT8301ESS 可以換成 LM5155」的結論。

## 2. JSON 到圖，誰做什麼？

1. 既有 local_review 重播人工決定，取得已接受的原料號與 14–30 V 需求。
2. 既有 range_assessment 重算保存的結果，確認來源未變、仍為 unknown。
3. [neo4j_graph.py](../neo4j_graph.py) 用確定性的 Python 映射欄位與 ID，不再呼叫 LLM。
4. 同一條參數化 Cypher 以一個交易寫入所有節點與關係。
5. 另一個程序重新查詢，核對三筆規格、兩個缺口與 pending 狀態。

第一版只支援既有的「規格 pending、未提供應用 context」情境。已覆核候選、其他廠牌來源及未知條件格式不會被猜測處理，會拒絕匯入。JSON 是保留的來源；圖是歷史投影，不自動刷新遠端文件。

## 3. 實際查回什麼？

| 規格 ID | 保存的範圍 | 保存的分類／條件 | 狀態 |
| --- | --- | --- | --- |
| F-BIAS-INTERNAL | 3.5–45 V | recommended_operating；internal_vcc_regulator；Tj −40 至 125°C | pending |
| F-BIAS-TIED | 2.97–16 V | recommended_operating；VCC 直接接 BIAS；相同 Tj 範圍 | pending |
| F-BIAS-ABS | −0.3–50 V | absolute_maximum；BIAS 相對 AGND | pending |

這是對已保存規格草稿的匯入／讀回驗證，不是本課重新覆核 datasheet，亦沒有把絕對最大值當正常運作規格。案件仍有 fact_review_missing 與 application_conditions_missing 兩個缺口，其他工程項目仍未評估。

實際案例檔案保留在 output/bom-neo4j-test-6b3ea866a351/：actual-import.json、actual-readback.json、verification.json。前兩份內容經程序核對相同；輸入 JSON 的既有雜湊未變。output 是本機忽略目錄，不是已上傳 GitHub 的證據。

## 4. 為什麼規格要綁版本？

同一元件日後會多出新擷取紀錄。如果只用「案件 → 元件 → 所有規格」查詢，舊案件會混入後來的規格。

所以 CONSIDERS 關係保存當時的 facts_sha256，查詢只取該批規格：

```cypher
MATCH (c:CaseSnapshot {id: $snapshot_id})-[cc:CONSIDERS]->(p:Component)
MATCH (p)-[:HAS_SPEC]->(s:Specification)
WHERE s.facts_sha256 = cc.facts_sha256
RETURN p.model, s.fact_id, s.min, s.max, s.review_status
ORDER BY s.id
```

原則：能沿路找到資料，不代表沿路找到的每一份資料都適用於這個案件。

## 5. 重跑方式與成本邊界

在專案根目錄，不啟動 DB 也能先看映射（預設使用本機已保存的案例，其他 checkout 需自行提供三個 --requirements／--facts／--assessment 路徑）：

```powershell
rtk uv run --no-project --python 3.13 python neo4j_graph.py plan
```

Docker Desktop 已啟動、18747 埠可用且固定映像已存在後，重跑真實測試與保存案例示範：

```powershell
rtk uv run --no-project --python 3.13 python scripts/verify_neo4j.py --run-isolated --demo
```

映像固定為 neo4j@sha256:89d577f2e49606de76441eca8cf7a0fe88e594cbaac4d2a3d86c6e59676e2b1e；沒有映像時需先下載 neo4j:5.26.29-community 並核對 digest。不使用浮動 latest。

驗證腳本每次建立新的隔離容器，綁定 127.0.0.1:18747、上限 2 GiB RAM／2 CPU、heap 512m、page cache 256m。這些是上限／設定，不是測得的最低需求或效能保證。Basic auth 使用每次隨機產生的專用密碼；不存入報告、不修改 OpenRouter 金鑰。Docker 管理者仍能從本機容器設定取得該測試密碼，這不是秘密保管庫。

完成後只停止該次測試容器，不停止 Docker Desktop、不刪除容器或 volume。測試密碼不提供為長期服務設定；日常教學重跑會建立新實例，累積資源需另外決定清理。本輪沒有留下執行中的 Neo4j 服務或瀏覽器 UI。

這輪沒有模型 API／雲端帳單；仍會使用本機磁碟、記憶體與下載流量。Docker Scout 映像弱點掃描因要求登入而未完成，不宣稱映像安全掃描通過。

## 6. 怎樣算驗證過？

- 13 項 mapper／transport／離線 CLI 測試通過；transport mock 只證明 HTTP 處理。
- 7 項真正 Neo4j 測試通過：獨立 CLI 讀回、重匯不增資料、中途錯誤整筆回滾、唯一約束、新舊版本隔離、字串注入保持資料、錯誤密碼拒絕。
- 全專案預設測試：196 discovered，189 passed，7 個 live tests 按設計 skipped；它們另外在新容器中實際執行，不把 skip 計成通過。
- 還不代表：Neo4j 高可用、效能、多租戶權限、工程適用性、GraphRAG 或生產環境完成。

設計與驗證詳見 ADR-001（本機稽核檔未公開） 及 audit（本機稽核檔未公開）。
