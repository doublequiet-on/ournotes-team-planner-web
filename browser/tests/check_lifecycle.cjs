const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {chromium}=require(process.env.OURNOTES_PLAYWRIGHT_MODULE || 'playwright');
const BASE=process.env.OURNOTES_BROWSER_URL||'http://127.0.0.1:8877/ournotes-planner/';
const DEST=path.resolve(process.argv[2]),reports=[],errors=[];
const ready=page=>page.waitForFunction(()=>window.Planner && document.getElementById('ownedCount').textContent.includes(' + '),null,{timeout:90000});
const importFixture=(page,request)=>page.locator('#import').setInputFiles({name:'fixture.json',mimeType:'application/json',buffer:Buffer.from(JSON.stringify(request))});
async function exported(page,name){
 const pending=page.waitForEvent('download');await page.locator('#exportResult').click();const file=await pending;
 await file.saveAs(path.join(DEST,name+'.json'));return JSON.parse(fs.readFileSync(path.join(DEST,name+'.json'),'utf8')).result;
}
function key(row,objective){const other=objective==='event_pt'?'shop_pt':'event_pt';return [row.totals[objective],row.totals[other],row.totals.cp_remaining,row.normal.power,row.challenge.power,-row.normal.song_id,-row.challenge.song_id];}
function compare(actual,expected){
 assert.equal(actual.search.optimality_proven,true);
 for(const objective of ['event_pt','shop_pt']){
  assert.deepEqual(key(actual.plans[objective],objective),key(expected.plans[objective],objective));
  for(const mode of ['normal','challenge'])assert.deepEqual(actual.top3[objective][mode].map(r=>key(r,objective)),expected.top3[objective][mode].map(r=>key(r,objective)));
 }
}

(async()=>{
 const browser=await chromium.launch({headless:true,channel: process.env.OURNOTES_BROWSER_CHANNEL || (process.platform === 'win32' ? 'msedge' : undefined)});
 const context=await browser.newContext({viewport:{width:1440,height:1050},acceptDownloads:true});
 await context.route('**/*',route=>new URL(route.request().url()).origin===new URL(BASE).origin?route.continue():route.abort());
 const page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));
 try{
  await page.goto(BASE);await ready(page);
  const fixture=JSON.parse(fs.readFileSync(path.join(DEST,'solver-top3-resume.json'),'utf8'));
  await importFixture(page,fixture.request);await page.locator('#calculate').click();
  await page.waitForFunction(()=>/最优验证\s+[1-9]/.test(document.getElementById('progressCount').textContent),null,{timeout:180000});
  assert.equal(await page.locator('#results').isHidden(),true);
  console.log('First proof complete; testing cancellation and persistence');
  await page.locator('#cancel').click();await page.waitForFunction(()=>document.getElementById('progressBox').hidden,null,{timeout:30000});
  assert.equal(await page.locator('#results').isHidden(),true);
  await page.reload();await ready(page);await page.waitForFunction(()=>!document.getElementById('resumeBox').hidden);
  const saved=await page.evaluate(()=>Planner.request('/api/bootstrap'));
  assert.equal(saved.resume.strategy,'solver');assert.ok(saved.resume.saved_steps>=1);assert.equal(saved.resume.active_job_id,null);
  assert.equal(saved.resume.status,'cancelled');
  const store='ournotes-browser-planner-v1-profile:'+new URL(BASE).pathname;
  assert.deepEqual(await page.evaluate(key=>JSON.parse(localStorage.getItem(key)).profile,store),fixture.request.profile);
  reports.push({case:'cancel hides partial optimum; completed proofs and profile survive reload',passed:true,saved_steps:saved.resume.saved_steps});
  await page.locator('#resumeSearch').click();
  await page.waitForFunction(()=>!document.getElementById('results').hidden,null,{timeout:300000});
  const resumed=await exported(page,'solver-resumed-browser');compare(resumed,fixture.expected);
  assert.ok(resumed.search.cached_steps>=1);
  for(const objective of ['event_pt','shop_pt'])for(const mode of ['normal','challenge'])assert.equal(new Set(resumed.top3[objective][mode].map(r=>r[mode].song_id)).size,3);
  reports.push({case:'resume matches desktop optimum and four distinct-song Top-3 lists',passed:true,cached_steps:resumed.search.cached_steps});
  console.log('Resume and four Top-3 lists agree');
  await page.screenshot({path:path.join(DEST,'browser-top3-desktop.png'),fullPage:true});

  const full=JSON.parse(fs.readFileSync(path.join(DEST,'full-catalog-skip.json'),'utf8'));
  const other=await context.newPage();await other.goto(BASE);await ready(other);
  await importFixture(page,full.request);await page.locator('#calculate').click();
  console.log('Testing entire 63-member / 64-Snap catalog');
  await page.waitForFunction(()=>document.getElementById('progressCount').textContent.includes('最优验证'),null,{timeout:90000});
  const denied=await other.evaluate(async request=>{try{await Planner.request('/api/optimize',{body:JSON.stringify(request)});return null;}catch(e){return e.message;}},full.request);
  assert.match(denied,/另一个标签页/);await other.close();
  reports.push({case:'shared browser cache protected from simultaneous tabs',passed:true});
  // Interrupt the expensive first full-pool proof, after preparation is saved.
  await page.reload();await ready(page);
  const boot=await page.evaluate(()=>Planner.request('/api/bootstrap'));
  assert.equal(boot.resume.status,'interrupted');assert.ok(boot.resume.saved_sheets>=1);
  reports.push({case:'reload interrupts workers; completed preparation remains resumable',passed:true,saved_sheets:boot.resume.saved_sheets});
  await page.locator('#resumeSearch').click();
  await page.waitForFunction(()=>!document.getElementById('results').hidden || document.getElementById('message').classList.contains('error'),null,{timeout:300000});
  if(await page.locator('#results').isHidden())throw new Error(await page.locator('#message').textContent());
  const complete=await exported(page,'full-catalog-browser');compare(complete,full.expected);
  assert.ok(complete.search.cached_sheets>=1);
  reports.push({case:'all 63 members and 64 Snaps, exact full-pool skip optimum',passed:true,browser_search_seconds:complete.search.elapsed_seconds});
  assert.deepEqual(errors,[]);
  fs.writeFileSync(path.join(DEST,'lifecycle-report.json'),JSON.stringify({passed:true,reports,errors},null,2));
  console.log(reports);
 }catch(error){
  await page.screenshot({path:path.join(DEST,'lifecycle-failure.png'),fullPage:true}).catch(()=>{});
  fs.writeFileSync(path.join(DEST,'lifecycle-report.json'),JSON.stringify({passed:false,error:error.message,reports,errors},null,2));throw error;
 }finally{await browser.close();}
})();
