import math
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from .models import TZ


def clean_bars(raw, trade_date, factors=None):
    rows, issues, seen = [], [], set()
    for item in sorted(raw, key=lambda x: str(x.get('trade_date', ''))):
        day = str(item.get('trade_date', '')).replace('-', '')
        if day > trade_date.replace('-', ''):
            continue
        if day in seen:
            issues.append(f'{day} 重复记录已排除')
            continue
        seen.add(day)
        try:
            values = [float(item[k]) for k in ['open', 'high', 'low', 'close', 'vol', 'amount']]
            o, h, l, close, vol, amount = values
            if not all(math.isfinite(v) for v in values) or min(o, h, l, close) <= 0 or min(vol, amount) < 0:
                raise ValueError()
            if not l <= min(o, close) <= max(o, close) <= h:
                raise ValueError()
            date = datetime.strptime(day, '%Y%m%d').date().isoformat()
        except (KeyError, TypeError, ValueError):
            issues.append(f'{day} 行情字段或 OHLC 无效，已隔离')
            continue
        factor = (factors or {}).get(day)
        if factor is not None:
            try:
                factor = float(factor)
                if not math.isfinite(factor) or factor <= 0:
                    raise ValueError()
            except (ValueError, TypeError):
                factor = None
                issues.append(f'{day} 复权因子无效，暂停复权指标')
        rows.append({'date': date, 'open': o, 'high': h, 'low': l, 'close': close,
                     'volume': vol * 100, 'amount': amount * 1000, 'factor': factor,
                     'adjusted_prices': item.get('adjusted_prices'),
                     'pct_change': item.get('pct_chg'), 'pre_close': item.get('pre_close')})
    complete_factors = bool(rows) and all(r['factor'] is not None for r in rows)
    last_factor = rows[-1]['factor'] if complete_factors else 1
    for row in rows:
        ratio = row['factor'] / last_factor if complete_factors else 1
        for field in ['open', 'high', 'low', 'close']:
            row['adjusted_' + field] = row['adjusted_prices'][field] if complete_factors and row['adjusted_prices'] else row[field] * ratio
        row.pop('adjusted_prices')
    return rows, issues, complete_factors


def indicators(bars, adjusted=True):
    closes = [r['adjusted_close'] if adjusted else r['close'] for r in bars]
    result = {}
    for window in (5, 10, 20, 60):
        result[f'ma{window}'] = sum(closes[-window:]) / window if len(closes) >= window else None
    volumes = [r['volume'] for r in bars if r['volume'] > 0]
    result['volume_ratio'] = None
    if bars and bars[-1]['volume'] > 0 and len(volumes) >= 6:
        result['volume_ratio'] = volumes[-1] / (sum(volumes[-6:-1]) / 5)
    ema12 = ema26 = closes[0] if closes else 0
    dea = 0.0
    for i, close in enumerate(closes):
        if i:
            ema12 += (close - ema12) * 2 / 13
            ema26 += (close - ema26) * 2 / 27
        dif = ema12 - ema26
        dea += (dif - dea) * 2 / 10
        bars[i]['dif'], bars[i]['dea'], bars[i]['macd'] = dif, dea, 2 * (dif - dea)
        for window in (5, 10, 20, 60):
            bars[i][f'ma{window}'] = sum(closes[i-window+1:i+1]) / window if i+1 >= window else None
    result.update({'dif': bars[-1]['dif'] if bars else None, 'dea': dea if bars else None,
                   'macd': bars[-1]['macd'] if bars else None, 'sample_count': len(bars)})
    if not adjusted:
        for key in ['ma5', 'ma10', 'ma20', 'ma60', 'dif', 'dea', 'macd']:
            result[key] = None
        for bar in bars:
            for key in ['ma5', 'ma10', 'ma20', 'ma60', 'dif', 'dea', 'macd']:
                bar[key] = None
    return result


def event_eligibility(item, cutoff, lookback=7):
    cutoff = datetime.fromisoformat(cutoff) if isinstance(cutoff, str) else cutoff
    lower = cutoff.date() - timedelta(days=lookback)
    published = item.get('published_at')
    if published:
        dt = datetime.fromisoformat(published)
        if dt.tzinfo is None:
            return 'unverified'
        if dt > cutoff or dt.astimezone(TZ).date() < lower:
            return 'excluded'
        return 'usable'
    day = item.get('published_date', '')
    if not day or day > cutoff.date().isoformat() or day < lower.isoformat():
        return 'excluded'
    if day < cutoff.date().isoformat():
        return 'usable'
    first_seen = item.get('first_seen_at')
    if first_seen and datetime.fromisoformat(first_seen) <= cutoff:
        return 'usable'
    return 'unverified'


def market_close(day, market):
    zone, hour = ('Asia/Hong_Kong', 16) if market == 'HSI' else ('America/New_York', 16)
    dt = datetime.strptime(day.replace('-', ''), '%Y%m%d').replace(hour=hour, tzinfo=ZoneInfo(zone))
    return dt.astimezone(TZ)


def sector_summary(sector, members, current, previous, limits, suspended):
    valid = {code: current[code] for code in members if code in current}
    common = [code for code in valid if code in previous]
    prev_amount = sum(float(previous[c]['amount']) for c in common)
    amount_change = (sum(float(valid[c]['amount']) for c in common) / prev_amount - 1) * 100 if prev_amount else None
    changes = [float(r['pct_chg']) for r in valid.values() if r.get('pct_chg') is not None]
    missing = [c for c in members if c not in valid and c not in suspended]
    limit_unknown = [c for c in valid if c not in limits]
    leaders = sorted(valid.items(), key=lambda v: float(v[1].get('pct_chg') or 0), reverse=True)[:3]
    return {**sector, 'members': members, 'valid': len(valid), 'total': len(members),
            'up': sum(v > 0 for v in changes), 'down': sum(v < 0 for v in changes),
            'flat': sum(v == 0 for v in changes), 'missing': len(missing),
            'suspended': sum(c in suspended and c not in valid for c in members),
            'pct_change': sum(changes) / len(changes) if changes else None,
            'amount_change': amount_change, 'common_count': len(common),
            'limit_up': sum(abs(float(r['close']) - limits[c]) < 0.005 for c, r in valid.items() if c in limits),
            'limit_unknown': len(limit_unknown), 'leaders': [{'code': c, 'pct_change': float(r.get('pct_chg') or 0),
                                                           'amount': float(r['amount']) * 1000} for c, r in leaders],
            'basis': '有效成分股等权平均涨跌幅', 'risk': '资料不足' if missing else '一般关注'}
