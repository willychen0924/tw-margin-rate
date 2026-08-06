# 台股上市櫃融資維持率：永久操作規則

本專案的唯一目的，是以逐股融資平均成本法估算台灣上市（TWSE）與櫃買（TPEx）市場融資維持率，維護自 2017 年起的雙圖網站，並在完整驗證後發布到 `willychen0924/tw-margin-rate` 的 GitHub Pages。

本文件適用於所有在此資料夾工作的 Codex 任務。公式與欄位細節以 `計算方式.md` 為準；給使用者的操作步驟以 `更新說明.md` 為準。

## 不可破壞的原則

1. 2026-08-03 移轉只授權刪除 `移轉紀錄.md` 所列、且已通過引用檢查的融資專用檔。舊專案其餘檔案屬價值篩選／動能流程，不得刪除、移動或覆蓋。
2. iCloud 的 `stock_data` 是獨立共用原始資料庫，不得搬進 Git、改名或隨專案刪除。
3. 不得使用或重新引入 calibrated/proxy 長歷史估算。`build_margin_maintenance_proxy_history.py` 只能作歷史稽核參考，不能產生正式結果。
4. 日常更新與歷史資料必須使用同一套逐股融資平均成本算法。不可把早期 proxy 與近期逐股值拼接。
5. 未完成資料、計算、測試、本地瀏覽器與公開頁面驗證前，不得發布。
6. `FINMIND_TOKEN`、Cookie、GitHub 憑證與其他密鑰不得寫入 iCloud 專案、Git、一般文件、測試快照、日誌或輸出檔。只可使用程序環境變數，或每台 Mac 各自的 `Path.home() / "Library/Application Support/tw-margin-rate/.env"`。
7. 不得在程式或設定中硬寫任何特定使用者的絕對家目錄。專案內路徑由 `Path(__file__)` 推導；家目錄由 `Path.home()` 推導；需要覆寫時使用 CLI 參數或非敏感環境變數。
8. 兩台 Mac 不得同時更新或發布。開始前確認另一台沒有執行抓取、計算或 Git 操作，並等待 iCloud 與 Git 同步完成。
9. 保留現行網站的配色、字體與已驗證版面。除非使用者另有指示，不做視覺重設。
10. 發現現行資料、文件與程式互相矛盾時，先停止發布，保存兩份結果並回報，不可自行挑選較順眼的版本。

## 可攜路徑規格

正式程式應以以下順序解析原始資料位置：

1. CLI `--stock-data`。
2. 非敏感環境變數 `STOCK_DATA_ROOT`。
3. 預設 `Path.home() / "Library" / "Mobile Documents" / "com~apple~CloudDocs" / "stock_data"`。

專案根目錄應由腳本位置解析，例如：

```python
PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_STOCK_DATA = (
    Path.home()
    / "Library"
    / "Mobile Documents"
    / "com~apple~CloudDocs"
    / "stock_data"
)
```

補抓快取、暫存檔與測試輸出放在專案內的可忽略目錄，或由 `tempfile` 建立；不得依賴舊專案。正式輸出只寫入本專案。

## 正式資料來源

- 共同原始資料：iCloud `stock_data`。
- 融資欄位：`raw/chips_margin`，對應 FinMind `TaiwanStockMarginPurchaseShortSale`。
- 收盤價與市場指數：`raw/prices`，對應 `TaiwanStockPrice`。
- 股票基本與市場別：`raw/stock_info`，對應 `TaiwanStockInfo`。
- `TaiwanStockPriceAdj` 可用於除權息研究或輔助檢查，但目前正式維持率成本更新使用當日未還原收盤價；不得靜默替換資料口徑。
- 若 iCloud 缺少最新交易日，可用 `FINMIND_TOKEN` 補抓相同 FinMind dataset。補抓必須逐交易日並檢查回傳筆數，避免全市場查詢上限截斷。
- 「FinMind抓取器.app」不是本專案依賴，不需安裝或執行；資料檢查與必要的 FinMind 補抓統一由 `更新融資維持率.command`／`scripts/update_margin_rate.py` 負責。

