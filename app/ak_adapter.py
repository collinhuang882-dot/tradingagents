"""AKShare adapters. All records entering the engine use explicit units and dates."""
import math
from datetime import date, timedelta
from . import storage
from .ak_data import fetch
from .models import now


def error(message):
    from .providers import ProviderError
    return ProviderError(message)


def day(value):
    return str(value or '')[:10].replace('-', '')


def code_id(value):
    code = str(value).split('.')[0].zfill(6)
    return code + ('.SH' if code.startswith('6') else '.SZ') if code.startswith(('0', '3', '6')) else None


class AKShare:
    def catalog(self, kind):
        if kind == 'stock':
            rows = fetch('stock_info_a_code_name', ttl=86400)
            return [{'id': code_id(r['code']), 'name': r['name'], 'kind': 'stock', 'sector': '',
                     'source': 'AKShare/交易所', 'provider': 'akshare'} for r in rows if code_id(r['code'])]
        rows = fetch('index_realtime_sw', symbol='二级行业', ttl=86400)
        return [{'id': 'ak:sw:' + str(r['指数代码']), 'name': r['指数名称'], 'kind': 'sector',
                 'sector': r['指数名称'], 'source': 'AKShare/申万二级行业指数', 'provider': 'akshare'} for r in rows]

    def trade_dates(self, start, end):
        rows = fetch('tool_trade_date_hist_sina', ttl=86400)
        dates = sorted({day(r['trade_date']) for r in rows})
        if not dates or end > dates[-1]:
            raise error('AKShare 交易日历尚未覆盖所选日期，不能按工作日猜测交易日')
        return [d for d in dates if start <= d <= end]

    def history(self, code, start, end, source=None):
        from .providers import ProviderError
        from .ifind import IFinD, configured
        try:
            return self._history(code, start, end, source)
        except ProviderError as primary:
            # Explicit EM tasks must first let the existing planner try Sina.
            if source not in (None, 'stock_zh_a_daily') or not configured():
                raise
            try:
                rows, factors, provider, notes = IFinD().history(code, start, end)
                return rows, factors, provider, ['AKShare 日线失败，已回退同花顺 iFinD'] + notes
            except ProviderError as secondary:
                raise error(f'{primary}；iFinD 备用失败：{secondary}') from None

    def _history(self, code, start, end, source=None):
        from .providers import ProviderError
        notes = []
        explicit_source = source
        for function in ([source] if source else ['stock_zh_a_hist', 'stock_zh_a_daily']):
            symbol = code[:6] if function == 'stock_zh_a_hist' else code[-2:].lower() + code[:6]
            try:
                raw = fetch(function, symbol=symbol, start_date=start, end_date=end, adjust='')
                if not raw:
                    raise error('日线为空')
                rows = normalize_bars(raw, function, code, end)
                if not rows:
                    raise error('所选日期范围没有有效日线')
                from .analytics import clean_bars
                checked, _, _ = clean_bars(rows, end)
                if not checked or day(checked[-1]['date']) != end:
                    raise error('所选交易日没有有效行情')
            except ProviderError:
                notes.append(f'{function} 不可用')
                continue
            factors = {}
            try:
                adjusted = fetch(function, symbol=symbol, start_date=start, end_date=end, adjust='hfq')
                adjusted = {r['trade_date']: r for r in normalize_bars(adjusted, function, code, end)}
                last = rows[-1]
                anchor = adjusted.get(last['trade_date'], {}).get('close')
                if anchor and anchor > 0:
                    scale = last['close'] / anchor
                    for r in rows:
                        a = adjusted.get(r['trade_date'])
                        if a and a['close'] > 0:
                            factors[r['trade_date']] = a['close'] / r['close']
                            r['adjusted_prices'] = {k: a[k] * scale for k in ('open', 'high', 'low', 'close')}
            except ProviderError:
                notes.append('后复权序列不可用，保留原始价格，暂停趋势指标')
            source = 'AKShare/' + ('东方财富' if function == 'stock_zh_a_hist' else '新浪财经')
            if function == 'stock_zh_a_daily' and not explicit_source:
                try:
                    recent = fetch('stock_zh_a_hist', symbol=code[:6], start_date=end[:6] + '01', end_date=end, adjust='')
                    recent = {day(r['日期']): r for r in recent}
                    for r in rows:
                        quote = recent.get(r['trade_date'])
                        if quote and quote.get('收盘') is not None and abs(quote['收盘'] - r['close']) < .005:
                            r['pct_chg'] = quote.get('涨跌幅')
                    if rows[-1]['pct_chg'] is not None:
                        source += '（当日涨跌幅：东方财富同日收盘核对）'
                except ProviderError:
                    pass
                if rows[-1]['pct_chg'] is None:
                    notes.append('备用来源未提供交易所当日涨跌幅，该字段显示不可用')
            elif function == 'stock_zh_a_daily' and rows[-1]['pct_chg'] is None:
                notes.append('备用来源未提供交易所当日涨跌幅，该字段显示不可用')
            return rows, factors, source, notes
        raise error(f'{code} 的 AKShare 日线来源均不可用')

    def sector(self, item, end, calendar=None):
        rows = fetch('index_hist_sw', symbol=item['id'].split(':')[-1], period='day')
        rows = sorted([r for r in rows if day(r['日期']) <= end], key=lambda r: day(r['日期']))
        if not rows or day(rows[-1]['日期']) != end:
            latest = day(rows[-1]['日期']) if rows else '无记录'
            raise error(f'{item["name"]} 所选交易日行业指数行情缺失（请求 {end}，来源最新 {latest}）')
        current = rows[-1]
        previous = rows[-2] if len(rows) > 1 else None
        change = (current['收盘'] / previous['收盘'] - 1) * 100 if previous and previous['收盘'] else None
        from .providers import ProviderError
        from .sector_research import index_metrics
        benchmark, benchmark_issues, benchmark_source = [], [], ''
        if calendar:
            for function in ('stock_zh_index_daily_em', 'stock_zh_index_daily'):
                try:
                    params = {'symbol':'sh000300'}
                    if function.endswith('_em'): params.update(start_date=calendar[0], end_date=end)
                    candidate = fetch(function, **params)
                    if not any(day(row.get('date'))==end and row.get('close') for row in candidate):
                        raise ProviderError(function+' 未覆盖研究截止交易日')
                    benchmark, benchmark_source = candidate, 'AKShare/'+function
                    break
                except ProviderError as exc: benchmark_issues.append(str(exc))
        metrics = index_metrics([{'date': day(r['日期']), 'close': r.get('收盘')} for r in rows], benchmark, calendar or [], end)
        metrics['benchmark_source'] = benchmark_source or '未取得可用基准'
        metrics['source_attempts'] = benchmark_issues
        if not benchmark and benchmark_issues: metrics['gaps'].append('基准获取失败：' + '；'.join(benchmark_issues))
        # Neither absolute amounts nor ratios are reliable before historical units are verified.
        summary = {**item, 'members': [], 'valid': None, 'total': None, 'up': None, 'down': None,
                   'flat': None, 'missing': 0, 'suspended': None, 'pct_change': change,
                   'amount': None, 'amount_change': None, 'common_count': None,
                   'limit_up': None, 'limit_unknown': None, 'leaders': [], 'risk': '资料不足',
                   'basis': '申万二级行业指数涨跌幅；非成分股等权统计；历史成分及涨停家数不可用',
                   'index_only': True, 'available': True, 'close': current['收盘'], 'trade_date': end,
                   'index_research': metrics}
        if calendar: summary['pct_change'] = metrics['pct_change']
        return {**item, 'members': [], 'aggregate': summary}

    def global_history(self, code, start, end):
        if code == 'HSI':
            rows = fetch('stock_hk_index_daily_sina', symbol='HSI')
        else:
            rows = fetch('stock_us_daily', symbol={'SPX': '.INX', 'IXIC': '.IXIC'}[code], adjust='')
        rows = sorted(rows, key=lambda r: day(r['date']))
        result, previous = [], None
        for row in rows:
            close = row.get('close')
            if close is None:
                continue
            dt = day(row['date'])
            if start <= dt <= end:
                result.append({'trade_date': dt, 'close': close,
                               'pct_chg': (close / previous - 1) * 100 if previous else None})
            previous = close
        return result

    def announcements(self, stocks, start, end):
        from .providers import ProviderError
        records, issues = [], []
        selected = {s[:6]: s for s in stocks}
        for code, stock in selected.items():
            try:
                rows = fetch('stock_individual_notice_report', security=code, symbol='全部',
                             begin_date=start, end_date=end, timeout=45)
                for r in rows:
                    ann_date = day(r['公告日期'])
                    if str(r['代码']).zfill(6) == code and start.replace('-', '') <= ann_date <= end.replace('-', ''):
                        records.append({'ann_date': ann_date, 'ts_code': stock,
                                        'name': r['名称'], 'title': r['公告标题'], 'url': r['网址'], 'rec_time': None})
            except ProviderError as exc:
                issues.append(f'{stock} 公告（{start} 至 {end}）：{exc}')
        if issues:
            issues.append('公告回看区间未全部完成采集')
        return records, issues



