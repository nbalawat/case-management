import { Fragment, useMemo, useState } from 'react'
import { api } from '../api'
import { useApi } from '../useApi'
import { Card, Loading, Pill } from '../components/ui'
import LineageGraph from '../components/lineage/LineageGraph'
import type { LEdge, LNode } from '../components/lineage/layout'

type Graph = { days: number; nodes: LNode[]; edges: LEdge[]; sent_to: string | null }
type Ds = { namespace: string; name: string; kind: string; rows?: number | null }
type Run = { id: number; job: string; run_id: string; state: string; at: string; inputs: Ds[]; outputs: Ds[] }

const when = (iso: string) => new Date(iso + (iso.endsWith('Z') ? '' : 'Z')).toLocaleString()
const PERIODS = [7, 30, 90]

/** Where this client's data came from and where it went: every pipeline, stream consumer, lake writer, backfill and
 * backup as OpenLineage run events, drawn as one graph. The same events can go to the client's own catalog. */
export default function Lineage() {
  const [days, setDays] = useState(30)
  const g = useApi<Graph>(`/lineage/graph?days=${days}`)
  const [selected, setSelected] = useState<string | null>(null)
  const pick = (id: string | null) => { setSelected(id); setShown(12) }
  const node = g.data?.nodes.find(x => x.id === selected) ?? null
  const job = node?.type === 'job' ? node.name : null
  const runs = useApi<{ events: Run[] }>(`/lineage/events?limit=50${job ? `&job=${encodeURIComponent(job)}` : ''}`)
  const [raw, setRaw] = useState<{ id: number; body: unknown } | null>(null)
  const [shown, setShown] = useState(12)
  const stats = useMemo(() => {
    const ns = g.data?.nodes ?? []
    return { datasets: ns.filter(x => x.type === 'dataset').length, jobs: ns.filter(x => x.type === 'job').length,
      runs: ns.reduce((a, x) => a + (x.runs ?? 0), 0), failed: ns.reduce((a, x) => a + (x.failed ?? 0), 0) }
  }, [g.data])
  const touching = (r: Run) => !node || node.type === 'job' || [...r.inputs, ...r.outputs].some(d => `ds:${d.namespace}|${d.name}` === node.id)
  const showRaw = (id: number) => (raw?.id === id ? setRaw(null) : api.get<unknown>(`/lineage/events/${id}`).then(body => setRaw({ id, body })))

  return (
    <>
      <div className="page-h"><h1>Data lineage</h1>
        <p>Every job that moves this client's data, what it read and what it wrote, from the files and topics data arrives on to the tables, lake and backups it ends up in.
          Each run is an <a href="https://openlineage.io" target="_blank" rel="noreferrer">OpenLineage</a> event{g.data?.sent_to ? <>, also sent to <b>{g.data.sent_to}</b></> : <>; set LINEAGE_URL to send them to a catalog such as Marquez, DataHub or OpenMetadata</>}.</p></div>
      <div className="toolbar">
        <div className="seg" role="group" aria-label="Period">{PERIODS.map(p => <button key={p} aria-pressed={days === p} onClick={() => setDays(p)}>{p} days</button>)}</div>
        {g.data && <span className="muted small">{stats.datasets} datasets · {stats.jobs} jobs · {stats.runs.toLocaleString()} runs{stats.failed ? ` · ${stats.failed} failed` : ''}</span>}
      </div>
      {!g.data ? <Loading error={g.error} /> : !g.data.nodes.length ? (
        <Card><p className="empty">No lineage in the last {days} days. It appears as soon as a data pipeline runs, a stream consumer or the lake writer moves data, or a backup is taken.</p></Card>
      ) : (
        <div className="lin">
          <Card className="lin-main"><LineageGraph nodes={g.data.nodes} edges={g.data.edges} selected={selected} onSelect={pick} /></Card>
          {node && (
            <Card className="lin-side" title={node.name} action={<button className="link" onClick={() => pick(null)}>Clear</button>}>
              <dl className="facts">
                {node.type === 'job' ? <>
                  <dt>Job</dt><dd>{node.name}</dd>
                  <dt>Runs</dt><dd>{(node.runs ?? 0).toLocaleString()}{node.failed ? <> · <Pill tone="critical">{node.failed} failed</Pill></> : null}</dd>
                  <dt>Last run</dt><dd>{node.last_run ? when(node.last_run) : 'n/a'} {node.last_state === 'FAIL' && <Pill tone="critical">failed</Pill>}</dd>
                </> : <>
                  <dt>Where</dt><dd className="lin-mono">{node.namespace}</dd>
                  <dt>Name</dt><dd className="lin-mono">{node.name}</dd>
                  <dt>Rows written</dt><dd>{(node.rows_written ?? 0).toLocaleString()} in {days} days</dd>
                  <dt>Read by</dt><dd>{g.data.edges.filter(e => e.from === node.id).map(e => e.to.slice(4)).join(', ') || 'nothing'}</dd>
                  <dt>Written by</dt><dd>{g.data.edges.filter(e => e.to === node.id).map(e => e.from.slice(4)).join(', ') || 'nothing: it is a source'}</dd>
                </>}
              </dl>
            </Card>
          )}
        </div>
      )}
      {g.data && g.data.nodes.length > 0 && (
        <Card title={job ? `Runs of ${job}` : node ? `Runs that touched ${node.name}` : 'Recent runs'}>
          {!runs.data ? <Loading error={runs.error} /> : (<>
            <table>
              <thead><tr><th>When</th><th>Job</th><th>Read</th><th>Wrote</th><th>State</th><th /></tr></thead>
              <tbody>{runs.data.events.filter(touching).slice(0, shown).map(r => (<Fragment key={r.id}>
                <tr>
                  <td className="nowrap">{when(r.at)}</td><td>{r.job}</td>
                  <td className="small">{r.inputs.map(d => d.name).join(', ')}</td>
                  <td className="small">{r.outputs.map(d => `${d.name}${d.rows != null ? ` (${d.rows.toLocaleString()})` : ''}`).join(', ')}</td>
                  <td>{r.state === 'FAIL' ? <Pill tone="critical">failed</Pill> : <Pill tone="good">complete</Pill>}</td>
                  <td><button className="link" onClick={() => showRaw(r.id)} aria-expanded={raw?.id === r.id}>{raw?.id === r.id ? 'Hide event' : 'Event'}</button></td>
                </tr>
                {raw?.id === r.id && <tr><td colSpan={6}><pre className="code lin-raw">{JSON.stringify(raw.body, null, 2)}</pre></td></tr>}
              </Fragment>))}</tbody>
            </table>
            {runs.data.events.filter(touching).length > shown && <button className="lin-more" onClick={() => setShown(shown + 25)}>Show more runs</button>}
          </>)}
        </Card>
      )}
    </>
  )
}
