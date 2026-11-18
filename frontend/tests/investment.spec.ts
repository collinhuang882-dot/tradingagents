import {test,expect} from '@playwright/test';

test('V4 深入成果可筛选、查看反驳与引用，窄屏保持完整',async({page})=>{
  const condition={description:'核验后续价格强度',verification_method:'读取同口径日线',time_window:'后续交易日',required_data:['日线']};
  const research={subjects:['600519.SH'],as_of:'2026-11-13T18:00:00+08:00',horizon:'盘后研究',gaps:[],
    adjudication:{stance:'中性观察',sufficiency:'受限',decision_basis:'技术表现不等于盈利改善。',evidence_ids:['e1'],observation_conditions:[condition],invalidation_conditions:[condition],limitations:['缺少财报']},
    theses:[{thesis_id:'bull1',side:'bull',subject:'600519.SH',statement:'价格趋势值得核验',status:'conditional',claim_type:'hypothesis',reasoning:'依据已有价格记录。',evidence_ids:['e1'],assumptions:['后续核验'],limitations:['单日快照'],invalidation_conditions:[condition]},
      {thesis_id:'bear1',side:'bear',subject:'600519.SH',statement:'缺少盈利改善证据',status:'supported',claim_type:'inference',reasoning:'价格不能直接证明盈利。',evidence_ids:['e1'],assumptions:[],limitations:[],invalidation_conditions:[condition]}],
    rebuttals:[{target_thesis_id:'bull1',explanation:'趋势持续性还未验证。',evidence_ids:['e1']}],disputes:[],risk_review:{approved:true,reasons:['仅支持技术观察'],limitations:[]}};
  const report={id:'answer-v4',artifact_type:'answer',conversation_id:'v4',source_report_id:'source',version:1,
    request:{as_of:research.as_of,trade_date:'2026-11-13',mode:'demo'},stocks:[],summary:[],limitations:[],investment_research:research};
  await page.route('**/api/reports/answer-v4**',async r=>r.fulfill({json:r.request().url().includes('/evidence/')?{title:'已核验价格',source:'测试快照',value:{close:100}}:report}));
  await page.goto('/#/report/answer-v4');
  await expect(page.getByRole('heading',{name:'投资论证与裁决 · 中性观察'})).toBeVisible();
  await expect(page.getByText('趋势持续性还未验证。')).toBeVisible();
  await page.getByLabel('论点方向').selectOption('bear');
  await expect(page.getByText('趋势持续性还未验证。')).toHaveCount(0);
  await expect(page.getByText('技术表现不等于盈利改善。')).toBeVisible();
  await page.getByRole('button',{name:'恢复全部论点'}).click();
  await expect(page.getByText('趋势持续性还未验证。')).toBeVisible();
  await page.getByRole('button',{name:'查看证据 e1'}).first().click();
  await expect(page.getByRole('heading',{name:'已核验价格'})).toBeVisible();
  await page.keyboard.press('Escape');
  await page.setViewportSize({width:390,height:844});
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth)).toBeTruthy();
});

test('深入模式随对话请求发送',async({page})=>{
  let submitted:any;
  await page.route('**/api/conversations**',async r=>{
    if(r.request().url().endsWith('/messages')){submitted=r.request().postDataJSON();return r.fulfill({json:{id:'message'}})}
    if(r.request().url().includes('/sync?'))return r.fulfill({json:{messages:[],cursor:0,reset:true}});
    if(r.request().url().endsWith('/changes'))return r.fulfill({json:[]});
    return r.fulfill({json:{id:'depth-test',mode:'demo',requirement_version:0,requirements:{}}});
  });
  await page.goto('/#/chat');
  await page.getByLabel('分析深度').selectOption('deep');
  await page.getByLabel('输入分析需求或问题').fill('请分析茅台的多空观点');
  await page.getByRole('button',{name:'发送',exact:true}).click();
  await expect.poll(()=>submitted?.research_depth).toBe('deep');
});

test('真实后端运行 V4 演示闭环并导出筛选论点',async({page,request})=>{
  const created=await request.post('/api/reviews',{headers:{'Idempotency-Key':'v4-browser-'+Date.now()},data:{
    trade_date:'2026-11-12',as_of:'2026-11-12T18:00:00+08:00',stocks:['600519.SH'],sectors:[],
    mode:'demo',execution_mode:'autonomous',research_depth:'deep'}});
  expect(created.ok()).toBeTruthy();const run=await created.json();
  await expect.poll(async()=>{const r=await request.get('/api/reviews/'+run.id);return (await r.json()).status},{timeout:45000}).toMatch(/completed|partial/);
  const result=await (await request.get('/api/reports/'+run.id)).json();
  expect(result.investment_research.execution_status).toBe('completed');
  expect(result.investment_research.disputes.length).toBeGreaterThan(0);
  await page.goto('/#/report/'+run.id);
  await page.getByRole('tab',{name:'多空与风险',exact:true}).click();
  await expect(page.getByRole('heading',{name:'投资论证与裁决 · 中性观察'})).toBeVisible();
  await page.getByLabel('论点方向').selectOption('bear');
  await page.getByRole('button',{name:'导出报告',exact:true}).click();
  await page.getByLabel('文件格式').selectOption('md');
  await page.getByLabel('导出范围').selectOption('filtered');
  await page.getByLabel('包含 K 线图表').uncheck();
  await page.getByRole('button',{name:'生成文件'}).click();
  const link=page.getByRole('link',{name:/文件已生成/});await expect(link).toBeVisible();
  const response=await request.get((await link.getAttribute('href'))!);
  const text=await response.text();
  expect(text).toContain('论点筛选摘录');expect(text).toContain('总体裁决和限制');expect(text).toContain('投资论证与裁决');
});
