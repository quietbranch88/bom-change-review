# 先測「需求有沒有讀對」，再比較便宜模型

目前是離線準備：10 個公開討論來源、60 個待擷取欄位、兩個候選模型各重複三次的 **60 次未執行計畫**。沒有呼叫 OpenRouter、讀取 API key 或產生模型評比結果。

這不是替料決策系統。第一個問題是：模型能否分清楚「客戶要什麼、沒說什麼、哪些話只是別人的建議」？

## 資料怎麼走

```text
公開討論 → AI 改寫成短文與角色區塊 → inputs.json → prompt → 模型 JSON（尚未執行）
                                  │                              │
                                  └→ 參考草稿 → 人工覆核          └→ 格式檢查／參考比對
```

- [inputs.json](inputs.json)：來源網址、查閱方式、段落位置、短文與欄位契約。模型輸入只取角色區塊、欄位定義與輸出結構。
- [references.json](references.json)：AI 依來源整理的答案草稿，**尚未經人工覆核**，不能稱為工程師認證的標準答案。
- [offline_eval.py](../offline_eval.py)：建立 prompt、檢查結構、列出試驗計畫、評分已存好的回答。沒有網路呼叫功能。
- 驗證紀錄（本機稽核檔未公開）：本機測試、錯誤注入、掃描結果和未驗證邊界。

產生 prompt 的路徑不讀 references.json，測試會檢查這點。這不是檔案存取安全隔離：未來若給 agent 整個資料夾權限，仍可能讀到答案。正式試跑只應傳送序列化後的 prompt，不給模型檔案工具。

## 十題各在測什麼

以下均是依真實公開提問製作的繁中改寫題，不是原始文件逐字輸入。跨元件領域只用來測擷取，不代表比較器已支援這些元件。

