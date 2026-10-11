let analysis = { meta: {}, cot: [], prices: [], yields: [], quality: {}, backtests: {} };
let markets = [];
let entities = [];
let selectedMarket = "10Y";
let selectedPeriod = "1Y";
let selectedEntity = "Lev_Funds";

let pricePositionChart;
let zscoreChart;
let compositionChart;
let flowChart;
let priceYieldChart;
let equityChart;

const chartText = '#9ca3af';
const chartGrid = 'rgba(255, 255, 255, 0.06)';

async function init() {
    try {
        await Promise.all([fetchMarkets(), fetchEntities()]);
        bindControls();
        await updateDashboard();
    } catch (error) {
        showError(error);
    }
}

function bindControls() {
    document.getElementById('market-select').addEventListener('change', async (event) => {
        selectedMarket = event.target.value;
        await updateDashboard();
    });
    document.getElementById('period-select').addEventListener('change', async (event) => {
        selectedPeriod = event.target.value;
        await updateDashboard();
    });
}

async function fetchJson(url) {
    const response = await fetch(url);
    const body = await response.json();
    if (!response.ok) {
        throw new Error(body.detail || body.error || `요청 실패 (${response.status})`);
    }
    return body;
}

async function fetchMarkets() {
    markets = await fetchJson('/api/markets');
    const select = document.getElementById('market-select');
    select.replaceChildren();
    markets.forEach((market) => {
        const option = document.createElement('option');
        option.value = market.id;
        option.textContent = `${market.id} · ${market.price_symbol}`;
        option.title = market.name;
        option.selected = market.id === selectedMarket;
        select.appendChild(option);
    });
}

async function fetchEntities() {
    entities = await fetchJson('/api/entities');
    const tabs = document.getElementById('entity-tabs');
    tabs.replaceChildren();
    entities.forEach((entity) => {
        const tab = document.createElement('button');
        tab.type = 'button';
        tab.className = `entity-tab ${entity.id === selectedEntity ? 'active' : ''}`;
        tab.textContent = entity.name;
        tab.addEventListener('click', () => {
            selectedEntity = entity.id;
            document.querySelectorAll('.entity-tab').forEach((item) => item.classList.remove('active'));
            tab.classList.add('active');
            renderDashboardData();
        });
        tabs.appendChild(tab);
    });
}

async function updateDashboard() {
    setLoading(true);
    hideError();
    try {
        analysis = await fetchJson(`/api/analysis?market=${encodeURIComponent(selectedMarket)}&period=${encodeURIComponent(selectedPeriod)}`);
        renderDashboardData();
        renderStatus();
    } catch (error) {
        showError(error);
    } finally {
        setLoading(false);
    }
}

function setLoading(isLoading) {
    document.querySelectorAll('select').forEach((control) => { control.disabled = isLoading; });
    const status = document.getElementById('data-status');
    if (isLoading) {
        status.className = 'data-status';
        document.getElementById('status-text').textContent = 'COT와 가격 데이터를 불러오는 중입니다.';
    }
}

function renderStatus() {
    const meta = analysis.meta;
    const status = document.getElementById('data-status');
    status.className = 'data-status ready';
    document.getElementById('status-text').textContent =
        `COT 기준 ${meta.cot_as_of} · 추정 공개 가능일 ${meta.estimated_available_date} · 가격 ${meta.price_as_of} · ${meta.price_symbol} · 품질 ${meta.quality_status}`;
}

function showError(error) {
    const message = error instanceof Error ? error.message : String(error);
    const panel = document.getElementById('error-panel');
    panel.hidden = false;
    panel.textContent = `데이터를 표시할 수 없습니다: ${message}`;
    const status = document.getElementById('data-status');
    status.className = 'data-status error';
    document.getElementById('status-text').textContent = '데이터 갱신에 실패했습니다.';
}

function hideError() {
    const panel = document.getElementById('error-panel');
    panel.hidden = true;
    panel.textContent = '';
}

function finite(value) {
    const number = Number(value);
    return Number.isFinite(number) ? number : null;
}

function negativeOrNull(value) {
    const number = finite(value);
    return number === null ? null : -number;
}

function formatNumber(value, decimals = 0, suffix = '') {
    const number = finite(value);
    if (number === null) return '--';
    return `${number.toLocaleString(undefined, {
        minimumFractionDigits: decimals,
        maximumFractionDigits: decimals,
    })}${suffix}`;
}

function signedNumber(value, decimals = 0, suffix = '') {
    const number = finite(value);
    if (number === null) return '--';
    const sign = number > 0 ? '+' : number < 0 ? '−' : '';
    return `${sign}${formatNumber(Math.abs(number), decimals, suffix)}`;
}

