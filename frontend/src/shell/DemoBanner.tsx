import { useState } from 'react'
import { X } from 'lucide-react'

const KEY = 'demo-banner-dismissed'

/** Says the data is synthetic. Dismissible for the session, so it does not cost a line on every screen of a demo. */
export default function DemoBanner({ client, sso }: { client: string; sso: boolean }) {
  const [hidden, setHidden] = useState(() => { try { return sessionStorage.getItem(KEY) === '1' } catch { return false } })
  if (hidden) return null
  return (
    <div className="sample flex items-center justify-center gap-3" role="note">
      <span>Demo environment for <b>{client}</b>. All customers, companies and transactions are synthetic.{' '}
        {sso ? 'You see only what your roles allow.' : 'Change person or client from the menu at the top right.'}</span>
      <button type="button" aria-label="Dismiss" className="grid h-6 min-h-0 w-6 shrink-0 place-items-center rounded border-0 bg-transparent p-0 text-current opacity-70 hover:opacity-100"
        onClick={() => { try { sessionStorage.setItem(KEY, '1') } catch { /* ignore */ } setHidden(true) }}><X size={14} /></button>
    </div>
  )
}
