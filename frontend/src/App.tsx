import { useEffect, useMemo, useState } from 'react'
import { NavLink, Route, Routes } from 'react-router-dom'
import { api, getPersonaId, getTenantId, isOperator, isStaff, OPERATOR, setPersonaId, type Persona } from './api'
import { authMode, signOut } from './auth'
import Clients from './pages/Clients'
import AppShell from './shell/AppShell'
import IdentityMenu, { type Client } from './shell/IdentityMenu'
import { makePeople, PeopleContext } from './shell/people'
import { switchClient } from './shell/switchClient'

type Meta = { tenant: { id: string; name: string }; personas: Persona[]; former?: Record<string, string>; default_person: string; sample_data: boolean }

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
  const [workAt, setWorkAt] = useState<Client[]>([])
  useEffect(() => { if (!sso) api.get<Client[]>('/tenants').then(setClients).catch(() => setClients([])) }, [sso])
  useEffect(() => {
    if (!isStaff()) return
    api.get<{ engagements: { tenant: string; tenant_name: string; live: boolean }[] }>('/provider/me')
      .then(r => setWorkAt(r.engagements.filter(e => e.live).map(e => ({ id: e.tenant, name: e.tenant_name, status: 'live' })))).catch(() => setWorkAt([]))
  }, [])
  useEffect(() => {
    if (operator) return
    api.get<Meta>('/meta').then(m => {
      if (!m.personas.some(p => p.id === getPersonaId())) { setPersonaId(m.default_person); setPid(m.default_person) }
      setMeta(m)
    }).catch(() => { if (!sso && getTenantId() !== 'meridian') switchClient('meridian') }) // the stored client no longer exists
  }, [operator, sso])
  const people = useMemo(() => makePeople(meta?.personas ?? [], pid, meta?.former), [meta, pid])
  const choosePerson = (id: string) => { setPersonaId(id); setPid(id) }

  if (operator) return (
    <>
      <header className="top operator">
        <div className="brand"><span className="mark" aria-hidden />CaseForge</div>
        <nav><NavLink to="/" end>Clients</NavLink></nav>
        {!sso && <ClientPicker clients={clients} />}
        <span className="persona"><span>Signed in as</span><b>Platform operations</b></span>
        {sso && <button className="link signout" onClick={signOut}>Sign out</button>}
      </header>
      <div className="sample">Operator console. Platform staff see how each client is running, in counts only. Client data is visible only inside that client, under its own access policy.</div>
      <main><Routes><Route path="*" element={<Clients />} /></Routes></main>
    </>
  )
  if (!meta || !people.persona) return null
  const persona = people.persona
  return (
    <PeopleContext.Provider value={people}>
      <AppShell clientName={meta.tenant.name} people={meta.personas} sso={sso} sample={meta.sample_data} onPerson={sso ? undefined : choosePerson}
        identity={([theme, setTheme]) => (
          <IdentityMenu persona={persona} people={meta.personas} clientId={getTenantId()} clientName={meta.tenant.name} clients={clients} demo={!sso}
            theme={theme} onTheme={setTheme} onPerson={choosePerson} onClient={switchClient} onSignOut={sso ? signOut : undefined} workAt={workAt} />)} />
    </PeopleContext.Provider>
  )
}
