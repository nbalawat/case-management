import { createContext, useContext, useEffect, useMemo, useState } from 'react'
import { NavLink, Route, Routes, useLocation } from 'react-router-dom'
import { api, getPersonaId, getTenantId, isOperator, OPERATOR, setPersonaId, setTenantId, type Persona } from './api'
import { authMode, signOut } from './auth'
import MyWork from './pages/MyWork'
import Ask from './pages/Ask'
import Overview from './pages/Overview'
import Cases from './pages/Cases'
import CaseWorkspace from './pages/CaseWorkspace'
import Team from './pages/Team'
import Onboarding from './pages/Onboarding'
import Data from './pages/Data'
import Agents from './pages/Agents'
import Workflows from './pages/Workflows'
import Access from './pages/Access'
import Platform from './pages/Platform'
import Clients from './pages/Clients'
import Customer from './pages/Customer'
import Counterparty from './pages/Counterparty'
import Entities from './pages/Entities'
import Filings from './pages/Filings'
import Filing from './pages/Filing'
import Alerts from './pages/Alerts'
import Reference from './pages/Reference'
import Kyc from './pages/Kyc'
import Sanctions from './pages/Sanctions'
import Fraud from './pages/Fraud'
import Detection from './pages/Detection'
import Search from './components/Search'

type Meta = { tenant: { id: string; name: string }; personas: Persona[]; default_person: string; sample_data: boolean }
type Client = { id: string; name: string; status: string }
type People = {
  persona: Persona | null
  nameOf: (id: string | null | undefined) => string
  /** Whether the signed-in person holds a permission, and who does if they do not. */
  can: (permission: string) => boolean
  holder: (permission: string) => string
}
const PeopleContext = createContext<People>({ persona: null, nameOf: id => id ?? '', can: () => false, holder: () => '' })
export const usePeople = () => useContext(PeopleContext)
export const usePersona = () => useContext(PeopleContext).persona
/** Turns a user id from the API into a display name. */
export const useNameOf = () => useContext(PeopleContext).nameOf

const NAV = [
  ['/', 'My work'], ['/ask', 'Ask'], ['/overview', 'Overview'], ['/cases', 'Cases'], ['/entities', 'Entities'], ['/kyc', 'KYC'], ['/sanctions', 'Sanctions'], ['/fraud', 'Fraud'], ['/filings', 'Reports'], ['/team', 'Team'], ['/agents', 'Agents'],
]
// Setting the platform up, as opposed to working in it.
const SETUP = [['/workflows', 'Workflows'], ['/reference', 'Lists'], ['/detection', 'Detection'], ['/access', 'Access'], ['/data', 'Data'], ['/platform', 'Integrations']]

/** The setup pages behind one menu, so the working pages keep the bar. It closes itself when the page changes. */
function SetupMenu() {
  const { pathname } = useLocation()
  const here = SETUP.find(([to]) => pathname.startsWith(to))
  return (
    <details className="setup" key={pathname}>
      <summary className={here ? 'active' : ''}>Setup</summary>
      <div>{SETUP.map(([to, label]) => <NavLink key={to} to={to}>{label}</NavLink>)}</div>
    </details>
  )
}

/** Switching client is a clean break: nothing from one client may linger on screen for the next. */
export function switchClient(id: string) {
  setTenantId(id)
  window.location.assign('/')
}

function ClientPicker({ clients }: { clients: Client[] }) {
  return (
    <label className="persona">
      <span>Client</span>
      <select value={getTenantId()} onChange={e => switchClient(e.target.value)}>
        {clients.map(c => <option key={c.id} value={c.id} disabled={c.status !== 'live'}>{c.name}{c.status !== 'live' ? ' (suspended)' : ''}</option>)}
        <option value={OPERATOR}>All clients · operator console</option>
      </select>
    </label>
  )
}

