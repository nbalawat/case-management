import { useState } from 'react'
import { useApi } from '../useApi'
import { Kpi, Loading } from '../components/ui'
import Register from '../components/governance/Register'
import AccessReviews from '../components/governance/AccessReviews'
import AuditSearch from '../components/governance/AuditSearch'
import type { Governance as View } from '../components/governance/types'

/** Governance: what changed in the setup and who agreed to it, whether access is still right, and the audit trail across cases. */
export default function Governance() {
  const { data, error, reload } = useApi<View>('/governance')
  const [msg, setMsg] = useState<string | null>(null)
  const [month] = useState(() => new Date(Date.now() - 30 * 86400000).toISOString())
  if (!data) return <Loading error={error} />
  const recent = data.register.filter(e => e.at >= month)
  const open = data.access.reviews.find(r => r.status === 'open')
  const outstanding = data.access.reviews.reduce((n, r) => n + (r.status === 'closed' ? r.outstanding : 0), 0)
  return (
    <>
      <div className="page-h"><h1>Governance</h1>
        <p>What changed in how the platform is set up and who agreed to it, whether everyone's access is still right, and the audit trail across cases.</p></div>
      <div className="kpis">
        <Kpi label="Changes in the last 30 days" value={recent.length} sub={`${recent.filter(e => e.second).length} carry a second person`} />
        <Kpi label="Access review" value={open ? `${open.decided} of ${open.people}` : 'None open'} sub={open ? `decided, due ${new Date(open.due).toLocaleDateString('en-US', { month: 'short', day: 'numeric' })}` : `${data.access.reviews.length} on record`} bad={open?.overdue} />
        <Kpi label="Access changes asked for, not yet made" value={outstanding} bad={outstanding > 0} />
        <Kpi label="Audit events you can search" value={data.audit.kinds.reduce((n, k) => n + k.count, 0).toLocaleString()} />
      </div>
      {msg && <p className="notice" role="status">{msg}</p>}
      <Register rows={data.register} areas={data.areas} />
      <AccessReviews access={data.access} onChange={m => { setMsg(m); reload() }} />
      <AuditSearch facets={data.audit} />
    </>
  )
}
