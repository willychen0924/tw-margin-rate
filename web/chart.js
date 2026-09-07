(() => {
  const root = document.getElementById('margin-maintenance-history');
  const data = [];
  const NS = 'http://www.w3.org/2000/svg';
  const bounds = { left: 64, right: 672, top: 18, bottom: 246 };
  let chartHeight = 300;
  let range = '1y';
  let scaleMode = 'linear';
  const configFor = market => market === 'twse'
    ? { maint: 'tw', index: 'ti', balance: 'tb', ratio: 'tr', maintLabel: '融資維持率', indexLabel: '加權指數', ratioStart: '2017-07-03' }
    : { maint: 'ot', index: 'oi', balance: 'ob', ratio: 'or', maintLabel: '融資維持率', indexLabel: '櫃買指數', ratioStart: '2017-07-03' };
  const charts = Array.from(root.querySelectorAll('[data-chart]')).map(section => {
    const query = role => section.querySelector(`[data-role="${role}"]`);
    const market = section.dataset.chart;
    return {
      section,
      market,
      config: configFor(market),
      svg: query('svg'),
      grid: query('grid'),
      axes: query('axes'),
      riskZone: query('risk-zone'),
      alertLine: query('alert-line'),
      alertLabel: query('alert-label'),
      maintPath: query('maint-path'),
      indexPath: query('index-path'),
      balancePath: query('balance-path'),
      ratioPath: query('ratio-path'),
      maintDot: query('maint-dot'),
      indexDot: query('index-dot'),
      balanceDot: query('balance-dot'),
      ratioDot: query('ratio-dot'),
      crosshair: query('crosshair'),
      hit: query('hit'),
      plot: query('plot'),
      tooltip: query('tooltip'),
      dateValue: query('date'),
      maintValue: query('maint-value'),
      indexValue: query('index-value'),
      balanceValue: query('balance-value'),
      ratioValue: query('ratio-value'),
      desc: section.querySelector('desc'),
      seriesVisibility: { maint: true, index: true, balance: true, ratio: true },
      shown: null,
    };
  });
  const isNarrow = () => root.getBoundingClientRect().width <= 700;
  function updateChartGeometry() {
    const narrow = isNarrow();
    bounds.left = narrow ? 64 : 39;
    bounds.right = narrow ? 672 : 712;
    bounds.bottom = narrow ? 366 : 246;
    chartHeight = narrow ? 500 : 290;
    charts.forEach(chart => {
      if (chart.tooltip.parentElement !== root) root.appendChild(chart.tooltip);
      chart.svg.setAttribute('viewBox', `0 0 760 ${chartHeight}`);
      // SVG text scales with the viewBox: keep an actual 12px rendered minimum.
      const renderedWidth = chart.svg.getBoundingClientRect().width;
      chart.svg.style.setProperty('--mmc-axis-font', `${Math.max(9, 12 * 760 / Math.max(1, renderedWidth))}px`);
      chart.hit.setAttribute('x', bounds.left);
      chart.hit.setAttribute('width', bounds.right - bounds.left);
      chart.hit.setAttribute('y', bounds.top);
      chart.hit.setAttribute('height', bounds.bottom - bounds.top);
    });
  }
  const parseDate = value => new Date(`${value}T00:00:00`);
  const displayDate = value => {
    const date = parseDate(value);
    const weekday = ['日', '一', '二', '三', '四', '五', '六'][date.getDay()];
    return `${date.getFullYear()}/${date.getMonth() + 1}/${date.getDate()}（${weekday}）`;
  };
  const pad2 = value => String(value).padStart(2, '0');
  const formatYearMonth = date => `${date.getFullYear()}/${pad2(date.getMonth() + 1)}`;
  const firstIndexOnOrAfter = (rows, date) => {
    const target = `${date.getFullYear()}-${pad2(date.getMonth() + 1)}-01`;
    let lo = 0, hi = rows.length;
    while (lo < hi) {
      const mid = Math.floor((lo + hi) / 2);
      if (rows[mid].d < target) lo = mid + 1;
      else hi = mid;
    }
    return Math.min(lo, rows.length - 1);
  };
  const pushTick = (ticks, idx, label) => {
    if (idx < 0 || ticks.some(tick => tick.idx === idx)) return;
    ticks.push({ idx, label });
  };
  const fixedMonthTicks = (rows, months) => {
    const first = parseDate(rows[0].d), last = parseDate(rows.at(-1).d);
    const ticks = [];
    for (let year = first.getFullYear(); year <= last.getFullYear(); year++) {
      for (const month of months) {
        const date = new Date(year, month, 1);
        const nextMonth = new Date(year, month + 1, 1);
        if (nextMonth <= first || date > last) continue;
        pushTick(ticks, firstIndexOnOrAfter(rows, date), formatYearMonth(date));
      }
    }
    return ticks;
  };
  const fixedYearTicks = (rows, yearStep) => {
    const first = parseDate(rows[0].d), last = parseDate(rows.at(-1).d);
    const ticks = [];
    for (let year = first.getFullYear(); year <= last.getFullYear(); year++) {
      if (year % yearStep !== 0) continue;
      const date = new Date(year, 0, 1);
      const startsInJanuary = year === first.getFullYear() && first.getMonth() === 0;
      if ((date < first && !startsInJanuary) || date > last) continue;
      pushTick(ticks, firstIndexOnOrAfter(rows, date), String(year));
    }
    return ticks;
  };
  const axisTicks = rows => {
    if (rows.length <= 1) return [{ idx: 0, label: rows[0]?.d ?? '' }];
    if (range === 'all') return fixedYearTicks(rows, 2);
    if (range === '5y') return fixedYearTicks(rows, 1);
    if (range === '3y' || range === '2y') return fixedMonthTicks(rows, [0, 6]);
    if (range === '3m') return fixedMonthTicks(rows, Array.from({ length: 12 }, (_, month) => month));
    return fixedMonthTicks(rows, [0, 3, 6, 9]);
  };
  const yearGuideIndexes = rows => {
    if (range === 'all' || rows.length <= 1) return [];
    const first = parseDate(rows[0].d), last = parseDate(rows.at(-1).d);
    const indexes = [];
    for (let year = first.getFullYear() + 1; year <= last.getFullYear(); year++) {
      const date = new Date(year, 0, 1);
      if (date > last) break;
      const idx = firstIndexOnOrAfter(rows, date);
      if (idx > 0 && idx < rows.length - 1) indexes.push(idx);
    }
    return indexes;
  };
  const subset = () => {
    if (range === 'all') return data;
    const cutoff = parseDate(data.at(-1).d);
    if (range === 'ytd') {
      cutoff.setMonth(0, 1);
    } else if (range.endsWith('m')) {
      cutoff.setMonth(cutoff.getMonth() - Number(range.slice(0, -1)));
    } else {
      cutoff.setFullYear(cutoff.getFullYear() - Number(range[0]));
    }
    return data.filter(row => parseDate(row.d) >= cutoff);
  };
  const niceBounds = (values, step, floorValue) => {
    const lo = Math.min(...values, floorValue);
    const hi = Math.max(...values);
    return [Math.floor((lo - step * 0.35) / step) * step, Math.ceil((hi + step * 0.35) / step) * step];
  };
  const xFor = (i, n) => bounds.left + (n <= 1 ? 0 : i / (n - 1)) * (bounds.right - bounds.left);
  const yFor = (v, domain) => bounds.bottom - (v - domain[0]) / (domain[1] - domain[0]) * (bounds.bottom - bounds.top);
  const indexFor = value => scaleMode === 'log' ? Math.log10(value) : value;
  const pathFor = (values, domain) => values.map((v, i) => `${i ? 'L' : 'M'}${xFor(i, values.length).toFixed(1)},${yFor(v, domain).toFixed(1)}`).join(' ');
  const nullablePathFor = (values, domain) => {
    let drawing = false;
    return values.map((value, i) => {
      if (!Number.isFinite(value)) { drawing = false; return ''; }
      const command = drawing ? 'L' : 'M';
      drawing = true;
      return `${command}${xFor(i, values.length).toFixed(1)},${yFor(value, domain).toFixed(1)}`;
    }).join(' ');
  };
  const fmtIndex = (market, value) => market === 'twse' ? Math.round(value).toLocaleString('zh-TW') : Number(value).toFixed(1);
  const fmtAxisIndex = (market, value) => {
    if (!isNarrow() || Math.abs(value) < 1000) return fmtIndex(market, value);
    return `${Number((value / 1000).toFixed(1))}k`;
  };
  const fmtMaintenance = value => (Math.round((value + Number.EPSILON) * 10) / 10).toFixed(1);
  const balanceFor = value => Number(value);
  const fmtBalanceAmount = value => Number(value).toLocaleString('zh-TW', { minimumFractionDigits: 1, maximumFractionDigits: 1, useGrouping: false });
  const fmtBalance = value => fmtBalanceAmount(balanceFor(value));
  const fmtRatio = value => Number(value).toLocaleString('zh-TW', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  const stepFor = values => {
    const span = Math.max(...values) - Math.min(...values);
    const rough = Math.max(span / 4, 1);
    const power = 10 ** Math.floor(Math.log10(rough));
    const unit = rough / power;
    return (unit <= 1 ? 1 : unit <= 2 ? 2 : unit <= 5 ? 5 : 10) * power;
  };
  const node = (name, attrs, text) => {
    const el = document.createElementNS(NS, name);
    Object.entries(attrs).forEach(([k, v]) => el.setAttribute(k, v));
    if (text != null) el.textContent = text;
    return el;
  };
  const resample = (values, n) => {
    if (!values || !values.length) return Array(n).fill(0);
    if (values.length === n) return values.slice();
    return Array.from({ length: n }, (_, i) => {
      const p = n === 1 ? 0 : i * (values.length - 1) / (n - 1);
      const a = Math.floor(p), b = Math.min(values.length - 1, Math.ceil(p)), t = p - a;
      return values[a] * (1 - t) + values[b] * t;
    });
  };

  function drawAxes(chart, rows, maintDomain, indexDomain, balanceDomain) {
    chart.grid.replaceChildren();
    chart.axes.replaceChildren();
    const ticks = 4;
    const narrow = isNarrow();
    const leftAxisX = narrow ? bounds.left - 9 : bounds.left - 25;
    const leftAxisAnchor = narrow ? 'end' : 'start';
    const rightAxisX = bounds.right + 9;
    for (let i = 0; i <= ticks; i++) {
      const y = bounds.top + i / ticks * (bounds.bottom - bounds.top);
      const mv = maintDomain[1] - i / ticks * (maintDomain[1] - maintDomain[0]);
      const indexTick = indexDomain[1] - i / ticks * (indexDomain[1] - indexDomain[0]);
      const iv = scaleMode === 'log' ? 10 ** indexTick : indexTick;
      chart.grid.appendChild(node('line', { x1: bounds.left, x2: bounds.right, y1: y, y2: y, class: 'mmc-grid-line' }));
      chart.axes.appendChild(node('text', { x: leftAxisX, y: y + 4, 'text-anchor': leftAxisAnchor, class: 'mmc-axis-label' }, Math.round(mv)));
      chart.axes.appendChild(node('text', { x: rightAxisX, y: y + 4, 'text-anchor': 'start', class: 'mmc-axis-label' }, fmtAxisIndex(chart.market, iv)));
    }
    yearGuideIndexes(rows).forEach(idx => {
      const x = xFor(idx, rows.length);
      chart.grid.appendChild(node('line', { x1: x, x2: x, y1: bounds.top, y2: bounds.bottom, class: 'mmc-year-line' }));
    });
    axisTicks(rows).forEach(tick => {
      let x = xFor(tick.idx, rows.length);
      const anchor = tick.idx === 0 ? 'start' : tick.idx === rows.length - 1 ? 'end' : 'middle';
      if (!narrow) {
        // Keep larger date labels clear of the horizontal axis titles.
        const fontSize = parseFloat(chart.svg.style.getPropertyValue('--mmc-axis-font'));
        const labelWidth = tick.label.length * fontSize * 0.64;
        const before = anchor === 'start' ? 0 : anchor === 'end' ? labelWidth : labelWidth / 2;
        const after = labelWidth - before;
        x = Math.max(leftAxisX + fontSize * 4 + 8 + before, Math.min(rightAxisX - 8 - after, x));
      }
      const label = node('text', { x, y: bounds.bottom + 24, 'text-anchor': anchor, class: 'mmc-axis-label mmc-x-axis-label' });
      if (narrow && tick.label.includes('/')) {
        // Two short rows retain year/month context without colliding on phones.
        const [year, month] = tick.label.split('/');
        label.appendChild(node('tspan', { x, dy: 0 }, year));
        label.appendChild(node('tspan', { x, dy: '1.15em' }, month));
        label.setAttribute('aria-label', tick.label);
      } else {
        label.textContent = tick.label;
      }
      chart.axes.appendChild(label);
    });
    const axisTitleY = bounds.bottom + 24;
    chart.axes.appendChild(node('text', { x: leftAxisX, y: axisTitleY, 'text-anchor': 'start', class: 'mmc-axis-title mmc-axis-title-maint' }, '維持率'));
    chart.axes.appendChild(node('text', { x: rightAxisX, y: axisTitleY, 'text-anchor': 'start', class: 'mmc-axis-title mmc-axis-title-index' }, '指數'));
    const alertY = yFor(130, maintDomain);
    chart.riskZone.setAttribute('x', bounds.left); chart.riskZone.setAttribute('y', Math.max(bounds.top, alertY));
    chart.riskZone.setAttribute('width', bounds.right - bounds.left); chart.riskZone.setAttribute('height', Math.max(0, bounds.bottom - alertY));
    chart.alertLine.setAttribute('x1', bounds.left); chart.alertLine.setAttribute('x2', bounds.right); chart.alertLine.setAttribute('y1', alertY); chart.alertLine.setAttribute('y2', alertY);
    chart.alertLabel.setAttribute('x', bounds.left + 5); chart.alertLabel.setAttribute('y', alertY - 6);
  }

  function updateDetail(chart, row) {
    chart.dateValue.textContent = row.d.replaceAll('-', '/');
    chart.maintValue.textContent = `${fmtMaintenance(row[chart.config.maint])}%`;
    chart.indexValue.textContent = fmtIndex(chart.market, row[chart.config.index]);
    chart.balanceValue.textContent = fmtBalance(row[chart.config.balance]);
    chart.ratioValue.textContent = Number.isFinite(row[chart.config.ratio]) ? `${fmtRatio(row[chart.config.ratio])}%` : '—';
  }

  function renderChart(chart, animate = true) {
    if (chart.animationFrame) cancelAnimationFrame(chart.animationFrame);
    const rows = subset();
    const keys = chart.config;
    const maint = rows.map(d => d[keys.maint]);
    const index = rows.map(d => d[keys.index]);
    const balance = rows.map(d => balanceFor(d[keys.balance]));
    const ratio = rows.map(d => d[keys.ratio] == null ? Number.NaN : Number(d[keys.ratio]));
    const finiteRatio = ratio.filter(Number.isFinite);
    const indexPlot = index.map(indexFor);
    const maintDomain = niceBounds(maint, 10, 130);
    const indexStep = scaleMode === 'log' ? 0.1 : chart.market === 'twse' ? 5000 : 25;
    const indexDomain = niceBounds(indexPlot, indexStep, Math.min(...indexPlot));
    const balanceStep = stepFor(balance);
    const balanceDomain = niceBounds(balance, balanceStep, Math.min(...balance));
    const ratioDomain = finiteRatio.length
      ? niceBounds(finiteRatio, 0.05, Math.min(...finiteRatio))
      : [0, 1];
    drawAxes(chart, rows, maintDomain, indexDomain, balanceDomain);
    const ratioStart = rows.find(row => Number.isFinite(row[keys.ratio]))?.d ?? keys.ratioStart;
    chart.desc.textContent = `${keys.maintLabel}、${keys.indexLabel}與融資餘額，${rows[0].d}至${rows.at(-1).d}；融資市值比自${ratioStart}起。`;
    const target = { maint, index, balance, ratio, maintDomain, indexDomain, balanceDomain, ratioDomain, rows };
    const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    if (!animate || reduced || !chart.shown) {
      chart.maintPath.setAttribute('d', pathFor(maint, maintDomain));
      chart.indexPath.setAttribute('d', pathFor(indexPlot, indexDomain));
      chart.balancePath.setAttribute('d', pathFor(balance, balanceDomain));
      chart.ratioPath.setAttribute('d', nullablePathFor(ratio, ratioDomain));
    } else {
      const oldMaint = resample(chart.shown.maint, maint.length);
      const oldIndex = resample(chart.shown.index, index.length);
      const oldBalance = resample(chart.shown.balance, balance.length);
      const start = performance.now();
      const duration = 260;
      const frame = now => {
        const t = Math.min(1, (now - start) / duration);
        const ease = 1 - Math.pow(1 - t, 3);
        const m = maint.map((v, i) => oldMaint[i] + (v - oldMaint[i]) * ease);
        const ix = index.map((v, i) => indexFor(oldIndex[i] + (v - oldIndex[i]) * ease));
        const b = balance.map((v, i) => oldBalance[i] + (v - oldBalance[i]) * ease);
        chart.maintPath.setAttribute('d', pathFor(m, maintDomain));
        chart.indexPath.setAttribute('d', pathFor(ix, indexDomain));
        chart.balancePath.setAttribute('d', pathFor(b, balanceDomain));
        if (t < 1) chart.animationFrame = requestAnimationFrame(frame);
      };
      chart.animationFrame = requestAnimationFrame(frame);
      chart.ratioPath.setAttribute('d', nullablePathFor(ratio, ratioDomain));
    }
    chart.shown = target;
    const last = rows.at(-1);
    const lx = xFor(rows.length - 1, rows.length);
    chart.maintDot.setAttribute('cx', lx); chart.maintDot.setAttribute('cy', yFor(last[keys.maint], maintDomain));
    chart.indexDot.setAttribute('cx', lx); chart.indexDot.setAttribute('cy', yFor(indexFor(last[keys.index]), indexDomain));
    chart.balanceDot.setAttribute('cx', lx); chart.balanceDot.setAttribute('cy', yFor(balanceFor(last[keys.balance]), balanceDomain));
    if (Number.isFinite(last[keys.ratio])) {
      chart.ratioDot.setAttribute('cx', lx); chart.ratioDot.setAttribute('cy', yFor(last[keys.ratio], ratioDomain));
    }
    chart.maintPath.style.display = chart.seriesVisibility.maint ? '' : 'none';
    chart.maintDot.style.display = chart.seriesVisibility.maint ? '' : 'none';
    chart.indexPath.style.display = chart.seriesVisibility.index ? '' : 'none';
    chart.indexDot.style.display = chart.seriesVisibility.index ? '' : 'none';
    chart.balancePath.style.display = chart.seriesVisibility.balance ? '' : 'none';
    chart.balanceDot.style.display = chart.seriesVisibility.balance ? '' : 'none';
    chart.ratioPath.style.display = chart.seriesVisibility.ratio && finiteRatio.length ? '' : 'none';
    chart.ratioDot.style.display = chart.seriesVisibility.ratio && Number.isFinite(last[keys.ratio]) ? '' : 'none';
    updateDetail(chart, last);
    chart.tooltip.style.visibility = 'hidden'; chart.crosshair.style.opacity = 0;
  }

  const renderAll = (animate = true) => {
    charts.forEach(chart => renderChart(chart, animate));
    resetInspection();
  };

  function applyInspectionRow(chart, row, selected) {
    const idx = chart.shown.rows.findIndex(candidate => candidate.d === row.d);
    const keys = chart.config, x = xFor(idx, chart.shown.rows.length);
    chart.crosshair.setAttribute('x1', x); chart.crosshair.setAttribute('x2', x);
    chart.crosshair.setAttribute('y1', bounds.top); chart.crosshair.setAttribute('y2', bounds.bottom);
    chart.crosshair.style.opacity = selected ? 1 : 0;
    for (const name of ['maint', 'index', 'balance', 'ratio']) {
      const value = row[keys[name]];
      const dot = chart[`${name}Dot`];
      dot.style.display = chart.seriesVisibility[name] && Number.isFinite(value) ? '' : 'none';
      if (!Number.isFinite(value)) continue;
      dot.setAttribute('cx', x);
      dot.setAttribute('cy', yFor(name === 'index' ? indexFor(value) : value, chart.shown[`${name}Domain`]));
    }
    updateDetail(chart, row);
    chart.tooltip.style.visibility = 'hidden';
    chart.tooltip.setAttribute('aria-hidden', 'true');
  }

  function inspectRow(row) {
    charts.forEach(chart => applyInspectionRow(chart, row, true));
  }

  function resetInspection() {
    charts.forEach(chart => applyInspectionRow(chart, chart.shown.rows.at(-1), false));
  }

  const preferencesMessageType = 'tw-margin-rate:series-visibility';
  const seriesNames = ['maint', 'index', 'balance', 'ratio'];
  const currentSeriesPreferences = () => Object.fromEntries(charts.map(chart => [
    chart.market,
    Object.fromEntries(seriesNames.map(series => [series, chart.seriesVisibility[series]])),
  ]));
  const saveSeriesPreferences = () => parent.postMessage({
    type: preferencesMessageType,
    action: 'set',
    value: currentSeriesPreferences(),
  }, '*');
  const applySeriesPreferences = value => {
    if (value == null || typeof value !== 'object') return;
    charts.forEach(chart => {
      const stored = value[chart.market];
      if (stored == null || typeof stored !== 'object') return;
      seriesNames.forEach(series => {
        if (typeof stored[series] !== 'boolean') return;
        chart.seriesVisibility[series] = stored[series];
        chart.section.querySelector(`[data-series="${series}"]`)?.setAttribute(
          'aria-pressed',
          String(stored[series]),
        );
      });
    });
    renderAll(false);
  };
  window.addEventListener('message', event => {
    if (event.source !== parent || event.data?.type !== preferencesMessageType) return;
    if (event.data.action === 'state') applySeriesPreferences(event.data.value);
  });

  function activate(group, value, attr) {
    root.querySelectorAll(`[${attr}]`).forEach(btn => {
      const active = btn.getAttribute(attr) === value;
      btn.setAttribute('aria-pressed', String(active));
      btn.classList.toggle('btn-primary', active);
    });
  }
  root.querySelectorAll('[data-range]').forEach(btn => btn.addEventListener('click', () => {
    range = btn.dataset.range; activate('range', range, 'data-range'); renderAll(true);
  }));
  charts.forEach(chart => {
    chart.section.querySelectorAll('[data-series]').forEach(btn => btn.addEventListener('click', () => {
      const series = btn.dataset.series;
      chart.seriesVisibility[series] = !chart.seriesVisibility[series];
      btn.setAttribute('aria-pressed', String(chart.seriesVisibility[series]));
      renderChart(chart, true);
      resetInspection();
      saveSeriesPreferences();
    }));
    const showInteraction = clientX => {
      if (!chart.shown) return;
      const rect = chart.svg.getBoundingClientRect();
      const px = (clientX - rect.left) / rect.width * 760;
      const idx = Math.max(0, Math.min(chart.shown.rows.length - 1, Math.round((px - bounds.left) / (bounds.right - bounds.left) * (chart.shown.rows.length - 1))));
      const row = chart.shown.rows[idx], keys = chart.config, x = xFor(idx, chart.shown.rows.length);
      inspectRow(row);
      const tooltipRows = [`<strong>${displayDate(row.d)}</strong>`];
      if (chart.seriesVisibility.maint) tooltipRows.push(`${keys.maintLabel}：${fmtMaintenance(row[keys.maint])}%`);
      if (chart.seriesVisibility.index) tooltipRows.push(`${keys.indexLabel}：${fmtIndex(chart.market, row[keys.index])}`);
      if (chart.seriesVisibility.balance) tooltipRows.push(`融資餘額：${fmtBalance(row[keys.balance])}億`);
      if (chart.seriesVisibility.ratio && Number.isFinite(row[keys.ratio])) tooltipRows.push(`融資市值比：${fmtRatio(row[keys.ratio])}%`);
      chart.tooltip.innerHTML = tooltipRows.join('<br>');
      chart.tooltip.setAttribute('aria-hidden', 'false'); chart.tooltip.style.visibility = 'visible';
      const plotRect = chart.plot.getBoundingClientRect();
      const box = chart.tooltip.getBoundingClientRect();
      const screenX = x / 760 * plotRect.width;
      const viewportPadding = 8;
      const desiredLeft = plotRect.left + screenX - box.width / 2;
      const tooltipLift = isNarrow() ? 16 : 12;
      const desiredTop = plotRect.top + bounds.bottom / chartHeight * plotRect.height - tooltipLift;
      chart.tooltip.style.left = `${Math.max(viewportPadding, Math.min(window.innerWidth - box.width - viewportPadding, desiredLeft))}px`;
      chart.tooltip.style.top = `${Math.max(viewportPadding, Math.min(window.innerHeight - box.height - viewportPadding, desiredTop))}px`;
    };
    const resetInteraction = resetInspection;
    let touchTimer = 0;
    let touchPointerId = null;
    let touchActive = false;
    let touchStartX = 0;
    let touchStartY = 0;
    let touchLastX = 0;
    const clearTouchTimer = () => {
      if (!touchTimer) return;
      window.clearTimeout(touchTimer);
      touchTimer = 0;
    };
    const finishTouch = event => {
      if (event.pointerType !== 'touch' || event.pointerId !== touchPointerId) return;
      clearTouchTimer();
      if (chart.hit.hasPointerCapture?.(event.pointerId)) chart.hit.releasePointerCapture(event.pointerId);
      touchPointerId = null;
      touchActive = false;
      resetInteraction();
    };
    chart.hit.addEventListener('pointerdown', event => {
      if (event.pointerType !== 'touch' || event.isPrimary === false) return;
      clearTouchTimer();
      touchPointerId = event.pointerId;
      touchStartX = event.clientX;
      touchStartY = event.clientY;
      touchLastX = event.clientX;
      touchTimer = window.setTimeout(() => {
        if (touchPointerId !== event.pointerId) return;
        touchTimer = 0;
        touchActive = true;
        try { chart.hit.setPointerCapture?.(event.pointerId); } catch {}
        showInteraction(touchLastX);
      }, 320);
    });
    chart.hit.addEventListener('pointermove', event => {
      if (event.pointerType !== 'touch') {
        showInteraction(event.clientX);
        return;
      }
      if (event.pointerId !== touchPointerId) return;
      touchLastX = event.clientX;
      if (!touchActive) {
        if (Math.hypot(event.clientX - touchStartX, event.clientY - touchStartY) > 10) {
          clearTouchTimer();
          touchPointerId = null;
        }
        return;
      }
      event.preventDefault();
      showInteraction(event.clientX);
    }, { passive: false });
    chart.hit.addEventListener('pointerleave', event => {
      if (event.pointerType === 'touch') {
        if (!touchActive) { clearTouchTimer(); touchPointerId = null; }
        return;
      }
      resetInteraction();
    });
    chart.hit.addEventListener('pointerup', finishTouch);
    chart.hit.addEventListener('pointercancel', finishTouch);
    chart.hit.addEventListener('contextmenu', event => {
      if (isNarrow()) event.preventDefault();
    });
  });
  updateChartGeometry();
  window.addEventListener('resize', () => {
    updateChartGeometry();
    renderAll(false);
  });
  const geometryObserver = new ResizeObserver(() => {
    const previousHeight = chartHeight;
    updateChartGeometry();
    if (chartHeight !== previousHeight) renderAll(false);
  });
  geometryObserver.observe(root);
  renderAll(false);
  parent.postMessage({ type: preferencesMessageType, action: 'get' }, '*');
})();
