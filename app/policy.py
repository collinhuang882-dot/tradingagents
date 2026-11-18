import io
import json
import re
import threading
from pathlib import Path
from datetime import datetime, timedelta
from urllib.parse import urljoin, urlparse
import httpx
from bs4 import BeautifulSoup
from pypdf import PdfReader
from . import storage
from .models import TZ, now
from .analytics import event_eligibility, market_close
from .providers import AKShare, ProviderError

SOURCES = {
    '国家发展改革委': 'https://www.ndrc.gov.cn/xxgk/zcfb/tz/',
    '中国证监会': 'https://www.csrc.gov.cn/searchList/625a2f95cc104c1d8b646f98d9f87470?_isAgg=true&_isJson=true&_pageSize=20&_template=index&page=1',
}
ALLOWED = {'www.ndrc.gov.cn', 'www.csrc.gov.cn'}
SYNC_LOCK = threading.Lock()
TOPIC_ALIASES = json.loads((Path(__file__).with_name('policy_topics.json')).read_text('utf-8'))


def fetch(url):
    # Validate every redirect. Only fixed government origins can be fetched by this collector.
    for _ in range(4):
        parts = urlparse(url)
        if parts.scheme != 'https' or parts.hostname not in ALLOWED or parts.port not in (None, 443):
            raise ProviderError('政策来源地址不在允许列表')
        from .agent_store import reserve
        reserve('network')
        response = httpx.get(url, timeout=18, follow_redirects=False, headers={'User-Agent': 'TradingAgentsResearch/1.0'})
        if response.is_redirect:
            url = urljoin(url, response.headers['location'])
            continue
        response.raise_for_status()
        if len(response.content) > 12_000_000:
            raise ProviderError('附件超过 12MB 限制')
        response.encoding = 'utf-8'
        return response
    raise ProviderError('来源重定向次数过多')


def parse_document(html, url, issuer, title='', listed_time=''):
    soup = BeautifulSoup(html, 'html.parser')
    metas = {str(m.get('name', '')).lower(): m.get('content', '') for m in soup.select('meta[name]')}
    title = metas.get('articletitle') or title or (soup.h1.get_text(strip=True) if soup.h1 else '')
    body = next((element for selector in ['.TRS_Editor', '#UCAP-CONTENT', '.Custom_UnionStyle', '.article-content', '.detail-news', '.content']
                 if (element := soup.select_one(selector)) is not None), None)
    if body:
        for tag in body.select('script,style,nav'):
            tag.decompose()
    text = body.get_text('\n', strip=True) if body else ''
    stamp = metas.get('pubdate') or metas.get('publishdate') or metas.get('firstpublishedtime') or listed_time
    if not stamp:
        match = re.search(r'(?:发布时间|发布日期|时间)[：:\s]*(\d{4}-\d{2}-\d{2}(?:\s+\d{2}:\d{2}(?::\d{2})?)?)', soup.get_text(' ', strip=True))
        stamp = match.group(1) if match else ''
    date_match = re.search(r'\d{4}-\d{2}-\d{2}', stamp)
    day = date_match.group() if date_match else ''
    published = None
    if day and re.search(r'\d{2}:\d{2}', stamp):
        try:
            parsed = datetime.fromisoformat(stamp.strip().replace('Z', '+00:00'))
            published = (parsed.replace(tzinfo=TZ) if parsed.tzinfo is None else parsed.astimezone(TZ)).isoformat()
        except ValueError:
            pass
    number = re.search(r'[\u4e00-\u9fff]{2,12}[〔\[〔\u3014]\d{4}[〕\]\u3015]\s*\d+\s*号', title + '\n' + text[:500])
    attachments = [urljoin(url, a['href']) for a in soup.select('a[href]') if a['href'].lower().endswith('.pdf')]
    kind = '征求意见稿' if '征求意见' in title else '解读' if '解读' in title else '正式文件'
    return {'title': title, 'issuer': issuer, 'document_no': number.group() if number else '',
            'body_text': text, 'published_date': day, 'published_at': published,
            'effective_date': None, 'first_seen_at': now().isoformat(), 'source_url': url,
            'source_urls': [url], 'attachments': attachments, 'kind': kind}


