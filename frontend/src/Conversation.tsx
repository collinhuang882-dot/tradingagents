import { useEffect, useRef, useState } from 'react';
import { api, post, navigate, labels, localTime } from './api';

const messageLabels: Record<string,string> = {received:'已收到',interpreting:'理解需求中',awaiting_clarification:'待你补充',completed:'已答复',failed:'处理失败',researching:'研究执行中',queued:'已排队',cancelled:'已停止'};
const kindLabels: Record<string,string> = {fetch_market:'股票日线',fetch_sector:'行业行情',fetch_global:'外围市场',search_policy:'政策与公告检索',search_announcement:'公司公告',fetch_document:'获取官方正文',validate_data:'核验资料',compare_sources:'交叉核验来源',analyze_market:'量价分析',analyze_sector:'行业分析',analyze_policy:'政策与外围分析',argue_bull:'多头证据',argue_bear:'空头反证',rebut_theses:'交叉反驳',adjudicate_theses:'综合裁决',review_risk:'风险审查'};
const sourceLabels: Record<string,string> = {'akshare.em.daily':'东方财富日线','akshare.sina.daily':'新浪日线','akshare.sw.daily':'申万行业指数','official.columns':'已接入的官方栏目与市场资料','akshare.sina.global':'新浪外围行情','akshare.em.announcements':'东方财富公告'};
const stopLabels: Record<string,string> = {no_progress:'连续两轮没有取得新证据，已停止补查',planning_budget:'达到规划次数上限',coverage_complete:'本次分析范围已完成'};
function answerText(message:any){let text=message.answer||'正在处理这条消息…';(message.citations||[]).forEach((id:string,i:number)=>{text=text.split('['+id+']').join('〔证据'+(i+1)+'〕')});return text}
const taskLabels: Record<string,string> = {pending:'等待',running:'执行中',succeeded:'已完成',abstained:'资料不足',failed:'失败',superseded:'已替代',skipped:'已跳过'};

export function ResearchGaps({gaps}: {gaps?:any[]}) {
  if (!gaps?.length) return null;
  const names:Record<string,string>={retrieval_miss:'未找到已有证据',not_fetched:'尚未取得',no_records:'限定范围无记录',provider_error:'来源调用失败',stale:'资料已过期',partial_coverage:'覆盖不完整',time_uncertain:'时间待核验',conflict:'来源存在差异',unsupported:'暂不支持',scope_ambiguous:'需要明确范围'};
  const statuses:Record<string,string>={open:'待处理',resolving:'正在补查',resolved:'已核实',unresolvable:'本轮未能补齐'};
  return <details className="quality-strip"><summary>本轮资料覆盖 · {gaps.filter(g=>g.status!=='resolved').length} 项待补齐</summary>
    {gaps.map(g=><div key={g.gap_id}><strong>{names[g.type]||g.type} · {statuses[g.status]||g.status}</strong><p>{g.detail}</p>{g.next_action&&<small>{g.next_action}</small>}</div>)}
  </details>;
}

export function ChatLink({rid, report=false}: {rid:string; report?:boolean}) {
  const [error,setError]=useState('');
  async function open(){
    try {
      const c=await post('/conversations',report?{report_id:rid}:{run_id:rid});
      navigate('/chat/'+c.id);
    }catch(e:any){setError(e.message)}
  }
  return <span><button onClick={open}>{report?'追问这份报告':'对话与补充要求'}</button>{error&&<span role="alert">{error}</span>}</span>;
}

