const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {chromium}=require(process.env.OURNOTES_PLAYWRIGHT_MODULE || 'playwright');
const BASE=process.env.OURNOTES_BROWSER_URL||'http://127.0.0.1:8877/ournotes-planner/';
const DEST=path.resolve(process.argv[2]);
const asset=fs.readdirSync(path.join(__dirname,'../dist/assets')).find(name=>/^solver-worker-.*\.js$/.test(name));

(async()=>{
 const browser=await chromium.launch({headless:true,channel: process.env.OURNOTES_BROWSER_CHANNEL || (process.platform === 'win32' ? 'msedge' : undefined)});
 const context=await browser.newContext();
 await context.route('**/*',route=>new URL(route.request().url()).origin===new URL(BASE).origin?route.continue():route.abort());
 const page=await context.newPage();
 try{
  await page.goto(BASE);await page.waitForFunction(()=>crossOriginIsolated && window.Planner,null,{timeout:90000});
  const response=await page.evaluate(async url=>{
   const worker=new Worker(url,{type:'module'});
   const shared=new SharedArrayBuffer(4096),control=new Int32Array(shared,0,2);
   const model={variables:[{name:'large integer',domain:['9007199254740993','9007199254740997']}],constraints:[],
    objective:{vars:[0],coeffs:['-1'],offset:0,scalingFactor:-1}};
   worker.postMessage({raw:JSON.stringify({model,parameters:{numSearchWorkers:1,randomSeed:1}}),shared});
   try{
    const end=performance.now()+30000;
    while(!Atomics.load(control,0)){if(performance.now()>end)throw new Error('WASM primitive timeout');await new Promise(r=>setTimeout(r,20));}
    return JSON.parse(new TextDecoder().decode(new Uint8Array(shared,8,Atomics.load(control,1)).slice()));
   }finally{worker.terminate();}
  },new URL('assets/'+asset,BASE).href);
  assert.equal(response.status,4);assert.deepEqual(response.solution,['9007199254740997']);
  fs.writeFileSync(path.join(DEST,'int64-report.json'),JSON.stringify({passed:true,response},null,2));
  console.log('WASM CP-SAT preserves exact integers beyond JavaScript Number precision');
 }finally{await browser.close();}
})();
