import copy
import json
import time
from . import storage, engine, agent_store as store
from .agent_models import Plan
from .agent_tools import execute_task, task_evidence, SOURCES, ANALYSES
from .planner import propose, demo_plan
from .providers import ProviderError
from .models import TERMINAL

DONE = {'succeeded', 'abstained', 'failed', 'skipped'}


def apply_plan(rid, state, value):
    plan = Plan.model_validate(value)
    if plan.base_plan_version != state['version']:
        raise ValueError('计划版本已过期')
    candidate = copy.deepcopy(state)
    request = storage.get_run(rid)['request']
    from .investment_research import enabled, DATA_KINDS, KINDS
    deep = enabled(storage.get_run(rid))
    ids = {t['id'] for t in candidate['tasks']}
    def invalidate(tid):
        for t in candidate['tasks']:
            if t['id'] == tid or tid in t['depends_on']:
                if t['status'] == 'running':
                    raise ValueError('不能修改运行中的任务')
                if t['status'] != 'superseded':
                    t['status'] = 'superseded'
                    invalidate(t['id']) if t['id'] != tid else None
    if any(a.type == 'finish' for a in plan.actions) and len(plan.actions) != 1:
        raise ValueError('finish 不能与其他动作混用')
    for action in plan.actions:
        if action.type == 'finish':
            if any(t['status'] in ('pending', 'running') for t in candidate['tasks']):
                raise ValueError('仍有未处理任务，不能结束')
            required = {'analyze_policy', 'argue_bull', 'argue_bear', 'review_risk'}
            if request['stocks']: required.add('analyze_market')
            if request['sectors']: required.add('analyze_sector')
            attempted = {t['kind'] for t in candidate['tasks'] if t['status'] in DONE}
            if deep: required = KINDS
            if (deep or not request.get('research_question')) and required - attempted and any((t.get('result') or {}).get('stocks') or (t.get('result') or {}).get('sectors') for t in candidate['tasks']):
                raise ValueError('已有可用行情，结束前必须尝试这些分析职责：' + ','.join(sorted(required-attempted)))
            candidate['stop_reason'] = action.reason
        elif action.type == 'skip_task':
            target = next((t for t in candidate['tasks'] if t['id'] == action.task_id), None)
            if not target or target['status'] != 'pending':
                raise ValueError('只能跳过未开始任务')
            target['status'] = 'skipped'
        else:
            task = action.task.model_dump()
            if deep and task['kind'] in ANALYSES and any(t['kind'] in DATA_KINDS and t['status'] in ('pending','running') for t in candidate['tasks']):
                raise ValueError('V4 必须先完成取证，再基于真实结果组织分析，不得预排论证')
            if request.get('data_policy') == 'local_only' and task['kind'] in DATA_KINDS:
                raise ValueError('仅现有资料模式禁止外部取证')
            if task.get('dispute_id'):
                debates = [t for t in candidate['tasks'] if t['kind'] == 'rebut_theses' and t['status'] == 'succeeded']
                allowed_disputes = {d['dispute_id'] for t in debates for d in (t.get('result') or {}).get('disputes', [])}
                if task['dispute_id'] not in allowed_disputes or task['kind'] not in DATA_KINDS:
                    raise ValueError('争议补查必须绑定真实争议和数据工具')
                lookups = [t for t in candidate['tasks'] if t.get('dispute_id')]
                if len(lookups) >= 4 or sum(t['dispute_id'] == task['dispute_id'] for t in lookups) >= 2:
                    raise ValueError('本轮争议补查次数达到上限')
            if task['id'] in ids:
                raise ValueError('任务 ID 重复，修订也必须使用新 ID')
            if not set(task['subjects']) <= set(request['stocks'] + request['sectors']):
                raise ValueError('任务对象超出范围')
            if any(not q.strip() or len(q) > 40 for q in task['query_terms']):
                raise ValueError('政策关键词必须为1至40字')
            if task['query_terms'] and task['kind'] != 'search_policy':
                raise ValueError('只有政策检索任务可指定关键词')
            if task['kind'] in ('fetch_market', 'fetch_sector', 'search_policy', 'fetch_global', 'search_announcement'):
                source = SOURCES.get(task['source_id'])
                if not source or source['kind'] != task['kind']:
                    raise ValueError('没有兼容的注册来源')
            if task['kind'] == 'fetch_market' and (not task['subjects'] or not set(task['subjects']) <= set(request['stocks'])):
                raise ValueError('日线对象无效')
            if task['kind'] == 'fetch_sector' and (not task['subjects'] or not set(task['subjects']) <= set(request['sectors'])):
                raise ValueError('行业对象无效')
            if task['kind'] == 'fetch_document':
                from urllib.parse import urlparse
                from .policy import ALLOWED
                u = urlparse(task['url'])
                if u.scheme != 'https' or u.hostname not in ALLOWED or u.port not in (443, None) or u.username:
                    raise ValueError('正文地址未授权')
                observed = {e.get('source_url') for t in candidate['tasks'] for e in (t.get('result') or {}).get('events', [])}
                observed.update(u for t in candidate['tasks'] for e in (t.get('result') or {}).get('events', []) for u in e.get('attachments', []))
                observed.update(request.get('document_urls', []))
                if task['url'] not in observed:
                    raise ValueError('正文地址未出现在已获取资料或用户输入中')
            if task['kind'] in ANALYSES and not task['depends_on']:
                raise ValueError('分析必须依赖取证或前置分析')
            retry_target = None
            if action.type == 'revise_task':
                if action.task_id not in ids:
                    raise ValueError('修订的任务不存在')
                retry_target = next(t for t in candidate['tasks'] if t['id'] == action.task_id)
                retry_target = copy.deepcopy(retry_target)
                invalidate(action.task_id)
            identity = {k: task[k] for k in ('kind', 'subjects', 'source_id', 'depends_on', 'url', 'query_terms')}
            identity['subjects'] = sorted(identity['subjects'])
            identity['depends_on'] = sorted(identity['depends_on'])
            identity['requirement_version'] = state.get('requirement_version', 0)
            signature = storage.digest(identity)
            previous = next((t for t in candidate['tasks'] if t.get('signature') == signature), None)
            if retry_target and retry_target['status'] == 'failed' and previous and previous['id'] == retry_target['id']:
                if sum(t.get('signature') == signature for t in candidate['tasks']) >= 2:
                    raise ValueError('相同输入的失败任务已重试，请补充证据或结束')
                previous = None
            if previous and previous['status'] not in DONE:
                raise ValueError('重复任务：原任务仍待执行或已经被替代')
            task.update(status=previous['status'] if previous else 'pending', signature=signature,
                        result=copy.deepcopy(previous.get('result')) if previous else None,
                        error=previous.get('error', '') if previous else '', reason=action.reason,
                        reused_from=previous['id'] if previous else None,
                        requirement_version=state.get('requirement_version', 0))
            candidate['tasks'].append(task)
            ids.add(task['id'])
    if len(candidate['tasks']) > 40 or (state['version'] == 0 and len(candidate['tasks']) > 12):
        raise ValueError('累计任务数量超出预算')
    by_id = {t['id']: t for t in candidate['tasks']}
    visited, visiting = set(), set()
    def visit(tid):
        if tid in visiting:
            raise ValueError('依赖环路')
        if tid in visited: return
        if tid not in by_id or by_id[tid]['status'] == 'superseded':
            raise ValueError('依赖不存在或已被替代')
        visiting.add(tid)
        for parent in by_id[tid]['depends_on']: visit(parent)
        visiting.remove(tid); visited.add(tid)
    for t in candidate['tasks']:
        if t['status'] != 'superseded':
            visit(t['id'])
            dep_kinds = {by_id[d]['kind'] for d in t['depends_on']}
            if t['kind'] == 'review_risk' and not {'argue_bull', 'argue_bear'} <= dep_kinds:
                raise ValueError('风险审查必须依赖多空结果')
            if deep:
                if t['kind'] in ('argue_bull', 'argue_bear') and dep_kinds & KINDS:
                    raise ValueError('首轮多空必须独立读取共享事实')
                if t['kind'] == 'rebut_theses' and not {'argue_bull', 'argue_bear'} <= dep_kinds:
                    raise ValueError('交叉反驳必须依赖双方论点')
                if t['kind'] == 'adjudicate_theses' and not {'argue_bull', 'argue_bear', 'rebut_theses'} <= dep_kinds:
                    raise ValueError('裁决必须依赖双方论点及反驳')
                if t['kind'] == 'review_risk' and 'adjudicate_theses' not in dep_kinds:
                    raise ValueError('V4风险检查必须读取裁决')
    if not candidate.get('stop_reason') and all(t['status'] != 'pending' for t in candidate['tasks']):
        candidate['no_progress'] += 1
    candidate['version'] += 1
    store.save(rid, candidate, plan=True)
    for a in plan.actions:
        store.action(rid, a.type, a.reason, plan_version=candidate['version'], action=a.model_dump())
    return candidate


