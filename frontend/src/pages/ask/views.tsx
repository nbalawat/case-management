import { Fragment, type ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { words } from '../../api'
import { Pill } from '../../components/ui'
import { isRecordId, objectWords, RECORD_ID, show } from './format'
import { toolLabel } from './steps'
import type { Breakdown, Calculation, CaseList, Filter, OneRecord, RecordList, Step, TeamLoad } from './types'

/** Where each record id in an answer may link: only ids a lookup showed, or real records the reader may open. */
export type Links = Map<string, string>

/** Answer text is plain text with **bold**, "- " bullets and record ids. Nothing in it is ever treated as HTML. */
function inline(text: string, links: Links): ReactNode[] {
  return text.split(/(\*\*[^*]+\*\*)/g).flatMap((part, i) => {
    if (part.startsWith('**') && part.endsWith('**')) return [<b key={i}>{inline(part.slice(2, -2), links)}</b>]
    return part.split(RECORD_ID).map((bit, j) => !isRecordId(bit) ? bit
      : links.has(bit) ? <Link key={`${i}-${j}`} to={links.get(bit)!} className="caseref">{bit}</Link>
        : <span key={`${i}-${j}`} className="caseref bad" title="No lookup returned this record, so it is not linked">{bit}</span>)
  })
}

export function AnswerText({ text, links }: { text: string; links: Links }) {
  const blocks: ReactNode[] = []
  let list: string[] = []
  const flush = () => { if (list.length) { blocks.push(<ul key={blocks.length}>{list.map((l, i) => <li key={i}>{inline(l, links)}</li>)}</ul>); list = [] } }
  for (const line of text.split('\n')) {
    if (/^\s*[-•]\s+/.test(line)) list.push(line.replace(/^\s*[-•]\s+/, ''))
    else { flush(); if (line.trim()) blocks.push(<p key={blocks.length} className={line.startsWith('Read as:') ? 'muted small' : ''}>{inline(line, links)}</p>) }
  }
  flush()
  return <div className="answer-text">{blocks}</div>
}

export function Filters({ filters, object }: { filters?: Filter[]; object?: string }) {
  if (!filters?.length) return <span className="muted small">All {objectWords(object)}, no filters</span>
  return <>{filters.map((f, i) => <Pill key={i}>{words(f.field)} {f.op} {Array.isArray(f.value) ? f.value.join(' or ') : String(f.value)}</Pill>)}</>
}

export function BreakdownView({ d }: { d: Breakdown }) {
  const unit = (i: number) => d.units?.[i] ?? ''
  const fmt = (v: unknown, i: number) => show(v, unit(i), d.measures[i])
  const matching = d.matching ?? d.matching_cases
  if (!d.group_by.length) return (
    <div className="kpis tight">{d.measures.map((m, i) => <div className="kpi" key={m}><span>{d.columns[i]}</span><b>{fmt(d.rows[0]?.values[i], i)}</b></div>)}</div>
  )
  const max = Math.max(1, ...d.rows.map(r => Math.abs(Number(r.values[0] ?? 0))))
  const more = d.truncated ? <p className="muted small">Showing {d.rows.length} of {d.groups} groups{matching !== undefined ? ` over ${matching.toLocaleString('en-US')} ${objectWords(d.object)}` : ''}.</p> : null
  if (d.group_by.length === 1) return (
    <>
      <div className="small muted bars-h"><span>{d.columns[0]}</span><span>{d.columns[1]}</span></div>
      {d.rows.map(r => (
        <div className="bar-row ask" key={r.key.join('|')} title={d.measures.map((_, i) => `${d.columns[i + 1]}: ${fmt(r.values[i], i)}`).join('\n')}>
          <span>{r.key[0]}</span>
          <div className="track"><div className="fill" style={{ width: `${(Math.abs(Number(r.values[0] ?? 0)) / max) * 100}%`, visibility: r.values[0] ? 'visible' : 'hidden' }} /></div>
          <b>{fmt(r.values[0], 0)}</b>
          <span className="muted small">{d.measures.slice(1).map((m, i) => `${fmt(r.values[i + 1], i + 1)} ${words(m).replace('avg', 'avg.')}`).join(' · ')}</span>
        </div>))}
      {d.note && <p className="muted small">{d.note}</p>}{more}
    </>
  )
  return (
    <div className="scroll"><table>
      <thead><tr>{d.columns.map((c, i) => <th key={c} className={i >= d.group_by.length ? 'num' : ''}>{c}</th>)}</tr></thead>
      <tbody>{d.rows.map(r => <tr key={r.key.join('|')}>{r.key.map((k, i) => <td key={i}>{k}</td>)}{r.values.map((v, i) => <td key={i} className="num">{fmt(v, i)}</td>)}</tr>)}</tbody>
    </table>{d.note && <p className="muted small">{d.note}</p>}{more}</div>
  )
}

function CaseListView({ d }: { d: CaseList }) {
  return (
    <div className="scroll"><table>
      <thead><tr><th>Case</th><th>Customer</th><th>Type</th><th>Stage</th><th>Owner</th><th className="num">Alerted</th><th className="num">Age (days)</th></tr></thead>
      <tbody>{d.cases.map(c => (
        <tr key={String(c.id)}><td className="nowrap"><Link to={`/cases/${c.id}`}>{c.id}</Link></td><td>{c.customer}</td><td>{c.case_type}</td><td>{c.stage}</td>
          <td>{c.owner}</td><td className="num">{show(c.alerted_amount, 'usd')}</td><td className="num">{show(c.age_days)}</td></tr>))}</tbody>
    </table>{d.truncated && <p className="muted small">Showing {d.cases.length} of {d.matching_cases} matching cases.</p>}</div>
  )
}

const MONEY = /amount|loss|recovered|credit|value/

function RecordListView({ d, links }: { d: RecordList; links: Links }) {
  const cols = d.columns.filter(c => c !== 'customer_id' || !d.columns.includes('customer'))
  const cell = (c: string, v: unknown) => {
    const text = typeof v === 'number' && MONEY.test(c) ? show(v, 'usd') : show(v)
    if (typeof v === 'string' && links.has(v)) return <Link to={links.get(v)!}>{text}</Link>
    if (c === 'case_id' && typeof v === 'string' && v) return <Link to={`/cases/${v}`}>{v}</Link>
    return text
  }
  return (
    <div className="scroll"><table>
      <thead><tr>{cols.map(c => <th key={c}>{words(c)}</th>)}</tr></thead>
      <tbody>{d.records.map((r, i) => <tr key={i}>{cols.map(c => <td key={c} className={typeof r[c] === 'number' ? 'num' : ''}>{cell(c, r[c])}</td>)}</tr>)}</tbody>
    </table>{d.truncated && <p className="muted small">Showing {d.records.length} of {d.matching.toLocaleString('en-US')} matching {objectWords(d.object)}.</p>}</div>
  )
}

function OneRecordView({ d }: { d: OneRecord }) {
  return <dl className="facts">{Object.entries(d.record).filter(([, v]) => v !== null && v !== '').slice(0, 16).map(([k, v]) => (
    <Fragment key={k}><dt>{words(k)}</dt><dd>{typeof v === 'number' && MONEY.test(k) ? show(v, 'usd') : show(v)}</dd></Fragment>))}</dl>
}

function StepHead({ step }: { step: Step }) {
  const i = step.input
  return (
    <div className="step-h"><b>{toolLabel(step)}</b>
      {step.tool === 'case_detail' ? <Pill>{i.case_id}</Pill> : step.tool === 'customer' ? <Pill>{i.name_or_id}</Pill>
        : step.tool === 'record' ? <Pill>{i.id}</Pill> : step.tool === 'policy_search' ? <Pill>{i.query}</Pill> : step.tool === 'calculate' ? <code className="small">{i.expression}</code>
          : step.tool === 'team_workload' ? null : <Filters filters={i.filters} object={i.object} />}
      {!step.ok && <Pill tone="critical">not allowed or not possible</Pill>}</div>
  )
}

export function StepView({ step, links }: { step: Step; links: Links }) {
  const r = step.result
  const body = !step.ok ? <p className="small">{step.summary}</p>
    : !r ? <p className="small muted">{step.summary}</p>
      : step.tool === 'breakdown' ? <BreakdownView d={r as Breakdown} />
        : step.tool === 'list_cases' || (step.tool === 'list_records' && 'cases' in (r as object)) ? <CaseListView d={r as CaseList} />
          : step.tool === 'list_records' ? <RecordListView d={r as RecordList} links={links} />
            : step.tool === 'record' && 'record' in (r as object) ? <OneRecordView d={r as OneRecord} />
              : step.tool === 'policy_search' ? <ul className="small">{((r as { passages?: { id: string; title: string; section: string; link: string }[] }).passages ?? []).map(p => <li key={p.id}><Link to={p.link}>{p.title}: {p.section}</Link> <span className="muted">{p.id}</span></li>)}</ul>
              : step.tool === 'calculate' ? <p className="small"><code>{(r as Calculation).expression}</code> = <b>{show((r as Calculation).value)}</b></p>
                : step.tool === 'team_workload' ? <BreakdownView d={{
                  columns: ['Person', 'Open cases', 'Past the stage clock'], group_by: ['owner'], measures: ['cases', 'late'], groups: 0, truncated: false,
                  rows: (r as TeamLoad).people.map(p => ({ key: [p.name], values: [p.open_cases, p.past_stage_clock], case_ids: [] })) }} />
                  : <p className="small muted">{step.summary}</p>
  return <div className="step"><StepHead step={step} />{body}</div>
}