function describeZScore(value, netPctValue) {
    const zScore = finite(value);
    const netPct = finite(netPctValue);
    if (zScore === null) {
        return '52주 이력이 충분하지 않아 아직 표준화 위치를 계산할 수 없습니다.';
    }

    const magnitude = Math.abs(zScore);
    let range;
    if (magnitude >= 3) range = '매우 극단적인 구간';
    else if (magnitude >= 2) range = '극단 구간';
    else if (magnitude >= 1) range = '평소 범위를 벗어나기 시작한 구간';
    else range = '최근 52주의 통상 범위';

    const absolute = netPct === null
        ? ''
        : `Net/OI ${signedNumber(netPct, 2, '%')}로 절대적으로는 ${netPct > 0 ? '순롱' : netPct < 0 ? '순숏' : '중립'}입니다. `;
    if (zScore > 0) {
        return `${absolute}Robust Z는 ${signedNumber(zScore, 2)}로 ${range}이며, 자기 과거보다 롱 방향입니다${netPct !== null && netPct < 0 ? '(즉 평소보다 덜 숏)' : ''}.`;
    }
    if (zScore < 0) {
        return `${absolute}Robust Z는 ${signedNumber(zScore, 2)}로 ${range}이며, 자기 과거보다 숏 방향입니다${netPct !== null && netPct > 0 ? '(즉 평소보다 덜 롱)' : ''}.`;
    }
    return `${absolute}Robust Z는 0.00으로 최근 52주 중앙값과 거의 같습니다.`;
}

function describeComposition(latest, previous) {
    const longValue = finite(latest[`${selectedEntity}_Long`]);
    const shortValue = finite(latest[`${selectedEntity}_Short`]);
    const previousLong = finite(previous[`${selectedEntity}_Long`]);
    const previousShort = finite(previous[`${selectedEntity}_Short`]);
    if (longValue === null || shortValue === null) {
        return 'Long/Short 구성 데이터를 계산할 수 없습니다.';
    }

    const net = longValue - shortValue;
    const gross = longValue + shortValue;
    const longShare = gross > 0 ? (longValue / gross) * 100 : null;
    const side = net > 0 ? '순롱' : net < 0 ? '순숏' : '중립';
    const balance = net === 0
        ? 'Long과 Short가 같습니다.'
        : `${net > 0 ? 'Long이' : 'Short가'} ${formatNumber(Math.abs(net))}계약 더 많아 ${side}입니다.`;

    let weeklyMove = '';
    if (previousLong !== null && previousShort !== null) {
        const longChange = longValue - previousLong;
        const shortChange = shortValue - previousShort;
        weeklyMove = ` 전주 대비 Long ${signedNumber(longChange)}계약, Short ${signedNumber(shortChange)}계약 변했습니다.`;
    }

    return `Long ${formatNumber(longValue)}계약, Short ${formatNumber(shortValue)}계약이며 ${balance} 방향성 포지션 중 Long 비중은 ${formatNumber(longShare, 1, '%')}입니다.${weeklyMove}`;
}

function renderChartInsights(latest, previous) {
    document.getElementById('zscore-summary').textContent =
        describeZScore(
            latest[`${selectedEntity}_RobustZ52`],
            latest[`${selectedEntity}_Pct`],
        );
    document.getElementById('composition-summary').textContent =
        describeComposition(latest, previous);
}

function updateStat(id, value, comparison = null, options = {}) {
    const { decimals = 0, suffix = '', changeLabel = '전주 대비', scale = 1 } = options;
    const card = document.getElementById(id);
    const valueEl = card.querySelector('.stat-value');
    const changeEl = card.querySelector('.stat-change');
    const current = finite(value);
    const previous = finite(comparison);

    if (current === null) {
        valueEl.textContent = '--';
        changeEl.textContent = '계산에 필요한 이력이 부족합니다.';
        changeEl.className = 'stat-change';
        return;
    }

    valueEl.textContent = formatNumber(current * scale, decimals, suffix);
    if (previous === null) {
        changeEl.textContent = '─';
        changeEl.className = 'stat-change';
        return;
    }

    const diff = (current - previous) * scale;
    if (diff > 0) {
        changeEl.textContent = `▲ ${formatNumber(Math.abs(diff), decimals, suffix)} · ${changeLabel}`;
        changeEl.className = 'stat-change change-up';
    } else if (diff < 0) {
        changeEl.textContent = `▼ ${formatNumber(Math.abs(diff), decimals, suffix)} · ${changeLabel}`;
        changeEl.className = 'stat-change change-down';
    } else {
        changeEl.textContent = `─ ${changeLabel}`;
        changeEl.className = 'stat-change';
    }
}

