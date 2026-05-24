import { useState, useEffect, useMemo } from 'react'
import { motion } from 'framer-motion'
import {
  Search, Filter, RefreshCw, ChevronUp, ChevronDown,
  Clock, AlertTriangle, CheckCircle2, Circle, Loader2,
  Building2, Calendar, Activity
} from 'lucide-react'

const STATUS_CONFIG = {
  aberta:       { label: 'Aberta',       badge: 'badge-blue',   icon: Circle },
  em_andamento: { label: 'Em andamento', badge: 'badge-yellow', icon: Activity },
  concluida:    { label: 'Concluída',    badge: 'badge-green',  icon: CheckCircle2 },
  pendente:     { label: 'Pendente',     badge: 'badge-gray',   icon: Clock },
}

const PRIO_CONFIG = {
  urgente: { label: 'Urgente', badge: 'badge-red' },
  alta:    { label: 'Alta',    badge: 'badge-yellow' },
  média:   { label: 'Média',   badge: 'badge-gray' },
  media:   { label: 'Média',   badge: 'badge-gray' },
  baixa:   { label: 'Baixa',   badge: 'badge-gray' },
}

function DiasAberta({ dias }) {
  if (dias == null) return <span className="text-text3">—</span>
  const color = dias > 180 ? 'text-red' : dias > 60 ? 'text-yellow' : 'text-text2'
  return <span className={`font-medium ${color}`}>{dias}d</span>
}

function Prazo({ prazo, diasPrazo }) {
  if (!prazo) return <span className="text-text3">—</span>
  const vencido = diasPrazo != null && diasPrazo < 0
  const urgente = diasPrazo != null && diasPrazo >= 0 && diasPrazo <= 7
  return (
    <div className="flex flex-col">
      <span className={`text-xs font-medium ${vencido ? 'text-red' : urgente ? 'text-yellow' : 'text-text1'}`}>
        {prazo}
      </span>
      {diasPrazo != null && (
        <span className={`text-[11px] ${vencido ? 'text-red/70' : 'text-text3'}`}>
          {vencido ? `${Math.abs(diasPrazo)}d atrasada` : `${diasPrazo}d restantes`}
        </span>
      )}
    </div>
  )
}

function ProgressBar({ realizadas, total }) {
  if (!total) return <span className="text-text3 text-xs">—</span>
  const pct = Math.round((realizadas / total) * 100)
  const color = pct === 100 ? 'bg-green' : pct > 50 ? 'bg-blue' : 'bg-yellow'
  return (
    <div className="flex items-center gap-2 min-w-[80px]">
      <div className="flex-1 h-1.5 bg-surface2 rounded-full overflow-hidden">
        <div className={`h-full ${color} rounded-full`} style={{ width: `${pct}%` }} />
      </div>
      <span className="text-[11px] text-text2 tabular-nums">{realizadas}/{total}</span>
    </div>
  )
}

