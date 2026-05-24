import { useState, useEffect, useMemo } from 'react'
import { Search, RefreshCw, Filter, Package, FlaskConical, CheckCircle2, Clock, AlertTriangle, Loader2, ChevronUp, ChevronDown } from 'lucide-react'

const STATUS_MAP = {
  'ESTOQUE':     { label: 'Estoque',     cls: 'bg-blue-900/30 text-blue-400 border-blue-800/40',       icon: Package },
  'RESERVADO':   { label: 'Reservado',   cls: 'bg-yellow-900/30 text-yellow-400 border-yellow-800/40', icon: Clock },
  'ENVIADO':     { label: 'Enviado Lab', cls: 'bg-purple-900/30 text-purple-400 border-purple-800/40', icon: FlaskConical },
  'LABORATORIO': { label: 'Laboratório', cls: 'bg-purple-900/30 text-purple-400 border-purple-800/40', icon: FlaskConical },
  'EM_ANALISE':  { label: 'Em análise',  cls: 'bg-yellow-900/30 text-yellow-400 border-yellow-800/40', icon: FlaskConical },
  'RESULTADO':   { label: 'Resultado',   cls: 'bg-green-900/30 text-green-400 border-green-800/40',    icon: CheckCircle2 },
  'DEVOLVIDO':   { label: 'Devolvido',   cls: 'bg-secondary text-muted-foreground border-border',      icon: CheckCircle2 },
  'UTILIZADO?':  { label: 'Verificar',   cls: 'bg-red-900/30 text-red-400 border-red-800/40',          icon: AlertTriangle },
}

function statusConfig(status) {
  if (!status) return { label: '—', cls: 'bg-secondary text-muted-foreground border-border', icon: Clock }
  const upper = status.toUpperCase().trim()
  if (STATUS_MAP[upper]) return STATUS_MAP[upper]
  if (upper.includes('ESTOQUE')) return STATUS_MAP['ESTOQUE']
  if (upper.includes('LAB') || upper.includes('ANALI')) return STATUS_MAP['LABORATORIO']
  if (upper.includes('RESERV')) return STATUS_MAP['RESERVADO']
  if (upper.includes('DEVOL')) return STATUS_MAP['DEVOLVIDO']
  if (upper.includes('RESULT')) return STATUS_MAP['RESULTADO']
  return { label: status, cls: 'bg-secondary text-muted-foreground border-border', icon: Clock }
}

function StatusBadge({ status }) {
  const sc = statusConfig(status)
  const Icon = sc.icon
  return (
    <span className={`inline-flex items-center gap-1 text-xs px-1.5 py-0.5 rounded border font-medium ${sc.cls}`}>
      <Icon size={10} />{sc.label}
    </span>
  )
}