function updateStandaloneStat(id, value, options = {}) {
    const { decimals = 0, suffix = '', note = '현재 값' } = options;
    const card = document.getElementById(id);
    card.querySelector('.stat-value').textContent = formatNumber(value, decimals, suffix);
    const changeEl = card.querySelector('.stat-change');
    changeEl.textContent = note;
    const number = finite(value);
    changeEl.className = `stat-change ${number > 0 ? 'change-up' : number < 0 ? 'change-down' : ''}`;
}

function renderDashboardData() {
    const cot = analysis.cot || [];
    const prices = analysis.prices || [];
    if (!cot.length) return;

    const latest = cot[cot.length - 1];
    const previous = cot[cot.length - 2] || latest;
    const latestPrice = prices[prices.length - 1];
    const priceFiveDaysAgo = prices[Math.max(0, prices.length - 6)] || latestPrice;

    updateStat('stat-price', latestPrice?.Close, priceFiveDaysAgo?.Close, {
        decimals: 3,
        changeLabel: '약 1주',
    });
    updateStat('stat-net-position', latest[`${selectedEntity}_Net`], previous[`${selectedEntity}_Net`], {
        decimals: 0,
    });
    updateStat('stat-oi-pct', latest[`${selectedEntity}_Pct`], previous[`${selectedEntity}_Pct`], {
        decimals: 2,
        suffix: '%',
    });
    updateStandaloneStat('stat-flow', latest[`${selectedEntity}_Flow4`], {
        decimals: 0,
        note: '4주 누적 계약 변화',
    });
    updateStat('stat-percentile', latest[`${selectedEntity}_P52`], previous[`${selectedEntity}_P52`], {
        decimals: 1,
        suffix: '%',
        scale: 100,
    });
    updateStat('stat-zscore', latest[`${selectedEntity}_RobustZ52`], previous[`${selectedEntity}_RobustZ52`], {
        decimals: 2,
    });

    const entityName = entities.find((entity) => entity.id === selectedEntity)?.name || selectedEntity;
    document.getElementById('main-chart-title').textContent = `${analysis.meta.price_symbol} 가격 · ${entityName} Net/OI`;
    renderChartInsights(latest, previous);
    renderPricePositionChart();
    renderZScoreChart();
    renderCompositionChart();
    renderFlowChart();
    renderPriceYieldChart();
    renderResearchValidation();
}

function baseChart(height) {
    return {
        chart: {
            height,
            toolbar: { show: false },
            background: 'transparent',
            animations: { speed: 300 },
        },
        dataLabels: { enabled: false },
        theme: { mode: 'dark' },
        grid: { borderColor: chartGrid, strokeDashArray: 4 },
        xaxis: {
            type: 'datetime',
            axisBorder: { show: false },
            axisTicks: { show: false },
            labels: { style: { colors: chartText } },
        },
        legend: { labels: { colors: chartText } },
        noData: { text: '표시할 데이터가 없습니다.', style: { color: chartText } },
    };
}

function renderPricePositionChart() {
    const options = {
        ...baseChart(395),
        series: [
            {
                name: `${analysis.meta.price_symbol} Price`,
                type: 'line',
                data: analysis.prices.map((row) => ({ x: row.Date, y: finite(row.Close) })),
            },
            {
                name: 'Net / OI',
                type: 'area',
                data: analysis.cot.map((row) => ({ x: row.Date, y: finite(row[`${selectedEntity}_Pct`]) })),
            },
        ],
        chart: { ...baseChart(395).chart, type: 'line' },
        colors: ['#60a5fa', '#10b981'],
        stroke: { curve: 'smooth', width: [2.4, 2] },
        fill: {
            type: ['solid', 'gradient'],
            gradient: { opacityFrom: 0.26, opacityTo: 0.03, stops: [0, 100] },
        },
        yaxis: [
            {
                title: { text: 'Price', style: { color: chartText } },
                labels: { style: { colors: chartText }, formatter: (value) => value.toFixed(2) },
            },
            {
                opposite: true,
                title: { text: 'Net / OI (%)', style: { color: chartText } },
                labels: { style: { colors: chartText }, formatter: (value) => `${value.toFixed(1)}%` },
            },
        ],
        tooltip: { shared: true, x: { format: 'yyyy-MM-dd' } },
    };
    if (pricePositionChart) pricePositionChart.destroy();
    pricePositionChart = new ApexCharts(document.querySelector('#price-position-chart'), options);
    pricePositionChart.render();
}

