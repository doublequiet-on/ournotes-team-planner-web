const fs = require('node:fs'), path = require('node:path'), assert = require('node:assert/strict');
const {chromium} = require(process.env.OURNOTES_PLAYWRIGHT_MODULE || 'playwright');
const BASE = process.env.OURNOTES_BROWSER_URL || 'http://127.0.0.1:8877/ournotes-planner/';
const DEST = path.resolve(process.argv[2]);
const reports = [], errors = [], requests = [], blockedInjections = [];

function key(row, objective) {
  const other = objective === 'event_pt' ? 'shop_pt' : 'event_pt';
  return [row.totals[objective], row.totals[other], row.totals.cp_remaining,
          row.normal.power, row.challenge.power, -row.normal.song_id, -row.challenge.song_id];
}
function compare(actual, expected) {
  assert.equal(actual.search.complete, true);
  assert.deepEqual(actual.difference, expected.difference);
  for (const objective of ['event_pt','shop_pt']) {
    assert.deepEqual(key(actual.plans[objective],objective),key(expected.plans[objective],objective));
    for (const mode of ['normal','challenge']) {
      assert.deepEqual(actual.top3[objective][mode].map(row=>key(row,objective)), expected.top3[objective][mode].map(row=>key(row,objective)));
      assert.equal(new Set(actual.top3[objective][mode].map(row=>row[mode].song_id)).size, expected.top3[objective][mode].length);
    }
  }
}

(async()=>{
  const browser=await chromium.launch({headless:true,channel: process.env.OURNOTES_BROWSER_CHANNEL || (process.platform === 'win32' ? 'msedge' : undefined)});
  const context=await browser.newContext({viewport:{width:1440,height:1050},acceptDownloads:true});
  await context.route('**/*',route=>{
    const url=new URL(route.request().url());
    if(url.origin===new URL(BASE).origin)return route.continue();
    blockedInjections.push({origin:url.origin,type:route.request().resourceType()});
    return route.abort();
  });
  await context.addInitScript(()=>{
    const original=Worker.prototype.postMessage;
    Worker.prototype.postMessage=function(value,...rest){
      if(value?.raw && value?.shared)window.__lastSolverModel=JSON.parse(value.raw);
      return original.call(this,value,...rest);
    };
  });
  const page=await context.newPage();
  page.on('pageerror',error=>errors.push(error.message));
  page.on('request',request=>requests.push({url:request.url(),method:request.method()}));
  page.on('console',message=>{if(message.type()==='error')console.log('Browser console:',message.text());});
  try {
    await page.goto(BASE);
    await page.waitForFunction(()=>(window.Planner && document.getElementById('ownedCount').textContent==='0 + 0') || !document.getElementById('loadError').hidden,null,{timeout:90000});
    if (await page.locator('#loadError').isVisible()) throw new Error(await page.locator('#loadError').textContent());
    assert.equal(await page.evaluate(()=>crossOriginIsolated),true);
    assert.equal(await page.locator('#loadError').isVisible(),false);
    const bootstrap=await page.evaluate(()=>Planner.request('/api/bootstrap'));
    assert.equal(bootstrap.catalog.version,require('../upstream.json').version);
    assert.equal(bootstrap.catalog.songs.length,85);
    assert.equal(bootstrap.catalog.members.length,63);
    assert.equal(bootstrap.calibration.power_recomputed,false);
    assert.equal(bootstrap.demo.is_demo,false);
    assert.ok(bootstrap.catalog.members.every(card=>card.thumbnail.startsWith('card-images/')));
    reports.push({case:'static Pages subpath, first-load isolation, public empty profile',passed:true});
    for(const name of ['sample-ap','all-expert-skip','solver-ap','solver-mixed-rounding','solver-judgement-ap']) {
      const fixture=JSON.parse(fs.readFileSync(path.join(DEST,name+'.json'),'utf8'));
      const begin=Date.now();
      console.log('Running browser oracle:',name);
      await page.locator('#import').setInputFiles({name:'fixture.json',mimeType:'application/json',buffer:Buffer.from(JSON.stringify(fixture.request))});
      await page.locator('#calculate').click();
      await page.waitForFunction(()=>!document.getElementById('results').hidden || (!document.getElementById('progressBox').hidden===false && document.getElementById('message').classList.contains('error')),null,{timeout:240000});
      if(await page.locator('#results').isHidden())throw new Error(await page.locator('#message').textContent());
      const download=page.waitForEvent('download');await page.locator('#exportResult').click();
      const file=await download;await file.saveAs(path.join(DEST,name+'-browser.json'));
      const output=JSON.parse(fs.readFileSync(path.join(DEST,name+'-browser.json'),'utf8'));
      compare(output.result,fixture.expected);
      if(name.startsWith('solver')) {
        assert.equal(output.result.search.algorithm,'adaptive_cp_sat_with_exact_score_oracle');
        assert.equal(output.result.search.optimality_proven,true);
      }
      reports.push({case:name,passed:true,browser_seconds:(Date.now()-begin)/1000,algorithm:output.result.search.algorithm||'matching'});
      console.log(reports.at(-1));
    }
    await page.screenshot({path:path.join(DEST,'browser-desktop.png'),fullPage:true});
    for(const width of [390,768]) {
      await page.setViewportSize({width,height:1000});
      assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
      await page.screenshot({path:path.join(DEST,'browser-'+width+'.png'),fullPage:true});
    }
    await page.locator('[data-tab="inventory"]').click();
    for(const [kind,count] of [['members',63],['snaps',64]]) {
      await page.locator('#addKind').selectOption(kind);
      await page.locator('#cardCatalog .card-art img').evaluateAll(images=>images.forEach(img=>img.loading='eager'));
      await page.waitForFunction(expected=>{
        const images=[...document.querySelectorAll('#cardCatalog .card-art img')];
        return images.length===expected && images.every(img=>img.complete && img.naturalWidth>0);
      },count);
    }
    reports.push({case:'desktop and mobile-width layout, card thumbnails',passed:true});
    assert.deepEqual(errors,[]);
    const appRequests=requests.filter(request=>request.url.startsWith(new URL(BASE).origin));
    const runtimes=appRequests.filter(request=>new URL(request.url).pathname.endsWith('/planner-runtime.zip'));
    assert.ok(runtimes.length>0);
    assert.ok(runtimes.every(request=>new URL(request.url).searchParams.get('v')===require('../package.json').version));
    assert.ok(appRequests.every(request=>request.method==='GET'));
    assert.ok(appRequests.every(request=>!new URL(request.url).pathname.includes('/api/')));
    assert.ok(blockedInjections.every(request=>request.origin.includes('kaspersky-labs.com')));
    reports.push({case:'application requests are static GETs; computation passes with all external requests blocked',passed:true});
    fs.writeFileSync(path.join(DEST,'browser-report.json'),JSON.stringify({passed:true,reports,requests:appRequests,blockedInjections,errors},null,2));
  } catch(error) {
    const model=await page.evaluate(()=>window.__lastSolverModel).catch(()=>null);
    if(model)fs.writeFileSync(path.join(DEST,'failure-model.json'),JSON.stringify(model));
    await page.screenshot({path:path.join(DEST,'browser-failure.png'),fullPage:true}).catch(()=>{});
    fs.writeFileSync(path.join(DEST,'browser-report.json'),JSON.stringify({passed:false,error:error.message,reports,requests,errors},null,2));
    throw error;
  } finally {await browser.close();}
})();
