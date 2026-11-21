import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import App from './App'
import SignIn, { NoRole } from './pages/SignIn'
import { OPERATOR, setPersonaId, setTenantId } from './api'
import { authMode, initAuth } from './auth'
import './styles/app.css'
import { applyTheme, readTheme } from './shell/theme'

applyTheme(readTheme()) // before the first paint, so a dark-theme user never sees a white flash

const root = createRoot(document.getElementById('root')!)

initAuth().then(({ who, error }) => {
  let screen = <BrowserRouter><App /></BrowserRouter>
  if (authMode() !== 'oidc') { /* demo mode: the pickers stand in for sign-in */ }
  else if (who === null) screen = <SignIn error={error} />
  else if (who.operator) setTenantId(OPERATOR)
  else if (who.tenant && who.known) { setTenantId(who.tenant.id); setPersonaId(who.subject!) }
  else screen = <NoRole who={who} />
  root.render(<StrictMode>{screen}</StrictMode>)
}).catch(() => root.render(<SignIn error="The platform could not be reached" />))
