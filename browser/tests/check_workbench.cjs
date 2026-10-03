const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {chromium}=require('playwright');
const BASE=process.env.OURNOTES_BROWSER_URL||'http://127.0.0.1:8881/ournotes-planner/';
const DEST=path.resolve(process.argv[2]||path.join(__dirname,'../../work/validation'));
fs.mkdirSync(DEST,{recursive:true});
const errors=[],reports=[];
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
async function ready(page){await page.waitForFunction(()=>document.getElementById('ownedCount').textContent||!document.getElementById('loadError').hidden,null,{timeout:120000});if(await page.locator('#loadError').isVisible())throw new Error(await page.locator('#loadError').textContent());}
async function complete(page){await page.waitForFunction(()=>!document.getElementById('calculate').disabled&&(!document.getElementById('results').hidden||(!document.getElementById('message').hidden&&document.getElementById('message').classList.contains('error'))),null,{timeout:300000});if(await page.locator('#results').isHidden())throw new Error(await page.locator('#message').textContent());}
async function exported(page,name){const promise=page.waitForEvent('download');await page.locator('#exportResult').click();const d=await promise,p=path.join(DEST,name+'.json');await d.saveAs(p);return JSON.parse(fs.readFileSync(p,'utf8'));}
async function job(page,input){const {job_id}=await page.evaluate(x=>window.Planner.request('/api/optimize',{body:JSON.stringify(x)}),input);const end=Date.now()+300000;while(Date.now()<end){const j=await page.evaluate(id=>window.Planner.request('/api/jobs/'+id),job_id);if(j.status==='error')throw new Error(j.error);if(j.status==='complete')return j.result;await sleep(200);}throw new Error('solver acceptance timeout');}
async function waitJob(page,id,predicate,timeout=120000){const end=Date.now()+timeout;while(Date.now()<end){const j=await page.evaluate(id=>window.Planner.request('/api/jobs/'+id),id);if(predicate(j))return j;await sleep(100);}throw new Error('job state timeout');}
(async()=>{
 const browser=await chromium.launch({headless:true,channel:process.env.OURNOTES_BROWSER_CHANNEL||(process.platform==='win32'?'msedge':undefined)});
 const context=await browser.newContext({viewport:{width:1440,height:1050},acceptDownloads:true});
 await context.route('**/*',route=>new URL(route.request().url()).origin===new URL(BASE).origin?route.continue():route.abort());
 const page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));
 try{
  await page.goto(BASE);await ready(page);assert.equal(await page.locator('#ownedCount').textContent(),'0 + 0');
  assert.equal(await page.locator('select[id*="song" i],select[id*="difficulty" i]').count(),0);
  await page.locator('[data-tab="archive"]').click();await page.locator('#sample').click();
  await page.locator('[data-tab="plan"]').click();await page.screenshot({path:path.join(DEST,'workbench-plan.png'),fullPage:true});
  await page.locator('#calculate').click();await complete(page);const first=await exported(page,'workbench-first');
  assert.equal(first.result.teams.length,15);assert.equal(first.result.search.chart_count,0);
  assert.equal(new Set(first.result.teams.map(t=>JSON.stringify([t.member_ids,[...t.snap_ids].sort((a,b)=>a-b)]))).size,15);
  reports.push('15 distinct skill-aware teams without songs');console.log(reports.at(-1));
  await page.locator('[data-copy="0"]').click();assert.ok((await page.locator('#copyText').inputValue()).includes('https://bdon.moe/tools/chart-data'));await page.locator('#closeCopy').click();
  await page.locator('[data-detail="0"]').click();const download=page.waitForEvent('download');await page.locator('#detailImage').click();await(await download).saveAs(path.join(DEST,'team.png'));await page.locator('[data-close="teamDetails"]').click();
  await page.screenshot({path:path.join(DEST,'workbench-results.png'),fullPage:true});
  for(const width of [768,390]){await page.setViewportSize({width,height:1000});assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));await page.screenshot({path:path.join(DEST,'workbench-'+width+'.png'),fullPage:true});}
  await page.setViewportSize({width:1440,height:1050});
  await page.locator('[data-tab="plan"]').click();
  await page.locator('#memberRarity').selectOption('4');await page.locator('#calculate').click();assert.ok((await page.locator('#message').textContent()).includes('筛选后'));await page.locator('#memberRarity').selectOption('0');
  await page.locator('#allowedCharacters').evaluate(el=>el.closest('details').open=true);await page.locator('[data-allowed-character]:not([data-allowed-character="0"])').first().click();await page.locator('#calculate').click();assert.ok((await page.locator('#message').textContent()).includes('筛选后'));await page.locator('[data-allowed-character="0"]').click();
  await page.locator('[data-tab="inventory"]').click();await page.locator('[data-edit="members"][data-id="59"]').first().click();await page.locator('[data-editor-field="level"]').fill('');await page.locator('#saveCard').click();
  await page.locator('[data-tab="plan"]').click();await page.locator('#calculate').click();await page.waitForFunction(()=>document.getElementById('message').textContent.includes('补齐'));await page.locator('#growthIssues [data-issue]').first().click();await page.locator('[data-editor-field="level"]').fill('20');await page.locator('#saveCard').click();
  reports.push('rarity/character filters and actionable missing-growth correction');
  await page.locator('#requiredLeader').selectOption('59');await page.locator('#bindingDetails').evaluate(el=>el.open=true);await page.locator('[data-bind-member="59"]').selectOption('33');
  await page.locator('#minEventBonus').fill('1');await page.locator('#minEventBonus').dispatchEvent('change');await page.locator('#count').selectOption('5');await page.locator('[data-goal="balanced"]').click();
  await page.locator('#calculate').click();await complete(page);const locked=await exported(page,'workbench-locked');
  const native=JSON.parse(fs.readFileSync(path.join(DEST,'workbench-native.json'),'utf8'));
  assert.deepEqual(locked.result.teams.map(t=>t.verified_selection_objective),native.result.teams.map(t=>t.verified_selection_objective));
  assert.ok(locked.result.teams.every(t=>t.leader_id===59&&t.slots.some(s=>s.member_id===59&&s.snap_id===33)&&t.bonuses_10000.event_pt>=100));
  reports.push('WASM equals native objectives with leader/binding/floor constraints');console.log(reports.at(-1));
  await page.reload();await ready(page);await page.locator('#calculate').click();await complete(page);const cached=await exported(page,'workbench-cached');assert.equal(cached.result.search.solver_calls,0);
  await page.locator('[data-evaluate="0"]').click();await page.locator('#calculate').click();await complete(page);const fixed=await exported(page,'workbench-fixed');assert.equal(fixed.result.search.solver_calls,0);assert.equal(fixed.result.teams[0].power,locked.result.teams[0].power);assert.deepEqual(fixed.result.teams[0].snap_ids,locked.result.teams[0].snap_ids);
  reports.push('reload cache and direct fixed-team evaluation');
  const impossible=structuredClone(native.input);impossible.team_settings.min_event_bonus_10000=1000000;const empty=await job(page,impossible);assert.deepEqual(empty.teams,[]);assert.equal(empty.search.exhausted,true);
  const boot=await page.evaluate(()=>window.Planner.request('/api/bootstrap'));
  const full=structuredClone(boot.sample);full.candidate_member_ids=boot.catalog.members.map(c=>c.id);full.candidate_snap_ids=boot.catalog.snaps.map(c=>c.id);
  full.profile.inventory.members=boot.catalog.members.map(c=>({id:c.id,level:Math.min(20,c.caps[0]),training_count:0,awakening_count:0,live_skill_level:1,gekisou_skill_level:1}));
  full.profile.inventory.snaps=boot.catalog.snaps.map(c=>({id:c.id,level:Math.min(20,c.caps[0]),limit_break_count:0}));full.team_settings.count=5;
  const {job_id}=await page.evaluate(x=>window.Planner.request('/api/optimize',{body:JSON.stringify(x)}),full);
  await sleep(1500);await page.evaluate(id=>window.Planner.request('/api/jobs/'+id+'/cancel',{body:'{}'}),job_id);
  await waitJob(page,job_id,j=>j.status!=='running',30000);
  const cancelled=await page.evaluate(id=>window.Planner.request('/api/jobs/'+id),job_id);assert.equal(cancelled.status,'cancelled');assert.ok(!cancelled.result);
  // A bounded synthetic pool supplies a deterministic complete-row interruption.
  const resume=structuredClone(native.input);resume.team_settings.count=15;resume.team_settings.strategy='portfolio';
  const id=(await page.evaluate(x=>window.Planner.request('/api/optimize',{body:JSON.stringify(x)}),resume)).job_id;
  await waitJob(page,id,j=>j.done>=1||j.status!=='running');
  await page.evaluate(id=>window.Planner.request('/api/jobs/'+id+'/cancel',{body:'{}'}),id);
  await waitJob(page,id,j=>j.status!=='running',30000);
  const resumed=await job(page,resume);assert.equal(resumed.teams.length,15);assert.ok(resumed.search.cached_teams>=1);
  reports.push('infeasible floor, full 63/64 pool cancellation, saved-row resume');console.log(reports.at(-1));
  const second=await context.newPage();await second.goto(BASE);await ready(second);await page.locator('[data-tab="archive"]').click();await page.locator('#archiveName').fill('最新档案');await page.locator('#archiveName').dispatchEvent('change');await second.waitForFunction(()=>document.getElementById('inputArea').disabled);assert.ok((await second.locator('#message').textContent()).includes('另一标签页'));await second.close();
  const migration=await browser.newContext();await migration.addInitScript(({input,scope})=>{if(!localStorage.getItem('migration-seeded')){localStorage.setItem('ournotes-browser-planner-v1-profile:'+scope,JSON.stringify(input));localStorage.setItem('migration-seeded','yes');}},{input:native.input,scope:new URL(BASE).pathname});const old=await migration.newPage();await old.goto(BASE);await ready(old);assert.equal(await old.locator('#ownedCount').textContent(),'5 + 10');await old.locator('[data-tab="archive"]').click();await old.locator('#copyProfile').click();assert.equal(await old.locator('#profileSelect option').count(),2);await migration.close();
  reports.push('cross-tab protection, old library migration and separate planning profile');
  assert.deepEqual(errors,[]);fs.writeFileSync(path.join(DEST,'workbench-report.json'),JSON.stringify({passed:true,reports,errors},null,2));console.log(JSON.stringify({passed:true,reports}));
 }catch(e){await page.screenshot({path:path.join(DEST,'workbench-failure.png'),fullPage:true}).catch(()=>{});fs.writeFileSync(path.join(DEST,'workbench-report.json'),JSON.stringify({passed:false,error:e.message,reports,errors},null,2));throw e;}finally{await browser.close();}
})();
