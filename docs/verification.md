# 驗證紀錄與限制

2026-10-04 的公開 demo 以合成覆核與應用接法、公開來源規格為輸入。這些是開發 fixture，不是實際 BOM、硬體簽核或獨立模型評測資料。

## 實際執行結果

- Windows、Python 3.13.14；從 Git index 匯出乾淨公開檔案，不使用私有 `.spec` 或既有 output。
- `python -m unittest discover -s tests -v`：287 discovered、263 passed、24 opt-in real-service tests skipped，56.968 秒。不是 287 項真實服務測試。
- 另外啟用隔離真實引擎：SQLite→Neo4j 同步恢復 4 項、Neo4j 12 項、官方 SDK→stdio MCP→Neo4j 8 項，全部通過。最新一次各 suite 為 11.764／11.777／35.496 秒。
- Neo4j Community 5.26.29，固定映像 digest，容器 2 CPU／2 GiB、heap 512 MiB／page cache 256 MiB；MCP SDK 2.2.0，協商 protocol 2026-07-28。這些是本機合成案例，不是 production。
- 真實模式公開 CLI 讀回確認：回應遺失時 ledger failed、圖資料已存在；明確重試後 synced；舊快照未變。測試另比較重試前後完整節點與關係。
- 乾淨目錄使用 `python -S system_demo.py --simulate-review --out output/fresh-demo` 成功；不用 site-packages 或金鑰。十個合成身分為 6 completed／4 busy，peak active=2，完成後 active／queue／outstanding=0。
- [防護變異腳本](../scripts/verify_demo_guards.py)的三種變異都讓原測試發生 AssertionError；各自 baseline 與還原後均通過。沒有修改預期值。
- [架構 SVG](architecture.svg)在 Chrome 實際開啟並檢查完整圖的文字、色彩邊界與箭頭；Mermaid 與程式中的節點／關係名稱對照。

程式雜湊與安全摘要見[機器可讀紀錄](verification.json)。完整原始本機報告不公開，避免洩漏操作紀錄與本機路徑。

## 有保留的失敗紀錄

第一次新容器啟動超過 420 秒，尚未跑到 suite；停止容器逾時也揭露驗證腳本未能保存失敗報告的缺陷。已用失敗測試修正「清理失敗仍保存報告」，但沒有宣稱冷啟動問題已修好。

後續明確重啟同一個專案隔離測試容器，曾發生操作未完成；也抓到 JSON null 屬性與 Neo4j `properties(n)` 不同，導致 unknown 案件錯誤標成同步失敗。已以固定 oracle 的失敗→通過測試修正投影核對，沒有放寬完整比較。另修正第一次下載沒有 output 父目錄時 CLI 失敗。

最後一次明確恢復在 2026-10-04 00:06:34–00:08:50 UTC，readiness=31.419 秒，24 項 real-service tests 和真實 CLI 都通過；測試容器已停止。停止後 exit=137、OOMKilled=false，不宣稱 graceful shutdown。這次成功不能代替新容器啟動可靠性證據，也不能抹掉前面失敗。

## 安全與依賴

Bandit 1.9.4 掃描公開 runtime modules 與驗證腳本：0 high／0 medium／5 low。五項是 subprocess import／call 提示；逐項確認固定 argv、預設 shell=False，以及恢復腳本的固定容器名稱格式，不把提示當成已證實的注入漏洞，也不宣稱「掃描零警告」。

pip-audit 2.10.1 對鎖定的 29 個套件未發現已知 advisory。detect-secrets 1.5.0 以 no-verify 掃描公開索引：15 個提示為 12 個來源／fixture／程式 SHA-256、2 個明確假測試密碼，以及 1 個掃描工具說明字串；全部逐項核對為非憑證。沒有將候選內容送去外部驗證。Docker 映像弱點掃描因 Docker Scout 登入不可用而未完成；Python 依賴掃描不替代映像掃描。

## 尚未驗證或實作

真實模型、HTTP OAuth、跨程序容量與美元預算控制均未實作；這輪沒有付費推論。合成權限不是登入或多租戶授權證據；受控工具延遲不是模型效能／正式 p95。單程序取消僅保證合作式 async 工作，不保證阻塞 SDK 或遠端請求立即停止。

JSON／SQLite 不跨檔案原子提交，syncing worker crash 不會自動重領。沒有共享資料庫遷移、正式部署、EDA 模擬、硬體實測或替料核准。
