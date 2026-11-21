import { useState } from 'react'
import { api } from '../../../api'
import { Card } from '../../ui'

type Answer = { question: string; answer: string; draft?: string; cites: string[]; sources: string[]; intent: string; provider?: string; fell_back?: string }
const ASKS = ['Why is this the recommendation?', 'Summarise this case', 'What are the largest transactions?', 'Draft a request to the customer', 'Draft a closure note']

type Props = {
  caseId: string; canWork: boolean
  onCite: (ids: string[]) => void
  onNote: (text: string) => void
  onRequest: (text: string) => void
  onError: (m: string) => void
}

/** Questions about this one case. It reads, never changes anything, and hands drafts to the person to edit. */
export default function Copilot({ caseId, canWork, onCite, onNote, onRequest, onError }: Props) {
  const [chat, setChat] = useState<Answer[]>([])
  const [question, setQuestion] = useState('')
  const [asking, setAsking] = useState(false)
  const ask = (q: string) => {
    if (!q.trim()) return
    setAsking(true)
    api.post<Answer>(`/cases/${caseId}/copilot`, { question: q }).then(a => { setChat(x => [a, ...x]); setQuestion('') })
      .catch(e => onError(e.message)).finally(() => setAsking(false))
  }
  return (
    <Card title="Copilot" className="copilot">
      <p className="muted small">Ask about this case. It reads the case record, changes nothing, and drafts only for you to edit and send.</p>
      <div className="inline">{ASKS.map(q => <button key={q} className="ghost" disabled={asking} onClick={() => ask(q)}>{q}</button>)}</div>
      <form className="inline" onSubmit={e => { e.preventDefault(); ask(question) }}>
        <input aria-label="Question for the copilot" value={question} onChange={e => setQuestion(e.target.value)} placeholder="Ask about this case" style={{ flex: 1 }} maxLength={500} />
        <button disabled={asking || !question.trim()}>{asking ? 'Asking…' : 'Ask'}</button>
      </form>
      {chat.map((a, i) => (
        <div className="answer" key={i}>
          <div className="q">{a.question}</div>
          <div style={{ whiteSpace: 'pre-wrap' }}>{a.answer}</div>
          {a.fell_back && <p className="muted small">Answered by the rule-based copilot, because {a.fell_back}.</p>}
          {a.provider && a.provider !== 'rules' && <p className="muted small">Written by {a.provider}; every figure and record id checked against what it read.</p>}
          {a.draft && <><pre>{a.draft}</pre>
            {a.intent === 'draft_request' && canWork && <button className="ghost" onClick={() => onRequest(a.draft!)}>Use as an information request</button>}
            {a.intent === 'draft_note' && <button className="ghost" onClick={() => onNote(a.draft!)}>Use as my note</button>}</>}
          <div className="small">{a.cites.length > 0 && <button className="cite" onClick={() => onCite(a.cites)}>{a.cites.length} transaction{a.cites.length > 1 ? 's' : ''}</button>}
            <span className="srcs">{a.sources.slice(0, 4).join(' · ')}</span></div>
        </div>))}
    </Card>
  )
}
