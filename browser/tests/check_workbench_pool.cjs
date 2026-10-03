const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {chromium}=require('playwright');
const BASE=process.env.OURNOTES_BROWSER_URL||'http://127.0.0.1:8881/ournotes-planner/';
const DEST=path.resolve(process.argv[2]||'work/validation');
(async()=>{
 const browser=await chromium.launch({headless:true,channel:process.platform==='win32'?'msedge':undefined});
 try{
  const page=await browser.newPage();await page.goto(BASE);await page.waitForFunction(()=>document.getElementById('ownedCount').textContent,null,{timeout:120000});
  const boot=await page.evaluate(()=>window.Planner.request('/api/bootstrap')),input=boot.sample;
  input.candidate_member_ids=boot.catalog.members.map(c=>c.id);input.candidate_snap_ids=boot.catalog.snaps.map(c=>c.id);
  input.profile.inventory.members=boot.catalog.members.map(c=>({id:c.id,level:Math.min(20,c.caps[0]),training_count:0,awakening_count:0,live_skill_level:1,gekisou_skill_level:1}));
  input.profile.inventory.snaps=boot.catalog.snaps.map(c=>({id:c.id,level:Math.min(20,c.caps[0]),limit_break_count:0}));input.team_settings.count=5;
  const id=(await page.evaluate(x=>window.Planner.request('/api/optimize',{body:JSON.stringify(x)}),input)).job_id;
  let last='',result;const end=Date.now()+900000;
  while(Date.now()<end){const j=await page.evaluate(id=>window.Planner.request('/api/jobs/'+id),id);const message=j.stage+' '+j.done+'/'+j.total;if(message!==last){console.log(message);last=message;}if(j.status==='error')throw new Error(j.error);if(j.status==='complete'){result=j.result;break;}await new Promise(r=>setTimeout(r,500));}
  assert.ok(result,'full-pool test exceeded its 15-minute test deadline');assert.equal(result.teams.length,5);assert.equal(result.search.chart_count,0);
  fs.writeFileSync(path.join(DEST,'workbench-full-pool.json'),JSON.stringify({input,result},null,2));console.log(JSON.stringify(result.search));
 }finally{await browser.close();}
})();
