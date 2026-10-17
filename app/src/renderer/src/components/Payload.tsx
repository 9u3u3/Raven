/** A redacted payload with each placeholder ([PERSON_1], [CARD_2]) marked, so the user sees what Raven hid
 * before anything leaves (PLAN4 7.5, Ask each time). */
export default function Payload({ text, className = '' }: { text: string; className?: string }) {
  const parts = text.split(/(\[[A-Z]+_\d+\])/g)
  return (
    <pre className={`selectable whitespace-pre-wrap break-words ${className}`}>
      {parts.map((p, k) =>
        /^\[[A-Z]+_\d+\]$/.test(p) ? (
          <mark key={k} className="rounded-sm bg-accent-soft px-0.5 text-accent" title="Raven hid this value">
            {p}
          </mark>
        ) : (
          p
        )
      )}
    </pre>
  )
}
