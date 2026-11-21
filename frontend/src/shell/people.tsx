import { createContext, useContext } from 'react'
import type { Persona } from '../api'

/** The signed-in person and the people of their client, shared with every page. */
export type People = {
  persona: Persona | null
  nameOf: (id: string | null | undefined) => string
  /** Whether the signed-in person holds a permission, and who does if they do not. */
  can: (permission: string) => boolean
  holder: (permission: string) => string
}

export const PeopleContext = createContext<People>({ persona: null, nameOf: id => id ?? '', can: () => false, holder: () => '' })
export const usePeople = () => useContext(PeopleContext)
export const usePersona = () => useContext(PeopleContext).persona
/** Turns a user id from the API into a display name. */
export const useNameOf = () => useContext(PeopleContext).nameOf

export function makePeople(all: Persona[], pid: string, former: Record<string, string> = {}): People {
  const byId = new Map(all.map(p => [p.id, p]))
  const persona = byId.get(pid) ?? null
  return {
    persona,
    nameOf: id => (id ? byId.get(id)?.name ?? former[id] ?? id : ''),
    can: permission => !!persona?.permissions.includes(permission),
    holder: permission => { const p = all.find(x => x.permissions.includes(permission)); return p ? `${p.name} (${p.title})` : 'nobody' },
  }
}
