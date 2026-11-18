import copy
import io
import json
import zipfile
from pathlib import Path
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle
from docx import Document
from docx.shared import Inches, Pt, Cm
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from . import storage


def filter_report(report, filters):
    r = copy.deepcopy(report)
    if r.get('investment_research'):
        research = r['investment_research']
        original_count = len(research['theses'])
        research['theses'] = [t for t in research['theses'] if all(filters.get('investment_'+field,'all') in ('all',t[key])
                             for field,key in [('side','side'),('status','status'),('subject','subject'),('type','claim_type')])]
        research['view'] = {'filters':{k:v for k,v in filters.items() if k.startswith('investment_')},
                            'hidden_count':original_count-len(research['theses']), 'source_artifact_id':report['id']}
    stock_ids, sector_ids, risks = filters.get('stocks', []), filters.get('sectors', []), filters.get('risks', [])
    q = filters.get('q', '').strip().lower()
    scoped = bool(stock_ids or sector_ids or risks)
    def match(item):
        if stock_ids and item['id'] not in stock_ids:
            return False
        if sector_ids and not set(item.get('sector_ids', [item['id']])) & set(sector_ids):
            return False
        if risks and item.get('risk') not in risks:
            return False
        return not q or q in (item['name'] + item['id'] + item.get('sector', '')).lower()
    r['stocks'] = [x for x in r['stocks'] if match(x)]
    selected_stock_ids = {s['id'] for s in r['stocks']}
    r['sectors'] = [s for s in r['sectors'] if (not sector_ids or s['id'] in sector_ids)
                    and (not stock_ids or bool(set(s['members']) & selected_stock_ids))
                    and (not risks or s.get('risk') in risks)
                    and (not q or q in (s['name'] + s['id']).lower())]
    selected = selected_stock_ids | {s['id'] for s in r['sectors']}
    evidence_subjects = {e['id']: e for e in report['evidence']}
    def claim_matches(claim):
        if not scoped:
            return True
        if claim['subject'] in selected:
            return True
        return any(set(evidence_subjects[e].get('linked_subjects', [])) & selected for e in claim['evidence_ids'] if e in evidence_subjects)
    for result in r['analysis'].values():
        result['claims'] = [c for c in result.get('claims', []) if claim_matches(c)]
    references = {eid for a in r['analysis'].values() for c in a.get('claims', []) for eid in c['evidence_ids']}
    # The original overview is preserved and explicitly labelled in filtered exports.
    references |= {eid for claim in r['summary'] for eid in claim['evidence_ids']}
    references |= research_references(r.get('investment_research', {}))
    r['evidence'] = [e for e in r['evidence'] if e['id'] in references or not scoped or e['subject'] in selected
                     or bool(set(e.get('linked_subjects', [])) & selected)]
    event_ids = {e['document']['id'] for e in r['evidence'] if e.get('document') and
                 (not scoped or e['subject'] in selected or bool(set(e.get('linked_subjects', [])) & selected))}
    def event_matches(e):
        if scoped and e['id'] not in event_ids:
            return False
        if filters.get('issuers') and e['issuer'] not in filters['issuers']:
            return False
        if filters.get('kinds') and e['kind'] not in filters['kinds']:
            return False
        day = e.get('published_date', '')
        if filters.get('event_start') and day < filters['event_start']:
            return False
        if filters.get('event_end') and day > filters['event_end']:
            return False
        body = filters.get('body_available')
        if body and bool(e.get('body_text')) != (body == 'yes'):
            return False
        searchable = e['title'] + ' ' + e['issuer']
        if filters.get('fulltext'):
            searchable += ' ' + e.get('body_text', '')
        if q and q not in searchable.lower():
            return False
        return True
    r['events'] = [e for e in r['events'] if event_matches(e)]
    r['visible_evidence'] = [e for e in r['evidence'] if
                            (not filters.get('sources') or e['source'] in filters['sources']) and
                            (not filters.get('qualities') or e['quality'] in filters['qualities']) and
                            (not filters.get('kinds') or e['kind'] in filters['kinds']) and
                            (not q or q in (e['title'] + ' ' + str(e['value']) if filters.get('fulltext') else e['title']).lower())]
    sort = filters.get('sort', 'code')
    if sort in ('pct_change', 'amount'):
        for key in ['stocks', 'sectors']:
            present = [s for s in r[key] if s.get(sort) is not None]
            missing = [s for s in r[key] if s.get(sort) is None]
            present.sort(key=lambda s: s['id'])
            present.sort(key=lambda s: s[sort], reverse=filters.get('descending', False))
            r[key] = present + sorted(missing, key=lambda s: s['id'])
    elif sort == 'risk':
        weights = {'需重点关注': 0, '一般关注': 1, '资料不足': 2}
        r['stocks'].sort(key=lambda s: (weights[s['risk']], s['id']))
    else:
        r['stocks'].sort(key=lambda s: s['id'])
    r['filtered'] = True
    r['filters'] = filters
    return r


