const fs = require('node:fs'), path = require('node:path'), assert = require('node:assert/strict');
const {chromium} = require(process.env.OURNOTES_PLAYWRIGHT_MODULE || 'playwright');
const BASE = process.env.OURNOTES_BROWSER_URL || 'http://127.0.0.1:8877/ournotes-planner/';
const DEST = path.resolve(process.argv[2]);
(async () => {
  const browser = await chromium.launch({headless: true, channel: process.env.OURNOTES_BROWSER_CHANNEL || (process.platform === 'win32' ? 'msedge' : undefined)});
  const context = await browser.newContext();
  let attempts = 0, permanentFailures = 0;
  await context.route('**/*', route => {
    const url = new URL(route.request().url());
    if (url.origin !== new URL(BASE).origin) return route.abort();
    if (url.pathname.endsWith('/card-images/members-1.webp') && ++attempts === 1) {
      return route.fulfill({status: 503, contentType: 'text/plain', body: 'Synthetic temporary asset failure'});
    }
    if (url.pathname.endsWith('/card-images/members-2.webp')) {
      permanentFailures++;
      return route.fulfill({status: 503, contentType: 'text/plain', body: 'Synthetic repeated asset failure'});
    }
    return route.continue();
  });
  try {
    const page = await context.newPage(); await page.goto(BASE);
    await page.waitForFunction(() => window.Planner && document.getElementById('ownedCount').textContent.includes(' + '), null, {timeout: 90000});
    await page.locator('[data-tab="inventory"]').click();
    const image = page.locator('#cardCatalog img[src*="members-1.webp"]');
    await image.evaluate(img => img.loading = 'eager');
    await page.waitForFunction(() => {
      const img = document.querySelector('#cardCatalog img[src*="members-1.webp"]');
      return img?.complete && img.naturalWidth > 0 && img.dataset.imageRetries === '1';
    }, null, {timeout: 15000});
    assert.equal(attempts, 2);
    assert.equal(await image.evaluate(img => img.parentElement.classList.contains('image-failed')), false);
    await page.locator('#cardCatalog img[src*="members-2.webp"]').evaluate(img => img.loading = 'eager');
    await page.waitForFunction(() => document.querySelector('#cardCatalog img[src*="members-2.webp"]')?.parentElement.classList.contains('image-failed'), null, {timeout: 15000});
    assert.equal(permanentFailures, 3);
    fs.writeFileSync(path.join(DEST, 'images-report.json'), JSON.stringify({passed: true, attempts, permanentFailures,
      reports: [{case: 'temporary 503 image failure recovers with a bounded static GET retry', passed: true},
                {case: 'repeated image failures stop after two retries and show the fallback', passed: true}]}, null, 2));
    console.log('Temporary card-image failure recovered');
  } finally {await browser.close();}
})();
