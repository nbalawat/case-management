import { Building2, ChevronDown, LogOut, Monitor, Moon, Sun } from 'lucide-react'
import type { Persona } from '../api'
import { OPERATOR } from '../api'
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuLabel, DropdownMenuRadioGroup, DropdownMenuRadioItem,
  DropdownMenuSeparator, DropdownMenuTrigger,
} from '../components/kit/dropdown-menu'
import type { ThemeChoice } from './theme'

export type Client = { id: string; name: string; status: string }

const initials = (name: string) => name.split(/\s+/).map(w => w[0]).slice(0, 2).join('').toUpperCase()

export const Avatar = ({ name }: { name: string }) =>
  <span aria-hidden className="grid h-8 w-8 shrink-0 place-items-center rounded-full bg-brand-soft text-[12px] font-semibold text-brand">{initials(name)}</span>

/**
 * Who you are and where. In the demo it also switches person and client, which a real deployment takes from the
 * sign-in token; with single sign-on it only shows who signed in, and signs out.
 */
export default function IdentityMenu({ persona, people, clientId, clientName, clients, demo, theme, onTheme, onPerson, onClient, onSignOut }: {
  persona: Persona; people: Persona[]; clientId: string; clientName: string; clients: Client[]; demo: boolean
  theme: ThemeChoice; onTheme: (t: ThemeChoice) => void; onPerson: (id: string) => void; onClient: (id: string) => void; onSignOut?: () => void
}) {
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <button type="button" className="identity flex min-h-0 items-center gap-2.5 rounded-xl border border-transparent bg-transparent py-1 pr-2 pl-1 text-left hover:border-line"
          aria-label={`Signed in as ${persona.name}, ${persona.title}. Open account menu`}>
          <Avatar name={persona.name} />
          <span className="hidden min-w-0 leading-tight md:block">
            <b className="block max-w-[180px] truncate text-[13.5px]">{persona.name}</b>
            <span className="block max-w-[180px] truncate text-xs text-muted">{persona.title}</span>
          </span>
          <ChevronDown size={15} className="text-muted" aria-hidden />
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-[300px]">
        <div className="flex items-center gap-2.5 px-2.5 py-2">
          <Avatar name={persona.name} />
          <div className="min-w-0 leading-tight"><b className="block truncate text-sm">{persona.name}</b>
            <span className="block truncate text-xs text-muted">{persona.title} · {clientName}</span></div>
        </div>
        {demo && <>
          <DropdownMenuSeparator />
          <DropdownMenuLabel>View as (demo)</DropdownMenuLabel>
          <DropdownMenuRadioGroup value={persona.id} onValueChange={onPerson}>
            {people.map(p => (
              <DropdownMenuRadioItem key={p.id} value={p.id}>
                <span className="min-w-0 flex-1 truncate">{p.name}</span><span className="max-w-[120px] truncate text-xs text-muted">{p.title}</span>
              </DropdownMenuRadioItem>))}
          </DropdownMenuRadioGroup>
          <DropdownMenuSeparator />
          <DropdownMenuLabel>Client</DropdownMenuLabel>
          <DropdownMenuRadioGroup value={clientId} onValueChange={onClient}>
            {clients.map(c => <DropdownMenuRadioItem key={c.id} value={c.id} disabled={c.status !== 'live'}>{c.name}{c.status !== 'live' ? ' (suspended)' : ''}</DropdownMenuRadioItem>)}
          </DropdownMenuRadioGroup>
          <DropdownMenuItem onSelect={() => onClient(OPERATOR)}><Building2 size={15} /> All clients · operator console</DropdownMenuItem>
        </>}
        <DropdownMenuSeparator />
        <DropdownMenuLabel>Appearance</DropdownMenuLabel>
        <DropdownMenuRadioGroup value={theme} onValueChange={v => onTheme(v as ThemeChoice)}>
          <DropdownMenuRadioItem value="light"><Sun size={15} /> Light</DropdownMenuRadioItem>
          <DropdownMenuRadioItem value="dark"><Moon size={15} /> Dark</DropdownMenuRadioItem>
          <DropdownMenuRadioItem value="system"><Monitor size={15} /> Match system</DropdownMenuRadioItem>
        </DropdownMenuRadioGroup>
        {onSignOut && <><DropdownMenuSeparator /><DropdownMenuItem onSelect={onSignOut}><LogOut size={15} /> Sign out</DropdownMenuItem></>}
      </DropdownMenuContent>
    </DropdownMenu>
  )
}
