// Regenerates docs/screenshots from a running app:  node scripts/screenshots.mjs [baseUrl]
// Needs Playwright (npm i -D playwright && npx playwright install chromium).
import { chromium } from 'playwright'

const base = process.argv[2] ?? 'http://localhost:8000'
const out = new URL('../docs/screenshots/', import.meta.url).pathname
const browser = await chromium.launch()
const page = await browser.newPage({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 2 })
const errors = []
page.on('console', m => m.type() === 'error' && errors.push(m.text()))
page.on('pageerror', e => errors.push(String(e)))
const shot = async name => { await page.evaluate(() => window.scrollTo(0, 0)); await page.waitForTimeout(150); return page.screenshot({ path: `${out}${name}.png`, fullPage: true }) }
const go = async path => { await page.goto(base + path); await page.waitForLoadState('networkidle') }

const as = async who => { await page.goto(base + '/'); await page.evaluate(w => localStorage.setItem('persona', w), who) }
const cases = await (await fetch(`${base}/api/cases?status=all`)).json()
const find = fn => cases.find(fn) ?? cases[0]

await as('ivan')
await go('/'); await shot('01-my-work')
await go('/overview'); await shot('02-overview')
await go('/cases'); await shot('03-cases')
await go(`/cases/${find(c => c.state === 'investigation' && c.owner === 'ivan').id}`); await shot('04-case-investigation')
await as('maya')
await go(`/cases/${find(c => c.state === 'mlro_review' || c.state === 'senior_approval').id}`); await shot('05-case-approval')
await as('fran')
await go(`/cases/${find(c => c.case_type === 'fraud_claim' && c.state === 'customer_contact').id}`); await shot('06-case-fraud')
await as('tara')
await go('/team'); await shot('07-team')
await as('omar')
await go('/workflows'); await shot('08-workflows')
await page.getByRole('button', { name: 'Check draft' }).click()
await page.getByText('version must be higher').waitFor()
await go('/access'); await shot('09-access')
await go('/platform')
await page.getByRole('button', { name: 'Run benchmark' }).click()
await page.getByRole('button', { name: 'Run benchmark' }).waitFor({ timeout: 120000 })
await shot('10-integrations-scale')
await go('/onboarding')
await page.getByRole('button', { name: 'Profile feed' }).click()
await page.getByRole('button', { name: 'Load and run detection' }).waitFor()
await page.getByRole('button', { name: 'Load and run detection' }).click()
await page.getByText('Count reconciliation').waitFor(); await page.waitForLoadState('networkidle'); await page.waitForTimeout(400)
await shot('11-data-onboarding')
await browser.close()
console.log(errors.length ? `Console errors:\n${errors.join('\n')}` : 'Done, no console errors.')