export default function App() {
  const operator = isOperator()
  const [clients, setClients] = useState<Client[]>([])
  const [meta, setMeta] = useState<Meta | null>(null)
  const [pid, setPid] = useState(getPersonaId())
  const sso = authMode() === 'oidc'
  useEffect(() => { if (!sso) api.get<Client[]>('/tenants').then(setClients).catch(() => setClients([])) }, [sso])
  useEffect(() => {
    if (operator) return
    api.get<Meta>('/meta').then(m => {
      if (!m.personas.some(p => p.id === getPersonaId())) { setPersonaId(m.default_person); setPid(m.default_person) }
      setMeta(m)
    }).catch(() => { if (!sso && getTenantId() !== 'meridian') switchClient('meridian') }) // the stored client no longer exists
  }, [operator, sso])
  const people = useMemo<People>(() => {
    const all = meta?.personas ?? []
    const byId = new Map(all.map(p => [p.id, p]))
    const persona = byId.get(pid) ?? null
    return {
      persona, nameOf: id => (id ? byId.get(id)?.name ?? id : ''),
      can: permission => !!persona?.permissions.includes(permission),
      holder: permission => { const p = all.find(x => x.permissions.includes(permission)); return p ? `${p.name} (${p.title})` : 'nobody' },
    }
  }, [meta, pid])
  if (operator) return (
    <>
      <header className="top operator">
        <div className="brand"><span className="mark" aria-hidden />CaseForge</div>
        <nav><NavLink to="/" end>Clients</NavLink></nav>
        {!sso && <ClientPicker clients={clients} />}
        <span className="persona"><span>Signed in as</span><b>Olivia Grant · Platform operations</b></span>
        {sso && <button className="link signout" onClick={signOut}>Sign out</button>}
      </header>
      <div className="sample">Operator console. Platform staff see how each client is running, in counts only. Client data is visible only inside that client, under its own access policy.</div>
      <main><Routes><Route path="*" element={<Clients />} /></Routes></main>
    </>
  )
  return (
    <PeopleContext.Provider value={people}>
      <header className="top">
        <div className="brand"><span className="mark" aria-hidden />CaseForge</div>
        <nav>{NAV.map(([to, label]) => <NavLink key={to} to={to} end={to === '/'}>{label}</NavLink>)}
        </nav>
        <SetupMenu />
        {meta && people.persona && <Search key={pid} />}
        {sso ? <>
          <span className="persona"><span>{meta?.tenant.name}</span><b>{people.persona ? `${people.persona.name} · ${people.persona.title}` : ''}</b></span>
          <button className="link signout" onClick={signOut}>Sign out</button>
        </> : <>
          <ClientPicker clients={clients} />
          <label className="persona">
            <span>Signed in as</span>
            <select value={pid} onChange={e => { setPersonaId(e.target.value); setPid(e.target.value) }}>
              {meta?.personas.map(p => <option key={p.id} value={p.id}>{p.name} · {p.title}</option>)}
            </select>
          </label>
        </>}
      </header>
      {meta?.sample_data && <div className="sample">Demo environment for <b>{meta.tenant.name}</b>. All customers, companies and transactions are synthetic. {sso
        ? 'You are signed in through your organisation, and see only what your roles allow.'
        : 'Switch client or person at the top right: each client has its own people, roles, workflows and data.'}</div>}
      {meta && people.persona && <main key={pid}>
        <Routes>
          <Route path="/" element={<MyWork />} />
          <Route path="/ask" element={<Ask />} />
          <Route path="/overview" element={<Overview />} />
          <Route path="/cases" element={<Cases />} />
          <Route path="/cases/:id" element={<CaseWorkspace />} />
          <Route path="/alerts" element={<Alerts />} />
          <Route path="/reference" element={<Reference />} />
          <Route path="/entities" element={<Entities />} />
          <Route path="/kyc" element={<Kyc />} />
          <Route path="/sanctions" element={<Sanctions />} />
          <Route path="/fraud" element={<Fraud />} />
          <Route path="/detection" element={<Detection />} />
          <Route path="/filings" element={<Filings />} />
          <Route path="/filings/:id" element={<Filing />} />
          <Route path="/customers/:id" element={<Customer />} />
          <Route path="/counterparties/:id" element={<Counterparty />} />
          <Route path="/team" element={<Team />} />
          <Route path="/agents" element={<Agents />} />
          <Route path="/data" element={<Data />} />
          <Route path="/onboarding" element={<Onboarding />} />
          <Route path="/workflows" element={<Workflows />} />
          <Route path="/access" element={<Access />} />
          <Route path="/platform" element={<Platform />} />
        </Routes>
      </main>}
    </PeopleContext.Provider>
  )
}
