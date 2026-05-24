import { useState, useEffect, useMemo } from 'react'
import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer, LineChart, Line, Cell,
} from 'recharts'
import { motion, AnimatePresence } from 'framer-motion'
import {
  X, AlertTriangle, Clock, CheckCircle2, Package, FlaskConical,
  TrendingUp, Building2, ChevronRight,
} from 'lucide-react'

// ── Status normalization — amostradores ───────────────────────────────────
const STATUS_DEF = {
  ESTOQUE:     { label: 'Estoque',    color: '#60a5fa' },
  RESERVADO:   { label: 'Reservado',  color: '#fbbf24' },
  ENVIADO:     { label: 'Em Lab',     color: '#a78bfa' },
  LABORATORIO: { label: 'Em Lab',     color: '#a78bfa' },
  EM_ANALISE:  { label: 'Em Análise', color: '#f97316' },
  RESULTADO:   { label: 'Resultado',  color: '#4ade80' },
  DEVOLVIDO:   { label: 'Devolvido',  color: '#71717a' },
}

// Status values that are actually empresa names (data quality issue)
const EMPRESA_FRAGMENTS = ['COMBUSTOL', 'ECOMIN', 'MINERA', 'LTDA', 'S.A.', 'ENGENHARIA']

function normalizeAmostrStatus(raw) {
  if (!raw) return null
  const u = raw.toUpperCase().trim()
  // skip empresa names stored as status
  if (EMPRESA_FRAGMENTS.some(f => u.includes(f))) return null
  if (STATUS_DEF[u]) return STATUS_DEF[u]
  if (u.includes('ESTOQUE')) return STATUS_DEF.ESTOQUE
  if (u.includes('RESERV')) return STATUS_DEF.RESERVADO
  if (u.includes('LAB') || u.includes('ANALI') || u.includes('ENVIAD')) return STATUS_DEF.LABORATORIO
  if (u.includes('DEVOL')) return STATUS_DEF.DEVOLVIDO
  if (u.includes('RESULT')) return STATUS_DEF.RESULTADO
  // unknown but not empresa → show as-is
  return { label: raw, color: '#52525b' }
}

// ── Demanda status ────────────────────────────────────────────────────────
const DMND_STATUS = {
  aberta:       { label: 'Aberta',       color: '#60a5fa' },
  em_andamento: { label: 'Em andamento', color: '#fbbf24' },
  concluida:    { label: 'Concluída',    color: '#4ade80' },
  pendente:     { label: 'Pendente',     color: '#71717a' },
}
function dmndCfg(s) {
  if (!s) return DMND_STATUS.pendente
  const k = s.toLowerCase().replace(/\s+/g, '_')
  return DMND_STATUS[k] || DMND_STATUS.pendente
}

// ── Drawer ────────────────────────────────────────────────────────────────
function Drawer({ open, onClose, title, children }) {
  return (
    <AnimatePresence>
      {open && (
        <>
          <motion.div
            key="bd"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.18 }}
            className="fixed inset-0 bg-black/60 z-40"
            onClick={onClose}
          />
          <motion.div
            key="panel"
            initial={{ x: '100%' }}
            animate={{ x: 0 }}
            exit={{ x: '100%' }}
            transition={{ type: 'spring', damping: 32, stiffness: 320 }}
            className="fixed right-0 top-0 h-full w-[520px] bg-[#111113] border-l border-[#27272a] z-50 flex flex-col shadow-2xl"
          >
            <div className="flex items-center justify-between px-5 py-4 border-b border-[#27272a] shrink-0">
              <span className="text-foreground font-semibold text-sm">{title}</span>
              <button
                onClick={onClose}
                className="text-muted-foreground hover:text-foreground transition-colors p-1 rounded hover:bg-secondary"
              >
                <X size={16} />
              </button>
            </div>
            <div className="flex-1 overflow-y-auto px-5 py-4 space-y-2">
              {children}
            </div>
          </motion.div>
        </>
      )}
    </AnimatePresence>
  )
}

