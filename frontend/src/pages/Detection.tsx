import { useState } from 'react'
import { useApi } from '../useApi'
import { Card, Kpi, Loading } from '../components/ui'
import PageTabs from '../components/layout/PageTabs'
import ScenarioCard from '../components/detection/ScenarioCard'
import Lookbacks from '../components/detection/LookbackCard'
import type { Desk } from '../components/detection/types'

/** Detection governance: what each scenario runs on at this client, changes tried against the past and approved by a second person, and lookbacks. */
export default function Detection() {
  const { data, error, reload } = useApi<Desk>('/detection')
  const [msg, setMsg] = useState<string | null>(null)
  if (!data) return <Loading error={error} />
  const changed = (m: string) => { setMsg(m); reload() }
  const waiting = data.scenarios.filter(s => s.pending).length
  const working = data.lookbacks.filter(l => l.status === 'working')
  return (
    <>
      <div className="page-h"><h1>Detection</h1>
        <p>What each scenario runs on for this client. A change is tried against the past year before it is proposed, and a second person puts it live. A lookback runs a past period again and sends everything it finds to people.</p></div>
      <div className="kpis">
        <Kpi label="Scenarios running" value={data.scenarios.filter(s => s.enabled).length} sub={`of ${data.scenarios.length}`} />
        <Kpi label="On this client's own settings" value={data.scenarios.filter(s => s.revision > 0).length} sub="the rest run on defaults" />
        <Kpi label="Changes waiting for a second person" value={waiting} bad={waiting > 0} />
        <Kpi label="Lookbacks in hand" value={data.lookbacks.filter(l => ['planned', 'working'].includes(l.status)).length} sub={`${working.reduce((n, l) => n + l.progress.open, 0)} of their cases open`} />
      </div>
      {msg && <p className="notice" role="status">{msg}</p>}
      <PageTabs label="Detection" tabs={[
        { id: 'scenarios', label: 'Scenarios', count: waiting, alert: waiting > 0, content: <>
      <Card title="Scenarios">{data.scenarios.map(sc => <ScenarioCard key={sc.scenario + sc.revision + (sc.pending?.id ?? '')} sc={sc} desk={data} onChange={changed} />)}</Card>
        </> },
        { id: 'lookbacks', label: 'Lookbacks', count: data.lookbacks.filter(l => ['planned', 'working'].includes(l.status)).length, content: <>
      <Card title={`Lookbacks (${data.lookbacks.length})`}><Lookbacks desk={data} onChange={changed} /></Card>
        </> },
      ]} />
    </>
  )
}
