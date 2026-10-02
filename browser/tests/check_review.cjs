// Regression checks for local input loss, damaged caches, and dead workers.
const fs = require('node:fs'), path = require('node:path'), assert = require('node:assert/strict');
const {chromium} = require(process.env.OURNOTES_PLAYWRIGHT_MODULE || 'playwright');
const BASE = process.env.OURNOTES_BROWSER_URL || 'http://127.0.0.1:8877/ournotes-planner/';
const DEST = path.resolve(process.argv[2]);
const fixture = JSON.parse(fs.readFileSync(path.join(DEST, 'sample-ap.json'), 'utf8')).request;
const STORE = 'ournotes-browser-planner-v1-profile:' + new URL(BASE).pathname;
const reports = [];
async function ready(page) {
  await page.waitForFunction(() => window.Planner && document.getElementById('ownedCount').textContent.includes(' + '), null, {timeout: 90000});
}
async function putProfile(page) {
  await page.locator('#import').setInputFiles({name: 'fixture.json', mimeType: 'application/json', buffer: Buffer.from(JSON.stringify(fixture))});
}
async function editName(page, value) {
  await page.evaluate(value => {
    const field = document.getElementById('name');
    field.value = value; field.dispatchEvent(new Event('change', {bubbles: true}));
  }, value);
}

(async () => {
  const browser = await chromium.launch({headless: true, channel: process.env.OURNOTES_BROWSER_CHANNEL || (process.platform === 'win32' ? 'msedge' : undefined)});
  async function check(name, action) {
    const context = await browser.newContext({acceptDownloads: true});
    await context.route('**/*', route => new URL(route.request().url()).origin === new URL(BASE).origin ? route.continue() : route.abort());
    try {await action(context); reports.push({case: name, passed: true});}
    catch (error) {reports.push({case: name, passed: false, error: error.message});}
    finally {await context.close();}
    console.log(reports.at(-1));
  }
  try {
    await check('older tab cannot overwrite the latest personal library', async context => {
      const a = await context.newPage(); await a.goto(BASE); await ready(a); await putProfile(a);
      const b = await context.newPage(); await b.goto(BASE); await ready(b);
      await editName(a, '最新的卡库输入');
      await b.waitForTimeout(250);
      // Simulate an already queued change as well as an ordinary edit.
      await editName(b, '旧标签页的输入');
      assert.equal(await b.evaluate(key => JSON.parse(localStorage.getItem(key)).name, STORE), '最新的卡库输入');
      assert.equal(await b.locator('#name').isDisabled(), true);
      assert.match(await b.locator('#browserProfileConflict').textContent(), /刷新/);
      assert.equal(await b.locator('#export').isEnabled(), true);
      await b.reload(); await ready(b);
      assert.equal(await b.locator('#profileName').textContent(), '最新的卡库输入');
    });
    await check('damaged resume cache does not prevent loading the personal library', async context => {
      const page = await context.newPage(); await page.goto(BASE); await ready(page); await putProfile(page);
      await page.evaluate(async scope => {
        const database = await new Promise((resolve, reject) => {
          const request = indexedDB.open('ournotes-browser-state:' + scope, 1);
          request.onsuccess = () => resolve(request.result); request.onerror = () => reject(request.error);
        });
        await new Promise((resolve, reject) => {
          const tx = database.transaction('state', 'readwrite');
          tx.objectStore('state').put(new Uint8Array([1, 2, 3, 4]).buffer, 'cache');
          tx.oncomplete = resolve; tx.onerror = () => reject(tx.error);
        }); database.close();
      }, new URL(BASE).pathname);
      await page.reload();
      await page.waitForFunction(() => document.getElementById('ownedCount').textContent.includes(' + ') || !document.getElementById('loadError').hidden, null, {timeout: 90000});
      assert.equal(await page.locator('#loadError').isHidden(), true, await page.locator('#loadError').textContent());
      assert.equal(await page.locator('#ownedCount').textContent(), '5 + 5');
      assert.match(await page.locator('#browserStorageError').textContent(), /续算.*损坏/);
      await page.locator('#calculate').click();
      await page.waitForFunction(() => !document.getElementById('results').hidden, null, {timeout: 90000});
      await page.reload(); await ready(page);
      assert.equal(await page.locator('#ownedCount').textContent(), '5 + 5');
      assert.equal(await page.locator('#browserStorageError').count(), 0);
    });
    await check('dead Python worker fails promptly and keeps library export available', async context => {
      await context.addInitScript(() => {
        const original = window.Worker; window.__reviewWorkers = [];
        window.Worker = class extends original {
          constructor(...args) {super(...args); window.__reviewWorkers.push(this);}
        };
      });
      const page = await context.newPage(); await page.goto(BASE); await ready(page); await putProfile(page);
      await page.evaluate(() => {
        const worker = window.__reviewWorkers[0]; worker.terminate();
        worker.onerror({message: 'regression test: process stopped'});
      });
      const response = await page.evaluate(async () => Promise.race([
        Planner.request('/api/check-growth', {body: '{}'}).then(() => 'unexpected success', error => error.message),
        new Promise(resolve => setTimeout(() => resolve('timed out'), 1500))
      ]));
      assert.notEqual(response, 'timed out'); assert.match(response, /刷新/);
      assert.equal(await page.locator('#export').isEnabled(), true);
      assert.equal(await page.evaluate(key => JSON.parse(localStorage.getItem(key)).profile.inventory.members.length, STORE), 5);
    });
  } finally {await browser.close();}
  const passed = reports.every(report => report.passed);
  fs.writeFileSync(path.join(DEST, 'review-report.json'), JSON.stringify({passed, reports}, null, 2));
  if (!passed) process.exitCode = 1;
})();