def normalize_bars(raw, function, code, end):
    em = function == 'stock_zh_a_hist'
    result = []
    for r in raw:
        dt = day(r.get('日期') if em else r.get('date'))
        if dt > end:
            continue
        keys = {'open': '开盘', 'high': '最高', 'low': '最低', 'close': '收盘'}
        row = {'trade_date': dt, 'ts_code': code,
               **{k: r.get(v if em else k) for k, v in keys.items()},
               'vol': r.get('成交量') if em else (r.get('volume') / 100 if r.get('volume') is not None else None),
               'amount': (r.get('成交额' if em else 'amount') / 1000) if r.get('成交额' if em else 'amount') is not None else None,
               'pct_chg': r.get('涨跌幅') if em else None}
        if all(isinstance(row[k], (float, int)) and math.isfinite(row[k]) and row[k] > 0 for k in keys):
            result.append(row)
    return sorted(result, key=lambda r: r['trade_date'])


def live_bundle(request):
    from .providers import catalog, ProviderError
    client = AKShare()
    end = request['trade_date'].replace('-', '')
    start = (date.fromisoformat(request['trade_date']) - timedelta(days=400)).strftime('%Y%m%d')
    dates = client.trade_dates(start, end)
    if not dates or dates[-1] != end:
        raise error('所选日期不是有效交易日')
    objects = {r['id']: r for r in catalog('live')}
    missing = [code for code in request['stocks'] + request['sectors'] if code not in objects]
    if missing:
        raise ProviderError('证券目录未覆盖 ' + '、'.join(missing) + '，请先同步证券目录')
    histories, factors, sources, sectors = {}, {}, {}, []
    issues, coverage_notes = [], []
    for code in request['stocks']:
        try:
            histories[code], factors[code], sources[code], notes = client.history(code, start, end, source=request.get('_source'))
            for note in notes:
                target = coverage_notes if note.endswith(' 不可用') or '已回退' in note else issues
                target.append(f'{code}：{note}' + ('，已使用备用来源' if target is coverage_notes else ''))
        except ProviderError as exc:
            histories[code], factors[code] = [], {}
            issues.append(str(exc))
    for sid in request['sectors']:
        try:
            sectors.append(client.sector(objects[sid], end, dates))
            coverage_notes.append(f'{objects[sid]["name"]} 使用行业指数行情，历史成分涨跌家数、涨停数及龙头统计不可用')
        except ProviderError as exc:
            issues.append(str(exc))
    return {'histories': histories, 'factors': factors, 'current': {}, 'previous': {}, 'limits': {},
            'suspended': [], 'sectors': sectors, 'catalog': objects, 'issues': issues, 'coverage_notes': coverage_notes,
            'source': 'AKShare', 'stock_sources': sources, 'fetched_at': now().isoformat(),
            'adjustment_basis': '后复权序列按复盘日收盘价格归一',
            'source_url': 'https://akshare.akfamily.xyz/data/stock/stock_quote.html'}