// ── Custom tooltip ────────────────────────────────────────────────────────
function CT({ active, payload, label }) {
  if (!active || !payload?.length) return null
  return (
    <div className="bg-[#18181b] border border-[#27272a] rounded-lg p-3 shadow-xl">
      {label && <p className="text-[#a1a1aa] text-xs mb-1.5">{label}</p>}
      {payload.map((p, i) => (
        <div key={i} className="flex items-center gap-2 text-xs">
          <span className="w-2 h-2 rounded-full shrink-0" style={{ background: p.color || p.fill }} />
          <span className="text-[#a1a1aa]">{p.name}:</span>
          <span className="text-[#e4e4e7] font-semibold">{p.value}</span>
        </div>
      ))}
    </div>
  )
}

// ── Section header ────────────────────────────────────────────────────────
function SH({ icon: Icon, title, sub, color = '#60a5fa' }) {
  return (
    <div className="flex items-center gap-2 mb-4">
      <div className="w-7 h-7 rounded-md flex items-center justify-center shrink-0"
        style={{ background: `${color}18`, border: `1px solid ${color}30` }}>
        <Icon size={13} style={{ color }} />
      </div>
      <div>
        <p className="text-foreground text-sm font-semibold leading-none">{title}</p>
        {sub && <p className="text-muted-foreground text-[10px] mt-0.5">{sub}</p>}
      </div>
    </div>
  )
}

function NoData({ msg = 'Dados insuficientes' }) {
  return (
    <div className="flex items-center justify-center h-28 gap-2 text-muted-foreground text-xs">
      <AlertTriangle size={13} className="text-yellow-600/70 shrink-0" /> {msg}
    </div>
  )
}

