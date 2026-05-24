import { useState, useEffect, useMemo } from 'react'
import ReactECharts from 'echarts-for-react'
import { TrendingUp, AlertTriangle, CheckCircle2, Clock } from 'lucide-react'

// ── Paleta ────────────────────────────────────────────────────────────────
const COLORS = {
  aberta:       '#60a5fa',
  em_andamento: '#fbbf24',
  concluida:    '#4ade80',
  pendente:     '#52525b',
  urgente:      '#f87171',
  alta:         '#fb923c',
  media:        '#71717a',
  baixa:        '#52525b',
}

const CHART_BG   = 'transparent'
const TEXT_COLOR = '#a1a1aa'
const GRID_COLOR = '#27272a'

// ── KPI Card ──────────────────────────────────────────────────────────────
function KpiCard({ icon: Icon, label, value, sub, color = '#60a5fa' }) {
  return (
    <div className="bg-card border border-border rounded-lg p-4 flex items-start gap-3">
      <div className="w-8 h-8 rounded-md flex items-center justify-center shrink-0"
           style={{ background: `${color}18`, border: `1px solid ${color}30` }}>
        <Icon size={15} style={{ color }} />
      </div>
      <div>
        <p className="text-muted-foreground text-xs">{label}</p>
        <p className="text-foreground text-xl font-bold leading-tight">{value}</p>
        {sub && <p className="text-muted-foreground text-[10px] mt-0.5">{sub}</p>}
      </div>
    </div>
  )
}

// ── Helpers ───────────────────────────────────────────────────────────────
function baseOpts(title) {
  return {
    backgroundColor: CHART_BG,
    title: title ? {
      text: title,
      textStyle: { color: TEXT_COLOR, fontSize: 11, fontWeight: 600, fontFamily: 'inherit' },
      top: 0, left: 0,
    } : undefined,
    tooltip: {
      backgroundColor: '#18181b',
      borderColor: '#27272a',
      textStyle: { color: '#e4e4e7', fontSize: 11 },
    },
    legend: {
      textStyle: { color: TEXT_COLOR, fontSize: 10 },
    },
  }
}

