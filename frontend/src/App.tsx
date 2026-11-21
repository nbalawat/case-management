import { createContext, useContext, useEffect, useMemo, useState } from 'react'
import { NavLink, Route, Routes } from 'react-router-dom'
import { api, getPersonaId, setPersonaId, type Persona } from './api'
import MyWork from './pages/MyWork'
import Overview from './pages/Overview'
import Cases from './pages/Cases'
import CaseWorkspace from './pages/CaseWorkspace'
import Team from './pages/Team'
import Onboarding from './pages/Onboarding'
import Workflows from './pages/Workflows'
import Access from './pages/Access'
import Platform from './pages/Platform'

type Meta = { tenant: string; personas: Persona[]; sample_data: boolean }
type People = { persona: Persona | null; nameOf: (id: string | null | undefined) => string }
const PeopleContext = createContext<People>({ persona: null, nameOf: id => id ?? '' })
export const usePersona = () => useContext(PeopleContext).persona
/** Turns a user id from the API into a display name. */
export const useNameOf = () => useContext(PeopleContext).nameOf

const NAV = [
  ['/', 'My work'], ['/overview', 'Overview'], ['/cases', 'Cases'], ['/team', 'Team'], ['/workflows', 'Workflows'],
  ['/access', 'Access'], ['/onboarding', 'Data onboarding'], ['/platform', 'Integrations and scale'],
]

export default function App() {
  const [meta, setMeta] = useState<Meta | null>(null)
  const [pid, setPid] = useState(getPersonaId())
  useEffect(() => { api.get<Meta>('/meta').then(setMeta).catch(() => setMeta(null)) }, [])
  const people = useMemo<People>(() => {
    const byId = new Map((meta?.personas ?? []).map(p => [p.id, p]))
    return { persona: byId.get(pid) ?? null, nameOf: id => (id ? byId.get(id)?.name ?? id : '') }
  }, [meta, pid])
  return (
    <PeopleContext.Provider value={people}>
      <header className="top">
        <div className="brand"><span className="mark" aria-hidden />CaseForge</div>
        <nav>{NAV.map(([to, label]) => <NavLink key={to} to={to} end={to === '/'}>{label}</NavLink>)}</nav>
        <label className="persona">
          <span>Signed in as</span>
          <select value={pid} onChange={e => { setPersonaId(e.target.value); setPid(e.target.value) }}>
            {meta?.personas.map(p => <option key={p.id} value={p.id}>{p.name} · {p.title}</option>)}
          </select>
        </label>
      </header>
      {meta?.sample_data && <div className="sample">Demo environment. All customers, companies and transactions are synthetic. Switch person at the top right to see the platform through each role.</div>}
      <main key={pid}>
        <Routes>
          <Route path="/" element={<MyWork />} />
          <Route path="/overview" element={<Overview />} />
          <Route path="/cases" element={<Cases />} />
          <Route path="/cases/:id" element={<CaseWorkspace />} />
          <Route path="/team" element={<Team />} />
          <Route path="/onboarding" element={<Onboarding />} />
          <Route path="/workflows" element={<Workflows />} />
          <Route path="/access" element={<Access />} />
          <Route path="/platform" element={<Platform />} />
        </Routes>
      </main>
    </PeopleContext.Provider>
  )
}
