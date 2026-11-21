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
// Read as the auditor, who sees every case; most people now see only part of the book.
const cases = await (await fetch(`${base}/api/cases?status=all`, { headers: { 'X-Tenant': 'meridian', 'X-Persona': 'audra' } })).json()
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
// Who sees what: a restricted case as someone cleared for it, restricting another, and an analyst's narrower view.
const secret = find(c => c.restricted && c.open)
await as('lena')
await go(`/cases/${secret.id}`); await page.getByText('Restricted case.').waitFor(); await shot('22-restricted-case')
await as('ivan')
const before = errors.length
await go(`/cases/${secret.id}`); await page.getByText('Case not found').waitFor()
errors.length = before // the 404 is the point: for him the case does not exist
await as('maya')
await go(`/cases/${find(c => c.state === 'investigation' && c.owner === 'ivan').id}`)
await page.getByRole('button', { name: 'Restrict…' }).click()
await page.getByLabel('Why is this case restricted?').fill('The subject is related to a board member')
await page.locator('.restrict-form').scrollIntoViewIfNeeded(); await page.screenshot({ path: `${out}23-restricting-a-case.png` })
await page.getByRole('button', { name: 'Cancel' }).click()
await as('ana')
await go('/'); await page.getByText('You can see:').waitFor()
await go('/cases'); await shot('24-cases-as-analyst')
// Entities: one customer's whole picture, the network around them, a counterparty under all its spellings, the desk, and search.
const H = { 'X-Tenant': 'meridian', 'X-Persona': 'audra' }
const desk = await (await fetch(`${base}/api/entities`, { headers: H })).json()
// The most telling subject: a customer of a flagged firm who already has an open case, at the firm most customers share.
let firm = desk.flagged[0], subject = null
for (const f of desk.flagged) {
  const d = await (await fetch(`${base}/api/counterparties/${f.id}`, { headers: H })).json()
  for (const c of d.customers) {
    if (!subject || c.open_cases > subject.open_cases || (c.open_cases === subject.open_cases && f.customers > firm.customers)) { subject = c; firm = f }
  }
}
await as('ivan')
await go(`/customers/${subject.party_id}`); await page.locator('.net svg').waitFor(); await shot('28-customer-360')
await page.locator('.net-node.flagged rect').first().hover(); await page.locator('.net').screenshot({ path: `${out}29-payment-network.png` })
await go(`/counterparties/${firm.id}`); await shot('30-counterparty')
await go('/entities'); await page.getByRole('button', { name: 'Same' }).first().click()
await page.getByText('Decisions on record').waitFor(); await shot('31-entities-desk')
await page.getByLabel('Search customers, counterparties, cases').fill('volkof')
await page.locator('.search-pop button').first().waitFor(); await page.screenshot({ path: `${out}32-search.png` })
await page.keyboard.press('Escape')
// Regulatory reports: the register, one accepted SAR with its document, and a CTR prepared and sent from a cash day.
await as('maya')
await go('/filings'); await page.getByText('Cash over $10,000').waitFor(); await shot('33-reports')
await page.getByRole('button', { name: 'Accepted' }).click()
await page.locator('table.rows tbody tr').first().click(); await page.locator('pre.doc').waitFor(); await shot('34-report-accepted')
await go('/filings'); await page.getByRole('button', { name: 'Prepare CTR' }).first().click()
await page.getByRole('button', { name: 'Send to the regulator' }).click()
await page.getByText('Record the regulator').waitFor(); await shot('35-ctr-sent')
// Casework at volume: a closing reason, a merge, bulk steps, waiting alerts, a QA scorecard and its findings, and the client's lists.
const fresh = await (await fetch(`${base}/api/cases?status=open`, { headers: H })).json()
const twins = fresh.filter(c => c.case_type === 'tm_alert_investigation' && fresh.some(o => o.id !== c.id && o.party_id === c.party_id && o.case_type === c.case_type))
await as('ana')
const twin = twins.find(c => !c.owner) ?? twins[0]
const keep = twins.find(c => c.id !== twin.id && c.party_id === twin.party_id)
await fetch(`${base}/api/cases/${keep.id}/claim`, { method: 'POST', headers: { 'X-Tenant': 'meridian', 'X-Persona': 'ana', 'Content-Type': 'application/json' }, body: '{}' })
await go(`/cases/${twin.id}`); await page.getByText('Related cases').waitFor()
await page.getByLabel('Closing reason').selectOption({ index: 2 })
await page.getByLabel('Why these are one matter').fill('Same pass-through pattern, carried over twice from the previous system')
await shot('36-closing-reason-and-merge')
await go('/cases')
const boxes = page.locator('table.rows tbody input[type=checkbox]')
for (const i of [0, 1, 2, 3]) await boxes.nth(i).check()
await page.locator('.bulk').waitFor(); await page.getByRole('button', { name: 'Claim', exact: true }).click()
await page.locator('.bulk-result').waitFor(); await page.screenshot({ path: `${out}37-bulk-actions.png` })
await go('/alerts'); await page.getByText('Hibernation rules').waitFor(); await shot('38-alert-intake')
await as('quinn')
const review = fresh.find(c => c.case_type === 'qa_review')
await go(`/cases/${review.id}`); await page.getByText('QA scorecard').waitFor()
for (const g of await page.locator('.scorecard .criterion').all()) await g.getByRole('button', { name: 'Met', exact: true }).click()
await page.locator('.scorecard .criterion').nth(1).getByRole('button', { name: 'Partly' }).click()
await page.locator('.scorecard .criterion').nth(1).locator('select').selectOption('weak_rationale')
await page.locator('.scorecard .criterion').nth(1).locator('input').fill('Says the activity is normal but not why')
await page.getByRole('button', { name: 'Save scorecard' }).click(); await page.getByText('Scorecard saved').waitFor()
await page.locator('.scorecard').scrollIntoViewIfNeeded(); await shot('39-qa-scorecard')
await as('tara')
await go('/team'); await page.getByText('Quality assurance findings').waitFor(); await shot('40-qa-findings')
await as('omar')
await go('/reference'); await page.getByText('Reasons for closing a case').waitFor(); await shot('41-lists')
// KYC depth: the desk, a rating waiting for a second person, an ownership chart, documents with custody, and flow of funds.
const kycDesk = await (await fetch(`${base}/api/kyc`, { headers: H })).json()
const owned = name => kycDesk.structures.find(x => x.party_name.startsWith(name)).party_id
await as('maya')
await go('/kyc'); await page.getByText('Lower ratings waiting for a second person').waitFor(); await shot('42-kyc-desk')
await go(`/customers/${kycDesk.pending_overrides[0].party_id}`); await page.locator('.scale').waitFor()
await page.locator('section.card', { hasText: 'Risk rating' }).first().screenshot({ path: `${out}43-risk-rating-override.png` })
await as('ivan')
await go(`/customers/${owned('Northwind')}`); await page.locator('svg.own').waitFor(); await shot('44-customer-kyc')
await page.locator('section.card', { hasText: 'Ownership and control' }).screenshot({ path: `${out}45-ownership.png` })
const papers = kycDesk.documents.find(d => d.expired) ?? kycDesk.documents[0]
await go(`/customers/${papers.party_id}`); await page.locator('table.docs').waitFor()
await page.locator('table.docs button.cite').first().click(); await page.locator('.custody').waitFor()
await page.locator('section.card', { hasText: 'Documents' }).last().screenshot({ path: `${out}46-documents.png` })
// The case whose alerted payments show the most: money both in and out, ideally through a flagged firm.
let moving = null, best = -1
for (const c of fresh.filter(c => c.case_type === 'tm_alert_investigation').slice(0, 40)) {
  const f = await (await fetch(`${base}/api/customers/${c.party_id}/flow?case_id=${c.id}`, { headers: H })).json()
  const score = Math.min(f.sources?.length ?? 0, f.destinations?.length ?? 0) * 10 + (f.sources?.length ?? 0) + (f.destinations?.length ?? 0)
    + 5 * [...(f.sources ?? []), ...(f.destinations ?? [])].filter(n => n.flags.length).length
  if (score > best) { best = score; moving = c }
}
await as('maya')
await go(`/cases/${moving.id}`); await page.locator('svg.flow').waitFor()
await page.locator('section.card', { hasText: 'Flow of funds' }).screenshot({ path: `${out}47-flow-of-funds.png` })
// Fraud and sanctions depth: the sanctions desk, a held payment waiting for a second person, and the fraud desk with a dispute on its clocks.
const sanctionsDesk = await (await fetch(`${base}/api/sanctions`, { headers: H })).json()
await as('maya')
await go('/sanctions'); await page.getByText('Held now').first().waitFor(); await shot('48-sanctions-desk')
const waiting = sanctionsDesk.holds.find(h => h.proposal) ?? sanctionsDesk.holds[0]
await go(`/cases/${waiting.case_id}`); await page.locator('.hold').first().waitFor(); await page.locator('.match').first().waitFor()
await page.locator('section.card', { hasText: 'List match' }).first().screenshot({ path: `${out}49-list-match.png` })
await page.locator('section.card', { has: page.locator('.hold') }).first().screenshot({ path: `${out}50-hold-second-person.png` })
const fraudDesk = await (await fetch(`${base}/api/fraud`, { headers: H })).json()
await as('fran')
await go('/fraud'); await page.locator('.reasons').waitFor(); await shot('51-fraud-desk')
await go(`/cases/${fraudDesk.disputes.find(d => d.status !== 'closed').case_id}`); await page.locator('section.card', { hasText: 'Disputes' }).first().waitFor()
await page.locator('section.card', { hasText: 'Disputes (' }).first().screenshot({ path: `${out}52-dispute.png` })
// Detection governance: a change waiting with its backtest, trying settings against the past, and a lookback run.
await as('maya')
await go('/detection'); await page.locator('.backtest').first().waitFor(); await shot('53-detection')
await as('omar')
await go('/detection')
const rapid = page.locator('.scn', { hasText: 'Rapid movement of funds' })
await rapid.getByRole('button', { name: 'Try different settings…' }).click()
await rapid.getByLabel('Hours within which it must leave').fill('24')
await rapid.getByRole('button', { name: 'Run against the last year' }).click(); await rapid.locator('.backtest').waitFor()
await shot('54-backtest')
await as('hugo')
await go('/detection')
await page.getByRole('button', { name: /^Run it: raise/ }).click(); await page.locator('.lb-prog').waitFor()
await page.locator('section.card', { hasText: 'Lookbacks (' }).screenshot({ path: `${out}55-lookback.png` })
// Management reporting and governance: last month's report, the change register, a recertification and the audit trail.
await as('tara')
await go('/overview/report'); await page.locator('.chart svg').first().waitFor(); await shot('56-management-report')
await as('maya')
await go('/governance'); await page.getByText('Change register').waitFor()
await page.getByLabel('Review name').fill('Access recertification, November'); await page.getByRole('button', { name: 'Start a review' }).click()
await page.getByText('Open: 0 of').waitFor()
await page.getByLabel('Note on Ana Ruiz').fill('Still triages both queues; confirmed with Tara.'); await page.getByRole('row', { name: /Ana Ruiz/ }).getByRole('button', { name: 'Keep' }).click()
await page.getByText('Open: 1 of').waitFor(); await shot('57-governance')
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
