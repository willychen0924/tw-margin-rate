# 台股上市櫃融資維持率

本專案以 FinMind／iCloud `stock_data` 的逐股融資買賣、餘額與收盤價，維護上市及櫃買市場的融資平均成本與維持率歷史，並產生 GitHub Pages 靜態網站。

正式暖機起點為 `2001-01-05`。移轉 A/B 證實 2015 起算會讓早期已發布歷史出現 0.01pp 差異，因此不可更改。

原始 archive 不放進專案。程式會依序使用 `--stock-data`、`STOCK_DATA_ROOT`，或自動尋找本專案同層的 `stock_data`。

## 日常更新

Finder 雙擊 `更新融資維持率.command`，或在終端機執行：

```bash
./更新融資維持率.command
```

只重現已發布基準：

```bash
./更新融資維持率.command --end 2026-07-30 --no-finmind-fetch
```

更新官方 TWSE 市場沿革後再計算：

```bash
./更新融資維持率.command --refresh-reference
```

發布必須明確加上 `--publish`；程式會先完成重算、歷史不變性、HTML 同步及專用測試，再允許提交與推送。推送後仍須人工開啟公開頁面核對。

完整規則見 `AGENTS.md`，公式見 `計算方式.md`，兩台 Mac 操作見 `更新說明.md`。
