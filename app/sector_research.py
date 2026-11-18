"""Deterministic index-level calculations, with explicit calendar and lineage."""
import math


def index_metrics(rows, benchmark, calendar, end):
    def series(items):
        out = {}
        for row in items:
            date = str(row['date'])[:10].replace('-', '')
            close = row.get('close')
            if date <= end and isinstance(close, (float, int)) and math.isfinite(close) and close > 0:
                if date in out and out[date] != close: raise ValueError('同日指数收盘数据冲突')
                out[date] = close
        return out
    prices, base = series(rows), series(benchmark)
    dates = sorted({d.replace('-', '') for d in calendar if d.replace('-', '') <= end})
    gaps, calculations = [], []
    out = {'layer': 'index', 'benchmark': '沪深300（000300）', 'benchmark_source': 'AKShare/stock_zh_index_daily_em',
           'as_of': end, 'pct_change': None, 'return_20d': None, 'relative_strength_pp': None,
           'ma5': None, 'ma20': None, 'max_drawdown_pct': None, 'amount_ratio': None,
           'gaps': gaps, 'calculations': calculations}
    def window(n):
        selected = dates[-n:]
        return selected if len(selected) == n and selected[-1] == end and all(d in prices for d in selected) else []
    def save(field, value, used, formula, unit):
        out[field] = value
        calculations.append({'field': field, 'formula': formula, 'unit': unit,
                             'start': used[0], 'end': used[-1], 'inputs': [{'date': d, 'close': prices[d]} for d in used]})
    pair = window(2)
    if pair: save('pct_change', (prices[pair[-1]]/prices[pair[0]]-1)*100, pair, '(close_end / close_previous - 1) * 100', '%')
    else: gaps.append('相邻交易日数据不足，当日涨跌幅不可计算')
    for n in (5,20):
        used = window(n)
        if used: save('ma'+str(n), sum(prices[d] for d in used)/n, used, 'sum(close) / N', '指数点')
        else: gaps.append(f'MA{n} 所需连续交易日不足')
    used = window(21)
    if used:
        result = (prices[used[-1]]/prices[used[0]]-1)*100
        save('return_20d', result, used, '(close_end / close_start - 1) * 100', '%')
        peak, drawdown = prices[used[0]], 0
        for d in used:
            peak = max(peak,prices[d]); drawdown = min(drawdown,prices[d]/peak-1)
        save('max_drawdown_pct', drawdown*100, used, 'min(close / running_max(close) - 1) * 100', '%')
        if all(d in base for d in used):
            save('relative_strength_pp', result-(base[used[-1]]/base[used[0]]-1)*100, used, 'sector_return_pct - benchmark_return_pct', '百分点')
            calculations[-1]['benchmark_inputs'] = [{'date': d, 'close': base[d]} for d in used]
        else: gaps.append('基准同区间数据不完整，相对强弱不可计算')
    else: gaps.append('区间收益和回撤所需连续交易日不足')
    gaps += ['行业成交额单位及历史口径尚未核验，不计算交投比值', '仅指数层，未覆盖历史成分广度、涨停数量和资金净流入']
    return out
