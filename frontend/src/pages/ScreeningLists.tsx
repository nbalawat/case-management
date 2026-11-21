import { useState } from 'react'
import { Link } from 'react-router-dom'
import { useApi } from '../useApi'
import { Card, Loading } from '../components/ui'
import { ListCard, LoadList } from '../components/sanctions/lists'
import type { List } from '../components/sanctions/types'

/**
 * Administration: the lists customers and payments are screened against. Loading a version, adding to the
 * client's own list or switching a list off all screen everyone again. Day-to-day sanctions work stays on the desk.
 */
export default function ScreeningLists() {
  const { data, error, reload } = useApi<{ lists: List[]; can_manage: boolean }>('/sanctions')
  const [msg, setMsg] = useState<string | null>(null)
  if (!data) return <Loading error={error} />
  const done = (m: string) => { setMsg(m); reload() }
  return (
    <>
      <div className="page-h"><h1>Screening lists</h1>
        <p>Published sanctions lists and the client's own list. Every change is versioned, kept on the audit trail, and followed by a rescreen. Held payments and matches are worked on the <Link to="/sanctions">Sanctions desk</Link>.</p></div>
      {msg && <p className="notice" role="status">{msg}</p>}
      {data.can_manage && <Card title="Load a new version"><LoadList lists={data.lists} onChange={done} /></Card>}
      <Card title={`Lists (${data.lists.length})`}>
        {data.lists.map(l => <ListCard key={l.code} l={l} canManage={data.can_manage} onChange={done} />)}
      </Card>
    </>
  )
}