export default function BI() {
  const [demandas, setDemandas]   = useState([])
  const [stats, setStats]         = useState(null)
  const [loading, setLoading]     = useState(true)

  useEffect(() => {
    Promise.all([
      fetch('/controle/demandas?limit=1000').then(r => r.json()),
      fetch('/controle/stats').then(r => r.json()),
    ]).then(([d, s]) => {
      setDemandas(Array.isArray(d) ? d : d.items || [])
      setStats(s)
    }).finally(() => setLoading(false))
  }, [])

  // ── Derivações ───────────────────────────────────────────────────────────
  const statusCounts = useMemo(() => {
    const c = { aberta: 0, em_andamento: 0, concluida: 0, pendente: 0 }
    demandas.forEach(d => { if (d.status in c) c[d.status]++ })
    return c
  }, [demandas])

  const prioCounts = useMemo(() => {
    const c = { urgente: 0, alta: 0, media: 0, baixa: 0 }
    demandas.forEach(d => {
      const k = (d.prioridade || '').toLowerCase().replace('é', 'e')
      if (k in c) c[k]++
    })
    return c
  }, [demandas])

  const topEmpresas = useMemo(() => {
    const m = {}
    demandas.forEach(d => {
      const n = d.empresa_nome || 'Sem empresa'
      m[n] = (m[n] || 0) + 1
    })
    return Object.entries(m)
      .sort((a, b) => b[1] - a[1])
      .slice(0, 10)
  }, [demandas])

  const diasBuckets = useMemo(() => {
    const buckets = { '0-30': 0, '31-60': 0, '61-180': 0, '181-365': 0, '365+': 0 }
    demandas.forEach(d => {
      const v = d.dias_aberta ?? 0
      if (v <= 30) buckets['0-30']++
      else if (v <= 60) buckets['31-60']++
      else if (v <= 180) buckets['61-180']++
      else if (v <= 365) buckets['181-365']++
      else buckets['365+']++
    })
    return buckets
  }, [demandas])

  // ── Urgentes abertas há >90 dias
  const criticos = useMemo(() =>
    demandas.filter(d => d.prioridade?.toLowerCase() === 'urgente' && (d.dias_aberta || 0) > 90 && d.status !== 'concluida').length
  , [demandas])

  // ── Chart options ─────────────────────────────────────────────────────────

  const statusDonut = {
    ...baseOpts(),
    tooltip: { ...baseOpts().tooltip, trigger: 'item' },
    legend: { ...baseOpts().legend, orient: 'vertical', right: 8, top: 'center' },
    series: [{
      type: 'pie', radius: ['50%', '75%'],
      center: ['40%', '50%'],
      data: [
        { value: statusCounts.aberta,       name: 'Abertas',       itemStyle: { color: COLORS.aberta } },
        { value: statusCounts.em_andamento,  name: 'Em andamento',  itemStyle: { color: COLORS.em_andamento } },
        { value: statusCounts.concluida,     name: 'Concluídas',    itemStyle: { color: COLORS.concluida } },
        { value: statusCounts.pendente,      name: 'Pendentes',     itemStyle: { color: COLORS.pendente } },
      ],
      label: { show: false },
      emphasis: { scale: true, scaleSize: 6 },
    }],
  }

  const prioBar = {
    ...baseOpts(),
    tooltip: { ...baseOpts().tooltip, trigger: 'axis' },
    grid: { left: 8, right: 8, top: 8, bottom: 8, containLabel: true },
    xAxis: { type: 'value', axisLabel: { color: TEXT_COLOR, fontSize: 10 }, splitLine: { lineStyle: { color: GRID_COLOR } } },
    yAxis: {
      type: 'category',
      data: ['Urgente', 'Alta', 'Média', 'Baixa'],
      axisLabel: { color: TEXT_COLOR, fontSize: 10 },
      axisLine: { show: false }, axisTick: { show: false },
    },
    series: [{
      type: 'bar', barMaxWidth: 22,
      data: [
        { value: prioCounts.urgente, itemStyle: { color: COLORS.urgente, borderRadius: [0,3,3,0] } },
        { value: prioCounts.alta,    itemStyle: { color: COLORS.alta,    borderRadius: [0,3,3,0] } },
        { value: prioCounts.media,   itemStyle: { color: '#6366f1',      borderRadius: [0,3,3,0] } },
        { value: prioCounts.baixa,   itemStyle: { color: COLORS.pendente, borderRadius: [0,3,3,0] } },
      ],
      label: { show: true, position: 'right', color: TEXT_COLOR, fontSize: 10 },
    }],
  }

  const empresasBar = {
    ...baseOpts(),
    tooltip: { ...baseOpts().tooltip, trigger: 'axis' },
    grid: { left: 8, right: 12, top: 4, bottom: 4, containLabel: true },
    xAxis: { type: 'value', axisLabel: { color: TEXT_COLOR, fontSize: 9 }, splitLine: { lineStyle: { color: GRID_COLOR } } },
    yAxis: {
      type: 'category',
      data: topEmpresas.map(([n]) => n.length > 26 ? n.slice(0, 26) + '…' : n).reverse(),
      axisLabel: { color: TEXT_COLOR, fontSize: 9 },
      axisLine: { show: false }, axisTick: { show: false },
    },
    series: [{
      type: 'bar', barMaxWidth: 18,
      data: topEmpresas.map(([, v]) => v).reverse(),
      itemStyle: { color: '#60a5fa', borderRadius: [0, 3, 3, 0] },
      label: { show: true, position: 'right', color: TEXT_COLOR, fontSize: 9 },
    }],
  }

  const diasBar = {
    ...baseOpts(),
    tooltip: { ...baseOpts().tooltip, trigger: 'axis' },
    grid: { left: 8, right: 8, top: 4, bottom: 4, containLabel: true },
    xAxis: {
      type: 'category',
      data: Object.keys(diasBuckets),
      axisLabel: { color: TEXT_COLOR, fontSize: 10 },
      axisLine: { lineStyle: { color: GRID_COLOR } }, axisTick: { show: false },
    },
    yAxis: { type: 'value', axisLabel: { color: TEXT_COLOR, fontSize: 10 }, splitLine: { lineStyle: { color: GRID_COLOR } } },
    series: [{
      type: 'bar', barMaxWidth: 40,
      data: Object.entries(diasBuckets).map(([k, v]) => ({
        value: v,
        itemStyle: {
          color: k === '365+' ? COLORS.urgente : k === '181-365' ? COLORS.em_andamento : '#60a5fa',
          borderRadius: [3, 3, 0, 0],
        },
      })),
      label: { show: true, position: 'top', color: TEXT_COLOR, fontSize: 10 },
    }],
  }

  if (loading) return (
    <div className="flex items-center justify-center h-full text-muted-foreground text-sm">
      Carregando dados de BI...
    </div>
  )

  const total = demandas.length

  return (
    <div className="flex flex-col gap-4 h-full overflow-y-auto">
      <div>
        <h1 className="text-foreground text-lg font-semibold">BI / Indicadores</h1>
        <p className="text-muted-foreground text-xs mt-0.5">{total} ordens analisadas</p>
      </div>

      {/* KPIs */}
      <div className="grid grid-cols-4 gap-3 shrink-0">
        <KpiCard icon={TrendingUp}    label="Total OS"         value={total}                    sub="todas as ordens"        color="#60a5fa" />
        <KpiCard icon={Clock}         label="Em andamento"     value={statusCounts.em_andamento} sub="aguardando conclusão"   color="#fbbf24" />
        <KpiCard icon={CheckCircle2}  label="Concluídas"       value={statusCounts.concluida}    sub={`${Math.round(statusCounts.concluida/total*100)}% do total`} color="#4ade80" />
        <KpiCard icon={AlertTriangle} label="Urgentes críticas" value={criticos}                 sub=">90 dias em aberto"     color="#f87171" />
      </div>

      {/* Linha 1: Status + Prioridade */}
      <div className="grid grid-cols-2 gap-3">
        <div className="bg-card border border-border rounded-lg p-4">
          <p className="text-xs font-semibold text-muted-foreground uppercase tracking-wider mb-3">Distribuição por Status</p>
          <ReactECharts option={statusDonut} style={{ height: 200 }} theme="dark" />
        </div>
        <div className="bg-card border border-border rounded-lg p-4">
          <p className="text-xs font-semibold text-muted-foreground uppercase tracking-wider mb-3">Distribuição por Prioridade</p>
          <ReactECharts option={prioBar} style={{ height: 200 }} theme="dark" />
        </div>
      </div>

      {/* Linha 2: Top empresas + Antiguidade */}
      <div className="grid grid-cols-2 gap-3">
        <div className="bg-card border border-border rounded-lg p-4">
          <p className="text-xs font-semibold text-muted-foreground uppercase tracking-wider mb-3">Top 10 Empresas por OS</p>
          <ReactECharts option={empresasBar} style={{ height: 280 }} theme="dark" />
        </div>
        <div className="bg-card border border-border rounded-lg p-4">
          <p className="text-xs font-semibold text-muted-foreground uppercase tracking-wider mb-3">Antiguidade das OS (dias em aberto)</p>
          <ReactECharts option={diasBar} style={{ height: 280 }} theme="dark" />
        </div>
      </div>
    </div>
  )
}
