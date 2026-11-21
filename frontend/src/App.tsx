import { createContext, useContext, useEffect, useState } from 'react'
import { NavLink, Route, Routes } from 'react-router-dom'
import { api, getPersonaId, setPersonaId, type Persona } from './api'
import Overview from './pages/Overview'
import Cases from './pages/Cases'
import CaseWorkspace from './pages/CaseWorkspace'
import Onboarding from './pages/Onboarding'
import Workflows from './pages/Workflows'
import Platform from './pages/Platform'

type Meta = { tenant: string; personas: Persona[]; sample_data: boolean }
export const PersonaContext = createContext<Persona | null>(null)
export const usePersona = () => useContext(PersonaContext)

const NAV = [
  ['/', 'Overview'], ['/cases', 'Cases'], ['/onboarding', 'Data onboarding'], ['/workflows', 'Workflows'], ['/platform', 'Integrations and scale'],
]

export default function App() {
  const [meta, setMeta] = useState<Meta | null>(null)
  const [pid, setPid] = useState(getPersonaId())
  useEffect(() => { api.get<Meta>('/meta').then(setMeta).catch(() => setMeta(null)) }, [])
  const persona = meta?.personas.find(p => p.id === pid) ?? null
  return (
    <PersonaContext.Provider value={persona}>
      <header className="top">
        <div className="brand"><span className="mark" aria-hidden />CaseForge</div>
        <nav>{NAV.map(([to, label]) => <NavLink key={to} to={to} end={to === '/'}>{label}</NavLink>)}</nav>
        <label className="persona">
          <span>Viewing as</span>
          <select value={pid} onChange={e => { setPersonaId(e.target.value); setPid(e.target.value) }}>
            {meta?.personas.map(p => <option key={p.id} value={p.id}>{p.name} · {p.title}</option>)}
          </select>
        </label>
      </header>
      {meta?.sample_data && <div className="sample">Demo environment. All customers, companies and transactions are synthetic.</div>}
      <main key={pid}>
        <Routes>
          <Route path="/" element={<Overview />} />
          <Route path="/cases" element={<Cases />} />
          <Route path="/cases/:id" element={<CaseWorkspace />} />
          <Route path="/onboarding" element={<Onboarding />} />
          <Route path="/workflows" element={<Workflows />} />
          <Route path="/platform" element={<Platform />} />
        </Routes>
      </main>
    </PersonaContext.Provider>
  )
}