export function AgentPlan({rid}: {rid:string}) {
  const [plan,setPlan]=useState<any>(null),[error,setError]=useState('');
  useEffect(()=>{let alive=true;const refresh=async()=>{try{const p=await api('/reviews/'+rid+'/plan');if(alive)setPlan(p)}catch(e:any){if(alive)setError(e.message)}};refresh();const timer=setInterval(refresh,2500);return()=>{alive=false;clearInterval(timer)}},[rid]);
  if(error)return <p role="alert">{error}</p>;
  if(!plan?.tasks?.length)return null;
  const waiting=plan.tasks.find((t:any)=>t.status==='running'&&t.waiting_seconds>=10);
  return <details className="quality-strip agent-plan"><summary>自主分析过程 · 计划 {plan.version} · {plan.tasks.filter((t:any)=>['succeeded','failed','abstained','skipped'].includes(t.status)).length} / {plan.tasks.length} 项已处理{waiting&&` · 正在等待${sourceLabels[waiting.source_id]||kindLabels[waiting.kind]}，已等待 ${waiting.waiting_seconds} 秒`}</summary>
    <p>模型请求 {plan.budget?.model||0} / {plan.budget?.model_limit||60} · 数据请求 {plan.budget?.network||0} / {plan.budget?.network_limit||80}</p>
    <ol>{plan.tasks.map((t:any,i:number)=><li key={t.id}><strong>{i+1}. {kindLabels[t.kind]||'分析任务'}</strong><span>{taskLabels[t.status]||t.status}</span><p>{t.reason}</p><small>{sourceLabels[t.source_id]||'使用已核验证据'}{t.depends_on.length>0&&' · 依赖任务 '+t.depends_on.map((id:string)=>plan.tasks.findIndex((p:any)=>p.id===id)+1).join('、')}</small>{t.error&&<p className="error">{t.error}</p>}</li>)}</ol>
    {plan.stop_reason&&<p>停止原因：{stopLabels[plan.stop_reason]||plan.stop_reason}</p>}
  </details>;
}