function renderZScoreChart() {
    const options = {
        ...baseChart(300),
        series: [{
            name: 'Robust Z (52W)',
            data: analysis.cot.map((row) => ({ x: row.Date, y: finite(row[`${selectedEntity}_RobustZ52`]) })),
        }],
        chart: { ...baseChart(300).chart, type: 'line' },
        colors: ['#f59e0b'],
        stroke: { width: 2.5, curve: 'smooth' },
        yaxis: { labels: { style: { colors: chartText }, formatter: (value) => value.toFixed(1) } },
        annotations: {
            yaxis: [
                { y: 2, borderColor: '#ef4444', strokeDashArray: 4 },
                { y: 0, borderColor: 'rgba(255,255,255,0.24)', strokeDashArray: 2 },
                { y: -2, borderColor: '#3b82f6', strokeDashArray: 4 },
            ],
        },
    };
    if (zscoreChart) zscoreChart.destroy();
    zscoreChart = new ApexCharts(document.querySelector('#zscore-chart'), options);
    zscoreChart.render();
}

function renderCompositionChart() {
    const options = {
        ...baseChart(300),
        series: [
            {
                name: 'Long',
                data: analysis.cot.map((row) => ({ x: row.Date, y: finite(row[`${selectedEntity}_Long`]) })),
            },
            {
                name: 'Short',
                data: analysis.cot.map((row) => ({ x: row.Date, y: negativeOrNull(row[`${selectedEntity}_Short`]) })),
            },
        ],
        chart: { ...baseChart(300).chart, type: 'bar', stacked: true },
        colors: ['#10b981', '#ef4444'],
        plotOptions: { bar: { borderRadius: 1, columnWidth: '78%' } },
        yaxis: {
            labels: {
                style: { colors: chartText },
                formatter: (value) => `${Math.abs(value / 1000).toFixed(0)}K`,
            },
        },
    };
    if (compositionChart) compositionChart.destroy();
    compositionChart = new ApexCharts(document.querySelector('#composition-chart'), options);
    compositionChart.render();
}

function renderFlowChart() {
    const options = {
        ...baseChart(290),
        series: [{
            name: '1W Net Flow',
            data: analysis.cot.map((row) => ({ x: row.Date, y: finite(row[`${selectedEntity}_Flow1`]) })),
        }],
        chart: { ...baseChart(290).chart, type: 'bar' },
        colors: ['#818cf8'],
        plotOptions: { bar: { borderRadius: 1, columnWidth: '70%' } },
        yaxis: {
            labels: {
                style: { colors: chartText },
                formatter: (value) => `${(value / 1000).toFixed(0)}K`,
            },
        },
        annotations: { yaxis: [{ y: 0, borderColor: 'rgba(255,255,255,0.25)' }] },
    };
    if (flowChart) flowChart.destroy();
    flowChart = new ApexCharts(document.querySelector('#flow-chart'), options);
    flowChart.render();
}

function renderPriceYieldChart() {
    const yields = analysis.yields || [];
    const options = {
        ...baseChart(300),
        series: [
            {
                name: `${analysis.meta.price_symbol} Price`,
                data: analysis.prices.map((row) => ({ x: row.Date, y: finite(row.Close) })),
            },
            {
                name: `${yields[0]?.Tenor || ''} Treasury Yield`,
                data: yields.map((row) => ({ x: row.Date, y: finite(row.Yield) })),
            },
        ],
        chart: { ...baseChart(300).chart, type: 'line' },
        colors: ['#60a5fa', '#f59e0b'],
        stroke: { width: [2.2, 2.2], curve: 'smooth' },
        yaxis: [
            {
                title: { text: 'Futures price', style: { color: chartText } },
                labels: { style: { colors: chartText }, formatter: (value) => value.toFixed(2) },
            },
            {
                opposite: true,
                title: { text: 'Yield (%)', style: { color: chartText } },
                labels: { style: { colors: chartText }, formatter: (value) => `${value.toFixed(2)}%` },
            },
        ],
        tooltip: { shared: true, x: { format: 'yyyy-MM-dd' } },
    };
    if (priceYieldChart) priceYieldChart.destroy();
    priceYieldChart = new ApexCharts(document.querySelector('#price-yield-chart'), options);
    priceYieldChart.render();
}

function percent(value, decimals = 1) {
    const number = finite(value);
    return number === null ? '--' : `${(number * 100).toFixed(decimals)}%`;
}

function metricClass(value) {
    const number = finite(value);
    if (number === null || number === 0) return '';
    return number > 0 ? 'metric-positive' : 'metric-negative';
}

