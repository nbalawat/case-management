import { useState } from 'react'
import { Play } from 'lucide-react'
import { api } from '../api'
import { useApi } from '../useApi'
import { Card, Loading, Pill } from '../components/ui'
import ResultChart from '../components/lake/ResultChart'
import { chartable, type Result } from '../components/lake/chartable'

type Column = { name: string; type: string; required: boolean }
type TableInfo = { table: string; rows: number; commits: number; last_commit: string | null; partitioned_by: string; columns: Column[] }
type Tables = { engine: string; max_rows: number; tables: TableInfo[]; examples: { title: string; sql: string }[] }
type Answer = Result & { ok: boolean; error?: string; truncated: boolean; seconds: number; scanned: Record<string, { files: number; rows: number }> }

const csv = (r: Result) => [r.columns, ...r.rows].map(row => row.map(v => (v === null ? '' : /[",\n]/.test(String(v)) ? `"${String(v).replaceAll('"', '""')}"` : String(v))).join(',')).join('\n')

function save(r: Result) {
  const url = URL.createObjectURL(new Blob([csv(r)], { type: 'text/csv' }))
  Object.assign(document.createElement('a'), { href: url, download: 'lake-query.csv' }).click()
  URL.revokeObjectURL(url)
}

/** Read-only SQL over this client's history in the lake, with time travel. Each query sees only this client's tables. */
export default function LakeSql() {
  const tables = useApi<Tables>('/lake/tables')
  const [sql, setSql] = useState<string | null>(null)
  const [asOf, setAsOf] = useState('')
  const [answer, setAnswer] = useState<Answer | null>(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const [view, setView] = useState<'table' | 'chart'>('table')
  if (!tables.data) return (
    <>
      <div className="page-h"><h1>Lake SQL</h1></div>
      {tables.error?.includes('LAKE_CATALOG') ? <Card><p className="empty">{tables.error} With the lake on, every event that crosses the bus is kept here per client.</p></Card> : <Loading error={tables.error} />}
    </>
  )
  const text = sql ?? tables.data.examples[0]?.sql ?? ''
  const run = () => {
    setBusy(true)
    api.post<Answer>('/lake/query', { sql: text, as_of: asOf ? new Date(asOf).toISOString() : null })
      .then(a => { setAnswer(a); setErr(null); if (a.ok && !chartable(a)) setView('table') })
      .catch(e => setErr(e.message)).finally(() => setBusy(false))
  }
  const scanned = answer?.ok ? Object.values(answer.scanned).reduce((n, s) => ({ files: n.files + s.files, rows: n.rows + s.rows }), { files: 0, rows: 0 }) : null
  return (
    <>
      <div className="page-h"><h1>Lake SQL</h1>
        <p>Ask the client's full history directly: every transaction, alert and case event kept in Apache Iceberg tables. Queries run on {tables.data.engine === 'duckdb' ? 'DuckDB' : tables.data.engine},
          read only, and see this client's tables and nothing else. Pick a moment to see the tables as they stood then.</p></div>
      <div className="lk">
        <aside className="lk-tables">
          {tables.data.tables.map(t => (
            <details key={t.table} open={t.rows > 0}>
              <summary><b>{t.table}</b> <span className="muted small">{t.rows.toLocaleString()} rows</span></summary>
              <ul>{t.columns.map(c => (
                <li key={c.name}><button className="lk-col" title="Insert into the query" onClick={() => setSql(`${text}${/\s$/.test(text) ? '' : ' '}${c.name}`)}>{c.name}</button><span>{c.type.toLowerCase()}</span></li>
              ))}</ul>
              <p className="muted small">{t.commits} commit{t.commits === 1 ? '' : 's'} · partitioned by {t.partitioned_by}</p>
            </details>
          ))}
          <h3>Examples</h3>
          <ul className="plain small">{tables.data.examples.map(e => <li key={e.title}><button className="link" onClick={() => setSql(e.sql)}>{e.title}</button></li>)}</ul>
        </aside>
        <div className="lk-main">
          <Card>
            <textarea className="code" rows={8} spellCheck={false} value={text} aria-label="SQL query"
              onChange={e => setSql(e.target.value)} onKeyDown={e => { if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') run() }} />
            <div className="toolbar">
              <button className="primary" disabled={busy} onClick={run}><Play size={14} /> {busy ? 'Running…' : 'Run'}</button>
              <span className="muted small">Ctrl or ⌘ + Enter</span>
              <label className="small lk-asof">As of <input type="datetime-local" value={asOf} onChange={e => setAsOf(e.target.value)} /></label>
              {asOf && <button onClick={() => setAsOf('')}>Now</button>}
            </div>
            {err && <p className="msg" role="alert">{err}</p>}
            {answer && !answer.ok && <div className="verdict bad" role="alert"><b>The query did not run</b><p>{answer.error}</p></div>}
          </Card>
          {answer?.ok && (
            <Card title={`${answer.rows.length.toLocaleString()} row${answer.rows.length === 1 ? '' : 's'}${answer.truncated ? ` (first ${tables.data.max_rows.toLocaleString()})` : ''}`}
              action={<span className="toolbar lk-tools">
                <span className="muted small">{(answer.seconds * 1000).toFixed(0)} ms · scanned {scanned!.files} file{scanned!.files === 1 ? '' : 's'}, {scanned!.rows.toLocaleString()} rows</span>
                {chartable(answer) && <span className="seg small" role="group" aria-label="View">
                  <button aria-pressed={view === 'table'} onClick={() => setView('table')}>Table</button>
                  <button aria-pressed={view === 'chart'} onClick={() => setView('chart')}>Chart</button></span>}
                <button onClick={() => save(answer)}>CSV</button></span>}>
              {view === 'chart' ? <ResultChart result={answer} /> : (
                <div className="lk-grid">
                  <table><thead><tr>{answer.columns.map((c, i) => <th key={c} className={/INT|DOUBLE|DECIMAL|FLOAT/.test(answer.types[i]) ? 'num' : ''}>{c}<small>{answer.types[i].toLowerCase()}</small></th>)}</tr></thead>
                    <tbody>{answer.rows.slice(0, 500).map((r, i) => <tr key={i}>{r.map((v, j) => <td key={j} className={typeof v === 'number' ? 'num' : ''}>{v === null ? <Pill>null</Pill> : typeof v === 'number' ? v.toLocaleString() : String(v)}</td>)}</tr>)}</tbody></table>
                  {answer.rows.length > 500 && <p className="muted small">Showing 500 of {answer.rows.length.toLocaleString()} rows; the CSV has them all.</p>}
                </div>
              )}
            </Card>
          )}
        </div>
      </div>
    </>
  )
}