def display(value, digits=2):
    return '不可用' if value is None else f'{value:,.{digits}f}'


def research_references(value):
    refs = set()
    if isinstance(value, dict):
        refs.update(value.get('evidence_ids', []))
        for child in value.values(): refs.update(research_references(child))
    elif isinstance(value, list):
        for child in value: refs.update(research_references(child))
    return refs


def research_blocks(report):
    research = report.get('investment_research')
    if not research: return []
    verdict = research['adjudication']
    out = [('h','投资论证与裁决'), ('p',verdict['stance']+'；证据'+verdict['sufficiency']),
           ('p','周期：'+(research.get('horizon') or '盘后研究，周期未指定')), ('p',verdict['decision_basis'])]
    if research.get('view'):
        out.append(('p','论点筛选摘录，隐藏 '+str(research['view']['hidden_count'])+' 项；总体裁决和限制仍适用于原研究范围。'+storage.dumps(research['view'])))
    for thesis in research['theses']:
        out += [('h2',('多头：' if thesis['side']=='bull' else '空头：')+thesis['statement']),
                ('p',thesis['reasoning']), ('p','依据：'+ '、'.join(thesis['evidence_ids'])),
                ('p','假设：'+'；'.join(thesis['assumptions'])), ('p','限制：'+'；'.join(thesis['limitations']))]
        for condition in thesis['invalidation_conditions']:
            out.append(('p','失效核验：'+condition['description']+'；'+condition['verification_method']))
        for rebuttal in research['rebuttals']:
            if rebuttal['target_thesis_id']==thesis['thesis_id']:
                out.append(('p','反方质疑：'+rebuttal['explanation']+'；依据：'+'、'.join(rebuttal['evidence_ids'])))
    for dispute in research['disputes']:
        resolution=next((r for r in verdict.get('resolutions',[]) if r['dispute_id']==dispute['dispute_id']),None)
        out += [('h2','核心争议：'+dispute['question']),('p',resolution['reason'] if resolution else '尚未解决')]
    for key,label in [('observation_conditions','观察条件'),('invalidation_conditions','结论失效条件')]:
        for condition in verdict.get(key,[]):
            out.append(('p',label+'：'+condition['description']+'；'+condition['verification_method']+'；'+condition['time_window']))
    out += [('p','限制：'+x) for x in dict.fromkeys(research['gaps']+verdict.get('limitations',[])+research['risk_review'].get('limitations',[]))]
    out.append(('h2','风险检查：'+('通过当前证据范围检查' if research['risk_review'].get('approved') else '未通过或未完成')))
    out.extend(('p',x) for x in research['risk_review'].get('reasons',[]))
    if research.get('changes'):out.append(('p','版本变化：'+storage.dumps(research['changes'])))
    return out


