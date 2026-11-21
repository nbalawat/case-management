import { useEffect, useState, type FormEvent } from 'react'
import { Link, useLocation, useParams } from 'react-router-dom'
import { api, words } from '../api'
import { useApi } from '../useApi'
import { Card, Loading, Pill } from '../components/ui'
import { PassageText } from '../components/PassageText'

type Head = { document: string; title: string; kind: string; scenarios: string[]; source: string; client_document: boolean; link: string; sections: { id: string; section: string }[] }
type Doc = Omit<Head, 'sections'> & { sections: { id: string; section: string; text: string }[] }
type Hit = { id: string; document: string; title: string; section: string; kind: string; text: string; score: number; link: string; client_document: boolean }

const anchor = (id: string) => id.split('#')[1] ?? ''

function Document({ name }: { name: string }) {
  const { data, error } = useApi<Doc>(`/knowledge/${name}`, [name])
  const { hash } = useLocation()
  useEffect(() => {
    if (data && hash) document.getElementById(hash.slice(1))?.scrollIntoView({ block: 'start' })
  }, [data, hash])
  if (!data) return <Loading error={error} />
  return (
    <Card title={data.title} action={<Pill tone={data.client_document ? 'human' : 'neutral'}>{data.client_document ? "This client's own document" : 'Platform library'}</Pill>}>
      <p className="muted small">{words(data.kind)}{data.scenarios.length > 0 && <> · scenarios: {data.scenarios.map(words).join(', ')}</>}{data.source && <> · {data.source}</>}</p>
      {data.sections.map(s => (
        <section key={s.id} id={anchor(s.id)} className={`passage${hash === `#${anchor(s.id)}` ? ' focus' : ''}`}>
          <h3>{s.section} <code className="muted small">{s.id}</code></h3>
          <PassageText text={s.text} />
        </section>
      ))}
    </Card>
  )
}

export default function Knowledge() {
  const { doc } = useParams()
  const { data, error } = useApi<Head[]>('/knowledge')
  const [q, setQ] = useState('')
  const [hits, setHits] = useState<Hit[] | null>(null)
  const [msg, setMsg] = useState<string | null>(null)
  const search = (e: FormEvent) => {
    e.preventDefault()
    if (q.trim().length < 2) return
    api.get<{ passages: Hit[] }>(`/knowledge/search?q=${encodeURIComponent(q.trim())}`).then(r => { setHits(r.passages); setMsg(null) }).catch(err => setMsg(err.message))
  }
  if (!data) return <Loading error={error} />
  return (
    <>
      <div className="page-h"><h1>Knowledge library</h1><p>The typologies, policies and procedures that Ask and the agents cite. Passages are cited as <code>kb:document#section</code>. Documents marked as this client's own replace the platform's version for this client only.</p></div>
      <form className="ask-box kb-search" onSubmit={search} role="search">
        <input aria-label="Search the library" placeholder="Search, for example: signs of structuring" value={q} onChange={e => setQ(e.target.value)} />
        <button className="btn" type="submit">Search</button>
      </form>
      {msg && <p className="verdict bad small">{msg}</p>}
      {hits && <Card title={`${hits.length} passage${hits.length === 1 ? '' : 's'}`}>
        {hits.length === 0 && <p className="empty">Nothing in the library matches.</p>}
        {hits.map(h => <div key={h.id} className="passage"><Link to={h.link}><b>{h.title}: {h.section}</b></Link> <span className="muted small">{h.id}</span><PassageText text={h.text} /></div>)}
      </Card>}
      <div className="kb-split">
        <Card title="Documents">
          <ul className="kb-docs">{data.map(d => (
            <li key={d.document}><Link to={d.link} className={d.document === doc ? 'active' : ''}>{d.title}</Link> <span className="muted small">{words(d.kind)}{d.client_document ? " · this client's own" : ''}</span></li>
          ))}</ul>
        </Card>
        {doc ? <Document name={doc} /> : <Card><p className="muted">Pick a document to read it.</p></Card>}
      </div>
    </>
  )
}
