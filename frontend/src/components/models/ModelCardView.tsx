import { useState } from 'react'
import { api } from '../../api'
import { useApi } from '../../useApi'
import { Card, Kpi, Loading, Pill } from '../ui'
import WorkloadChart from './WorkloadChart'
import { day, num, pct, STATUS_TONE, type ModelCard, type Registry } from './types'

const DRIFT_TONE = { stable: 'good', watch: 'warning', moved: 'serious', too_few: 'neutral' } as const

/** One version's model card: how it did on alerts it never saw, what it reads, whether today's alerts still look like
 * the ones it learned from, and who did what to it. The approve and promote controls sit here, beside the evidence. */
export default function ModelCardView({ id, can, onChange }: { id: string; can: Registry['can']; onChange: (msg: string) => void }) {
  const { data: m, error, reload } = useApi<ModelCard>(`/ml/models/${id}`)
  const [note, setNote] = useState('')
  const [err, setErr] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  if (!m) return <Card><Loading error={error} /></Card>

  const act = (path: string, body: object, done: string) => {
    setBusy(true); setErr(null)
    api.post(`/ml/models/${m.id}/${path}`, body)
      .then(() => { setNote(''); reload(); onChange(done) })
      .catch(e => setErr(e.message)).finally(() => setBusy(false))
  }
  const own = m.trained_by === can.me
  const mt = m.metrics
  const moved = m.drift.features.filter(f => f.state === 'watch' || f.state === 'moved')
  const judged = m.drift.enough !== false

  return (
    <div className="ws-stack">
      <Card title={`${m.name.replace(/_/g, ' ')} v${m.version}`} action={<Pill tone={STATUS_TONE[m.status]}>{m.status}</Pill>}>
        <p className="muted">{m.algorithm}, trained {day(m.trained)} by {m.trained_by} on {m.data.train_rows?.toLocaleString()} decided alerts
          {m.data.from ? ` from ${day(m.data.from)}` : ''}, then tested on the newest {m.data.test_rows?.toLocaleString()}{m.data.test_from ? ` (from ${day(m.data.test_from)})` : ''}, which it never saw.</p>
        {m.note && <p className="ml-note">{m.note}</p>}
        <div className="kpis tight">
          <Kpi label="AUC on unseen alerts" value={num(mt.auc)} sub={mt.other_algorithm ? `${mt.other_algorithm.split(' (')[0]} scored ${num(mt.other_auc)}` : undefined} />
          <Kpi label="Could wait, reaching 95%" value={pct(mt.work.could_wait_at_95)} sub={`${pct(mt.work.could_wait_at_99)} at 99%`} />
          <Kpi label="Precision-recall AUC" value={num(mt.pr_auc)} sub={`against a base rate of ${pct(mt.base_rate, 1)}`} />
          <Kpi label="Calibration (Brier)" value={num(mt.brier, 3)} sub="lower is better" />
        </div>
        {(m.status === 'candidate' || m.status === 'approved' || m.status === 'challenger' || m.status === 'champion') && can.approve && (
          <div className="ml-actions">
            {m.status === 'candidate' && (<>
              <textarea rows={2} aria-label="Approval note" placeholder="What you checked: the test result, the drift, the reasons on a few alerts" value={note} onChange={e => setNote(e.target.value)} />
              <div className="inline">
                <button className="primary" disabled={busy || own || note.trim().length < 20} onClick={() => act('approve', { note }, `Version ${m.version} approved. It can now be put to work.`)}>Approve</button>
                {own ? <span className="muted small">You trained this version, so someone else approves it.</span>
                  : note.trim().length < 20 && <span className="muted small">Say what you checked (20 characters or more).</span>}
              </div>
            </>)}
            {m.status !== 'candidate' && (
              <div className="inline">
                {m.status !== 'champion' && <button className="primary" disabled={busy} onClick={() => act('promote', { role: 'champion' }, `Version ${m.version} now ranks every new alert.`)}>Make champion</button>}
                {m.status === 'approved' && <button disabled={busy} onClick={() => act('promote', { role: 'challenger' }, `Version ${m.version} now scores alongside the champion, for comparison only.`)}>Run as challenger</button>}
                <button disabled={busy} onClick={() => act('retire', {}, `Version ${m.version} retired.`)}>Retire</button>
              </div>
            )}
            {err && <p className="msg" role="alert">{err}</p>}
          </div>
        )}
      </Card>

      <div className="cols-2">
        <Card title="Work it saves">
          <WorkloadChart work={mt.work} />
          <p className="muted small">Working the newest alerts highest score first. Where the line crosses 95%, everything to its right could wait or go to a lighter review.</p>
        </Card>
        <Card title={`Drift in the last ${m.drift.days} days`} action={!judged ? <Pill>too few alerts to judge</Pill> : moved.length ? <Pill tone="warning">{moved.length} moved</Pill> : <Pill tone="good">stable</Pill>}>
          {m.drift.alerts === 0 ? <p className="empty">No alerts in the last {m.drift.days} days to compare.</p> : (<>
            <p className="muted small">{m.drift.alerts.toLocaleString()} recent alerts compared with what it learned from (population stability index; over 0.25 has moved).
              {m.drift.scored ? ` It scored ${m.drift.scored.toLocaleString()} of them, ${pct(m.drift.mean_score ?? 0)} on average.` : ''}</p>
            {!judged && <p className="notice small">With {m.drift.alerts} alerts, chance alone can move these figures a long way, so they are shown for reference and not judged.
              Drift is judged from {m.drift.min_alerts} alerts in the period.</p>}
            <table><thead><tr><th>Feature</th><th className="num">PSI</th><th /></tr></thead>
              <tbody>{m.drift.features.slice(0, 8).map(f => <tr key={f.feature}><td>{f.label}</td><td className="num">{f.psi.toFixed(3)}</td><td><Pill tone={DRIFT_TONE[f.state]}>{f.state === 'too_few' ? 'not judged' : f.state}</Pill></td></tr>)}</tbody></table>
          </>)}
        </Card>
      </div>

      <div className="cols-2">
        <Card title="What it reads">
          <p className="muted small">{m.features.length} features from the <b>{m.feature_set.id.replace(/_/g, ' ')}</b> set, version {m.feature_set.version}, each as of the moment the alert was raised.</p>
          <ul className="ml-features">{m.features.map(f => <li key={f.name}>{f.label}</li>)}</ul>
        </Card>
        <Card title="History">
          <ol className="ml-history">
            <li><b>Trained</b> by {m.trained_by} <span className="muted small">{day(m.trained)}</span></li>
            {m.history.map((h, i) => <li key={i}><b>{h.what[0].toUpperCase() + h.what.slice(1)}</b> by {h.by} <span className="muted small">{day(h.at)}</span>{h.note ? <div className="small">{h.note}</div> : null}</li>)}
          </ol>
          <p className="muted small lin-mono" title="The stored model file is checked against this before it is loaded">sha256 {m.sha256.slice(0, 16)}…</p>
        </Card>
      </div>
    </div>
  )
}