export default function Conversation({id}: {id?:string}) {
  const [conv,setConv]=useState<any>(null),[messages,setMessages]=useState<any[]>([]),[changes,setChanges]=useState<any[]>([]);
  const [text,setText]=useState(''),[mode,setMode]=useState('live'),[error,setError]=useState(''),[sending,setSending]=useState(false),[evidence,setEvidence]=useState<any>(null);
  const [history,setHistory]=useState<any[]>([]),[historyQuery,setHistoryQuery]=useState(''),[historyCursor,setHistoryCursor]=useState<number|null>(null);
  const [depth,setDepth]=useState('auto');
  const [policy,setPolicy]=useState('inherit'),[replyTo,setReplyTo]=useState<any>(null),[older,setOlder]=useState<any[]>([]);
  useEffect(()=>{let active=true;api('/conversations?q='+encodeURIComponent(historyQuery)).then(rows=>{if(active){setHistory(Array.isArray(rows)?rows:[]);setHistoryCursor(null)}}).catch(()=>{});return()=>{active=false}},[historyQuery,id]);
  const retry=useRef<{signature:string;key:string}|null>(null);
  useEffect(()=>{
    setMessages([]);setOlder([]);setReplyTo(null);setPolicy('inherit');setConv(null);setError('');if(!id)return;
    let alive=true,inFlight=false,cursor=0;
    const refresh=async()=>{if(inFlight)return;inFlight=true;try{const [c,delta,a]=await Promise.all([api('/conversations/'+id),api('/conversations/'+id+'/sync?after_cursor='+cursor),api('/conversations/'+id+'/changes')]);if(alive){setConv(c);setMessages(previous=>{const byId=new Map((delta.reset?[]:previous).map((m:any)=>[m.id,m]));for(const m of delta.messages)byId.set(m.id,m);return Array.from(byId.values()).sort((a:any,b:any)=>a.seq-b.seq)});cursor=delta.cursor;setChanges(a);setError(previous=>previous.startsWith('连接中断，正在重连：')?'':previous)}}catch(e:any){if(alive)setError('连接中断，正在重连：'+e.message)}finally{inFlight=false}};
    refresh();const timer=setInterval(refresh,1500);return()=>{alive=false;clearInterval(timer)};
  },[id]);
  async function send(){
    if(!text.trim()||sending)return;setSending(true);setError('');
    try{
      let c=conv;
      if(!id){c=await post('/conversations',{mode});setConv(c)}
      if(!c)throw new Error('会话正在加载，请稍后再试');
      const payload={text,base_requirement_version:c.requirement_version,question_id:c.pending?.question_id||null,reply_to:replyTo?.id||null,research_policy:policy,research_depth:depth};
      const signature=JSON.stringify([c.id,payload]);
      const key=retry.current?.signature===signature?retry.current.key:crypto.randomUUID();retry.current={signature,key};
      await post('/conversations/'+c.id+'/messages',{...payload,client_message_id:key});
      retry.current=null;setText('');setReplyTo(null);setPolicy('inherit');if(!id)navigate('/chat/'+c.id);
    }catch(e:any){setError(e.message)}finally{setSending(false)}
  }
  return <div className="conversation-page">
    <header className="page-heading"><div><h1>对话分析</h1><p>描述你要研究的股票与日期，再沿着证据继续追问。</p></div><a href="#/chat">新会话</a></header>
    <div className="conversation-layout">
      <section className="conversation-thread" aria-label="研究对话">
        {messages.length>=50&&<button onClick={async()=>{try{const before=older[0]?.seq||messages[0]?.seq;const rows=await api('/conversations/'+id+'/messages?limit=50&before_seq='+before);setOlder(previous=>[...rows,...previous])}catch(e:any){setError(e.message)}}}>加载更早消息</button>}
        {!messages.length&&<div className="conversation-empty"><h2>这次想研究什么？</h2><p>例如：复盘 2026-11-12 的贵州茅台，重点关注量价和白酒政策。</p><p>你也可以从历史报告打开“追问这份报告”，直接核对已有结论。</p></div>}
        {[...older,...messages].filter((m,i,all)=>all.findIndex(x=>x.id===m.id)===i).map(m=><article key={m.id} className="conversation-message"><div className="user-message"><small>你 · {localTime(m.received_at)}</small><p>{m.text}</p></div><div className="assistant-message"><small>研究助手 · {messageLabels[m.status]||m.status}</small><p>{answerText(m)}</p>
          {m.request&&<dl className="request-card"><dt>对象</dt><dd>{[...m.request.stocks,...m.request.sectors].join('、')}</dd><dt>交易日</dt><dd>{m.request.trade_date}</dd><dt>截止时间</dt><dd>{localTime(m.request.as_of)}</dd><dt>重点</dt><dd>{m.request.focus?.join('、')||'综合复盘'}</dd><dt>数据</dt><dd>{m.request.mode==='demo'?'合成演示':'正式数据'}</dd></dl>}
          {m.result_set&&<div className="conversation-results"><p>比较范围：{m.result_set.trade_date} · 缺失值显示为 —</p><table><thead><tr>{Object.entries(m.result_set.columns).map(([key,title])=><th key={key}>{String(title)}</th>)}</tr></thead><tbody>{m.result_set.rows.map((row:any)=><tr key={row.id}>{Object.keys(m.result_set.columns).map(key=><td key={key}>{row[key]===null||row[key]===undefined?'—':typeof row[key]==='number'?row[key].toLocaleString('zh-CN',{maximumFractionDigits:4}):row[key]}</td>)}</tr>)}</tbody></table></div>}
          {['received','queued','interpreting','researching'].includes(m.status)&&m.turn_id&&<button onClick={async()=>{try{await post('/turns/'+m.turn_id+'/cancel')}catch(e:any){setError(e.message)}}}>停止这次请求</button>}
          {m.artifact_id&&<a className="action-link" href={'#/report/'+m.artifact_id}>查看与导出本次回答</a>}
          {m.actions?.map((a:any,i:number)=><a className="action-link" key={i} href={'#/run/'+a.run_id}>{a.type==='create_review'?'查看分析任务':'查看变更目标'}</a>)}
          {m.citations?.map((eid:string,i:number)=><button key={eid} className="citation-button" onClick={async()=>{try{setEvidence(await api('/reports/'+m.report_id+'/evidence/'+encodeURIComponent(eid)))}catch(e:any){setError(e.message)}}}>证据 {i+1}</button>)}
          <button className="citation-button" onClick={()=>setReplyTo(m)}>回复这条回答</button>
          {m.limitations?.map((s:string)=><p key={s}><small>{s}</small></p>)}
          <ResearchGaps gaps={m.gaps}/>
          {m.status==='failed'&&m.turn_id&&<button onClick={async()=>{try{await post('/turns/'+m.turn_id+'/resume')}catch(e:any){setError(e.message)}}}>恢复这次请求</button>}
        </div></article>)}
        {error&&<p className="notice danger" role="alert">{error}</p>}
        <form className="conversation-compose" onSubmit={e=>{e.preventDefault();send()}}>
          {!id&&<label>数据模式 <select value={mode} onChange={e=>setMode(e.target.value)}><option value="live">正式数据</option><option value="demo">样例演示（合成数据）</option></select></label>}
          {replyTo&&<div className="notice"><span>正在回复：{replyTo.answer?.slice(0,100)||replyTo.text}</span><button type="button" onClick={()=>setReplyTo(null)}>取消引用</button></div>}
          <label>资料查询方式 <select aria-label="资料查询方式" value={policy==='inherit'?(conv?.research_policy||'auto'):policy} onChange={e=>setPolicy(e.target.value)}><option value="auto">自动补查必要资料</option><option value="local_only">仅使用现有资料</option></select></label>
          <label>分析深度 <select aria-label="分析深度" value={depth} onChange={e=>setDepth(e.target.value)}><option value="auto">自动识别</option><option value="quick">快速回答</option><option value="deep">深入分析（多空与裁决）</option></select></label>
          <label htmlFor="research-message">{conv?.pending?'补充你的要求':'输入分析需求或问题'}</label>
          <textarea id="research-message" value={text} onChange={e=>setText(e.target.value)} maxLength={8000} rows={4} placeholder="股票、交易日期，以及你想重点了解的问题"/>
          <div><small>沿用当前研究范围；必要时补查并保留来源。</small><button className="primary" type="submit" disabled={sending||!text.trim()||(!!id&&!conv)}>{sending?'发送中…':'发送'}</button></div>
        </form>
      </section>
      <aside className="conversation-context"><details className="conversation-history"><summary>历史会话</summary><label>查找会话 <input aria-label="查找会话" value={historyQuery} onChange={e=>setHistoryQuery(e.target.value)} placeholder="股票代码、日期或标题"/></label>{history.map(item=><p key={item.id}><a href={'#/chat/'+item.id}>{item.title}</a><small> {item.mode==='demo'?'演示':'正式'}</small></p>)}{history.length>=30&&<button onClick={async()=>{try{const cursor=historyCursor||history.at(-1)?.cursor;const rows=await api('/conversations?before='+cursor+'&q='+encodeURIComponent(historyQuery));setHistory(h=>[...h,...rows]);setHistoryCursor(rows.at(-1)?.cursor||cursor)}catch(e:any){setError(e.message)}}}>更多会话</button>}</details><h2>当前研究</h2>{conv?.threads?.length>1&&<label>研究主题<select aria-label="研究主题" value={conv.active_thread_id} onChange={async e=>{try{const next=await post('/conversations/'+id+'/threads/'+e.target.value+'/activate');setConv(next)}catch(err:any){setError(err.message)}}}>{conv.threads.map((t:any)=><option value={t.id} key={t.id}>{t.requirements?.stocks?.join(' / ')||'待明确对象'} · {t.requirements?.trade_date||''}</option>)}</select></label>}{conv?.run_id?<><p>{conv.requirements?.stocks?.join('、')}</p><p>{conv.requirements?.trade_date} · {conv.mode==='demo'?'演示':'正式数据'}</p><p>截止 {localTime(conv.requirements?.as_of)}</p><p>需求版本 {conv.requirement_version}</p><a href={'#/run/'+conv.run_id}>查看任务与报告</a><AgentPlan rid={conv.run_id}/><button onClick={async()=>{try{await post('/reviews/'+conv.run_id+'/cancel');setError('已提交停止请求')}catch(e:any){setError(e.message)}}}>停止当前任务</button></>:<p>提供股票和交易日期后，这里会显示解析结果。</p>}
        {conv?.budget&&<p>对话模型请求 累计 {conv.budget.model} 次</p>}
        {changes.map(a=><div key={a.id}><p>{a.status==='pending'?'变更待应用':a.status==='applied'?'变更已应用':a.status==='withdrawn'?'变更已撤回':'变更未应用'}</p>{a.status==='pending'&&<button onClick={async()=>{try{await post('/conversations/'+id+'/changes/'+a.id+'/withdraw')}catch(e:any){setError(e.message)}}}>撤回</button>}</div>)}
      </aside>
    </div>
    {evidence&&<div className="conversation-evidence" role="dialog" aria-label="引用证据"><button onClick={()=>setEvidence(null)}>关闭</button><h2>{evidence.title}</h2><p>{evidence.source} · {evidence.event_time}</p><pre>{typeof evidence.value==='string'?evidence.value:JSON.stringify(evidence.value,null,2)}</pre></div>}
  </div>;
}
