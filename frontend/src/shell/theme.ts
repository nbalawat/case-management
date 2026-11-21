import { useCallback, useEffect, useState } from 'react'

/** Light, dark or follow the system. Stored per browser; applied as data-theme on <html>. */
export type ThemeChoice = 'light' | 'dark' | 'system'
const KEY = 'theme'

const systemDark = () => typeof window !== 'undefined' && window.matchMedia?.('(prefers-color-scheme: dark)').matches

export function readTheme(): ThemeChoice {
  try { return (localStorage.getItem(KEY) as ThemeChoice) || 'light' } catch { return 'light' }
}

export const isDark = (choice: ThemeChoice) => choice === 'dark' || (choice === 'system' && systemDark())

export function applyTheme(choice: ThemeChoice) {
  document.documentElement.dataset.theme = isDark(choice) ? 'dark' : 'light'
}

export function useTheme() {
  const [choice, setChoice] = useState<ThemeChoice>(readTheme)
  useEffect(() => {
    applyTheme(choice)
    if (choice !== 'system') return
    const mq = window.matchMedia('(prefers-color-scheme: dark)')
    const on = () => applyTheme('system')
    mq.addEventListener('change', on)
    return () => mq.removeEventListener('change', on)
  }, [choice])
  const set = useCallback((c: ThemeChoice) => { try { localStorage.setItem(KEY, c) } catch { /* private mode */ } setChoice(c) }, [])
  return [choice, set] as const
}