| 題目／原始來源 | 要抓的錯誤 |
|---|---|
| [E01：LT8301ESS 替代需求](https://e2e.ti.com/support/power-management-group/power-management/f/power-management-forum/1222180/lm5155-replace-lt8301ess) | 網站 LM5155 標籤不是客戶的原始料號；1.5 A 沒有自動等於連續電流 |
| [E02：LM5155 缺料](https://e2e.ti.com/support/power-management-group/power-management/f/power-management-forum/1093801/lm5155-possible-alternatives-to-replace-lm5155) | 提問者候選與支援人員建議分開；歷史缺料不是今天庫存 |
| [E03：TXB0104 替代](https://e2e.ti.com/support/logic-group/logic/f/logic-forum/1464630/txb0104-q1-txb0104-replacement-material) | 訊號電平不是電源輸出；明確不改板要存 false |
| [E04：LPDDR4 相容性](https://community.nxp.com/t5/i-MX-Processors/IMX-8M-plus-quad-lpddr-compatibility/m-p/1998106) | 原記憶體、候選和處理器角色不同；die 不能偷換成 rank |
| [E05：MC68332 替代](https://community.nxp.com/t5/8-bit-Microcontrollers/MC68332-Replacement-amp-25MHz-gt-16MHz-Compatibility/m-p/2329442) | 希望免改韌體不等於可以免改；MHz 與 kHz 分開 |
| [E06：SN74LVC1G125DBV 查詢](https://e2e.ti.com/support/logic-group/logic/f/logic-forum/1282284/sn74lvc1g125-sn74lvc1g125dbv) | 不偷偷用回覆中的完整料號修正提問原文 |
| [E07：歷史生命週期](https://e2e.ti.com/support/amplifiers-group/amplifiers/f/amplifiers-forum/1276292/tlc3702-case-576614-technical-data-inquiry) | 舊資料表 Active 不是已確認的當前狀態 |
| [E08：iMX6Dual 改 DualPlus](https://community.nxp.com/t5/i-MX-Processors/Is-it-simply-possible-to-replace-an-i-MX6Dual-by-an-i/td-p/962278) | 詢問相容性不等於已驗證；詢問改板風險不等於禁止改板 |
| [E09：LM5164 pin-to-pin](https://e2e.ti.com/support/power-management-group/power-management/f/power-management-forum/988665/lm5164-q1-pin-to-pin-compatible) | 納入需求方補充的 500 mA 下限，不從電動車背景推定 AEC-Q100 |
| [E10：AP7362-33SP-13 替代](https://e2e.ti.com/support/power-management-group/power-management/f/power-management-forum/994420/alternative-to-ap7362-33sp-13) | 不能用料號的 33 猜出客戶輸出電壓要求 |

查閱日期：2026-09-25。E02、E09 原頁讀取逾時，依官方網頁的搜尋索引內容整理；其餘八題讀取原頁。沒有保存整頁快照或圖面，E03 不解讀附圖。只保留必要改寫，未保存討論者姓名、email 或私人 BOM。

## 一起看 E01

模型會看到：Q1 要替換 LT8301ESS；Q2 明列輸入 14–30 V、輸出 5 V／1.5 A／7.5 W、flyback、AEC-Q100。另有網站標籤 M1 和支援回覆 R1 作為干擾。

其中兩個欄位的參考格式如下；這只是教學片段，實際回答必須包含該題全部欄位：

```json
{
  "input_max": {"value": 30, "unit": "V", "evidence_ids": ["Q2"]},
  "continuous_current": {"value": null, "unit": null, "evidence_ids": []}
}
```

30 V 是明文資訊，程式能比對值、單位、證據 ID。電流是不是「連續值」沒說，因此保持 null；LLM 不能自行補完。人工覆核者要對照原始來源，確認改寫與參考草稿沒有丟失條件，不是只看 JSON 漂不漂亮。

## 評分規則與限制

1. **結構**：全部且僅有指定欄位、型別和單位正確；拒絕重複 JSON key、NaN、額外欄位、回覆或 metadata 證據。未知 scalar 為 null、未提及候選為 []，未知值不附假證據。
2. **值**：與參考草稿精確比對；數字 14 與 14.0 相同，true 不等於 1。料號不去尾碼、不改大小寫；陣列順序不影響結果。
3. **證據**：比較參考指定的最小證據集合，與值分開計分。合法 ID 不代表內容支持該值。

`all_fields_match=true` 只表示符合這份草稿 rubric，**不等於可安全替料，也不是 LLM 品質認證**。格式不合格的回答先回報格式錯誤，不對剩餘欄位算部分語意分數。

證據集合採精確匹配，有可能錯拒另一組合理證據。例如 E07 的生命週期查詢目前指定 Q2，而 Q1 的供應狀態詢問也可能被覆核者視為支持。實跑遇到此類差異要保留原回答、人工裁決、版本化修訂 rubric 並重評所有模型，不能只替某個模型放寬。當前分數應稱「參考匹配率」，不能直接稱正確率。

這十題已參與 prompt／評分工具開發，是 development set。AI 同時參與改寫與參考答案，可能共同簡化或漏讀來源。測試用參考答案回填只證明評分有可通過路徑，沒有測到模型能力。正式比較還需人工覆核和未參與開發的新題；不能用這批短文宣稱原始 PDF、表格、英文長文、prompt injection 或真實工程判斷能力。

## 本機操作

在專案目錄用 Python 3.11 以上執行；本次驗證版本為 3.13.14。

```powershell
python offline_eval.py check
python offline_eval.py prompt --case E01
python offline_eval.py plan
python offline_eval.py grade --case E01 --response path/to/model-response.json
python -m unittest discover -s tests -v
```

若沒有直接可用的 python，把每行的 `python` 改為 `uv run --no-project --python 3.13 python`。上面的 response 路徑是占位符，需換成你儲存的完整模型回答檔；目前沒有模型回答檔，也沒有 API runner。工具輸出到終端，不覆寫答案或建立審核紀錄。

- `check`：檢查十題參考結構，不能核實來源或模型準確度。
- `prompt`：只列模型輸入，不執行模型。
- `plan`：兩個候選 ID 為 `mistralai/ministral-3b-2512`、`google/gemini-3.1-flash-lite`，每題三次，總共 60 次。都是 not_run；成本、延遲、provider 為 null。同題 prompt hash 一致。這不代表 endpoint 或 schema 支援已驗證。
- `grade`：完整匹配 exit 0；不匹配 exit 1；檔案／JSON／資料集錯誤 exit 2。CLI 已用實際子程序驗證，不只是函式 mock。

下一步先一起覆核 E01 的來源與參考，再確認試跑預算。未來 runner 必須記錄实际 provider、模型設定、token 用量、單次費用、重試與失敗；60 次只是初步重複觀察，不足以證明穩定可靠。即使模型擷取成功，工程可用性仍在另一個審核階段。
