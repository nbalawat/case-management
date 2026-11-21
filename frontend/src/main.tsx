import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import App from './App'
import SignIn, { NoRole } from './pages/SignIn'
import { api, getTenantId, OPERATOR, setPersonaId, setStaffMode, setTenantId } from './api'
import { authMode, initAuth } from './auth'
import './styles/app.css'
import { applyTheme, readTheme } from './shell/theme'

applyTheme(readTheme()) // before the first paint, so a dark-theme user never sees a white flash

const root = createRoot(document.getElementById('root')!)

type Engaged = { engagements: { tenant: string; live: boolean }[] }

/** Provider staff work at whichever client they last chose, if they are still engaged there; otherwise the first one. */
async function chooseStaffClient(console: boolean): Promise<boolean> {
  setStaffMode(true)
  const live = (await api.get<Engaged>('/provider/me')).engagements.filter(e => e.live).map(e => e.tenant)
  if (getTenantId() === OPERATOR && console) return true
  if (live.includes(getTenantId())) return true
  if (console) { setTenantId(OPERATOR); return true }
  if (!live.length) return false
  setTenantId(live[0])
  return true
}

initAuth().then(async ({ who, error }) => {
  let screen = <BrowserRouter><App /></BrowserRouter>
  if (authMode() !== 'oidc') { /* demo mode: the pickers stand in for sign-in */ }
  else if (who === null) screen = <SignIn error={error} />
  else if (who.staff) { if (!(await chooseStaffClient(!!who.operator))) screen = <NoRole who={who} /> }
  else if (who.tenant && who.known) { setTenantId(who.tenant.id); setPersonaId(who.subject!) }
  else screen = <NoRole who={who} />
  root.render(<StrictMode>{screen}</StrictMode>)
}).catch(() => root.render(<SignIn error="The platform could not be reached" />))
