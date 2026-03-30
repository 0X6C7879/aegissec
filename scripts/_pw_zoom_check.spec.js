const { test } = require('playwright/test');

test('zoom check', async ({ page }) => {
  await page.goto('http://127.0.0.1:5173/sessions/98483f13-17e7-432e-8b39-7ac9c29aa403/chat', { waitUntil: 'networkidle' });
  await page.focus('body');
  const before = await page.evaluate(() => ({ dpr: window.devicePixelRatio, scale: window.visualViewport?.scale ?? null, width: window.innerWidth, height: window.innerHeight }));
  await page.keyboard.press('Control+Equal');
  await page.waitForTimeout(200);
  const after = await page.evaluate(() => ({ dpr: window.devicePixelRatio, scale: window.visualViewport?.scale ?? null, width: window.innerWidth, height: window.innerHeight }));
  console.log('ZOOM_CHECK '+JSON.stringify({before, after}));
});
