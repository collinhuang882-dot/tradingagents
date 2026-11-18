"""iFinD HTTP stock daily fallback. Credentials never enter run snapshots."""
import math
import time
from datetime import datetime
import httpx
from . import storage

BASE = 'https://quantapi.51ifind.com/api/v1/'


def configured():
    cfg = storage.settings()
    return bool(cfg.get('ifind_enabled') and (cfg.get('ifind_access_token') or cfg.get('ifind_refresh_token')))


def post(endpoint, payload, headers):
    from .providers import ProviderError
    from .agent_store import reserve, scope
    reserve('network')
    remaining = (scope.get() or {}).get('deadline', float('inf')) - time.monotonic()
    if remaining <= 0:
        raise ProviderError('iFinD 请求时间预算已耗尽')
    try:
        response = httpx.post(BASE + endpoint, json=payload,
                              headers={'Content-Type': 'application/json', **headers},
                              timeout=min(30, remaining), follow_redirects=False)
        response.raise_for_status()
        result = response.json()
    except (httpx.HTTPError, ValueError):
        raise ProviderError('iFinD HTTP 请求失败，请检查网络、凭证与账号权限') from None
    if not isinstance(result, dict) or result.get('errorcode') not in (0, '0'):
        # Never echo vendor messages: they may contain request credentials.
        raise ProviderError('iFinD 返回业务错误，请检查 token 有效期、行情权限及额度')
    return result


class IFinD:
    def __init__(self):
        from .providers import ProviderError
        cfg = storage.settings()
        if not configured():
            raise ProviderError('iFinD 股票日线备用未启用或未配置凭证')
        self.token = cfg.get('ifind_access_token', '')
        self.refresh = cfg.get('ifind_refresh_token', '')

    def quotes(self, code, start, end, cps):
        from .providers import ProviderError
        if not self.token:
            result = post('get_access_token', None, {'refresh_token': self.refresh})
            self.token = result.get('data', {}).get('access_token')
            if not isinstance(self.token, str) or not self.token:
                raise ProviderError('iFinD 未返回有效 access_token')
        payload = {'codes': code, 'indicators': 'open,high,low,close,volume,amount,preClose,changeRatio',
                   'startdate': start, 'enddate': end,
                   'functionpara': {'CPS': str(cps), 'Interval': 'D', 'Fill': 'Omit', 'Currency': 'MHB'}}
        result = post('cmd_history_quotation', payload, {'access_token': self.token})
        tables = result.get('tables')
        if not isinstance(tables, list) or len(tables) != 1 or not isinstance(tables[0], dict) or tables[0].get('thscode') != code:
            raise ProviderError('iFinD 返回的证券代码或表结构不符合请求')
        data = tables[0]
        times, values = data.get('time'), data.get('table')
        if not isinstance(times, list) or not isinstance(values, dict):
            raise ProviderError('iFinD 日线时间轴或字段结构无效')
        fields = ('open', 'high', 'low', 'close', 'volume', 'amount')
        if any(not isinstance(values.get(k), list) or len(values[k]) != len(times) for k in fields):
            raise ProviderError('iFinD 日线字段缺失或长度不一致')
        rows, seen = [], set()
        for i, stamp in enumerate(times):
            try:
                day = datetime.strptime(str(stamp)[:10].replace('-', ''), '%Y%m%d').strftime('%Y%m%d')
                if not start <= day <= end:
                    continue
                if day in seen:
                    raise ValueError()
                seen.add(day)
                numeric = {k: float(values[k][i]) for k in fields}
                if not all(math.isfinite(v) for v in numeric.values()):
                    raise ValueError()
                o, h, l, close = (numeric[k] for k in ('open', 'high', 'low', 'close'))
                if min(o, h, l, close) <= 0 or not l <= min(o, close) <= max(o, close) <= h or min(numeric['volume'], numeric['amount']) < 0:
                    raise ValueError()
                optional = {}
                for key, target in [('preClose', 'pre_close'), ('changeRatio', 'pct_chg')]:
                    series = values.get(key)
                    value = series[i] if isinstance(series, list) and len(series) == len(times) else None
                    optional[target] = float(value) if value is not None else None
                    if optional[target] is not None and not math.isfinite(optional[target]):
                        raise ValueError()
                rows.append({'ts_code': code, 'trade_date': day,
                             **{k: numeric[k] for k in ('open','high','low','close')},
                             'vol': numeric['volume'] / 100, 'amount': numeric['amount'] / 1000, **optional})
            except (ValueError, TypeError, IndexError):
                raise ProviderError('iFinD 日线日期、数值或 OHLC 校验失败') from None
        rows.sort(key=lambda r: r['trade_date'])
        if not rows or rows[-1]['trade_date'] != end:
            raise ProviderError('iFinD 未提供指定交易日有效行情，不能用旧数据替代')
        return rows

    def history(self, code, start, end):
        from .providers import ProviderError
        # A refresh token obtains a current access token on each fallback operation.
        if self.refresh:
            self.token = ''
        rows = self.quotes(code, start, end, 1)
        factors, notes = {}, []
        try:
            adjusted = {r['trade_date']: r for r in self.quotes(code, start, end, 3)}
            scale = rows[-1]['close'] / adjusted[end]['close']
            for row in rows:
                adj = adjusted.get(row['trade_date'])
                if adj:
                    factors[row['trade_date']] = adj['close'] / row['close']
                    row['adjusted_prices'] = {k: adj[k] * scale for k in ('open','high','low','close')}
            if len(factors) != len(rows):
                notes.append('iFinD 后复权序列不完整，暂停趋势指标')
        except ProviderError:
            notes.append('iFinD 后复权序列不可用，保留原始价格，暂停趋势指标')
        return rows, factors, '同花顺 iFinD/HTTP 股票日线', notes