def blocks(report):
    """One semantic document feeds both exporters, preserving evidence and limitations."""
    if report.get('artifact_type') == 'answer':
        out = [('title', '连续对话研究成果'), ('p', f'版本 v{report["version"]}；资料截止 {report["request"]["as_of"]}'),
               ('p', '来源报告：' + report['source_report_id']), ('h','回答')]
        if not report.get('investment_research'): out.append(('p',report['answer']))
        if report['request']['mode'] == 'demo': out.append(('p','样例演示：行情与事件为合成资料'))
        result = report.get('result_set')
        if result:
            out += [('h','比较结果'),('p','结果筛选与排序：' + storage.dumps(result['filters']))]
            fields = list(result['columns'])
            rows = [[str(result['columns'][key]) for key in fields]]
            rows += [[display(row.get(key),4) if isinstance(row.get(key),(int,float)) else str(row.get(key) if row.get(key) is not None else '不可用') for key in fields] for row in result['rows']]
            out.append(('table',rows))
        out.extend(research_blocks(report))
        out.append(('h','限制与缺口'))
        out.extend(('p',note) for note in report.get('issues',report.get('limitations',[])))
        for gap in report.get('gaps',[]):
            if gap['status']=='resolved': out.append(('p',gap['detail']))
        refs = {eid for claim in report['summary'] for eid in claim['evidence_ids']}
        refs.update(research_references(report.get('investment_research', {})))
        if result: refs.update(eid for row in result['rows'] for eid in row['evidence_ids'])
        out.append(('h','引用证据'))
        for evidence in report['evidence']:
            if evidence['id'] not in refs: continue
            out += [('h2',evidence['id']+' '+evidence['title']),
                    ('p',f'来源：{evidence["source"]}；时间：{evidence.get("event_time","未知")}；质量：{evidence["quality"]}'),
                    ('p',storage.dumps(evidence['value']))]
            if evidence.get('source_url'): out.append(('p',evidence['source_url']))
        return out
    out = [('title', report['request']['trade_date'] + ' 关注范围盘后复盘')]
    if report['request']['mode'] == 'demo':
        out.append(('p', '样例数据 · 合成行情和虚构事件 · 规则分析，未调用模型'))
    out.append(('p', f'版本 v{report["version"]}　信息截止 {report["request"]["as_of"]}　生成 {report["created"]}'))
    out.append(('p', '报告状态：' + ('部分完成' if report['status']=='partial' else '已完成')))
    out.extend(research_blocks(report))
    if report.get('filtered'):
        out.append(('p', '筛选摘录。筛选条件：' + storage.dumps(report['filters'])))
    if report.get('execution_mode') == 'autonomous':
        out.append(('h', '自主分析过程摘要'))
        out.append(('p', '分析重点：' + '、'.join(report.get('focus', []))))
        out.append(('p', f'计划版本 {report.get("plan_version", 0)}；需求版本 {report.get("requirement_version", 0)}；停止原因：{report.get("stop_reason", "")}'))
        for action in report.get('decision_summary', []):
            out.append(('p', f'{action["task_id"]}：{action["reason"]}；来源 {action.get("source_id") or "已核验证据"}；状态 {action["status"]}'))
    if report.get('coverage_notes'):
        out.append(('h', '数据范围说明'))
        for note in report['coverage_notes']:
            out.append(('p', note))
    out.append(('h', '数据范围与缺口'))
    for issue in report['issues']:
        out.append(('p', issue))
    out.append(('h', '原报告概览' if report.get('filtered') else '复盘概览'))
    for claim in report['summary']:
        out.append(('p', claim['text'] + ' [' + ', '.join(claim['evidence_ids']) + ']'))
    out.append(('h', '板块表现'))
    for s in report['sectors']:
        if s.get('index_only'):
            out.append(('p', f'{s["name"]}：行业指数涨跌幅 {display(s["pct_change"])}%，成交额较前一交易日变化 {display(s["amount_change"])}%。历史成分涨跌、涨停及领涨股统计不可用。口径：{s["basis"]}。'))
            metrics = s.get('index_research')
            if metrics:
                out += [('p','指数层；基准：'+metrics['benchmark']+'；来源：'+metrics['benchmark_source']),
                        ('table',[['指标','数值'],['20日收益（%）',display(metrics['return_20d'])],
                                  ['相对强弱（百分点）',display(metrics['relative_strength_pp'])],
                                  ['区间最大回撤（%）',display(metrics['max_drawdown_pct'])],
                                  ['MA5（指数点）',display(metrics['ma5'])],['MA20（指数点）',display(metrics['ma20'])]])]
                out.extend(('p','限制：'+gap) for gap in metrics['gaps'])
        else:
            out.append(('p', f'{s["name"]}：有效成分 {s["valid"]}/{s["total"]}，涨幅 {display(s["pct_change"])}%，上涨 {s["up"]} 家，下跌 {s["down"]} 家，未知 {s["missing"]} 家。口径：{s["basis"]}。'))
    out.append(('h', '重点个股'))
    for s in report['stocks']:
        out.append(('h2', s['name'] + ' ' + s['id']))
        out.append(('p', f'{s["status"]}；{s["risk"]}；{s["basis"]}'))
        out.append(('table', [['指标', '数值'], ['收盘价（元）', display(s['close'])], ['涨跌幅（%）', display(s['pct_change'])],
                              ['MA20（元）', display(s['metrics']['ma20'])], ['日成交量 / 前5日均量', display(s['metrics']['volume_ratio'])],
                              ['MACD（12,26,9）', display(s['metrics']['macd'])]]))
        out.append(('chart', s))
    out.append(('h', '政策与外围'))
    for e in report['events']:
        out.append(('p', f'{e["title"]}（{e["issuer"]}，{e.get("published_at") or e.get("published_date")}，{e["quality"]}）'))
        out.append(('p', e.get('body_text', '')[:1600] or '正文不可用，请查看原文。'))
        if e.get('source_url'):
            out.append(('p', e['source_url']))
    for m in report['markets']:
        out.append(('p', f'{m["name"]}，交易日 {m["trade_date"]}，收盘 {display(m["close"])}，涨跌幅 {display(m.get("pct_change"))}%。'))
    labels = {'market': '量价分析', 'sector': '板块分析', 'policy': '政策分析', 'bull': '多头证据', 'bear': '空头反证', 'risk': '风险与次日观察'}
    for key, title in labels.items():
        if report.get('investment_research') and key in ('bull','bear','risk'): continue
        out.append(('h', title))
        claims = report['analysis'][key].get('claims', [])
        if not claims:
            out.append(('p', report['analysis'][key].get('note') or '没有可用分析结果。'))
        for claim in claims:
            out.append(('p', claim['text'] + ' [' + ', '.join(claim['evidence_ids']) + ']'))
            if claim.get('condition'):
                out.append(('p', '成立或观察条件：' + claim['condition']))
            if claim.get('limitation'):
                out.append(('p', '局限：' + claim['limitation']))
    out.append(('h', '证据与来源'))
    for e in report['evidence']:
        out.append(('h2', e['id'] + ' ' + e['title']))
        out.append(('p', f'来源：{e["source"]}；时间：{e.get("event_time", "未知")}；质量：{e["quality"]}'))
        out.append(('p', storage.dumps(e['value']) if isinstance(e['value'], dict) else str(e['value'])))
        if e.get('source_url'):
            out.append(('p', e['source_url']))
    return out