def sync_policies(force=False):
    if not SYNC_LOCK.acquire(blocking=False):
        return source_status()
    try:
        for issuer, url in SOURCES.items():
            old = next((r for r in source_status() if r['name'] == issuer), {})
            if not force and old.get('last_success') and now() - datetime.fromisoformat(old['last_success']) < timedelta(minutes=30):
                continue
            status = {'name': issuer, 'url': url, 'status': 'error', 'last_success': old.get('last_success'),
                      'checked_at': now().isoformat(), 'count': 0, 'coverage': '当前栏目最近 12 条文件，非全量历史档案'}
            try:
                response = fetch(url)
                if issuer == '中国证监会':
                    results = response.json()['data']['results']
                    links = [(urljoin('https://www.csrc.gov.cn', r['url']), r['title'], r.get('publishedTimeStr', '')) for r in results]
                else:
                    soup = BeautifulSoup(response.text, 'html.parser')
                    links = [(urljoin(url, a['href']), a.get_text(strip=True), '') for a in soup.select('a[href]')
                             if re.search(r'/t\d{8}_\d+\.html', a['href']) and len(a.get_text(strip=True)) > 8
                             and '/zcfb/tz/' in urljoin(url, a['href'])]
                unique = list(dict.fromkeys(links))[:12]
                if not unique:
                    raise ProviderError('栏目未解析到文件，需检查官网结构')
                failures = 0
                for link, title, stamp in unique:
                    try:
                        doc = parse_document(fetch(link).text, link, issuer, title, stamp)
                        if len(doc['body_text']) < 100 and doc['attachments']:
                            try:
                                pdf = PdfReader(io.BytesIO(fetch(doc['attachments'][0]).content))
                                doc['body_text'] += '\n' + '\n'.join(page.extract_text() or '' for page in pdf.pages[:50])
                            except Exception:
                                doc['body_note'] = '附件正文不可用，可查看原文链接'
                        if not doc['published_date']:
                            failures += 1
                        if not doc['body_text'].strip():
                            failures += 1
                            doc['body_note'] = '正文不可用，可查看原文链接'
                        storage.policy_upsert(doc)
                        status['count'] += 1
                    except Exception:
                        failures += 1
                status.update(status='limited' if failures else 'ok', last_success=now().isoformat(),
                              note=f'已保存 {status["count"]} 条，{failures} 条存在获取或时间缺口')
            except Exception:
                status['note'] = '官方来源获取失败，请检查网络或稍后重试；保留上次已采集资料'
            with storage.db() as c:
                c.execute('INSERT OR REPLACE INTO sources VALUES(?,?)', (issuer, storage.dumps(status)))
        return source_status()
    finally:
        SYNC_LOCK.release()


def source_status():
    with storage.db() as c:
        return [json.loads(r[0]) for r in c.execute('SELECT content FROM sources')]


def search_policies(topics, cutoff, lookback):
    with storage.db() as c:
        rows = [json.loads(r[0]) for r in c.execute('SELECT content FROM policies')]
    candidates = []
    versions = {}
    for row in rows:
        identity = row['issuer'] + ':' + (row.get('document_no') or row['source_url'])
        versions.setdefault(identity, []).append(row)
    selected_rows = []
    cutoff_dt = datetime.fromisoformat(cutoff)
    for group in versions.values():
        observed = [r for r in group if datetime.fromisoformat(r['first_seen_at']) <= cutoff_dt]
        if observed:
            selected_rows.append(max(observed, key=lambda r: r['first_seen_at']))
        else:
            # A later discovered original may use its publication date, but never a known revision.
            selected_rows.append(min(group, key=lambda r: r['first_seen_at']))
    for row in selected_rows:
        quality = event_eligibility(row, cutoff, lookback)
        if quality == 'excluded':
            continue
        text = row['title'] + '\n' + row['body_text']
        matched = [t for t in topics if t and any(term in text for term in [t] + TOPIC_ALIASES.get(t, []))]
        if not matched and not any(t in text for t in ['资本市场', '上市公司', '证券市场', '宏观']):
            continue
        candidates.append({**row, 'quality': quality, 'matched_topics': matched, 'score': len(matched)})
    candidates.sort(key=lambda x: (x['score'], x['published_date'], x['first_seen_at']), reverse=True)
    # Retain the newest observed version that was available at cutoff for each logical document.
    unique = {}
    for row in candidates:
        identity = row['issuer'] + ':' + (row['document_no'] or row['source_url'])
        unique.setdefault(identity, row)
    return list(unique.values())[:20], len(unique)


