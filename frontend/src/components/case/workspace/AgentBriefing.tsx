import { api } from '../../../api'
import { Card } from '../../ui'
import type { Act, CaseDetail } from './types'

const OPEN_BY_DEFAULT = ['typology_analyst', 'sanctions_adjudicator', 'fraud_analyst', 'kyc_refresh']

/**
 * What the agents found and recommend. Every finding names its sources; a source chip opens the Transactions tab
 * with just those records showing.
 */
export default function AgentBriefing({ d, act, onCite }: { d: CaseDetail; act: Act; onCite: (ids: string[]) => void }) {
  const { case: c, agent_run: run, access } = d
  const rec = run?.steps.find(s => s.agent === 'orchestrator')
  return (
    <Card title="Agent briefing" action={access.can_work ? <button className="ghost" onClick={() => act(() => api.post(`/cases/${c.id}/agents/run`), 'Agent investigation re-run.')}>Re-run</button> : undefined}>
      {!run ? <p className="empty">No agent run yet for this case.</p> : <>
        <div className={`rec ${run.recommendation}`}>
          <div><span className="muted small">Recommendation · autonomy {d.workflow.autonomy}</span>
            <b>{run.recommendation === 'escalate' ? 'Escalate' : run.recommendation === 'close' ? 'Close, no further action' : 'No recommendation'}</b></div>
          <div className="conf"><span className="muted small">Confidence</span><b>{Math.round(run.confidence * 100)}%</b></div>
        </div>
        {rec && <ul className="signals">{rec.findings.map((f, i) => <li key={i} className={f.text.startsWith('+') ? 'up' : 'down'}>{f.text.slice(2)}</li>)}</ul>}
        <p className="muted small">The agent prepares and recommends. Only a person can move, close or file this case.</p>
        {run.steps.filter(s => s.agent !== 'orchestrator' && s.agent !== 'narrative_writer').map(s => s.outcome === 'skipped' || s.outcome === 'refused' ? (
          <p key={s.agent} className="skipped small"><b>{s.title}</b> {s.outcome === 'refused' ? 'was refused: ' : 'did not run: '}{s.summary}</p>) : (
          <details key={s.agent} open={OPEN_BY_DEFAULT.includes(s.agent)}>
            <summary><b>{s.title}</b><span className="muted"> {s.summary}</span></summary>
            <ul className="findings">{s.findings.map((f, i) => (
              <li key={i}>{f.text}{f.cites.length > 0 && <button className="cite" onClick={() => onCite(f.cites)}>{f.cites.length} source{f.cites.length > 1 ? 's' : ''}</button>}
                {f.sources && f.sources.length > 0 && <span className="srcs">{f.sources.slice(0, 3).join(' · ')}</span>}</li>))}</ul>
            {!!s.rejected && <p className="muted small">{s.rejected} statement(s) cited nothing and were dropped.</p>}
          </details>))}
        <p className="muted small">Provider: {run.provider}. Every finding names the records it rests on.</p>
      </>}
    </Card>
  )
}