def materialize(rid, state):
    request = storage.get_run(rid)['request']
    stocks, sectors, evidences, events, markets, notes, issues = {}, {}, {}, {}, {}, [], []
    analysis = {key: {'claims': [], 'note': '尚未形成分析', 'failed': True} for key in ('market', 'sector', 'policy', 'bull', 'bear', 'risk')}
    if not request['stocks']: analysis['market'] = {'claims': [], 'note': '未选择股票'}
    if not request['sectors']: analysis['sector'] = {'claims': [], 'note': '未选择行业'}
    if request.get('research_question'):
        analysis = {key: {'claims': [], 'note': '本轮按问题取证，未要求该项完整分析'} for key in analysis}
    sources = {}
    active = [t for t in state['tasks'] if t['status'] not in ('superseded', 'skipped')]
    if request.get('research_question'):
        for index, task in enumerate(active):
            if task['status'] != 'failed': continue
            recovered = any(later['kind'] == task['kind'] and later['status'] == 'succeeded'
                            and set(later['subjects']) == set(task['subjects'])
                            and any(e.get('quality') == 'usable' for e in (later.get('result') or {}).get('evidence', []))
                            for later in active[index+1:])
            if not recovered: issues.append(task.get('error') or task['kind'] + ' 执行失败')
        if str(state.get('stop_reason','')).startswith(('invalid_plan', 'system_error')):
            issues.append('补查未完成：' + state['stop_reason'])
    latest_data = {}
    for t in active:
        result = t.get('result') or {}
        if t['status'] == 'failed' and t['kind'] in ANALYSES:
            analysis[ANALYSES[t['kind']][0]] = {'claims': [], 'note': t.get('error') or '分析执行失败', 'failed': True}
        if t['status'] in ('succeeded', 'abstained'):
            for s in result.get('stocks', []): stocks[s['id']] = s
            for s in result.get('sectors', []): sectors[s['id']] = s
            for e in result.get('evidence', []): evidences[e['id']] = e
            for e in result.get('events', []): events[e.get('id') or e['source_url']] = e
            for m in result.get('markets', []): markets[m['id']] = m
            if t['kind'] in ('fetch_market', 'fetch_sector', 'search_policy', 'fetch_global', 'search_announcement'):
                for subject in t['subjects'] or ['global']:
                    latest_data[(t['kind'], subject)] = result
            for source in result.get('sources', []): sources[source['name']] = source
            notes.extend(result.get('coverage_notes', []))
            if t['kind'] in ('compare_sources', 'validate_data'): issues.extend(result.get('issues', []))
            if t['kind'] in ANALYSES:
                key = ANALYSES[t['kind']][0]
                analysis[key] = {**result, 'failed': result.get('outcome') == 'abstained'}
    for result in latest_data.values(): issues.extend(result.get('issues', []))
    for code in request['stocks']:
        if not request.get('research_question') and (code not in stocks or not stocks[code]['available']): issues.append(code + ' 当日行情缺失')
    for sid in request['sectors']:
        if not request.get('research_question') and sid not in sectors: issues.append(sid + ' 当日行业行情缺失')
    if not request.get('research_question') and not any(t['kind'] == 'search_policy' and t['status'] == 'succeeded' for t in active):
        issues.append('政策公告与外围检索未完成')
    # Source disagreement is checked even if the planner omitted a comparison task.
    prices = {}
    for e in evidences.values():
        if e['kind'] == '行情' and e['quality'] in ('usable','conflicting'):
            for field in ('close', 'pct_change'):
                v = e['value'].get(field)
                if v is not None: prices.setdefault((e['subject'], field), []).append(v)
    conflict_subjects = set()
    for (subject, field), values in prices.items():
        if max(values) - min(values) > .0100001:
            issues.append(f'{subject} {field} 来源冲突，尚未解决')
            conflict_subjects.add(subject)
    # A later source with no percentage must not erase a verified same-day quote.
    for code, stock in stocks.items():
        if stock.get('pct_change') is not None or not stock.get('available') or code in conflict_subjects:
            continue
        quote = next((e for e in evidences.values() if e['kind'] == '行情' and e['subject'] == code
                      and e['quality'] == 'usable' and e['value'].get('date') == request['trade_date']
                      and e['value'].get('pct_change') is not None and e['value'].get('close') is not None
                      and stock.get('close') is not None and abs(e['value']['close'] - stock['close']) < .005), None)
        if quote:
            stock = copy.deepcopy(stock)
            stock['pct_change'] = quote['value']['pct_change']
            stock['pct_change_source'] = quote['id']
            stocks[code] = stock
            issues = [i for i in issues if i != code + '：备用来源未提供交易所当日涨跌幅，该字段显示不可用']
            notes.append(f'{code} 涨跌幅使用同日收盘核对一致的 {quote["source"]} 证据 {quote["id"]}')
    conflicting_ids = {e['id'] for e in evidences.values() if e['kind'] == '行情' and e['subject'] in conflict_subjects}
    if conflicting_ids:
        for e in evidences.values():
            if e['id'] in conflicting_ids: e['quality'] = 'conflicting'
        for result in analysis.values():
            original = result.get('claims', [])
            result['claims'] = [c for c in original if not set(c['evidence_ids']) & conflicting_ids]
            if len(result['claims']) != len(original):
                result.update(failed=True, note='引用行情存在未解决冲突，暂停相关结论')
    if request.get('user_material'):
        sid = storage.snapshot({'source': 'user_supplied', 'text': request['user_material']})
        e = engine.evidence(rid, 'user-material', '用户资料', 'global', '用户补充材料（未核验）', request['user_material'], sid, 'user_supplied', 'limited')
        evidences[e['id']] = e
        notes.append('用户补充资料未核验来源与时间，不作为官方事实。')
    gaps = list(dict.fromkeys(issues + [f'{k}：{v.get("note", "分析缺失")}' for k, v in analysis.items() if v.get('failed')]))
    return {'run_id': rid, 'data': {'stocks': list(stocks.values()), 'sectors': list(sectors.values()),
            'evidence': list(evidences.values()), 'issues': list(dict.fromkeys(issues)), 'coverage_notes': list(dict.fromkeys(notes))},
            'policy': {'events': list(events.values()), 'markets': list(markets.values()), 'evidence': [],
                       'issues': [], 'analysis': analysis.pop('policy'), 'sources': list(sources.values())},
            **analysis, 'gaps': gaps}


