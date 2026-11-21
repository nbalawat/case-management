import { useState } from 'react'
import { api, fmtDateTime } from '../api'
import { useNameOf } from '../shell/people'
import { Pill } from './ui'

export type StreamJob = {
  job: string; title: string; source: string; state: string; since: string | null; also_in_application: boolean
  started_by: string | null; settings_changed: boolean
}
export type StreamJobsView = { engine: string; connected: boolean; note: string; jobs: StreamJob[]; controlled_here: boolean; can_control: boolean }

const ACTIVE = new Set(['RUNNING', 'CREATED', 'INITIALIZING', 'RESTARTING', 'RECONCILING'])
const stateText = (s: string) => (s === 'NOT SUBMITTED' ? 'Not started' : s.charAt(0) + s.slice(1).toLowerCase())

/** A client's detection rules on the stream processor, with start, stop and restart for those allowed to change detection. */
export default function StreamJobs({ view, onChange }: { view: StreamJobsView; onChange: () => void }) {
  const nameOf = useNameOf()
  const [busy, setBusy] = useState('')
  const [error, setError] = useState<string | null>(null)
  const act = (job: string, what: 'start' | 'stop' | 'restart') => {
    setBusy(job); setError(null)
    api.post(`/stream-jobs/${job}/${what}`, {}).catch(e => setError(e.message)).finally(() => { setBusy(''); onChange() })
  }
  const controls = view.controlled_here && view.can_control
  return (
    <>
      <h4>Stream detection <Pill tone={view.connected ? 'good' : 'neutral'}>{view.engine === 'flink' ? (view.connected ? 'Apache Flink' : 'Flink not answering') : 'In the application'}</Pill></h4>
      {view.jobs.length === 0 ? <p className="muted">{view.note}</p> : (
        <table><thead><tr><th>Job</th><th>State</th><th>Running since</th><th>Alerts arrive as</th>{controls && <th />}</tr></thead>
          <tbody>{view.jobs.map(x => {
            const active = ACTIVE.has(x.state)
            return (
              <tr key={x.job}>
                <td><b>{x.title}</b>
                  {x.also_in_application && <div className="muted small">The application is raising this rule as well. Switch it off under Detection to stop the double work.</div>}
                  {x.settings_changed && <div className="small"><Pill tone="warning">Settings changed</Pill> This client's settings have changed since the job started. Restart it to apply them.</div>}</td>
                <td><Pill tone={x.state === 'RUNNING' ? 'good' : x.state === 'NOT SUBMITTED' ? 'neutral' : 'warning'}>{stateText(x.state)}</Pill></td>
                <td>{x.state === 'RUNNING' && x.since ? <>{fmtDateTime(x.since)}{x.started_by && <div className="muted small">Started by {nameOf(x.started_by)}</div>}</> : '–'}</td>
                <td>{x.source}</td>
                {controls && <td className="nowrap">{active
                  ? <><button disabled={busy === x.job} onClick={() => act(x.job, 'restart')}>Restart</button> <button disabled={busy === x.job} onClick={() => act(x.job, 'stop')}>Stop</button></>
                  : <button className="primary" disabled={busy === x.job} onClick={() => act(x.job, 'start')}>{busy === x.job ? 'Starting…' : 'Start'}</button>}</td>}
              </tr>)
          })}</tbody></table>)}
      {error && <p className="verdict bad small" role="alert">{error}</p>}
      {view.connected && view.jobs.length > 0 && <p className="muted small">{view.controlled_here
        ? (view.can_control ? 'A job starts on this client\'s settings in force. A restarted job reads the client\'s payments again from the start, and alerts it has already raised are dropped as duplicates.'
          : 'Starting and stopping a job needs the permission to approve detection changes.')
        : 'Jobs are submitted from the command line here. Connect Flink\'s SQL Gateway to start and stop them from this page.'}</p>}
    </>
  )
}
