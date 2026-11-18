import { test, expect } from '@playwright/test';

test('断线后沿游标恢复且不重复消息',async({page})=>{
  let recover=false;
  const cursors:string[]=[];
  const message={id:'m1',seq:1,text:'断线测试问题',answer:'处理中',status:'interpreting',received_at:'2026-11-13T18:00:00+08:00',citations:[],actions:[]};
  await page.route('**/api/conversations**',async route=>{
    const url=new URL(route.request().url());
    if(url.pathname.endsWith('/changes'))return route.fulfill({json:[]});
    if(url.pathname.endsWith('/sync')){
      const cursor=url.searchParams.get('after_cursor')||'0';cursors.push(cursor);
      if(cursor==='0')return route.fulfill({json:{messages:[message],cursor:1,reset:true}});
      if(!recover)return route.abort('failed');
      return route.fulfill({json:{messages:[{...message,status:'completed',answer:'重连后已完成'}],cursor:3,reset:false}});
    }
    if(url.pathname==='/api/conversations')return route.fulfill({json:[]});
    return route.fulfill({json:{id:'reconnect',mode:'demo',requirements:{},requirement_version:0,budget:{model:0}}});
  });
  await page.goto('/#/chat/reconnect');
  await expect(page.getByText('断线测试问题',{exact:true})).toBeVisible();
  await expect(page.getByRole('alert')).toContainText('连接中断，正在重连');
  recover=true;
  await expect(page.getByText('重连后已完成',{exact:true})).toBeVisible();
  await expect(page.getByText('断线测试问题',{exact:true})).toHaveCount(1);
  await expect(page.getByRole('alert')).toHaveCount(0);
  expect(cursors.filter(c=>c==='1').length).toBeGreaterThanOrEqual(2);
});

test('对话成果保留比较顺序并提供独立导出', async ({page})=>{
  const artifact={id:'answer-test',artifact_type:'answer',conversation_id:'chat-test',source_report_id:'source-test',
    version:2,request:{as_of:'2026-11-13T18:00:00+08:00',trade_date:'2026-11-13',mode:'demo'},stocks:[],summary:[],limitations:['仅本次查询范围'],
    result_set:{columns:{name:'股票',amount:'成交额'},rows:[{id:'b',name:'乙公司',amount:20},{id:'a',name:'甲公司',amount:null}]}};
  await page.route('**/api/reports/answer-test**',r=>r.fulfill({json:artifact}));
  await page.goto('/#/report/answer-test');
  await expect(page.getByRole('heading',{name:'对话研究成果',exact:true})).toBeVisible();
  await expect(page.locator('tbody tr').first()).toContainText('乙公司');
  await expect(page.locator('tbody tr').last()).toContainText('不可用');
  await expect(page.getByText('外围数据不可用，请查看数据缺口。')).toHaveCount(0);
  await page.getByRole('button',{name:'导出本次成果'}).click();
  await expect(page.getByLabel('包含 K 线图表')).not.toBeChecked();
  await page.setViewportSize({width:390,height:844});
  expect(await page.evaluate(()=>document.documentElement.scrollWidth <= window.innerWidth)).toBeTruthy();
});

test('自然语言入口显示数据模式、输入限制与澄清', async ({ page }) => {
  const conv = {id:'conversation-test',mode:'demo',requirement_version:0,requirements:{},pending:null,budget:{model:1}};
  let items:any[]=[];
  await page.route('**/api/conversations**', async route=>{
    const url=new URL(route.request().url());
    if(url.pathname.endsWith('/changes'))return route.fulfill({json:[]});
    if(url.pathname.endsWith('/sync'))return route.fulfill({json:{messages:items,cursor:1,reset:true,has_more:false}});
    if(url.pathname.endsWith('/messages')){
      if(route.request().method()==='POST'){
        const body=route.request().postDataJSON();
        items=[{id:'m1',text:body.text,received_at:'2026-11-13T10:00:00+08:00',status:'awaiting_clarification',answer:'请明确要复盘的交易日期。',actions:[],citations:[]}];
        return route.fulfill({status:202,json:{id:'m1'}});
      }
      return route.fulfill({json:items});
    }
    return route.fulfill({json:conv});
  });
  await page.goto('/#/chat');
  await expect(page.getByRole('heading',{name:'对话分析',exact:true})).toBeVisible();
  await page.getByLabel('数据模式').selectOption('demo');
  await page.getByLabel('输入分析需求或问题').fill('帮我复盘贵州茅台');
  await page.getByRole('button',{name:'发送',exact:true}).click();
  await expect(page.getByText('请明确要复盘的交易日期。')).toBeVisible();
  await page.reload();
  await expect(page.getByText('帮我复盘贵州茅台',{exact:true})).toBeVisible();
});

test('窄屏对话不溢出，提供任务入口和引用证据', async ({page})=>{
  await page.setViewportSize({width:390,height:844});
  await page.route('**/api/conversations/**',async route=>{
    if(route.request().url().includes('/changes'))return route.fulfill({json:[]});
    if(route.request().url().includes('/sync'))return route.fulfill({json:{messages:[{id:'m1',seq:1,text:'结论依据是什么？',answer:'依据本次收盘行情。',received_at:'2026-11-13T10:00:00+08:00',status:'completed',citations:['e1'],report_id:'report1',actions:[]}],cursor:1,reset:true,has_more:false}});
    return route.fulfill({json:{id:'test',mode:'demo',requirement_version:1,requirements:{},budget:{model:2}}});
  });
  await page.route('**/api/reports/report1/evidence/e1',r=>r.fulfill({json:{title:'贵州茅台日线',source:'样例数据',event_time:'2026-11-12',value:{close:1400}}}));
  await page.goto('/#/chat/test');
  await page.getByRole('button',{name:'证据 1',exact:true}).click();
  await expect(page.getByRole('dialog',{name:'引用证据'})).toBeVisible();
  await page.getByRole('button',{name:'关闭',exact:true}).click();
  expect(await page.evaluate(()=>document.documentElement.scrollWidth <= window.innerWidth)).toBeTruthy();
});