def consume_changes(rid, state):
    with storage.db() as c:
        c.execute('BEGIN IMMEDIATE')
        if storage.get_run(rid)['cancel']: return state
        rows = c.execute("SELECT * FROM conversation_changes WHERE run_id=? AND status='pending' ORDER BY rowid", (rid,)).fetchall()
        if not rows: return state
        for row in rows:
            change = json.loads(row['content'])
            state['focus'] = change['focus']
            state['requirement_version'] = change['version']
            for task in state['tasks']:
                if task['kind'] in ANALYSES and task['status'] != 'running': task['status'] = 'superseded'
            invalidating = True
            while invalidating:
                invalidating = False
                old_ids = {t['id'] for t in state['tasks'] if t['status']=='superseded'}
                for task in state['tasks']:
                    if task['status'] != 'superseded' and set(task['depends_on']) & old_ids:
                        task['status'] = 'superseded'; invalidating = True
            c.execute("UPDATE conversation_changes SET status='applied' WHERE id=?", (row['id'],))
            c.execute('INSERT INTO events(run_id,stage,event_type,status,created,detail) VALUES(?,?,?,?,?,?)',
                      (rid, '自主规划', 'user_change', 'completed', __import__('datetime').datetime.now().isoformat(),
                       storage.dumps({'message': '已应用新的分析重点', 'change_id': row['id'], 'requirement_version': change['version']})))
        state['stop_reason'] = ''
        c.execute('INSERT OR REPLACE INTO agent_states VALUES(?,?)', (rid, storage.dumps(state)))
    return state


