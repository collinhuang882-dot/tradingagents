import {useState} from 'react';

export default function InvestmentResearch({research, onEvidence, onFiltersChange}: {research:any; onEvidence:(id:string)=>void; onFiltersChange?:(filters:any)=>void}) {
  const [side,setSide]=useState('all'),[status,setStatus]=useState('all'),[subject,setSubject]=useState('all'),[type,setType]=useState('all');
  function update(field:string,value:string){
    const next={investment_side:side,investment_status:status,investment_subject:subject,investment_type:type,[field]:value};
    setSide(next.investment_side);setStatus(next.investment_status);setSubject(next.investment_subject);setType(next.investment_type);onFiltersChange?.(next);
  }
  if(!research) return <p className="notice">旧版成果未包含结构化多空裁决，可在对话中选择深入分析。</p>;
  const verdict=research.adjudication;
  const states:Record<string,string>={supported:'有证据支持',conditional:'有条件支持',unresolved:'待验证',refuted:'已反驳',withdrawn:'已撤回'};
  const items=research.theses.filter((t:any)=>(side==='all'||t.side===side)&&(status==='all'||t.status===status)&&(subject==='all'||t.subject===subject)&&(type==='all'||t.claim_type===type));
  const citations=(ids:string[])=>(ids||[]).map(id=><button type="button" className="citation-button" key={id} onClick={()=>onEvidence(id)}>查看证据 {id.slice(-12)}</button>);
  return <section className="panel" aria-label="投资论证与裁决">
    <h2>投资论证与裁决 · {verdict.stance}</h2>
    <p>证据{verdict.sufficiency} · {research.horizon} · 截止 {research.as_of}</p>
    <p>{verdict.decision_basis}</p>{citations(verdict.evidence_ids)}
    {research.changes&&<p className="notice">相较上一版：{research.changes.previous_stance||'未形成结构化判断'} → {research.changes.current_stance}。{research.changes.reason}</p>}
    {research.changes?.theses?.length>0&&<details><summary>查看论点变化（文字匹配用于对照，不等于因果认定）</summary>{research.changes.theses.map((t:any,i:number)=><p key={i}>{t.type}：{t.before&&`原：${t.before} `}{t.after&&`本轮：${t.after}`}</p>)}</details>}
    <p>筛选仅改变论点列表，不改变总体裁决；导出可选择完整成果或当前筛选摘录，总体限制始终保留。</p>
    <label>论点方向 <select aria-label="论点方向" value={side} onChange={e=>update('investment_side',e.target.value)}><option value="all">全部</option><option value="bull">多头</option><option value="bear">空头</option></select></label>{' '}
    <label>论点状态 <select aria-label="论点状态" value={status} onChange={e=>update('investment_status',e.target.value)}><option value="all">全部</option>{Object.entries(states).map(([key,label])=><option key={key} value={key}>{label}</option>)}</select></label>{' '}
    <label>论点对象 <select aria-label="论点对象" value={subject} onChange={e=>update('investment_subject',e.target.value)}><option value="all">全部</option>{research.subjects.map((s:string)=><option key={s} value={s}>{s}</option>)}</select></label>{' '}
    <label>论据类型 <select aria-label="论据类型" value={type} onChange={e=>update('investment_type',e.target.value)}><option value="all">全部</option><option value="fact">事实</option><option value="derived">计算结果</option><option value="inference">推断</option><option value="hypothesis">待验证假设</option></select></label>{' '}
    <button onClick={()=>{setSide('all');setStatus('all');setSubject('all');setType('all');onFiltersChange?.({investment_side:'all',investment_status:'all',investment_subject:'all',investment_type:'all'})}}>恢复全部论点</button>
    {!items.length&&<p>当前筛选没有可展示的论点；证据不足时不强行生成观点。</p>}
    {items.map((t:any)=><details key={t.thesis_id} open><summary>{t.side==='bull'?'多头':'空头'} · {t.statement} · {states[t.status]}</summary>
      <p>{t.subject} · {t.claim_type==='fact'?'事实':t.claim_type==='derived'?'计算结果':t.claim_type==='hypothesis'?'待验证假设':'推断'}</p><p>{t.reasoning}</p>
      {citations(t.evidence_ids)}<p>假设：{t.assumptions.join('；')||'未声明额外假设'}</p>
      {t.invalidation_conditions.map((c:any,i:number)=><p key={i}>失效核验：{c.description}；{c.verification_method}；{c.time_window}</p>)}
      {t.limitations.map((x:string)=><p key={x}>局限：{x}</p>)}
      {research.rebuttals.filter((r:any)=>r.target_thesis_id===t.thesis_id).map((r:any,i:number)=><div key={i}><strong>反方质疑</strong><p>{r.explanation}</p>{citations(r.evidence_ids)}</div>)}
    </details>)}
    <h3>关键争议</h3>{!research.disputes.length&&<p>本轮未形成需要继续补查的关键争议。</p>}
    {research.disputes.map((d:any)=>{const resolution=verdict.resolutions?.find((r:any)=>r.dispute_id===d.dispute_id);return <div key={d.dispute_id}><strong>{d.question}</strong><p>{d.materiality}</p><p>{resolution?.reason||'尚未解决'}</p>{citations(resolution?.evidence_ids)}</div>})}
    {['observation_conditions','invalidation_conditions'].map(key=><div key={key}><h3>{key==='observation_conditions'?'后续观察条件':'结论失效条件'}</h3>{verdict[key]?.length?verdict[key].map((c:any,i:number)=><p key={i}>{c.description} · 核验：{c.verification_method} · 时间：{c.time_window} · 所需资料：{c.required_data.join('、')}</p>):<p>尚无足够依据形成条件化判断。</p>}</div>)}
    <h3>风险检查与资料限制</h3><p>{research.risk_review.approved?'通过当前证据范围检查':'未通过或尚未完成风险检查'}</p>
    {[...new Set<string>([...(research.risk_review.reasons||[]),...(research.risk_review.limitations||[]),...(verdict.limitations||[]),...research.gaps])].map(x=><p key={x}>{x}</p>)}
  </section>;
}
