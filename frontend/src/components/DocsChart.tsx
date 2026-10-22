import { useState } from 'react'

type Row = { month: string; TOR: number; COE: number }
const SERIES = [
  { key: 'TOR' as const, label: 'TOR', color: 'var(--series-1)' },
  { key: 'COE' as const, label: 'COE', color: 'var(--series-2)' },
]

const W = 560, H = 240
const M = { top: 18, right: 8, bottom: 30, left: 36 }
const BAR_MAX = 24, BAR_GAP = 2, RADIUS = 4

function niceMax(value: number): number {
  if (value <= 4) return 4
  const pow = 10 ** Math.floor(Math.log10(value))
  for (const step of [1, 2, 2.5, 5, 10]) if (step * pow >= value) return step * pow
  return 10 * pow
}

function monthLabel(month: string, index: number): string {
  const [y, m] = month.split('-').map(Number)
  const name = new Date(y, m - 1, 1).toLocaleString(undefined, { month: 'short' })
  return index === 0 || m === 1 ? `${name} ${y}` : name
}

/** Column with a rounded data-end and a square baseline (marks spec). */
function barPath(x: number, y: number, w: number, h: number): string {
  const r = Math.min(RADIUS, h, w / 2)
  return `M${x},${y + h}V${y + r}Q${x},${y} ${x + r},${y}H${x + w - r}Q${x + w},${y} ${x + w},${y + r}V${y + h}Z`
}

export function DocsChart({ data }: { data: Row[] }) {
  const [hover, setHover] = useState<{ i: number; key: 'TOR' | 'COE' } | null>(null)
  const [showTable, setShowTable] = useState(false)

  const plotW = W - M.left - M.right
  const plotH = H - M.top - M.bottom
  const max = niceMax(Math.max(1, ...data.flatMap((d) => [d.TOR, d.COE])))
  const ticks = [0, 0.25, 0.5, 0.75, 1].map((t) => t * max).filter((v) => Number.isInteger(v))
  const band = plotW / data.length
  const barW = Math.min(BAR_MAX, band * 0.3)
  const groupW = barW * 2 + BAR_GAP
  const y = (v: number) => M.top + plotH - (v / max) * plotH
  const last = data.length - 1

  return (
    <div className="relative">
      <div className="mb-2 flex items-center justify-between gap-2">
        <div className="flex items-center gap-4 text-xs text-ink-2" aria-label="Legend">
          {SERIES.map((s) => (
            <span key={s.key} className="inline-flex items-center gap-1.5">
              <span aria-hidden className="inline-block h-2.5 w-2.5 rounded-sm" style={{ background: s.color }} />{s.label}
            </span>
          ))}
        </div>
        <button className="text-xs text-accent hover:underline" onClick={() => setShowTable((v) => !v)}>
          {showTable ? 'Show chart' : 'Show table'}
        </button>
      </div>

      {showTable ? (
        <table className="num w-full text-left text-sm">
          <thead><tr className="border-b border-line text-xs text-muted"><th className="py-1">Month</th><th>TOR</th><th>COE</th></tr></thead>
          <tbody>{data.map((d, i) => (
            <tr key={d.month} className="border-b border-line text-ink"><td className="py-1">{monthLabel(d.month, i)}</td><td>{d.TOR}</td><td>{d.COE}</td></tr>
          ))}</tbody>
        </table>
      ) : (
        <svg viewBox={`0 0 ${W} ${H}`} className="w-full" role="img"
          aria-label="Documents generated per month by type">
          {ticks.map((t) => (
            <g key={t}>
              <line x1={M.left} x2={W - M.right} y1={y(t)} y2={y(t)}
                stroke={t === 0 ? 'var(--axis)' : 'var(--line)'} strokeWidth={1} />
              <text x={M.left - 6} y={y(t)} dy="0.32em" textAnchor="end" fontSize={13} fill="var(--muted)" className="num">{t}</text>
            </g>
          ))}
          {data.map((d, i) => {
            const x0 = M.left + band * i + (band - groupW) / 2
            return (
              <g key={d.month}>
                {SERIES.map((s, j) => {
                  const v = d[s.key]
                  const x = x0 + j * (barW + BAR_GAP)
                  const h = (v / max) * plotH
                  const active = hover?.i === i && hover.key === s.key
                  return (
                    <g key={s.key}>
                      {v > 0 && <path d={barPath(x, y(v), barW, h)} fill={s.color} opacity={hover && !active ? 0.55 : 1} />}
                      {i === last && v > 0 && (
                        <text x={x + barW / 2} y={y(v) - 4} textAnchor="middle" fontSize={13} fill="var(--ink-2)" className="num">{v}</text>
                      )}
                      {/* Hit target taller and wider than the mark (interaction spec). */}
                      <rect x={x - 1} y={M.top} width={barW + 2} height={plotH} fill="transparent" tabIndex={0}
                        aria-label={`${monthLabel(d.month, i)} ${s.label}: ${v}`}
                        onMouseEnter={() => setHover({ i, key: s.key })} onMouseLeave={() => setHover(null)}
                        onFocus={() => setHover({ i, key: s.key })} onBlur={() => setHover(null)} />
                    </g>
                  )
                })}
                <text x={M.left + band * i + band / 2} y={H - 8} textAnchor="middle" fontSize={13} fill="var(--muted)">
                  {monthLabel(d.month, i)}
                </text>
              </g>
            )
          })}
        </svg>
      )}

      {hover && !showTable && (() => {
        const d = data[hover.i]
        const s = SERIES.find((x) => x.key === hover.key)!
        const left = ((M.left + band * hover.i + band / 2) / W) * 100
        return (
          <div className="pointer-events-none absolute top-6 -translate-x-1/2 rounded-md border border-line bg-surface px-2.5 py-1.5 text-xs shadow-sm"
            style={{ left: `${left}%` }}>
            <div className="text-muted">{monthLabel(d.month, 0)}</div>
            <div className="flex items-center gap-1.5 text-ink">
              <span aria-hidden className="inline-block h-2 w-2 rounded-sm" style={{ background: s.color }} />
              {s.label}: <span className="num font-semibold">{d[hover.key]}</span>
            </div>
          </div>
        )
      })()}
    </div>
  )
}
