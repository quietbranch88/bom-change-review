# 五分鐘 Demo

需要 Python 3.11 以上。預設路徑不使用網路、Docker、API key 或模型額度；每次用新的輸出目錄。

```sh
python system_demo.py --simulate-review --out output/demo-01
```

先看 `lifecycle.stages`：

| 階段 | 應觀察到的狀態 |
| --- | --- |
| initial | unknown 兩個缺項 |
| internal received | unknown 尚待規格覆核 |
| internal reviewed | matches_requirement 單項條件符合 |
| tied received | unknown 尚待規格覆核 |
| tied reviewed | violates_requirement 零缺項也不會通過 |
| unknown received | unknown 規格覆核與接法對應都尚未完成 |
| unknown reviewed | unknown 接法對應仍未確定 |

所有覆核／接法均為模擬，overall 仍需要工程覆核。

接著看 `sync`：

1. `initial.sync_state` 為 synced，舊版案件已讀回核對。
2. `lost_ack.sync_state` 為 failed，但 `commit_observed_after_failed_ack` 為 true。這是刻意注入的「已提交，回應遺失」。
3. `recovered.sync_state` 為 synced：重開真實 SQLite ledger，明確重試並讀回。
4. `old_snapshot_unchanged` 為 true，舊的未知與缺項並未被新版本覆蓋。

最後看 `concurrency`：10 個不同合成身分同時提交，2 個進行中、4 個等待，其他 4 個繁忙。結果包含每個任務的 queue_ms、elapsed_ms。越權身分為 denied、非法工具為 tool_not_allowed、假引用為 invalid_citations。完成後名額和等待隊列都回到零。

完整結果在 `output/demo-01/report.json`；原始 bundle 與 SQLite ledger 也在同一資料夾。既有資料夾不會被覆寫。

## 真實 Neo4j 和 MCP

這條路徑使用一次性隔離容器及合成資料。需要 Docker 與 uv；腳本只停止自己建立的容器，不刪容器或 volume。

```sh
uv sync --extra mcp --frozen
docker pull neo4j@sha256:89d577f2e49606de76441eca8cf7a0fe88e594cbaac4d2a3d86c6e59676e2b1e
uv run --frozen --extra mcp python scripts/verify_neo4j.py --run-isolated --mcp --system-demo --readiness-seconds 420
```

它依序執行真實 DB、stdio MCP、SQLite 同步恢復測試，再執行真實 Neo4j 模式的公開 CLI。版本是 Neo4j Community 5.26.29；容器限制 2 CPU／2 GiB、heap 512 MiB、page cache 256 MiB。隨機測試密碼只進子程序環境與 Docker 本機設定，不寫入公開報告。

420 秒是這條指令允許的啟動等待上限，不是啟動速度承諾。先前重資源主機曾超過預設 150 秒；最新執行結果見[驗證紀錄](verification.md)。新容器設定停用 Neo4j usage reporting，未宣稱所有網路流量都被隔離。

真實模式只替換同步的圖儲存邊界；多人延遲仍是受控 fixture，模型仍是腳本。MCP 的真實 client/server/query 證據由單獨 suite 提供。

## 測試防護是否真的有用

```sh
python -m unittest discover -s tests -v
python scripts/verify_demo_guards.py
```

第二條指令在暫存副本移除讀回核對、案件權限、隊列上限三個防護，要求原測試失敗，再還原副本並要求通過。測試與預期值不變，原始專案不被變異。