export default function Demandas() {
  const [demandas, setDemandas] = useState([])
  const [loading, setLoading] = useState(true)
  const [search, setSearch] = useState('')
  const [filtroStatus, setFiltroStatus] = useState('todos')
  const [filtroPrio, setFiltroPrio] = useState('todos')
  const [sort, setSort] = useState({ col: 'dias_aberta', dir: 'desc' })
  const [page, setPage] = useState(0)
  const PER_PAGE = 20

  useEffect(() => {
    setLoading(true)
    fetch('/controle/demandas?limit=500')
      .then(r => r.json())
      .then(d => {
        setDemandas(Array.isArray(d) ? d : d.items || [])
        setLoading(false)
      })
      .catch(() => setLoading(false))
  }, [])

  const filtered = useMemo(() => {
    let d = [...demandas]
    if (search) {
      const q = search.toLowerCase()
      d = d.filter(x =>
        (x.empresa_nome || '').toLowerCase().includes(q) ||
        (x.numero_os || '').toString().includes(q) ||
        (x.descricao || '').toLowerCase().includes(q)
      )
    }
    if (filtroStatus !== 'todos') d = d.filter(x => x.status === filtroStatus)
    if (filtroPrio !== 'todos') d = d.filter(x =>
      (x.prioridade || '').toLowerCase().replace('é', 'e') === filtroPrio.replace('é', 'e')
    )
    d.sort((a, b) => {
      let va = a[sort.col] ?? 0
      let vb = b[sort.col] ?? 0
      if (typeof va === 'string') va = va.toLowerCase()
      if (typeof vb === 'string') vb = vb.toLowerCase()
      return sort.dir === 'asc' ? (va > vb ? 1 : -1) : (va < vb ? 1 : -1)
    })
    return d
  }, [demandas, search, filtroStatus, filtroPrio, sort])

  const paginated = filtered.slice(page * PER_PAGE, (page + 1) * PER_PAGE)
  const totalPages = Math.ceil(filtered.length / PER_PAGE)

  function toggleSort(col) {
    setSort(s => s.col === col ? { col, dir: s.dir === 'asc' ? 'desc' : 'asc' } : { col, dir: 'desc' })
    setPage(0)
  }

  function SortIcon({ col }) {
    if (sort.col !== col) return <ChevronUp size={12} className="text-text3 opacity-30" />
    return sort.dir === 'asc'
      ? <ChevronUp size={12} className="text-blue" />
      : <ChevronDown size={12} className="text-blue" />
  }

  // Contagens por status
  const counts = useMemo(() => {
    const c = {}
    demandas.forEach(d => { c[d.status] = (c[d.status] || 0) + 1 })
    return c
  }, [demandas])

  return (
    <div className="space-y-4">
      {/* Título */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-text1 text-xl font-semibold">Demandas / OS</h1>
          <p className="text-text2 text-sm mt-0.5">
            {loading ? 'Carregando...' : `${demandas.length} ordens de serviço`}
          </p>
        </div>
        <button
          onClick={() => { setLoading(true); fetch('/controle/demandas?limit=500').then(r => r.json()).then(d => { setDemandas(Array.isArray(d) ? d : d.items || []); setLoading(false) }).catch(() => setLoading(false)) }}
          className="btn-secondary gap-1.5"
        >
          <RefreshCw size={13} className={loading ? 'animate-spin' : ''} />
          Atualizar
        </button>
      </div>

      {/* Status pills */}
      <div className="flex gap-2 flex-wrap">
        {[
          { key: 'todos', label: `Todas (${demandas.length})` },
          { key: 'aberta', label: `Abertas (${counts.aberta || 0})` },
          { key: 'em_andamento', label: `Em andamento (${counts.em_andamento || 0})` },
          { key: 'concluida', label: `Concluídas (${counts.concluida || 0})` },
          { key: 'pendente', label: `Pendentes (${counts.pendente || 0})` },
        ].map(({ key, label }) => (
          <button
            key={key}
            onClick={() => { setFiltroStatus(key); setPage(0) }}
            className={`px-3 py-1 rounded-full text-xs font-medium border transition-all ${
              filtroStatus === key
                ? 'bg-blue/15 border-blue/40 text-blue'
                : 'bg-surface2 border-border text-text2 hover:border-blue/30'
            }`}
          >
            {label}
          </button>
        ))}
      </div>

      {/* Filtros */}
      <div className="flex gap-3 items-center">
        <div className="relative flex-1 max-w-sm">
          <Search size={13} className="absolute left-3 top-1/2 -translate-y-1/2 text-text3" />
          <input
            value={search}
            onChange={e => { setSearch(e.target.value); setPage(0) }}
            placeholder="Buscar empresa, OS, descrição..."
            className="w-full bg-surface2 border border-border rounded-btn pl-8 pr-3 py-2 text-sm text-text1 placeholder:text-text3 focus:outline-none focus:border-blue/50 transition-colors"
          />
        </div>
        <div className="flex items-center gap-2">
          <Filter size={13} className="text-text3" />
          <select
            value={filtroPrio}
            onChange={e => { setFiltroPrio(e.target.value); setPage(0) }}
            className="bg-surface2 border border-border rounded-btn px-3 py-2 text-sm text-text1 focus:outline-none focus:border-blue/50"
          >
            <option value="todos">Prioridade</option>
            <option value="urgente">Urgente</option>
            <option value="alta">Alta</option>
            <option value="media">Média</option>
          </select>
        </div>
      </div>

      {/* Tabela */}
      <div className="card p-0 overflow-hidden">
        {loading ? (
          <div className="flex items-center justify-center h-40 gap-2 text-text3">
            <Loader2 size={16} className="animate-spin" /> Carregando demandas...
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="table-base">
              <thead>
                <tr className="bg-surface2/50">
                  <th className="cursor-pointer hover:text-text1" onClick={() => toggleSort('empresa_nome')}>
                    <div className="flex items-center gap-1"><Building2 size={12} /> Empresa <SortIcon col="empresa_nome" /></div>
                  </th>
                  <th>OS</th>
                  <th>Status</th>
                  <th>Prioridade</th>
                  <th className="cursor-pointer hover:text-text1" onClick={() => toggleSort('dias_aberta')}>
                    <div className="flex items-center gap-1"><Clock size={12} /> Aberta há <SortIcon col="dias_aberta" /></div>
                  </th>
                  <th className="cursor-pointer hover:text-text1" onClick={() => toggleSort('prazo')}>
                    <div className="flex items-center gap-1"><Calendar size={12} /> Prazo <SortIcon col="prazo" /></div>
                  </th>
                  <th>Medições</th>
                </tr>
              </thead>
              <tbody>
                {paginated.length === 0 ? (
                  <tr>
                    <td colSpan={7} className="text-center text-text3 py-10">
                      Nenhuma demanda encontrada
                    </td>
                  </tr>
                ) : paginated.map((d, i) => {
                  const sc = STATUS_CONFIG[d.status] || STATUS_CONFIG.pendente
                  const pc = PRIO_CONFIG[(d.prioridade || '').toLowerCase()] || PRIO_CONFIG.media
                  return (
                    <motion.tr
                      key={d.id}
                      initial={{ opacity: 0 }}
                      animate={{ opacity: 1 }}
                      transition={{ delay: i * 0.02 }}
                      className="cursor-pointer hover:bg-surface2/60 transition-colors"
                    >
                      <td>
                        <div className="font-medium text-text1 text-sm truncate max-w-[220px]" title={d.empresa_nome}>
                          {d.empresa_nome || '—'}
                        </div>
                        {d.descricao && (
                          <div className="text-text3 text-[11px] truncate max-w-[220px]" title={d.descricao}>
                            {d.descricao}
                          </div>
                        )}
                      </td>
                      <td>
                        <span className="font-mono text-text2 text-xs">{d.numero_os || '—'}</span>
                      </td>
                      <td>
                        <span className={sc.badge}>
                          <sc.icon size={10} />
                          {sc.label}
                        </span>
                      </td>
                      <td>
                        <span className={pc.badge}>{pc.label}</span>
                      </td>
                      <td><DiasAberta dias={d.dias_aberta} /></td>
                      <td><Prazo prazo={d.prazo} diasPrazo={d.dias_para_prazo} /></td>
                      <td>
                        <ProgressBar
                          realizadas={d.realizadas ?? 0}
                          total={d.total_medicoes ?? 0}
                        />
                      </td>
                    </motion.tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}

        {/* Paginação */}
        {!loading && totalPages > 1 && (
          <div className="flex items-center justify-between px-4 py-3 border-t border-border bg-surface2/30">
            <span className="text-xs text-text3">
              {page * PER_PAGE + 1}–{Math.min((page + 1) * PER_PAGE, filtered.length)} de {filtered.length}
            </span>
            <div className="flex gap-1">
              <button
                disabled={page === 0}
                onClick={() => setPage(p => p - 1)}
                className="px-3 py-1 text-xs rounded bg-surface2 border border-border text-text2 disabled:opacity-30 hover:border-blue/40 transition-colors"
              >
                Anterior
              </button>
              <button
                disabled={page >= totalPages - 1}
                onClick={() => setPage(p => p + 1)}
                className="px-3 py-1 text-xs rounded bg-surface2 border border-border text-text2 disabled:opacity-30 hover:border-blue/40 transition-colors"
              >
                Próxima
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