function renderResearchValidation() {
    const quality = analysis.quality || {};
    const backtest = analysis.backtests?.[selectedEntity];
    if (!backtest) return;

    const badge = document.getElementById('quality-badge');
    badge.textContent = quality.status === 'PASS' ? '데이터 검증 통과' : '데이터 검토 필요';
    badge.className = `quality-badge ${String(quality.status || '').toLowerCase()}`;

    const signal = finite(backtest.current?.composite_signal) || 0;
    const signalLabel = signal > 0 ? 'LONG' : signal < 0 ? 'SHORT' : 'NEUTRAL';
    document.getElementById('current-signal').textContent = backtest.current?.entry_pending
        ? `${signalLabel} · 진입 대기`
        : signalLabel;
    document.getElementById('entry-status').textContent = backtest.current?.entry_pending
        ? `최신 COT의 보수적 진입 목표일 ${backtest.current?.research_entry_target_date || '다음 화요일'} 이후 첫 종가 대기`
        : `연구 진입 기준일 ${backtest.current?.research_entry_date}`;

    const correlation = finite(quality.price_yield_60d_correlation);
    const corrEl = document.getElementById('price-yield-correlation');
    corrEl.textContent = correlation === null ? '--' : correlation.toFixed(3);
    corrEl.className = correlation !== null && correlation < 0 ? 'metric-positive' : 'metric-negative';
    document.getElementById('quality-cot-rows').textContent = formatNumber(quality.cot_rows, 0);
    document.getElementById('quality-price-rows').textContent = formatNumber(quality.price_rows, 0);

    const tableBody = document.getElementById('backtest-table-body');
    tableBody.replaceChildren();
    backtest.strategies.forEach((strategy) => {
        const row = document.createElement('tr');
        const values = [
            strategy.name,
            formatNumber(strategy.active_weeks, 0),
            percent(strategy.win_rate),
            percent(strategy.annualized_return),
            formatNumber(strategy.sharpe, 2),
            percent(strategy.max_drawdown),
        ];
        values.forEach((value, index) => {
            const cell = document.createElement('td');
            cell.textContent = value;
            if (index >= 3) {
                const sourceValue = index === 3 ? strategy.annualized_return : index === 4 ? strategy.sharpe : strategy.max_drawdown;
                cell.className = metricClass(sourceValue);
            }
            row.appendChild(cell);
        });
        tableBody.appendChild(row);
    });

    renderEquityChart(backtest.equity || []);
    renderFactorTable(backtest.factor_validation || []);
}

function renderEquityChart(rows) {
    const options = {
        ...baseChart(300),
        series: [
            { name: 'Flow + Trend', data: rows.map((row) => ({ x: row.Date, y: finite(row.FlowTrend) })) },
            { name: 'Crowding Reversal', data: rows.map((row) => ({ x: row.Date, y: finite(row.CrowdingReversal) })) },
            { name: 'Composite', data: rows.map((row) => ({ x: row.Date, y: finite(row.Composite) })) },
        ],
        chart: { ...baseChart(300).chart, type: 'line' },
        colors: ['#60a5fa', '#f59e0b', '#10b981'],
        stroke: { width: [1.8, 1.8, 2.5], curve: 'straight' },
        yaxis: { labels: { style: { colors: chartText }, formatter: (value) => value.toFixed(2) } },
        tooltip: { shared: true, x: { format: 'yyyy-MM-dd' } },
    };
    if (equityChart) equityChart.destroy();
    equityChart = new ApexCharts(document.querySelector('#equity-chart'), options);
    equityChart.render();
}

function renderFactorTable(rows) {
    const tableBody = document.getElementById('factor-table-body');
    tableBody.replaceChildren();
    const featureNames = [...new Set(rows.map((row) => row.feature))];
    featureNames.forEach((feature) => {
        const featureRows = rows.filter((row) => row.feature === feature);
        const byHorizon = Object.fromEntries(featureRows.map((row) => [row.horizon, row]));
        const row = document.createElement('tr');
        const cells = [
            feature,
            formatNumber(byHorizon['1W']?.rank_ic, 3),
            formatNumber(byHorizon['4W']?.rank_ic, 3),
            formatNumber(byHorizon['13W']?.rank_ic, 3),
            formatNumber(Math.min(...featureRows.map((item) => item.observations)), 0),
        ];
        cells.forEach((value, index) => {
            const cell = document.createElement('td');
            cell.textContent = value;
            if (index > 0 && index < 4) cell.className = metricClass(featureRows[index - 1]?.rank_ic);
            row.appendChild(cell);
        });
        tableBody.appendChild(row);
    });
}

document.addEventListener('DOMContentLoaded', init);