「TWSE 上市日期」與「下市公司代號」兩份市場沿革資料已納入 `data/reference/`，包含固定日期 snapshot、`.latest` 工作檔與來源 URL／SHA-256 manifest；`scripts/fetch_market_reference.py` 可由 TWSE 官方端點原子更新。更新參考資料後仍須通過市場沿革測試與完整歷史差異檢查，不能直接覆蓋回歸基準。

## 股票範圍與計算口徑

- 僅納入股票代號恰為四位數的普通股。
- 排除 `0` 開頭的 ETF 與 `91` 開頭的 TDR。
- 上市、櫃買分開彙總，不得互相混入。
- 正式暖機起點：`2001-01-05`；網站顯示起點：`2017-07-03`。
- 每次日常更新都要延續相同的逐股成本狀態。若尚未建立經驗證的增量 state，應從暖機起點完整重算。
- 正式公式、餘額為零、缺價與市場沿革處理見 `計算方式.md`。

### 暖機起點的 A/B 結論

移轉時已用相同原始資料、相同市場沿革主檔與相同程式完整重算：

- `2001-01-05`：2017-07-03 至 2026-07-30 共 2,207 個共同日期，TWSE 與 TPEx 所有歷史列均與已發布 JSON 完全一致。
- `2015-01-01`：TWSE 有 44 列、TPEx 有 30 列相差 0.01 個百分點；第一筆分別在 2017-07-03 與 2017-07-04。7/30 最新值雖相同，仍不符合歷史完全一致條件。

因此正式暖機固定為 `2001-01-05`。不得改回 2015 或其他日期，除非再次完成全歷史 A/B、保存差異並取得使用者明確同意。

## 每次更新的強制流程

1. 確認工作樹、分支與遠端；保存使用者既有變更，不覆蓋不相關檔案。
2. 確認另一台 Mac 沒有同時更新。
3. 檢查 iCloud 檔案已實際下載：必要 parquet 不得帶 `dataless`/`offline` 旗標，檔案大小須大於 0，並實際讀取 schema 或尾端資料。
4. 分別找出 `chips_margin`、`prices`、`stock_info` 的最新日期；不可只看資料夾修改時間。
5. 以台灣交易日判斷最新應有完整交易日。三種資料日期不一致時，先補齊或停止，不以部分市場資料出圖。
6. 若需 FinMind 補抓，使用環境中的 `FINMIND_TOKEN`；不得在輸出中顯示 token。確認全市場筆數合理並保存查詢日期與 dataset 記錄。
7. 以逐股算法完整重算，或使用已通過「與完整重算逐日一致」測試的增量續算。上市與櫃買同時產生。
8. 先寫候選檔或暫存檔並驗證，通過後才原子式取代 `data/processed/margin-maintenance-history.json`。失敗不得留下半成品。
9. 由同一份 processed JSON 更新 `docs/index.html` 的嵌入歷史、摘要卡、圖例最新值、資料日期與隱藏的無障礙日期，並將專案根目錄 `index.html` 更新為完全相同的 Pages 相容鏡像。兩份 HTML 必須逐位元一致，不得手動各自修改。
10. 執行全部自動測試與下列資料驗證。
11. 啟動本地網站，用瀏覽器檢查桌面與窄螢幕、線性與對數模式、提示框、上下兩圖與頁尾。
12. 對照前一個已發布版本；在上一發布日以前的逐日值必須完全一致。若不一致，保存 diff 並停止發布。
13. 只有全部通過才可提交、推送並發布 GitHub Pages。
14. 開啟公開網址（加 cache-busting query），再次核對最新日期、四個最新數字、互動與版面。回報 commit、公開網址及驗證摘要。

## 自動驗證最低要求

正式移轉需補上融資專用測試；舊專案既有的 16 項測試屬價值篩選器，不能單獨證明本專案正確。最低應覆蓋：

