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

const as = async (who, client = 'meridian') => {
  await page.goto(base + '/')
  await page.evaluate(([w, c]) => { localStorage.setItem('tenant', c); if (w) localStorage.setItem(`persona:${c}`, w) }, [who, client])
}
const cases = await (await fetch(`${base}/api/cases?status=all`, { headers: { 'X-Tenant': 'meridian' } })).json()
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
// Data pipelines: sources in object storage with files waiting, then one batch and what it did with each file.
await go('/data'); await shot('17-data-pipelines')
await page.getByRole('button', { name: 'Run all sources' }).click()
await page.getByRole('button', { name: 'Hide' }).waitFor({ timeout: 120000 })  // the page opens the last run of the batch itself
await page.waitForLoadState('networkidle'); await page.waitForTimeout(400)
await shot('18-data-pipeline-run')
await go('/onboarding')
await page.getByRole('button', { name: 'Profile feed' }).click()
await page.getByRole('button', { name: 'Load and run detection' }).waitFor()
await page.getByRole('button', { name: 'Load and run detection' }).click()
await page.getByText('Count reconciliation').waitFor(); await page.waitForLoadState('networkidle'); await page.waitForTimeout(400)
await shot('11-data-onboarding')
// Ask anything: a question answered with the cases behind it.
await as('tara')
await go('/ask')
await page.getByRole('button', { name: 'Which scenario produces the most SARs?' }).click()
await page.getByText('How this was answered').waitFor(); await page.waitForTimeout(400)
await page.evaluate(() => window.scrollTo(0, 0)); await page.waitForTimeout(200)
await page.screenshot({ path: `${out}15-ask.png` })
await as('ana')
await go('/ask')
for (const q of ['Who has the most open work?', 'What should I work on first?']) {
  await page.getByLabel('Your question').fill(q)
  await page.getByRole('button', { name: 'Ask', exact: true }).click()
  await page.getByText('Looking through').waitFor({ state: 'detached' }); await page.waitForTimeout(300)
}
await shot('16-ask-as-analyst')
// Agents: the dial as it starts, then an agent allowed to act after its evaluation, with lookback and tuning run.
await as('omar')
await go('/agents'); await shot('19-agents')
await page.getByLabel('Sanctions adjudicator on Sanctions screening hit').selectOption('L3')
await page.locator('label.limit', { hasText: 'Adjudicator may discount' }).locator('input').fill('0.9')
await page.getByRole('button', { name: 'Evaluate this draft' }).click()
await page.getByText('Ready to publish.').waitFor({ timeout: 60000 })
await page.getByRole('button', { name: 'Publish' }).click()
await page.getByText('Agent policy published.').waitFor()
for (const card of ['Lookback', 'Scenario tuning']) {
  await page.locator('section.card', { hasText: card }).getByRole('button', { name: 'Run' }).click()
  await page.getByText(card === 'Lookback' ? 'Lookback finished.' : 'Tuning review finished.').waitFor({ timeout: 60000 })
}
await page.waitForLoadState('networkidle'); await shot('20-agents-acting')
await as('ivan')
await go(`/cases/${find(c => c.state === 'investigation' && c.owner === 'ivan').id}`)
for (const q of ['Draft a request to the customer', 'Why is this the recommendation?']) {
  await page.getByRole('button', { name: q }).click(); await page.getByText(q, { exact: true }).nth(1).waitFor()
}
await page.locator('section.copilot').scrollIntoViewIfNeeded(); await page.waitForTimeout(300)
await page.locator('section.copilot').screenshot({ path: `${out}21-copilot.png` })
// A second client: different people, roles and workloads, and its own access policy to edit.
await as('joy', 'kestrel')
await go('/'); await shot('12-second-client-my-work')
await as('pat', 'kestrel')
await go('/access')
const box = page.getByLabel('Access policy')
const draft = (await box.inputValue()).replace('version: 1.0.0', 'version: 1.1.0')
  .replace('default_person: nia', '  - {id: uma, name: Uma Reyes, role: fraud_analyst, queues: [fraud], about: New joiner.}\n\ndefault_person: nia')
await box.fill(draft)
await page.getByRole('button', { name: 'Check draft' }).click()
await page.getByText('Valid. Publishing would').waitFor()
await shot('13-second-client-access')
// The provider's own view across clients, and onboarding a new one.
await as(null, '@operator')
await go('/')
await page.getByPlaceholder('Northwind Credit Union').fill('Northwind Credit Union')
await page.getByText('Ready to onboard').waitFor()
await shot('14-operator-console')
await browser.close()
console.log(errors.length ? `Console errors:\n${errors.join('\n')}` : 'Done, no console errors.')