def cancel_state(rid, state):
    for task in state['tasks']:
        if task['status'] in ('running', 'pending'):
            task['status'] = 'cancelled'
            storage.stage_status(rid, task['id'], 'skipped', '用户取消')
    store.save(rid,state)
    storage.update_run(rid,status='cancelled')


def execute(rid):
    store.begin(rid)
    state = store.load(rid)
    request = storage.get_run(rid)['request']
    from .investment_research import enabled, next_plan, artifact as research_artifact, DATA_KINDS
    deep = enabled(storage.get_run(rid))
    planning_limit = 16 if deep else 8
    state.setdefault('focus', request.get('focus', []))
    if not state['tasks'] and request.get('parent_run_id'):
        parent = storage.get_run(request['parent_run_id'])
        if parent and all(parent['request'].get(k) == request.get(k) for k in ('stocks', 'sectors', 'trade_date', 'as_of', 'mode')):
            for task in store.load(parent['id'])['tasks']:
                if task['kind'] in (DATA_KINDS if deep else ('fetch_market', 'fetch_sector')) and task['status'] == 'succeeded':
                    copied = copy.deepcopy(task)
                    copied['depends_on'] = []
                    copied['dispute_id'] = ''
                    copied['reason'] = '复用同范围、同截止时间和同配置的已核验数据快照'
                    state['tasks'].append(copied)
            store.save(rid, state)

    if deep and not state['tasks'] and request.get('parent_run_id'):
        from .investment_research import seed_parent
        state['tasks'] = seed_parent(request)
        store.save(rid, state)

    for t in state['tasks']:
        if t['status'] == 'running': t['status'] = 'pending'
    storage.update_run(rid, status='analyzing', error='', state={'deadline': time.time()+(1800 if deep else 900)})
    try:
        with store.context('run:' + rid):
            while True:
                if storage.get_run(rid)['cancel']: raise engine.Cancelled()
                state = consume_changes(rid, state)
                current = materialize(rid, state)
                ready = [t for t in state['tasks'] if t['status'] == 'pending' and
                         all(next(p for p in state['tasks'] if p['id']==d)['status'] in DONE for d in t['depends_on'])]
                if ready:
                    for t in sorted(ready, key=lambda x: -x['priority']):
                        if storage.get_run(rid)['cancel']: raise engine.Cancelled()
                        t['input_refs'] = sorted({e['snapshot_id'] for e in task_evidence(t, state['tasks'])})
                        parents = sorted(storage.digest(p.get('result')) for p in state['tasks'] if p['id'] in t['depends_on'])
                        t['execution_signature'] = storage.digest([t['kind'], sorted(t['subjects']), t['source_id'], t['url'], t.get('query_terms', []), t['input_refs'], parents, state['focus']])
                        reusable = next((p for p in state['tasks'] if p['id'] != t['id'] and p.get('execution_signature') == t['execution_signature'] and p['status'] in ('succeeded', 'abstained')), None)
                        if reusable:
                            t.update(status=reusable['status'], result=copy.deepcopy(reusable.get('result')), error=reusable.get('error',''), reused_from=reusable['id'])
                            store.save(rid,state)
                            store.action(rid,'task_reused','复用相同输入的已处理任务',task_id=t['id'],original_task_id=reusable['id'])
                            continue
                        t['status'] = 'running'; t['started_at'] = time.time(); store.save(rid, state)
                        storage.stage_status(rid, t['id'], 'running', t['reason'])
                        try:
                            started = time.monotonic()
                            t['result'] = execute_task(rid, t, state['tasks'], state['focus'])
                            t['result'].update(task_id=t['id'], source_id=t['source_id'],
                                duration_ms=round((time.monotonic()-started)*1000),
                                status='limited' if t['result'].get('issues') or t['result'].get('gaps') else 'ok',
                                requested_range={'trade_date':request['trade_date'],'as_of':request['as_of']})
                            t['status'] = 'abstained' if t['result'].get('outcome') == 'abstained' else 'succeeded'
                        except ProviderError as exc:
                            t['status'], t['error'] = 'failed', str(exc)
                            t['error_code'] = 'timeout' if '超时' in str(exc) else 'schema_changed' if '格式' in str(exc) else 'unavailable'
                            t['result'] = {'status':'error','error_code':t['error_code'],'retryable':t['error_code']=='timeout'}
                        except (ValueError, KeyError, TypeError) as exc:
                            t['status'], t['error'] = 'failed', '工具数据结构校验失败：' + type(exc).__name__
                        if storage.get_run(rid)['cancel']: raise engine.Cancelled()
                        store.save(rid, state)
                        storage.stage_status(rid, t['id'], 'completed' if t['status']=='succeeded' else 'failed', t['error'])
                        store.action(rid, 'task_result', t['error'] or '任务已处理', task_id=t['id'], status=t['status'])
                    current = materialize(rid, state)
                    progress = storage.digest([sorted(e['id'] for e in current['data']['evidence']), current['gaps'], [(t['id'],t['status']) for t in state['tasks'] if deep and t['status'] in DONE]])
                    state['no_progress'] = state['no_progress'] + 1 if progress == state.get('progress') else 0
                    state['progress'] = progress
                    # Drain the accepted plan before requesting another plan, unless a new failure needs a repair.
                    next_ready = any(t['status'] == 'pending' and all(next(p for p in state['tasks'] if p['id']==d)['status'] in DONE for d in t['depends_on']) for t in state['tasks'])
                    if next_ready and not any(t['status'] == 'failed' and t['kind'] in ('fetch_market', 'fetch_sector') for t in ready):
                        continue
                if state.get('stop_reason'): break
                if state['rounds'] >= planning_limit or state['no_progress'] >= 2:
                    state['stop_reason'] = 'planning_budget' if state['rounds'] >= planning_limit else 'no_progress'; break
                state['budget'] = store.budget('run:' + rid)
                failure = ''
                for attempt in range(2):
                    if state['rounds'] >= planning_limit: raise store.BudgetExceeded('规划轮次预算耗尽')
                    state['rounds'] += 1; store.save(rid, state)
                    try:
                        plan = next_plan(rid, state)
                        if plan is None:
                            plan = demo_plan(request, state, current['gaps']) if request['mode']=='demo' else propose(rid, request, state, current['gaps'], failure)
                        if deep and any(a.get('task', {}).get('dispute_id') for a in plan['actions']):
                            for action in plan['actions']:
                                try:
                                    state = apply_plan(rid, state, {'base_plan_version': state['version'], 'actions': [action]})
                                except ValueError as exc:
                                    state.setdefault('research_lookup_rejections', []).append({'task_id': action['task']['id'], 'dispute_id': action['task']['dispute_id'], 'reason': str(exc)})
                                    store.action(rid, 'research_lookup_rejected', str(exc), task_id=action['task']['id'])
                            store.save(rid, state)
                            break
                        store.action(rid, 'plan_proposed', '模型提出计划变更' if request['mode']=='live' else '演示计划', proposal=plan)
                        state = apply_plan(rid, state, plan)
                        break
                    except ValueError as exc:
                        failure = str(exc)[:800]
                        store.action(rid, 'plan_rejected', failure)
                        if attempt: raise ProviderError('invalid_plan：' + failure)
    except engine.Cancelled:
        cancel_state(rid, state); store.end(rid); return
    except (ProviderError, store.BudgetExceeded) as exc:
        state['stop_reason'] = str(exc)
    except Exception as exc:
        state['stop_reason'] = 'system_error:' + type(exc).__name__
    try:
        if storage.get_run(rid)['cancel']: raise engine.Cancelled()
        store.save(rid, state)
        current = materialize(rid, state)
        if request.get('research_question') and not any((t.get('result') or {}).get('evidence') for t in state['tasks'] if t['status'] in ('succeeded','abstained')):
            raise ProviderError('补查没有取得可核验资料：' + state.get('stop_reason', ''))
        if not request.get('research_question') and not current['data']['stocks'] and not current['data']['sectors']:
            raise ProviderError('没有可用行情报告：' + state.get('stop_reason', ''))
        current['metadata'] = {'schema_version': 2, 'execution_mode': 'autonomous', 'plan_version': state['version'],
            'requirement_version': state.get('requirement_version', 0), 'focus': state['focus'],
            'conversation_id': request.get('conversation_id'), 'parent_report_id': request.get('parent_run_id'),
            'decision_summary': [{'task_id': t['id'], 'reason': t['reason'], 'source_id': t['source_id'], 'status': t['status']} for t in state['tasks']],
            'unresolved_gaps': current['gaps'], 'stop_reason': state.get('stop_reason') or 'coverage_complete'}
        if deep:
            research = research_artifact(request, state)
            current['metadata'].update(schema_version=4, investment_research=research)
            if research['research_status'] != 'supported' or research['execution_status'] != 'completed':
                current.setdefault('extra_issues', []).append('深入研究受限：' + research['adjudication']['decision_basis'])
        # Report publication and pending-change check share a write lock with message changes.
        with storage.db() as c:
            c.execute('BEGIN IMMEDIATE')
            pending = c.execute("SELECT * FROM conversation_changes WHERE run_id=? AND status='pending' ORDER BY rowid", (rid,)).fetchall()
            for change in pending:
                detail = json.loads(change['content'])
                followup_request = {**request, 'focus': detail['focus'], 'parent_run_id': rid,
                                    'conversation_id': change['conversation_id']}
                followup = storage.create_run(followup_request, 'freeze:' + change['id'], connection=c)
                detail['followup_run_id'] = followup['id']
                c.execute("UPDATE conversation_changes SET status='applied',content=? WHERE id=?", (storage.dumps(detail), change['id']))
                conv_row = c.execute('SELECT content FROM conversations WHERE id=?', (change['conversation_id'],)).fetchone()
                if conv_row:
                    conv = json.loads(conv_row[0]);conv.update(run_id=followup['id'],report_id=None,requirements=followup_request)
                    c.execute('UPDATE conversations SET content=? WHERE id=?', (storage.dumps(conv), conv['id']))
                current['data']['coverage_notes'].append('收尾时收到的新要求已创建关联补充任务：' + followup['id'])
            c.execute("UPDATE runs SET status='generating' WHERE id=?", (rid,))
        engine.report_node(current)
    except engine.Cancelled:
        cancel_state(rid, state)
    except Exception as exc:
        storage.update_run(rid, status='failed', error=str(exc) if isinstance(exc, ProviderError) else '报告整合失败')
    finally:
        store.end(rid)