- 平均成本更新的買進、賣出、現償、清倉、重新建倉與零餘額。
- 股票篩選：四位數普通股、ETF、TDR 與非四位代號。
- 上市／櫃買市場沿革與轉板個案。
- 缺收盤價沿用規則，且不得跨股票或產生無限值。
- 市場公式的固定小型 fixture，可手算驗證。
- 日期遞增、無重複、兩市場共同日期一致、必要欄位型別與有限值。
- 完整重算與增量續算逐日、逐市場完全一致。
- JSON 與 `docs/index.html`、根目錄 `index.html` 內嵌資料完全一致；兩份 HTML 逐位元一致；摘要卡、圖例與日期取自同一最新列。
- 更新失敗不覆蓋上一個有效產物。
- 固定回歸基準：2026-07-30 上市 `132.50`、加權指數 `39933.30`；櫃買 `118.32`、櫃買指數 `326.23`。網站一位小數分別顯示 `132.5%`、`118.3%`。

2026-08-04 已查證並經使用者核准更正 1459 聯發與 1589 永冠-KY 的歷史市場歸屬：兩者均為 TWSE 上市股，不得因減資換股或停止買賣而在 `TaiwanStockInfo` 暫時消失時改歸櫃買。此更正取代移轉時的舊基準 `132.49` / `118.34`；不可在沒有官方市場沿革證據時再次重錄基準。

任何回歸基準的更新都要附理由與舊、新差異，不能直接重錄 snapshot。

## 網站不可回歸規格

- 頁面標題：「台股上市櫃融資維持率」。
- 上方上市、下方櫃買。
- 顯示 130% 警戒線；線上不得出現貼在線旁的 130% 小標。
- 保留線性／對數切換與中文日期。
- 提示框沿圖表下緣左右移動並縮小，不改變圖表欄位高度。
- 左右座標標題直排，不出現多餘小標籤。
- 頁面底部及圓角不得漏白。
- 配色與字體維持現行已發布版本。
- 更新後摘要卡、圖例、資料日期與嵌入歷史必須同步。

## 建議的移轉結構與順序

```text
.
├── AGENTS.md
├── 計算方式.md
├── 更新說明.md
├── pyproject.toml
├── .env.example                 # 只列變數名，不放 token
├── scripts/
│   ├── build_margin_maintenance_history.py
│   ├── update_margin_maintenance_chart_data.py
│   ├── validate_margin_outputs.py
│   └── update_margin_rate.py     # 單一安全入口，先驗證再取代
├── src/tw_margin_rate/
│   ├── finmind.py
│   ├── paths.py
│   └── calculation.py
├── tests/
│   ├── fixtures/
│   └── test_margin_*.py
├── data/processed/
│   └── margin-maintenance-history.json
├── docs/index.html
└── index.html                    # 與 docs/index.html 完全相同的 Pages 相容鏡像
```

移轉順序：先複製最小必要程式與基準產物；再拆出路徑、計算與驗證模組；補齊市場沿革資料取得方式及融資專用測試；用唯讀 iCloud 資料做完整重算；比對基準；瀏覽器驗證；最後才設定遠端與發布。不要把整個價值篩選器專案原樣搬入。

## 發布界線

- 公開站：`https://willychen0924.github.io/tw-margin-rate/`。
- 公開儲存庫：`willychen0924/tw-margin-rate`。
- iCloud 正式專案的 `origin` 只可為 `https://github.com/willychen0924/tw-margin-rate.git` 或 `git@github.com:willychen0924/tw-margin-rate.git`；若不符即停止發布。SSH 金鑰只可放在各台 Mac 的 `~/.ssh`，不得放入 iCloud 或 Git。
- 任何 GitHub 登入、Pages 設定或遠端變更都須在正確儲存庫確認後進行。
- 發布後仍需以公開網址核對；推送成功不等於頁面已正確部署。
