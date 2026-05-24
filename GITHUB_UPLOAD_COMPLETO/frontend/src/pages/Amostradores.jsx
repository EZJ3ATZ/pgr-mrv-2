import { useState, useEffect, useMemo } from 'react'
import { motion } from 'framer-motion'
import {
  Search, RefreshCw, Filter, Package, FlaskConical,
  CheckCircle2, Clock, AlertTriangle, Loader2, ChevronUp, ChevronDown
} from 'lucide-react'

const STATUS_MAP = {
  'ESTOQUE':       { label: 'Estoque',      badge: 'badge-blue',   icon: Package },
  'RESERVADO':     { label: 'Reservado',    badge: 'badge-yellow', icon: Clock },
  'ENVIADO':       { label: 'Enviado Lab',  badge: 'badge-purple', icon: FlaskConical },
  'LABORATORIO':   { label: 'Laboratório',  badge: 'badge-purple', icon: FlaskConical },
  'EM_ANALISE':    { label: 'Em análise',   badge: 'badge-yellow', icon: FlaskConical },
  'RESULTADO':     { label: 'Resultado',    badge: 'badge-green',  icon: CheckCircle2 },
  'DEVOLVIDO':     { label: 'Devolvido',    badge: 'badge-gray',   icon: CheckCircle2 },
  'UTILIZADO?':    { label: 'Verificar',    badge: 'badge-red',    icon: AlertTriangle },
}

function statusConfig(status) {
  if (!status) return { label: status || '—', badge: 'badge-gray', icon: Clock }
  const upper = status.toUpperCase().trim()
  // Tentar correspondência exata
  if (STATUS_MAP[upper]) return STATUS_MAP[upper]
  // Correspondência parcial
  if (upper.includes('ESTOQUE')) return STATUS_MAP['ESTOQUE']
  if (upper.includes('LAB') || upper.includes('ANALI')) return STATUS_MAP['LABORATORIO']
  if (upper.includes('RESERV')) return STATUS_MAP['RESERVADO']
  if (upper.includes('DEVOL')) return STATUS_MAP['DEVOLVIDO']
  if (upper.includes('RESULT')) return STATUS_MAP['RESULTADO']
  return { label: status, badge: 'badge-gray', icon: Clock }
}

function DiasParado({ dias }) {
  if (dias == null) return <span className="text-text3">—</span>
  const color = dias > 90 ? 'text-red' : dias > 30 ? 'text-yellow' : 'text-text2'
  return <span className={`font-medium text-xs ${color}`}>{dias}d parado</span>
}