// ─────────────────────────────────────────────────────────────────────────────
export default function BI() {
  const [demandas,     setDemandas]     = useState([])
  const [amostradores, setAmostradores] = useState([])
  const [loading,      setLoading]      = useState(true)
  const [drawer,       setDrawer]       = useState(null) // { title, type, items }

  useEffect(() => {
    Promise.all([
      fetch('/controle/demandas?limit=1000').then(r => r.json()).catch(() => []),
      fetch('/controle/amostradores?limit=2000').then(r => r.json()).catch(() => []),
    ]).then(([d, a]) => {
      setDemandas(Array.isArray(d) ? d : d.items || [])
      setAmostradores(Array.isArray(a) ? a : a.items || [])
    }).finally(() => setLoading(false))
  }, [])

  const open = (title, type, items) => setDrawer({ title, type, items })

  // S1 — Amostradores por status (normalizado)
  const amostrData = useMemo(() => {
    const m = {}
    amostradores.forEach(a => {
      const cfg = normalizeAmostrStatus(a.status)
      if (!cfg) return
      const k = cfg.label
      if (!m[k]) m[k] = { label: k, count: 0, color: cfg.color, items: [] }
      m[k].count++
      m[k].items.push(a)
    })
    return Object.values(m).sort((a, b) => b.count - a.count)
  }, [amostradores])

  // S2 — Demandas por status (kanban cards)
  const dmndStatus = useMemo(() => {
    const m = { aberta: [], em_andamento: [], concluida: [], pendente: [] }
    demandas.forEach(d => {
      const k = (d.status || 'pendente').toLowerCase().replace(/\s+/g, '_')
      ;(m[k] || m.pendente).push(d)
    })
    return Object.entries(DMND_STATUS).map(([k, cfg]) => ({
      ...cfg, key: k, count: (m[k] || []).length, items: m[k] || [],
    }))
  }, [demandas])

  // S3 — Demandas atrasadas
  const atrasadas = useMemo(() => {
    const today = new Date().toISOString().slice(0, 10)
    return demandas
      .filter(d => d.prazo && d.prazo < today && d.status?.toLowerCase() !== 'concluida')
      .sort((a, b) => (a.prazo || '').localeCompare(b.prazo || ''))
  }, [demandas])

  // S4 — Evolução mensal (concluídas)
  const evolucao = useMemo(() => {
    const m = {}
    demandas.forEach(d => {
      if (d.status?.toLowerCase() !== 'concluida') return
      const dt = d.prazo
      if (!dt || dt.length < 7) return
      const ym = dt.slice(0, 7)
      m[ym] = (m[ym] || 0) + 1
    })
    return Object.entries(m)
      .sort(([a], [b]) => a.localeCompare(b))
      .slice(-12)
      .map(([mes, concluidas]) => ({ mes: mes.slice(5), concluidas }))
  }, [demandas])

  // S6 — Lab
  const labItems = useMemo(() =>
    amostradores.filter(a => {
      if (!a.status) return false
      const u = a.status.toUpperCase()
      return u.includes('LAB') || u.includes('ANALI') || u === 'ENVIADO' || u === 'EM_ANALISE'
    }).sort((a, b) => (a.data_envio_lab || '').localeCompare(b.data_envio_lab || ''))
  , [amostradores])

  // S7 — Top empresas
  const topEmpresas = useMemo(() => {
    const m = {}
    demandas.forEach(d => {
      const n = d.empresa_nome || 'Sem empresa'
      if (!m[n]) m[n] = { empresa: n, total: 0, items: [] }
      m[n].total++
      m[n].items.push(d)
    })
    return Object.values(m).sort((a, b) => b.total - a.total).slice(0, 10)
  }, [demandas])

  if (loading) return (
    <div className="flex items-center justify-center h-full text-muted-foreground text-sm gap-2">
      <div className="w-4 h-4 rounded-full border-2 border-blue-500 border-t-transparent animate-spin" />
      Carregando dados operacionais...
    </div>
  )

  return (
    <div className="space-y-5 pb-10">

      {/* Header */}
      <div>
        <h1 className="text-foreground text-lg font-semibold">Analytics Operacional</h1>
        <p className="text-muted-foreground text-xs mt-0.5">
          {demandas.length} OS · {amostradores.length} amostradores
        </p>
      </div>

      {/* ── S1: Status Amostradores ──────────────────────────────────────── */}
      <div className="bg-card border border-border rounded-lg p-4">
        <SH icon={Package} title="Status dos Amostradores" sub="Clique numa barra para ver a lista" color="#60a5fa" />
        {amostrData.length === 0 ? <NoData /> : (
          <ResponsiveContainer width="100%" height={Math.max(160, amostrData.length * 36)}>
            <BarChart
              layout="vertical"
              data={amostrData}
              margin={{ top: 2, right: 44, bottom: 2, left: 4 }}
              onClick={({ activePayload }) => {
                if (activePayload?.[0]) {
                  const d = activePayload[0].payload
                  open(`Amostradores — ${d.label}`, 'amostradores', d.items)
                }
              }}
              style={{ cursor: 'pointer' }}
            >
              <CartesianGrid horizontal={false} stroke="#27272a" />
              <XAxis type="number" tick={{ fill: '#71717a', fontSize: 10 }} axisLine={false} tickLine={false} />
              <YAxis type="category" dataKey="label" tick={{ fill: '#a1a1aa', fontSize: 11 }}
                axisLine={false} tickLine={false} width={80} />
              <Tooltip content={<CT />} cursor={{ fill: '#ffffff08' }} />
              <Bar dataKey="count" name="Qtd" radius={[0, 4, 4, 0]} maxBarSize={24}
                label={{ position: 'right', fill: '#71717a', fontSize: 10 }}>
                {amostrData.map((e, i) => <Cell key={i} fill={e.color} />)}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        )}
      </div>

      {/* ── S2: Demandas por Status ──────────────────────────────────────── */}
      <div className="bg-card border border-border rounded-lg p-4">
        <SH icon={Clock} title="Demandas por Status" sub="Clique num card para ver as OS" color="#fbbf24" />
        <div className="grid grid-cols-4 gap-3">
          {dmndStatus.map(({ key, label, color, count, items }) => (
            <button
              key={key}
              onClick={() => open(`Demandas — ${label}`, 'demandas', items)}
              className="bg-secondary/30 border border-border rounded-lg p-4 text-left hover:border-[#3f3f46] transition-all group"
            >
              <div className="flex items-center justify-between mb-2">
                <span className="text-xs text-muted-foreground font-medium">{label}</span>
                <ChevronRight size={11} className="text-muted-foreground group-hover:text-foreground transition-colors" />
              </div>
              <p className="text-3xl font-bold" style={{ color }}>{count}</p>
              <div className="mt-2.5 h-1 rounded-full bg-secondary overflow-hidden">
                <div className="h-full rounded-full transition-all"
                  style={{ width: `${demandas.length ? Math.round(count / demandas.length * 100) : 0}%`, background: color }} />
              </div>
              <p className="text-[10px] text-muted-foreground mt-1">
                {demandas.length ? Math.round(count / demandas.length * 100) : 0}% do total
              </p>
            </button>
          ))}
        </div>
      </div>

      {/* ── S3: Demandas Atrasadas ───────────────────────────────────────── */}
      <div className="bg-card border border-border rounded-lg p-4">
        <SH icon={AlertTriangle}
          title="Demandas Atrasadas"
          sub={atrasadas.length > 0 ? `${atrasadas.length} OS com prazo vencido` : 'Nenhuma atrasada'}
          color="#f87171" />
        {atrasadas.length === 0 ? (
          <div className="flex items-center justify-center h-20 gap-2 text-green-400 text-sm">
            <CheckCircle2 size={15} /> Nenhuma demanda atrasada
          </div>
        ) : (
          <>
            <div className="overflow-x-auto">
              <table className="table-base">
                <thead>
                  <tr>
                    <th>OS</th>
                    <th>Empresa</th>
                    <th>Status</th>
                    <th>Prazo</th>
                    <th>Atraso</th>
                    <th>Responsável</th>
                  </tr>
                </thead>
                <tbody>
                  {atrasadas.slice(0, 15).map(d => {
                    const cfg = dmndCfg(d.status)
                    const atraso = d.dias_para_prazo != null ? Math.abs(d.dias_para_prazo) : null
                    return (
                      <tr key={d.id} className="hover:bg-secondary/20 transition-colors">
                        <td><span className="font-mono text-foreground text-xs font-medium">{d.numero_os || `#${d.id}`}</span></td>
                        <td><span className="text-foreground text-sm">{d.empresa_nome || '—'}</span></td>
                        <td>
                          <span className="inline-flex text-xs px-1.5 py-0.5 rounded border font-medium"
                            style={{ color: cfg.color, borderColor: `${cfg.color}40`, background: `${cfg.color}18` }}>
                            {cfg.label}
                          </span>
                        </td>
                        <td><span className="text-xs tabular-nums text-foreground">{d.prazo}</span></td>
                        <td>
                          {atraso != null
                            ? <span className="text-xs font-medium text-red-400">{atraso}d</span>
                            : <span className="text-muted-foreground/50 text-xs">—</span>}
                        </td>
                        <td><span className="text-muted-foreground text-xs">{d.responsavel || '—'}</span></td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
            {atrasadas.length > 15 && (
              <button
                onClick={() => open('Todas as Demandas Atrasadas', 'demandas', atrasadas)}
                className="w-full text-center text-xs text-blue-400 hover:text-blue-300 py-2.5 border-t border-border transition-colors mt-1"
              >
                Ver todas as {atrasadas.length} demandas atrasadas →
              </button>
            )}
          </>
        )}
      </div>

      {/* ── S4: Evolução Mensal ──────────────────────────────────────────── */}
      <div className="bg-card border border-border rounded-lg p-4">
        <SH icon={TrendingUp} title="Evolução Mensal — OS Concluídas" sub="Últimos 12 meses" color="#4ade80" />
        {evolucao.length < 2 ? (
          <NoData msg="Dados insuficientes — aguardando histórico de conclusões" />
        ) : (
          <ResponsiveContainer width="100%" height={200}>
            <LineChart data={evolucao} margin={{ top: 4, right: 16, bottom: 0, left: 0 }}>
              <CartesianGrid stroke="#27272a" strokeDasharray="3 3" />
              <XAxis dataKey="mes" tick={{ fill: '#71717a', fontSize: 10 }} axisLine={false} tickLine={false} />
              <YAxis tick={{ fill: '#71717a', fontSize: 10 }} axisLine={false} tickLine={false} allowDecimals={false} />
              <Tooltip content={<CT />} />
              <Line type="monotone" dataKey="concluidas" name="Concluídas" stroke="#4ade80"
                strokeWidth={2} dot={{ fill: '#4ade80', r: 3 }} activeDot={{ r: 5 }} />
            </LineChart>
          </ResponsiveContainer>
        )}
      </div>

      {/* ── S5: Produtividade ────────────────────────────────────────────── */}
      <div className="bg-card border border-border rounded-lg p-4">
        <SH icon={TrendingUp} title="Produtividade Operacional" color="#a78bfa" />
        <div className="flex items-center justify-center h-24 text-muted-foreground text-xs text-center leading-relaxed">
          ⏳ Aguardando volume operacional<br />
          <span className="text-[10px] mt-1 block">Disponível após 3+ meses de dados históricos</span>
        </div>
      </div>

      {/* ── S6 + S7 ──────────────────────────────────────────────────────── */}
      <div className="grid grid-cols-2 gap-4">

        {/* S6: Laboratório */}
        <div className="bg-card border border-border rounded-lg p-4">
          <SH icon={FlaskConical} title="Amostradores em Lab" sub={`${labItems.length} em análise`} color="#a78bfa" />
          {labItems.length === 0 ? (
            <NoData msg="Nenhum amostrador em laboratório" />
          ) : (
            <div className="space-y-2 max-h-[280px] overflow-y-auto pr-1">
              {labItems.map(a => (
                <div key={a.id} className="flex items-center gap-3 p-2.5 rounded-lg bg-secondary/30 border border-border">
                  <div className="w-8 h-8 rounded-md bg-purple-500/10 border border-purple-500/20 flex items-center justify-center shrink-0">
                    <FlaskConical size={13} className="text-purple-400" />
                  </div>
                  <div className="flex-1 min-w-0">
                    <p className="text-foreground text-xs font-medium font-mono">{a.codigo || '—'}</p>
                    <p className="text-muted-foreground text-[10px] truncate">{a.empresa_nome || '—'}</p>
                  </div>
                  <div className="text-right shrink-0">
                    <p className="text-[10px] text-muted-foreground tabular-nums">{a.data_envio_lab || '—'}</p>
                    {a.tempo_parado != null && (
                      <p className={`text-[10px] font-medium ${a.tempo_parado > 30 ? 'text-yellow-400' : 'text-muted-foreground'}`}>
                        {a.tempo_parado}d
                      </p>
                    )}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* S7: Top Empresas */}
        <div className="bg-card border border-border rounded-lg p-4">
          <SH icon={Building2} title="Top 10 Empresas por OS" sub="Clique para ver as OS da empresa" color="#60a5fa" />
          {topEmpresas.length === 0 ? <NoData /> : (
            <ResponsiveContainer width="100%" height={280}>
              <BarChart
                layout="vertical"
                data={topEmpresas}
                margin={{ top: 2, right: 40, bottom: 2, left: 4 }}
                onClick={({ activePayload }) => {
                  if (activePayload?.[0]) {
                    const d = activePayload[0].payload
                    open(`OS — ${d.empresa}`, 'demandas', d.items)
                  }
                }}
                style={{ cursor: 'pointer' }}
              >
                <CartesianGrid horizontal={false} stroke="#27272a" />
                <XAxis type="number" tick={{ fill: '#71717a', fontSize: 9 }} axisLine={false} tickLine={false} />
                <YAxis type="category" dataKey="empresa" tick={{ fill: '#a1a1aa', fontSize: 9 }}
                  axisLine={false} tickLine={false} width={115}
                  tickFormatter={v => v.length > 20 ? v.slice(0, 20) + '…' : v} />
                <Tooltip content={<CT />} cursor={{ fill: '#ffffff08' }} />
                <Bar dataKey="total" name="Total OS" fill="#60a5fa" radius={[0, 4, 4, 0]} maxBarSize={20}
                  label={{ position: 'right', fill: '#71717a', fontSize: 9 }} />
              </BarChart>
            </ResponsiveContainer>
          )}
        </div>
      </div>

      {/* ── Drawer ───────────────────────────────────────────────────────── */}
      <Drawer open={!!drawer} onClose={() => setDrawer(null)} title={drawer?.title || ''}>
        {drawer?.type === 'demandas' && (
          <>
            {(drawer.items || []).length === 0 ? (
              <p className="text-muted-foreground text-sm text-center py-8">Nenhuma OS</p>
            ) : (drawer.items || []).map(d => {
              const cfg = dmndCfg(d.status)
              const atraso = d.dias_para_prazo != null ? Math.abs(d.dias_para_prazo) : null
              return (
                <div key={d.id} className="p-3 rounded-lg bg-secondary/30 border border-border">
                  <div className="flex items-center justify-between mb-1.5">
                    <span className="font-mono text-foreground text-xs font-medium">{d.numero_os || `#${d.id}`}</span>
                    <span className="inline-flex text-[10px] px-1.5 py-0.5 rounded border font-medium"
                      style={{ color: cfg.color, borderColor: `${cfg.color}40`, background: `${cfg.color}18` }}>
                      {cfg.label}
                    </span>
                  </div>
                  <p className="text-foreground text-sm font-medium">{d.empresa_nome || '—'}</p>
                  {d.descricao && (
                    <p className="text-muted-foreground text-xs mt-1 line-clamp-2">{d.descricao}</p>
                  )}
                  <div className="flex flex-wrap items-center gap-x-3 gap-y-0.5 mt-2 text-[10px] text-muted-foreground">
                    {d.prazo && <span>📅 {d.prazo}</span>}
                    {d.responsavel && <span>👤 {d.responsavel}</span>}
                    {d.dias_aberta != null && <span>⏱ {d.dias_aberta}d aberta</span>}
                    {atraso != null && d.prazo && new Date().toISOString().slice(0,10) > d.prazo && (
                      <span className="text-red-400">⚠ {atraso}d atrasada</span>
                    )}
                  </div>
                </div>
              )
            })}
          </>
        )}
        {drawer?.type === 'amostradores' && (
          <>
            {(drawer.items || []).length === 0 ? (
              <p className="text-muted-foreground text-sm text-center py-8">Nenhum amostrador</p>
            ) : (drawer.items || []).map(a => (
              <div key={a.id} className="p-3 rounded-lg bg-secondary/30 border border-border">
                <div className="flex items-center justify-between mb-1.5">
                  <span className="font-mono text-foreground text-xs font-medium">{a.codigo || '—'}</span>
                  <span className="text-[10px] text-muted-foreground border border-border px-1.5 py-0.5 rounded bg-secondary">
                    {a.tipo || '—'}
                  </span>
                </div>
                <p className="text-foreground text-sm font-medium">{a.empresa_nome || '—'}</p>
                <div className="flex flex-wrap items-center gap-x-3 gap-y-0.5 mt-2 text-[10px] text-muted-foreground">
                  {a.data_entrada   && <span>📥 {a.data_entrada}</span>}
                  {a.data_medicao   && <span>📊 {a.data_medicao}</span>}
                  {a.data_envio_lab && <span>🔬 {a.data_envio_lab}</span>}
                  {a.tempo_parado != null && (
                    <span className={a.tempo_parado > 30 ? 'text-yellow-400' : ''}>
                      ⏸ {a.tempo_parado}d parado
                    </span>
                  )}
                </div>
              </div>
            ))}
          </>
        )}
      </Drawer>

    </div>
  )
}
