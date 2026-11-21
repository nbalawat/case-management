// Walks single sign-on in a browser and saves three screenshots. Needs the app running with sign-in on and the
// built-in development identity provider:  AUTH_MODE=oidc DEV_IDP=1 uv run uvicorn app.main:app --port 8000
import { chromium } from 'playwright'

const base = process.argv[2] ?? 'http://localhost:8000'
const out = new URL('../docs/screenshots/', import.meta.url).pathname
const browser = await chromium.launch()
const page = await browser.newPage({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 2 })
const errors = []
page.on('console', m => m.type() === 'error' && !/401|403|404/.test(m.text()) && errors.push(m.text()))
page.on('pageerror', e => errors.push(String(e)))
const check = (ok, what) => { if (!ok) throw new Error(`Expected: ${what}`) }

await page.goto(base + '/cases'); await page.getByRole('heading', { name: 'Sign in' }).waitFor()
check((await fetch(`${base}/api/cases`, { headers: { 'X-Tenant': 'meridian', 'X-Persona': 'maya' } })).status === 401, 'headers alone are refused')
await page.getByLabel('Organisation').fill('nowhere'); await page.getByRole('button', { name: 'Continue' }).click()
await page.getByRole('alert').waitFor()
await page.getByLabel('Organisation').fill('meridian')
await page.screenshot({ path: `${out}25-sign-in.png` })
await page.getByRole('button', { name: 'Continue' }).click()
await page.getByText('Choose who to sign in as.').waitFor()
check(page.url().includes('/dev-idp/meridian/authorize') && page.url().includes('code_challenge_method=S256'), 'sent to the identity provider with PKCE')
await page.screenshot({ path: `${out}26-identity-provider.png` })
await page.getByRole('link', { name: /Ana Ruiz/ }).click()
await page.getByRole('heading', { name: 'Ana Ruiz' }).waitFor()
check(new URL(page.url()).search === '', 'the code is gone from the address bar')
check(await page.getByRole('combobox').count() === 0, 'no client or person switcher when signed in')
await page.getByText('You can see:').waitFor()
await page.screenshot({ path: `${out}27-signed-in.png` })
await page.goto(base + '/cases'); await page.locator('tbody tr').first().waitFor(); await page.waitForLoadState('networkidle') // still signed in after a reload
const shown = await page.locator('tbody tr').count()
await page.getByRole('button', { name: 'Sign out' }).click(); await page.getByRole('heading', { name: 'Sign in' }).waitFor()

// Platform staff sign in through their own provider and land in the operator console.
await page.getByRole('button', { name: 'Platform staff sign in here' }).click()
await page.getByRole('link', { name: /Olivia Grant/ }).click()
await page.getByText('Operator console.').waitFor()
await page.getByRole('button', { name: 'Sign out' }).click(); await page.getByRole('heading', { name: 'Sign in' }).waitFor()

// A different person at the same client sees a different book.
await page.getByLabel('Organisation').fill('Meridian'); await page.getByRole('button', { name: 'Continue' }).click()
await page.getByRole('link', { name: /Audra Klein/ }).click()
await page.getByRole('heading', { name: 'Audra Klein' }).waitFor()
await page.goto(base + '/cases'); await page.locator('tbody tr').first().waitFor(); await page.waitForLoadState('networkidle')
check(await page.locator('tbody tr').count() > shown, 'the auditor is listed more cases than the analyst')
await browser.close()
console.log(errors.length ? `Console errors:\n${errors.join('\n')}` : `Done, no console errors. Analyst saw ${shown} open cases.`)