function DiasParado({ dias }) {
  if (dias == null) return <span className="text-muted-foreground/50">—</span>
  const color = dias > 90 ? 'text-red-400' : dias > 30 ? 'text-yellow-400' : 'text-muted-foreground'
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

  const counts = useMemo(() => {
    const c = {}
    items.forEach(it => { const sc = statusConfig(it.status); c[sc.label] = (c[sc.label] || 0) + 1 })
    return c
  }, [items])

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
      let va = a[sort.col] ?? '', vb = b[sort.col] ?? ''
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
    if (sort.col !== col) return <ChevronUp size={11} className="text-muted-foreground opacity-30" />
    return sort.dir === 'asc' ? <ChevronUp size={11} className="text-blue-400" /> : <ChevronDown size={11} className="text-blue-400" />
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-foreground text-lg font-semibold">Amostradores</h1>
          <p className="text-muted-foreground text-xs mt-0.5">
            {loading ? 'Carregando...' : `${items.length} amostradores cadastrados`}
          </p>
        </div>
        <button onClick={load} className="btn-secondary">
          <RefreshCw size={13} className={loading ? 'animate-spin' : ''} /> Atualizar
        </button>
      </div>

      {/* KPIs */}
      {!loading && (
        <div className="grid grid-cols-4 gap-3">
          {[
            { label: 'Estoque',     key: 'Estoque',     icon: Package,     color: 'text-blue-400',   bg: 'bg-blue-500/10' },
            { label: 'Laboratório', key: 'Laboratório', icon: FlaskConical, color: 'text-purple-400', bg: 'bg-purple-500/10' },
            { label: 'Reservados',  key: 'Reservado',   icon: Clock,       color: 'text-yellow-400', bg: 'bg-yellow-500/10' },
            { label: 'Devolvidos',  key: 'Devolvido',   icon: CheckCircle2, color: 'text-green-400',  bg: 'bg-green-500/10' },
          ].map(({ label, key, icon: Icon, color, bg }) => (
            <button
              key={key}
              onClick={() => { setFiltroStatus(key === filtroStatus ? 'todos' : key); setPage(0) }}
              className={`card flex items-center gap-3 cursor-pointer transition-colors text-left ${filtroStatus === key ? 'border-blue-500/40' : ''}`}
            >
              <div className={`w-8 h-8 rounded-lg ${bg} flex items-center justify-center shrink-0`}>
                <Icon size={15} className={color} />
              </div>
              <div>
                <div className={`text-lg font-bold ${color}`}>{counts[key] || 0}</div>
                <div className="text-muted-foreground text-xs">{label}</div>
              </div>
            </button>
          ))}
        </div>
      )}

      {/* Filtros */}
      <div className="flex gap-3 items-center">
        <div className="relative flex-1 max-w-sm">
          <Search size={13} className="absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground" />
          <input value={search} onChange={e => { setSearch(e.target.value); setPage(0) }}
            placeholder="Buscar código, tipo, empresa..." className="form-input pl-8" />
        </div>
        <div className="flex items-center gap-2">
          <Filter size={13} className="text-muted-foreground" />
          <select value={filtroStatus} onChange={e => { setFiltroStatus(e.target.value); setPage(0) }}
            className="form-input w-40">
            {statusOptions.map(s => <option key={s} value={s}>{s === 'todos' ? 'Todos os status' : s}</option>)}
          </select>
        </div>
      </div>

      {/* Tabela */}
      <div className="card p-0 overflow-hidden">
        {loading ? (
          <div className="flex items-center justify-center h-40 gap-2 text-muted-foreground">
            <Loader2 size={16} className="animate-spin" /> Carregando amostradores...
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="table-base">
              <thead>
                <tr>
                  <th className="cursor-pointer hover:text-foreground w-28" onClick={() => toggleSort('codigo')}>
                    <div className="flex items-center gap-1">Código <SortIcon col="codigo" /></div>
                  </th>
                  <th className="cursor-pointer hover:text-foreground w-20" onClick={() => toggleSort('tipo')}>
                    <div className="flex items-center gap-1">Tipo <SortIcon col="tipo" /></div>
                  </th>
                  <th>Status</th>
                  <th className="cursor-pointer hover:text-foreground" onClick={() => toggleSort('empresa_nome')}>
                    <div className="flex items-center gap-1">Empresa <SortIcon col="empresa_nome" /></div>
                  </th>
                  <th className="cursor-pointer hover:text-foreground" onClick={() => toggleSort('avaliador')}>
                    <div className="flex items-center gap-1">Avaliador <SortIcon col="avaliador" /></div>
                  </th>
                  <th className="cursor-pointer hover:text-foreground" onClick={() => toggleSort('data_entrada')}>
                    <div className="flex items-center gap-1">Entrada <SortIcon col="data_entrada" /></div>
                  </th>
                  <th className="cursor-pointer hover:text-foreground" onClick={() => toggleSort('data_medicao')}>
                    <div className="flex items-center gap-1">Medição <SortIcon col="data_medicao" /></div>
                  </th>
                  <th className="cursor-pointer hover:text-foreground w-28" onClick={() => toggleSort('tempo_parado')}>
                    <div className="flex items-center gap-1">Inativo <SortIcon col="tempo_parado" /></div>
                  </th>
                </tr>
              </thead>
              <tbody>
                {paginated.length === 0 ? (
                  <tr><td colSpan={8} className="text-center text-muted-foreground py-10">Nenhum amostrador encontrado</td></tr>
                ) : paginated.map(it => (
                  <tr key={it.id} className="hover:bg-secondary/20 transition-colors">
                    <td><span className="font-mono text-foreground font-medium text-sm">{it.codigo || '—'}</span></td>
                    <td>
                      <span className="text-xs px-1.5 py-0.5 rounded border bg-secondary text-muted-foreground border-border">
                        {it.tipo || '—'}
                      </span>
                    </td>
                    <td><StatusBadge status={it.status} /></td>
                    <td>
                      <span className="text-foreground text-sm truncate block max-w-[180px]" title={it.empresa_nome}>
                        {it.empresa_nome || <span className="text-muted-foreground/50">—</span>}
                      </span>
                    </td>
                    <td><span className="text-muted-foreground text-sm">{it.avaliador || <span className="text-muted-foreground/50">—</span>}</span></td>
                    <td><span className="text-muted-foreground text-xs tabular-nums">{it.data_entrada || '—'}</span></td>
                    <td><span className="text-muted-foreground text-xs tabular-nums">{it.data_medicao || <span className="text-muted-foreground/50">—</span>}</span></td>
                    <td><DiasParado dias={it.tempo_parado} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {!loading && totalPages > 1 && (
          <div className="flex items-center justify-between px-4 py-3 border-t border-border bg-secondary/20">
            <span className="text-xs text-muted-foreground">
              {page * PER_PAGE + 1}–{Math.min((page + 1) * PER_PAGE, filtered.length)} de {filtered.length}
            </span>
            <div className="flex gap-1">
              <button disabled={page === 0} onClick={() => setPage(p => p - 1)}
                className="px-3 py-1 text-xs rounded bg-secondary border border-border text-muted-foreground disabled:opacity-30 hover:text-foreground transition-colors">
                Anterior
              </button>
              <button disabled={page >= totalPages - 1} onClick={() => setPage(p => p + 1)}
                className="px-3 py-1 text-xs rounded bg-secondary border border-border text-muted-foreground disabled:opacity-30 hover:text-foreground transition-colors">
                Próxima
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