export default function Amostradores() {
  const [items, setItems] = useState([])
  const [loading, setLoading] = useState(true)
  const [search, setSearch] = useState('')
  const [filtroStatus, setFiltroStatus] = useState('todos')
  const [sort, setSort] = useState({ col: 'codigo', dir: 'asc' })
  const [page, setPage] = useState(0)
  const PER_PAGE = 25

  const load = () => {
    setLoading(true)
    fetch('/controle/amostradores?limit=1000')
      .then(r => r.json())
      .then(d => { setItems(Array.isArray(d) ? d : d.items || []); setLoading(false) })
      .catch(() => setLoading(false))
  }
  useEffect(load, [])

  // Contagens por status normalizado
  const counts = useMemo(() => {
    const c = {}
    items.forEach(it => {
      const sc = statusConfig(it.status)
      c[sc.label] = (c[sc.label] || 0) + 1
    })
    return c
  }, [items])

  // Status únicos para o filtro
  const statusOptions = useMemo(() => {
    const set = new Set(items.map(it => statusConfig(it.status).label))
    return ['todos', ...Array.from(set).sort()]
  }, [items])

  const filtered = useMemo(() => {
    let d = [...items]
    if (search) {
      const q = search.toLowerCase()
      d = d.filter(x =>
        (x.codigo || '').toLowerCase().includes(q) ||
        (x.tipo || '').toLowerCase().includes(q) ||
        (x.empresa_nome || '').toLowerCase().includes(q) ||
        (x.avaliador || '').toLowerCase().includes(q)
      )
    }
    if (filtroStatus !== 'todos') {
      d = d.filter(x => statusConfig(x.status).label === filtroStatus)
    }
    d.sort((a, b) => {
      let va = a[sort.col] ?? ''
      let vb = b[sort.col] ?? ''
      if (typeof va === 'string') va = va.toLowerCase()
      if (typeof vb === 'string') vb = vb.toLowerCase()
      return sort.dir === 'asc' ? (va > vb ? 1 : -1) : (va < vb ? 1 : -1)
    })
    return d
  }, [items, search, filtroStatus, sort])

  const paginated = filtered.slice(page * PER_PAGE, (page + 1) * PER_PAGE)
  const totalPages = Math.ceil(filtered.length / PER_PAGE)

  function toggleSort(col) {
    setSort(s => s.col === col ? { col, dir: s.dir === 'asc' ? 'desc' : 'asc' } : { col, dir: 'asc' })
    setPage(0)
  }

  function SortIcon({ col }) {
    if (sort.col !== col) return <ChevronUp size={11} className="text-text3 opacity-30" />
    return sort.dir === 'asc' ? <ChevronUp size={11} className="text-blue" /> : <ChevronDown size={11} className="text-blue" />
  }

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-text1 text-xl font-semibold">Amostradores</h1>
          <p className="text-text2 text-sm mt-0.5">
            {loading ? 'Carregando...' : `${items.length} amostradores cadastrados`}
          </p>
        </div>
        <button onClick={load} className="btn-secondary gap-1.5">
          <RefreshCw size={13} className={loading ? 'animate-spin' : ''} />
          Atualizar
        </button>
      </div>

      {/* KPIs rápidos */}
      {!loading && (
        <div className="grid grid-cols-4 gap-3">
          {[
            { label: 'Estoque',     key: 'Estoque',     icon: Package,    color: 'text-blue',   bg: 'bg-blue/10' },
            { label: 'Laboratório', key: 'Laboratório', icon: FlaskConical, color: 'text-purple', bg: 'bg-purple/10' },
            { label: 'Reservados',  key: 'Reservado',   icon: Clock,      color: 'text-yellow', bg: 'bg-yellow/10' },
            { label: 'Devolvidos',  key: 'Devolvido',   icon: CheckCircle2, color: 'text-green', bg: 'bg-green/10' },
          ].map(({ label, key, icon: Icon, color, bg }) => (
            <motion.button
              key={key}
              whileHover={{ scale: 1.02 }}
              onClick={() => { setFiltroStatus(key === filtroStatus ? 'todos' : key); setPage(0) }}
              className={`card flex items-center gap-3 cursor-pointer transition-colors ${filtroStatus === key ? 'border-blue/40' : ''}`}
            >
              <div className={`w-8 h-8 rounded-lg ${bg} flex items-center justify-center shrink-0`}>
                <Icon size={15} className={color} />
              </div>
              <div className="text-left">
                <div className={`text-lg font-bold ${color}`}>{counts[key] || 0}</div>
                <div className="text-text2 text-xs">{label}</div>
              </div>
            </motion.button>
          ))}
        </div>
      )}

      {/* Filtros */}
      <div className="flex gap-3 items-center">
        <div className="relative flex-1 max-w-sm">
          <Search size={13} className="absolute left-3 top-1/2 -translate-y-1/2 text-text3" />
          <input
            value={search}
            onChange={e => { setSearch(e.target.value); setPage(0) }}
            placeholder="Buscar código, tipo, empresa..."
            className="w-full bg-surface2 border border-border rounded-btn pl-8 pr-3 py-2 text-sm text-text1 placeholder:text-text3 focus:outline-none focus:border-blue/50 transition-colors"
          />
        </div>
        <div className="flex items-center gap-2">
          <Filter size={13} className="text-text3" />
          <select
            value={filtroStatus}
            onChange={e => { setFiltroStatus(e.target.value); setPage(0) }}
            className="bg-surface2 border border-border rounded-btn px-3 py-2 text-sm text-text1 focus:outline-none focus:border-blue/50"
          >
            {statusOptions.map(s => (
              <option key={s} value={s}>{s === 'todos' ? 'Todos os status' : s}</option>
            ))}
          </select>
        </div>
      </div>

      {/* Tabela */}
      <div className="card p-0 overflow-hidden">
        {loading ? (
          <div className="flex items-center justify-center h-40 gap-2 text-text3">
            <Loader2 size={16} className="animate-spin" /> Carregando amostradores...
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="table-base">
              <thead>
                <tr className="bg-surface2/50">
                  <th className="cursor-pointer hover:text-text1 w-28" onClick={() => toggleSort('codigo')}>
                    <div className="flex items-center gap-1">Código <SortIcon col="codigo" /></div>
                  </th>
                  <th className="cursor-pointer hover:text-text1 w-20" onClick={() => toggleSort('tipo')}>
                    <div className="flex items-center gap-1">Tipo <SortIcon col="tipo" /></div>
                  </th>
                  <th>Status</th>
                  <th className="cursor-pointer hover:text-text1" onClick={() => toggleSort('empresa_nome')}>
                    <div className="flex items-center gap-1">Empresa <SortIcon col="empresa_nome" /></div>
                  </th>
                  <th className="cursor-pointer hover:text-text1" onClick={() => toggleSort('avaliador')}>
                    <div className="flex items-center gap-1">Avaliador <SortIcon col="avaliador" /></div>
                  </th>
                  <th className="cursor-pointer hover:text-text1" onClick={() => toggleSort('data_entrada')}>
                    <div className="flex items-center gap-1">Entrada <SortIcon col="data_entrada" /></div>
                  </th>
                  <th className="cursor-pointer hover:text-text1" onClick={() => toggleSort('data_medicao')}>
                    <div className="flex items-center gap-1">Medição <SortIcon col="data_medicao" /></div>
                  </th>
                  <th className="cursor-pointer hover:text-text1 w-28" onClick={() => toggleSort('tempo_parado')}>
                    <div className="flex items-center gap-1">Inativo <SortIcon col="tempo_parado" /></div>
                  </th>
                </tr>
              </thead>
              <tbody>
                {paginated.length === 0 ? (
                  <tr>
                    <td colSpan={8} className="text-center text-text3 py-10">
                      Nenhum amostrador encontrado
                    </td>
                  </tr>
                ) : paginated.map((it, i) => {
                  const sc = statusConfig(it.status)
                  const Icon = sc.icon
                  return (
                    <motion.tr
                      key={it.id}
                      initial={{ opacity: 0 }}
                      animate={{ opacity: 1 }}
                      transition={{ delay: i * 0.01 }}
                      className="hover:bg-surface2/60 transition-colors"
                    >
                      <td>
                        <span className="font-mono text-text1 font-medium text-sm">{it.codigo || '—'}</span>
                      </td>
                      <td>
                        <span className="badge badge-gray">{it.tipo || '—'}</span>
                      </td>
                      <td>
                        <span className={sc.badge}>
                          <Icon size={10} />
                          {sc.label}
                        </span>
                      </td>
                      <td>
                        <span className="text-text1 text-sm truncate block max-w-[180px]" title={it.empresa_nome}>
                          {it.empresa_nome || <span className="text-text3">—</span>}
                        </span>
                      </td>
                      <td>
                        <span className="text-text2 text-sm">{it.avaliador || <span className="text-text3">—</span>}</span>
                      </td>
                      <td>
                        <span className="text-text2 text-xs tabular-nums">{it.data_entrada || '—'}</span>
                      </td>
                      <td>
                        <span className="text-text2 text-xs tabular-nums">{it.data_medicao || <span className="text-text3">—</span>}</span>
                      </td>
                      <td>
                        <DiasParado dias={it.tempo_parado} />
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
              <button disabled={page === 0} onClick={() => setPage(p => p - 1)}
                className="px-3 py-1 text-xs rounded bg-surface2 border border-border text-text2 disabled:opacity-30 hover:border-blue/40 transition-colors">
                Anterior
              </button>
              <button disabled={page >= totalPages - 1} onClick={() => setPage(p => p + 1)}
                className="px-3 py-1 text-xs rounded bg-surface2 border border-border text-text2 disabled:opacity-30 hover:border-blue/40 transition-colors">
                Próxima
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
