/** A passage's text: paragraphs, and lines starting with "- " as a list. */
export function PassageText({ text }: { text: string }) {
  const blocks = text.split(/\n\s*\n/)
  return <>{blocks.map((b, i) => {
    const lines = b.split('\n')
    if (lines.every(l => l.trim().startsWith('- '))) return <ul key={i}>{lines.map((l, j) => <li key={j}>{l.trim().slice(2)}</li>)}</ul>
    return <p key={i}>{lines.join(' ')}</p>
  })}</>
}
