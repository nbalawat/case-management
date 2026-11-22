import { useState } from 'react'
import { api } from '../api'
import { useApi } from '../useApi'
import { Card, Kpi, Loading } from '../components/ui'
import PageTabs from '../components/layout/PageTabs'
import RegistryTable from '../components/models/RegistryTable'
import ModelCardView from '../components/models/ModelCardView'
import FeaturesTab from '../components/models/FeaturesTab'
import { num, pct, type Registry } from '../components/models/types'

/** Models that rank alerts: the registry of versions, each with its card, training on decided alerts, a second
 * person's approval before a version is put to work, and the feature store the models read from. */
export default function Models() {
  const { data, error, reload } = useApi<Registry>('/ml/models')
  const [picked, setPicked] = useState<string | null>(null)
  const [msg, setMsg] = useState<string | null>(null)
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)
  if (!data) return <Loading error={error} />

  const champion = data.versions.find(v => v.status === 'champion')
  const waiting = data.versions.filter(v => v.status === 'candidate').length
  const selected = picked ?? champion?.id ?? data.versions[0]?.id ?? null
  const enough = data.data.examples >= data.data.min_rows && data.data.positives >= data.data.min_each && data.data.examples - data.data.positives >= data.data.min_each
  const changed = (m: string) => { setMsg(m); reload() }
  const train = () => {
    setBusy(true); setMsg(null)
    api.post<{ id: string; version: number }>('/ml/models/train', { note })
      .then(r => { setNote(''); setPicked(r.id); changed(`Version ${r.version} trained. It is a candidate until someone other than you approves it.`) })
      .catch(e => setMsg(e.message)).finally(() => setBusy(false))
  }

  return (
    <>
      <div className="page-h"><h1>Models</h1>
        <p>A model reads each new alert's customer behaviour and history and estimates how likely the alert is to matter, so the queue can be worked highest risk first.
          It learns from alerts people have already decided. A version is tested on the newest alerts it never saw, and someone other than the person who trained it approves it before it ranks anything.</p></div>
      <div className="kpis">
        <Kpi label="Ranking new alerts" value={champion ? `v${champion.version}` : 'None'} sub={champion ? champion.algorithm.split(' (')[0] : 'Alerts keep their scenario score'} />
        <Kpi label="AUC on unseen alerts" value={num(champion?.auc)} sub="0.5 is a coin toss, 1.0 perfect" />
        <Kpi label="Could wait, reaching 95%" value={pct(champion?.could_wait_at_95)} sub="of alerts, still reaching 95% of the ones that mattered" />
        <Kpi label="Waiting for approval" value={waiting} bad={waiting > 0} />
      </div>
      {msg && <p className="notice" role="status">{msg}</p>}
      <PageTabs label="Models" tabs={[
        { id: 'registry', label: 'Registry', count: waiting, alert: waiting > 0, content: <>
          <Card title="Versions" action={<span className="muted small">{data.data.examples.toLocaleString()} decided alerts to learn from, {data.data.positives.toLocaleString()} of which mattered</span>}>
            {data.versions.length ? <RegistryTable rows={data.versions} selected={selected} onSelect={id => { setPicked(id); setMsg(null) }} />
              : <p className="empty">No model yet. Train one below once enough alerts have been decided.</p>}
            {data.can.train && (
              <div className="ml-train">
                <textarea rows={2} aria-label="Training note" placeholder="Why train now: new outcomes, a new typology, drift on the current version" value={note} onChange={e => setNote(e.target.value)} />
                <div className="inline">
                  <button className="primary" disabled={busy || !enough} onClick={train}>{busy ? 'Training…' : 'Train a new version'}</button>
                  {!enough && <span className="muted small">Needs {data.data.min_rows} decided alerts with {data.data.min_each} of each outcome.</span>}
                </div>
              </div>
            )}
          </Card>
          {selected && <ModelCardView key={selected} id={selected} can={data.can} onChange={changed} />}
        </> },
        { id: 'features', label: 'Feature store', content: <FeaturesTab /> },
      ]} />
    </>
  )
}
