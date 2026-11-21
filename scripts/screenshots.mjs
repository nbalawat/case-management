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

await go('/'); await shot('01-overview')
await go('/cases'); await shot('02-cases')
const cases = await (await fetch(`${base}/api/cases`)).json()
const list = Array.isArray(cases) ? cases : cases.cases ?? cases.items
const pick = list.find(c => c.state === 'investigation' && c.case_type === 'tm_alert_investigation') ?? list[0]
await go(`/cases/${pick.id}`); await shot('03-case-workspace')
await go('/workflows'); await shot('06-workflows')
await go('/platform')
await page.getByRole('button', { name: 'Run benchmark' }).click()
await page.getByRole('button', { name: 'Run benchmark' }).waitFor({ timeout: 120000 })
await shot('07-integrations-scale')
await go('/onboarding')
await page.getByRole('button', { name: 'Profile feed' }).click()
await page.getByRole('button', { name: 'Load and run detection' }).waitFor()
await shot('04-onboarding-mapping')
await page.getByRole('button', { name: 'Load and run detection' }).click()
await page.getByText('loaded with rejects').waitFor()
await shot('05-onboarding-reconcile')
await browser.close()
console.log(errors.length ? `Console errors:\n${errors.join('\n')}` : 'Done, no console errors.')