def demo_events(request, topics):
    cutoff = datetime.fromisoformat(request['as_of'])
    rows = []
    for i, topic in enumerate(topics[:5] or ['资本市场']):
        rows.append({'id': f'demo-policy-{i}', 'title': f'关于 {topic} 行业发展的样例通知（虚构）',
                     'issuer': '样例发布机关', 'document_no': f'演示〔2026〕{i+1}号', 'kind': '正式文件',
                     'published_date': cutoff.date().isoformat(), 'published_at': (cutoff - timedelta(hours=1)).isoformat(),
                     'first_seen_at': (cutoff - timedelta(minutes=50)).isoformat(), 'effective_date': None,
                     'body_text': f'第一条：本条为演示文本，用于验证 {topic} 相关政策的来源展示和证据引用，不代表真实政策。',
                     'source_url': '', 'source_urls': [], 'quality': 'usable', 'matched_topics': [topic]})
    return rows


def collect_events(request, topics):
    if request['mode'] == 'demo':
        events = demo_events(request, topics)
        return {'events': events, 'matched_count': len(events), 'issues': [], 'sources': [],
                'markets': [{'id': code, 'name': name, 'close': value, 'pct_change': change,
                             'trade_date': (datetime.fromisoformat(request['as_of']) - timedelta(days=1)).date().isoformat(),
                             'source': '样例数据'} for code, name, value, change in [('SPX', '标普500', 6100, .62), ('IXIC', '纳斯达克综合', 19200, -.24), ('HSI', '恒生指数', 22100, .38)]]}
    sources = sync_policies()
    events, total = search_policies(topics, request['as_of'], request['lookback'])
    issues = []
    coverage_notes = ['官方政策检索范围为已采集栏目最近文件，无法保证完整历史覆盖。']
    for source in sources:
        if source['status'] != 'ok':
            issues.append(f'{source["name"]}：{source["note"]}')
    markets, market_issues = collect_markets(request)
    announcements, announcement_issues = collect_announcements(request)
    events.extend(announcements)
    issues.extend(market_issues + announcement_issues)
    matched_count = total + sum(e['kind'] == '公司公告' for e in events)
    events.sort(key=lambda e: (e.get('score', 0), e.get('published_at') or e['published_date']), reverse=True)
    return {'events': events[:20], 'matched_count': matched_count, 'markets': markets, 'sources': sources, 'issues': issues, 'coverage_notes': coverage_notes}


def collect_markets(request):
    issues = []
    markets = []
    client = AKShare()
    end = request['trade_date'].replace('-', '')
    start = (datetime.fromisoformat(request['as_of']) - timedelta(days=14)).strftime('%Y%m%d')
    for code, name in [('SPX', '标普500'), ('IXIC', '纳斯达克综合'), ('HSI', '恒生指数')]:
        try:
            rows = client.global_history(code, start, end)
            valid = [r for r in rows if market_close(r['trade_date'], code) <= datetime.fromisoformat(request['as_of'])]
            if not valid:
                raise ProviderError('截止前无已收盘行情')
            row = max(valid, key=lambda r: r['trade_date'])
            markets.append({'id': code, 'name': name, 'trade_date': row['trade_date'], 'close': row['close'],
                            'pct_change': row.get('pct_chg'), 'close_at': market_close(row['trade_date'], code).isoformat(), 'source': 'AKShare/新浪财经'})
        except ProviderError as exc:
            issues.append(f'{name}：{exc}')
    return markets, issues


def collect_announcements(request):
    events, issues = [], []
    client = AKShare()
    announcement_start = (datetime.fromisoformat(request['as_of']).date() - timedelta(days=request['lookback'])).isoformat()
    announcements, announcement_issues = client.announcements(request['stocks'], announcement_start, request['trade_date'])
    issues.extend(announcement_issues)
    for row in announcements:
        code = row['ts_code']
        document = {'id': storage.digest(row), 'title': row['title'], 'issuer': row.get('name', code),
                    'kind': '公司公告', 'published_date': datetime.strptime(row['ann_date'], '%Y%m%d').date().isoformat(),
                    'published_at': None, 'first_seen_at': now().isoformat(), 'body_text': '',
                    'source_url': row['url'], 'subject': code, 'matched_topics': [code]}
        quality = event_eligibility(document, request['as_of'], request['lookback'])
        if quality != 'excluded':
            events.append({**document, 'quality': quality})
    return events, issues
