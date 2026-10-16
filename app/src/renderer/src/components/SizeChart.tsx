// Treemap and sunburst of one folder (section 9.6). ECharts with a theme from the color tokens.
import { SunburstChart, TreemapChart } from 'echarts/charts'
import { TooltipComponent } from 'echarts/components'
import * as echarts from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import { useEffect, useRef } from 'react'
import { size } from '../lib/format'
import type { SpaceNode } from '../lib/types'
import { useApp } from '../store'

echarts.use([TreemapChart, SunburstChart, TooltipComponent, CanvasRenderer])

function css(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim()
}

type Datum = { name: string; value: number; path: string; isDir: boolean; rest?: boolean; ghost?: number; children?: Datum[]; itemStyle?: object }

/** Every item of a level, by its real name. The scan sends at most 40 items and one "Small items" block
 * for the files that are too small to list. */
function toData(nodes: SpaceNode[], colors: string[], ghostColor: string, depth: number, rest: string): Datum[] {
  return nodes
    .filter((n) => n.size > 0)
    .sort((a, b) => b.size - a.size)
    .map((n) => {
      const d: Datum = { name: n.name, value: n.size, path: n.path, isDir: n.is_dir, rest: n.rest, ghost: n.ghost, itemStyle: { color: n.rest ? rest : colors[n.cat] ?? colors[7] } }
      // Ghost space: a compressed item gets a hatched outline (section 9.6).
      if (n.ghost) d.itemStyle = { ...d.itemStyle, borderColor: ghostColor, borderWidth: 2, borderType: 'dashed' }
      if (depth > 1 && n.children?.length) d.children = toData(n.children, colors, ghostColor, depth - 1, rest)
      return d
    })
}

export default function SizeChart({
  tree,
  mode,
  onDrill,
  onOpen,
  onHover
}: {
  tree: SpaceNode
  mode: 'treemap' | 'sunburst'
  onDrill: (path: string) => void
  onOpen: (path: string) => void
  onHover: (path: string | null) => void
}) {
  const el = useRef<HTMLDivElement>(null)
  const chart = useRef<echarts.ECharts | null>(null)
  const theme = useApp((s) => s.theme)
  const cb = useRef({ onDrill, onOpen, onHover })
  cb.current = { onDrill, onOpen, onHover }

  useEffect(() => {
    if (!el.current) return
    const c = echarts.init(el.current, undefined, { renderer: 'canvas' })
    chart.current = c
    c.on('click', (p: any) => {
      const d = p.data as Datum | undefined
      if (d?.isDir && !d.rest) cb.current.onDrill(d.path)
    })
    c.on('dblclick', (p: any) => {
      const d = p.data as Datum | undefined
      if (d && !d.isDir && !d.rest) cb.current.onOpen(d.path)
    })
    c.on('mouseover', (p: any) => cb.current.onHover((p.data as Datum | undefined)?.path ?? null))
    c.on('mouseout', () => cb.current.onHover(null))
    const ro = new ResizeObserver(() => c.resize())
    ro.observe(el.current)
    return () => {
      ro.disconnect()
      c.dispose()
    }
  }, [])

  useEffect(() => {
    const c = chart.current
    if (!c) return
    const colors = Array.from({ length: 8 }, (_, i) => css(`--cat-${i}`))
    const text = css('--text')
    const muted = css('--muted')
    const bg = css('--bg')
    const data = toData(tree.children ?? [], colors, css('--accent'), mode === 'treemap' ? 1 : 2, css('--surface-2'))
    const mono = css('--font-mono') || 'monospace'
    const tooltip = {
      backgroundColor: css('--console-bg'),
      borderWidth: 0,
      padding: [8, 12],
      extraCssText: 'border-radius:0;box-shadow:none;border:1px solid rgba(255,255,255,.08);',
      textStyle: { color: css('--console-text'), fontFamily: mono, fontSize: 11 },
      formatter: (p: any) => {
        const d = p.data as Datum
        if (!d) return ''
        const ghost = d.ghost ? `<br/>restores to ${size(d.ghost)}` : ''
        const pct = tree.size ? ` · ${Math.round((d.value / tree.size) * 100)}%` : ''
        const hint = d.rest ? '' : d.isDir ? '<br/><span style="opacity:.6">click to open</span>' : '<br/><span style="opacity:.6">double-click to show</span>'
        return `<b>${d.name}</b><br/>${size(d.value)}${pct}${ghost}${hint}`
      }
    }
    const series =
      mode === 'treemap'
        ? {
            type: 'treemap',
            data,
            width: '100%',
            height: '100%',
            top: 0,
            left: 0,
            roam: false,
            nodeClick: false,
            breadcrumb: { show: false },
            squareRatio: 0.5 * (1 + Math.sqrt(5)),
            animationDuration: 600,
            animationEasing: 'cubicOut',
            animationDurationUpdate: 350,
            animationEasingUpdate: 'cubicOut',
            visibleMin: 0,
            emphasis: { itemStyle: { borderColor: text, borderWidth: 1 } },
            label: {
              show: true,
              position: 'insideTopLeft',
              padding: [10, 12],
              color: text,
              overflow: 'truncate',
              ellipsis: '…',
              fontFamily: 'system-ui, sans-serif',
              fontSize: 13,
              formatter: (p: any) => `{n|${p.name}}\n{s|${size(p.value)}}`,
              rich: {
                n: { color: text, fontSize: 13, fontWeight: 500, lineHeight: 18 },
                s: { color: text, opacity: 0.7, fontFamily: mono, fontSize: 11.5, lineHeight: 16 }
              }
            },
            upperLabel: { show: false },
            itemStyle: { borderWidth: 0, gapWidth: 2, borderColor: bg, borderRadius: 0 },
            levels: [{ itemStyle: { borderWidth: 0, gapWidth: 2, borderColor: bg, borderRadius: 0 } }]
          }
        : {
            type: 'sunburst',
            data,
            radius: ['14%', '96%'],
            nodeClick: false,
            sort: undefined,
            animationDuration: 700,
            animationEasing: 'cubicOut',
            animationDurationUpdate: 350,
            emphasis: { focus: 'ancestor' },
            itemStyle: { borderColor: bg, borderWidth: 2, borderRadius: 0 },
            label: { color: text, fontSize: 11, minAngle: 8, rotate: 'radial', overflow: 'truncate', ellipsis: '…' },
            levels: [{}, { label: { show: true, minAngle: 6, fontSize: 12 } }, { label: { show: false } }]
          }
    c.setOption({ animation: true, animationDuration: 600, tooltip, series: [series] }, { notMerge: true })
  }, [tree, mode, theme])

  return <div ref={el} className="h-full w-full" />
}