def chart_png(stock):
    bars = stock['bars'][-60:]
    fig, (price, volume) = plt.subplots(2, 1, figsize=(9, 4.8), sharex=True, gridspec_kw={'height_ratios': [3, 1]})
    for i, b in enumerate(bars):
        o, h, l, c = [b['adjusted_' + key] for key in ['open', 'high', 'low', 'close']]
        color = '#c95148' if c >= o else '#278474'
        price.vlines(i, l, h, color=color, linewidth=.8)
        price.add_patch(Rectangle((i-.3, min(o, c)), .6, max(abs(c-o), .001), facecolor=color))
        volume.bar(i, b['volume'], color=color, width=.6)
    for window, color in [(5, '#b97716'), (20, '#4776b4'), (60, '#8962a5')]:
        values = [b.get(f'ma{window}') for b in bars]
        price.plot(range(len(bars)), [v if v is not None else float('nan') for v in values], label=f'MA{window}', color=color, linewidth=1)
    price.set_title(stock['id'] + ' | Daily OHLC / Volume', loc='left', fontsize=11)
    price.legend(loc='upper left', fontsize=8)
    for ax in [price, volume]:
        ax.spines[['top', 'right']].set_visible(False)
        ax.grid(axis='y', alpha=.18)
    ticks = list(range(0, len(bars), max(1, len(bars)//5)))
    volume.set_xticks(ticks, [bars[i]['date'][5:] for i in ticks], fontsize=8)
    fig.tight_layout()
    stream = io.BytesIO()
    fig.savefig(stream, format='png', dpi=130)
    plt.close(fig)
    return stream.getvalue()


def export_report(report, spec, target):
    selected = filter_report(report, spec['filters']) if spec['scope']=='filtered' else report
    content = blocks(selected)
    images = {s['id']: chart_png(s) for s in selected['stocks'] if s['bars']} if spec['charts'] and selected.get('artifact_type') != 'answer' else {}
    if spec['format'] == 'md':
        lines = []
        for kind, value in content:
            if kind in ('title', 'h', 'h2'):
                lines.append({'title': '# ', 'h': '## ', 'h2': '### '}[kind] + value)
            elif kind == 'p':
                lines.append(value)
            elif kind == 'table':
                lines.append('\n'.join(['| ' + ' | '.join(row) + ' |' + ('\n| ' + ' | '.join(['---']*len(row)) + ' |' if i==0 else '') for i, row in enumerate(value)]))
            elif kind == 'chart' and value['id'] in images:
                lines.append(f'![{value["name"]}日K线](charts/{value["id"]}.png)')
        text = '\n\n'.join(lines)
        if spec['charts']:
            path = target.with_suffix('.zip')
            with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as archive:
                archive.writestr('report.md', text.encode('utf-8'))
                for code, data in images.items():
                    archive.writestr(f'charts/{code}.png', data)
        else:
            path = target.with_suffix('.md')
            path.write_text(text, 'utf-8')
        return path
    document = Document()
    section = document.sections[0]
    section.page_width, section.page_height = Cm(21), Cm(29.7)
    section.top_margin = section.bottom_margin = Cm(2)
    section.left_margin = section.right_margin = Cm(2)
    for name in ['Normal', 'Title', 'Heading 1', 'Heading 2']:
        style = document.styles[name]
        style.font.name = 'Microsoft YaHei'
        style.element.get_or_add_rPr().rFonts.set(qn('w:eastAsia'), 'Microsoft YaHei')
    document.styles['Normal'].font.size = Pt(10)
    document.styles['Normal'].paragraph_format.space_after = Pt(6)
    document.styles['Normal'].paragraph_format.line_spacing = 1.2
    for kind, value in content:
        if kind == 'title':
            document.add_paragraph(value, 'Title')
        elif kind in ('h', 'h2'):
            document.add_heading(value, 1 if kind=='h' else 2)
        elif kind == 'p':
            document.add_paragraph(value)
        elif kind == 'table':
            table = document.add_table(rows=0, cols=2)
            table.style = 'Light Shading Accent 1'
            for values in value:
                cells = table.add_row().cells
                for cell, text in zip(cells, values):
                    cell.text = text
            repeat = OxmlElement('w:tblHeader')
            table.rows[0]._tr.get_or_add_trPr().append(repeat)
        elif kind == 'chart' and value['id'] in images:
            document.add_picture(io.BytesIO(images[value['id']]), width=Cm(16.5))
    footer = section.footer.paragraphs[0]
    footer.alignment = 2
    footer.add_run('TradingAgents　')
    field = OxmlElement('w:fldSimple')
    field.set(qn('w:instr'), 'PAGE')
    footer._p.append(field)
    path = target.with_suffix('.docx')
    document.save(path)
    return path
