// Phone layout check for the list tables (run by tests/test_phone_layout.py).
// At 375px: no horizontal scroll, Streak/Lists/Best/Awards columns hidden, a row tap opens the
// compact line with those four values. At 1280px the columns are visible and the line stays hidden.
const { chromium } = require('playwright');
(async () => {
  const base = process.argv[2], out = [];
  const b = await chromium.launch();
  for (const vw of [375, 1280]) {
    const page = await (await b.newContext({ viewport: { width: vw, height: 900 } })).newPage();
    const errs = []; page.on('pageerror', e => errs.push(e.message));
    for (const p of ['', 'season/2015-16/', 'season/2012-13/', 'season/2006-07/', '?list=R12', 'teams/', 'seasons/', 'records/', 'team/golden-state-warriors/']) {
      await page.goto(base + p, { waitUntil: 'load' }); await page.waitForTimeout(300);
      const r = await page.evaluate(() => {
        const t = document.querySelector('table.lt'), res = { overflow: document.documentElement.scrollWidth > innerWidth };
        if (!t) return res;
        const dx = t.querySelector('td.dx');
        res.dxVisible = !!dx && getComputedStyle(dx).display !== 'none';
        res.xrowVisible = getComputedStyle(t.querySelector('tr.xrow')).display !== 'none';
        return res;
      });
      if (vw === 375 && await page.$('table.lt')) {
        await page.click('table.lt tr.xr td.trend');
        r.afterTap = await page.evaluate(() => {
          const x = document.querySelector('table.lt tr.xrow');
          return getComputedStyle(x).display !== 'none' && /Streak.*Lists.*Best.*Awards/.test(x.textContent);
        });
        r.overflowAfterTap = await page.evaluate(() => document.documentElement.scrollWidth > innerWidth);
      }
      out.push({ vw, p, ...r, errors: errs.length });
    }
  }
  await b.close();
  console.log(JSON.stringify(out));
})();
