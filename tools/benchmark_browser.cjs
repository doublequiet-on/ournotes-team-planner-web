// Serial alternating cold-request comparison. Independent public old/new sites.
const fs = require('node:fs'), path = require('node:path'), http = require('node:http');
const assert = require('node:assert/strict');
const {chromium} = require('../browser/node_modules/playwright');
const ROOT = path.resolve(__dirname, '..'), DEST = path.join(ROOT, 'work/performance');
const rounds = Number(process.env.OURNOTES_BENCHMARK_ROUNDS || 5);
fs.mkdirSync(DEST, {recursive: true});
function server(directory) {
  return new Promise(resolve => {
    const listener = http.createServer((request, response) => {
      let relative = decodeURIComponent(new URL(request.url, 'http://localhost').pathname).replace(/^\/ournotes-planner\//, '');
      if (!relative) relative = 'index.html';
      const file = path.resolve(directory, relative);
      if (!file.startsWith(directory + path.sep) || !fs.existsSync(file) || !fs.statSync(file).isFile()) {
        response.writeHead(404); response.end(); return;
      }
      const type = {'.js': 'text/javascript', '.mjs': 'text/javascript', '.wasm': 'application/wasm', '.html': 'text/html', '.css': 'text/css'}[path.extname(file)];
      response.writeHead(200, {'Content-Type': type || 'application/octet-stream', 'Cache-Control': 'no-store'});
      fs.createReadStream(file).pipe(response);
    });
    listener.listen(0, '127.0.0.1', () => resolve({listener, url: `http://127.0.0.1:${listener.address().port}/ournotes-planner/`}));
  });
}
function key(plan, objective) {
  const other = objective === 'event_pt' ? 'shop_pt' : 'event_pt';
  return [plan.totals[objective], plan.totals[other], plan.totals.cp_remaining,
    plan.normal.power, plan.challenge.power, -plan.normal.song_id, -plan.challenge.song_id];
}
function business(result) {
  return ['event_pt', 'shop_pt'].map(objective => [key(result.plans[objective], objective),
    ...['normal', 'challenge'].map(mode => result.top3[objective][mode].map(plan => key(plan, objective)))]);
}
function median(values) {values.sort((a,b) => a-b); return values[Math.floor(values.length / 2)];}
(async () => {
  const sites = {baseline: await server(path.join(ROOT, 'work/performance/baseline-site')),
    optimized: await server(path.join(ROOT, 'browser/dist'))};
  const browser = await chromium.launch({headless:true, channel: process.env.OURNOTES_BROWSER_CHANNEL || (process.platform === 'win32' ? 'msedge' : undefined)});
  const samples = [], references = {};
  try {
    for (let round=0; round<rounds; round++) for (const name of (round === 0 ?
      ['sample-ap', 'solver-top3-resume', 'full-catalog-skip'] : ['sample-ap', 'solver-top3-resume'])) {
      const fixture = JSON.parse(fs.readFileSync(path.join(ROOT, 'work/validation', name + '.json')));
      for (const variant of (round % 2 ? ['optimized', 'baseline'] : ['baseline', 'optimized'])) {
        const context = await browser.newContext();
        await context.route('**/*', route => new URL(route.request().url()).origin === new URL(sites[variant].url).origin ? route.continue() : route.abort());
        try {
          const page = await context.newPage();
          await page.goto(sites[variant].url);
          await page.waitForFunction(() => window.Planner && document.getElementById('ownedCount').textContent.includes(' + '), null, {timeout:90000});
          const value = await page.evaluate(async input => {
            const begin = performance.now();
            const {job_id} = await Planner.request('/api/optimize', {body:JSON.stringify(input)});
            for (;;) {
              const state = await Planner.request('/api/jobs/' + job_id);
              if (state.status === 'complete') return {seconds:(performance.now()-begin)/1000, result:state.result};
              if (state.status !== 'running') throw new Error(state.error || state.status);
              await new Promise(resolve => setTimeout(resolve, 25));
            }
          }, fixture.request);
          assert.deepEqual(business(value.result), business(fixture.expected));
          const row = {round, name, variant, seconds:value.seconds, search_seconds:value.result.search.elapsed_seconds,
            diagnostics:value.result.search.diagnostics, browser_diagnostics:value.result.search.browser_diagnostics};
          samples.push(row); console.log(JSON.stringify(row));
          references[name] = business(value.result);
        } finally {await context.close();}
      }
    }
    const medians = Object.fromEntries(['sample-ap', 'solver-top3-resume', 'full-catalog-skip'].map(name => [name,
      Object.fromEntries(['baseline', 'optimized'].map(variant => [variant, median(samples.filter(r=>r.name===name && r.variant===variant).map(r=>r.seconds))]))]));
    fs.writeFileSync(path.join(DEST, 'browser.json'), JSON.stringify({passed:true, rounds, samples, medians,
      rounds_by_case:{'sample-ap':rounds, 'solver-top3-resume':rounds, 'full-catalog-skip':1},
      browser:browser.version(), scope:'request to complete result including final persistence; excludes initial download and profile import'}, null, 2));
    console.log(JSON.stringify({medians}));
  } finally {await browser.close(); for (const {listener} of Object.values(sites)) listener.close();}
})().catch(error => {console.error(error); process.exitCode=1;});
