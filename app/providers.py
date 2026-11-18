import hashlib
import json
import math
import random
import time
from datetime import date, datetime, timedelta
import httpx
from . import storage
from .models import now, TZ

DEMO_STOCKS = [
    ('600519.SH', '贵州茅台', '白酒'), ('000858.SZ', '五粮液', '白酒'),
    ('000568.SZ', '泸州老窖', '白酒'), ('300750.SZ', '宁德时代', '电池'),
    ('002594.SZ', '比亚迪', '汽车整车'), ('601127.SH', '赛力斯', '汽车整车'),
    ('600036.SH', '招商银行', '银行'), ('601398.SH', '工商银行', '银行'),
    ('601318.SH', '中国平安', '保险'), ('600030.SH', '中信证券', '证券'),
    ('600900.SH', '长江电力', '电力'), ('601012.SH', '隆基绿能', '光伏设备'),
]


class ProviderError(Exception):
    pass


def request_json(url, payload, headers=None, retries=2):
    for attempt in range(retries + 1):
        try:
            from .agent_store import reserve, scope
            reserve('model')
            deadline = (scope.get() or {}).get('deadline', float('inf'))
            timeout = max(.1, min(45, deadline - time.monotonic()))
            r = httpx.post(url, json=payload, headers=headers, timeout=timeout)
            if r.status_code == 429 or r.status_code >= 500:
                if attempt < retries:
                    retry = r.headers.get('retry-after', '')
                    time.sleep(min(float(retry) if retry.isdigit() else 2 ** attempt, 15))
                    continue
            r.raise_for_status()
            return r.json()
        except (httpx.TimeoutException, httpx.NetworkError):
            if attempt == retries:
                raise ProviderError('请求超时或网络不可用，请检查连接后重试') from None
            time.sleep(2 ** attempt)
        except (httpx.HTTPStatusError, ValueError):
            raise ProviderError('服务返回异常，请检查权限、地址和模型配置') from None
    raise ProviderError('请求失败')


from .ak_adapter import AKShare


def catalog(mode):
    if mode == 'demo':
        rows = [{'id': code, 'name': name, 'sector': industry, 'kind': 'stock', 'source': '样例目录'} for code, name, industry in DEMO_STOCKS]
        rows += [{'id': 'demo:' + s, 'name': s, 'sector': s, 'kind': 'sector', 'source': '样例分类'} for s in dict.fromkeys(s for _, _, s in DEMO_STOCKS)]
        return rows
    with storage.db() as c:
        return [item for r in c.execute('SELECT content FROM catalog') if (item := json.loads(r[0])).get('provider') == 'akshare']


def sync_catalog():
    client = AKShare()
    counts, warnings = {}, []
    for kind, label in [('stock', '股票'), ('sector', '行业')]:
        try:
            rows = client.catalog(kind)
            if not rows:
                raise ProviderError('接口返回空目录，已保留原目录')
            with storage.db() as c:
                for row in rows:
                    c.execute('INSERT OR REPLACE INTO catalog VALUES(?,?)', (row['id'], storage.dumps(row)))
            counts[kind] = len(rows)
        except ProviderError as exc:
            counts[kind] = 0
            warnings.append(f'{label}同步失败：{exc}')
    if not sum(counts.values()):
        raise ProviderError('；'.join(warnings))
    return {'count': sum(counts.values()), 'counts': counts, 'warnings': warnings}


def trading_days(end, count=140):
    # Explicit synthetic calendar for demo mode only. Live mode uses exchange trade_cal.
    day = date.fromisoformat(end)
    days = []
    while len(days) < count:
        if day.weekday() < 5:
            days.append(day.strftime('%Y%m%d'))
        day -= timedelta(days=1)
    return list(reversed(days))


def demo_bundle(request):
    objects = {x['id']: x for x in catalog('demo')}
    days = trading_days(request['trade_date'])
    histories, factors, current, previous, limits = {}, {}, {}, {}, {}
    for index, (code, _, _) in enumerate(DEMO_STOCKS):
        rng = random.Random(int(hashlib.sha256((code + request['trade_date']).encode()).hexdigest()[:10], 16))
        price = [1500, 135, 120, 260, 98, 115, 42, 6, 58, 26, 28, 18][index]
        rows = []
        for n, day in enumerate(days):
            pre = price
            o = pre * (1 + rng.uniform(-.008, .008))
            price = pre * (1 + rng.uniform(-.023, .027))
            volume = rng.uniform(100000, 600000)
            rows.append({'ts_code': code, 'trade_date': day, 'open': round(o, 2), 'close': round(price, 2),
                         'high': round(max(o, price) * 1.01, 2), 'low': round(min(o, price) * .99, 2),
                         'vol': volume, 'amount': volume * price / 10, 'pre_close': pre,
                         'pct_chg': (price / pre - 1) * 100})
        histories[code] = rows
        factors[code] = {d: 1.0 for d in days}
        current[code], previous[code] = rows[-1], rows[-2]
        limits[code] = round(rows[-1]['pre_close'] * (1.2 if code.startswith('300') else 1.1), 2)
    sectors = []
    for sector in request['sectors']:
        name = objects[sector]['name']
        sectors.append({'id': sector, 'name': name, 'source': '样例分类',
                        'members': [code for code, _, s in DEMO_STOCKS if s == name]})
    return {'histories': histories, 'factors': factors, 'current': current, 'previous': previous,
            'limits': limits, 'suspended': [], 'sectors': sectors, 'catalog': objects,
            'issues': ['样例模式使用合成行情、工作日日历和虚构事件，仅用于验证产品流程。'],
            'source': '样例数据', 'fetched_at': now().isoformat()}


def live_bundle(request):
    from .ak_adapter import live_bundle as fetch_bundle
    return fetch_bundle(request)
