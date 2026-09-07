# 網站原始碼

請修改這裡的原始碼，不要直接修改生成的兩份 `index.html`。

- `chart.html`：摘要卡、期間切換、雙圖與圖例。靜態數值是生成前的佔位值。
- `chart.css`：既有暖米色主題、響應式排版與圖表文字。
- `chart.js`：四條圖線、獨立刻度、雙圖同步與長按互動。`const data = []` 在生成時由 processed JSON 填入。
- `document.html`：內層文件，組合前三份來源。
- `page.html`：外層自包含文件與既有圖線開關記憶橋接。

在專案根目錄執行：

```sh
.venv/bin/python scripts/update_margin_maintenance_chart_data.py \
  --history data/processed/margin-maintenance-history.json \
  --html docs/index.html --mirror index.html
.venv/bin/python -m unittest discover -s tests
node --test tests/chart_logic.test.cjs
```

生成器先驗證候選內容再原子取代檔案；兩份 HTML 應逐位元相同。一般資料更新入口仍可使用 `--html` 生成暫存候選，不需改變既有發布流程。輸出維持單檔可攜，不會向外部 CDN 下載 JavaScript；圖表資料與計算公式不在 UI 修改時重算。

## 互動驗收

- 320、360、390、768、1280px：文件無水平溢出，摘要卡的餘額漲跌標籤不裁切，手機圖例維持 2×2、點擊高度至少 32px。
- 刻度實際顯示至少 12px，手機年／月分兩行；3m 為逐月，其餘原有頻率保留。
- 任一市場 hover／長按拖曳，同步兩市場日期、圖例與十字線；僅操作中的圖顯示浮動 tooltip，避免兩個浮層重疊。
- 2026-09-07 依使用者要求精簡：不顯示起訖日期列、日期查詢、前後日／回最新按鈕、選取狀態、圖表重複日期、展開說明或頁尾來源文字；也不保留固定日期及鍵盤逐日模式。公式與來源保留在專案文件。
- 離開 hover 或結束長按即返回最新，日期只在提示框及無障礙描述中提供。每市場四條線各自記憶；刷新回預設 1y。
- 本機實際觸控裝置仍應驗收原有長按約 0.32 秒及垂直捲動行為。
