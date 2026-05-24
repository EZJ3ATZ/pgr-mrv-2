import { useState, useEffect, useMemo } from 'react'
import { motion } from 'framer-motion'
import {
  Search, RefreshCw, Building2, Mail, Phone,
  MapPin, Loader2, ChevronUp, ChevronDown, User
} from 'lucide-react'

export default function Empresas() {
  const [items, setItems] = useState([])
  const [loading, setLoading] = useState(true)
  const [search, setSearch] = useState('')
  const [sort, setSort] = useState({ col: 'nome', dir: 'asc' })
  const [page, setPage] = useState(0)
  const PER_PAGE = 25

  const load = () => {
    setLoading(true)
    fetch('/controle/empresas?limit=500')
      .then(r => r.json())
      .then(d => { setItems(Array.isArray(d) ? d : d.items || []); setLoading(false) })
      .catch(() => setLoading(false))
  }
  useEffect(load, [])

  const filtered = useMemo(() => {
    let d = [...items]
    if (search) {
      const q = search.toLowerCase()
      d = d.filter(x =>
        (x.nome || '').toLowerCase().includes(q) ||
        (x.cnpj || '').includes(q) ||
        (x.cidade || '').toLowerCase().includes(q) ||
        (x.contato || '').toLowerCase().includes(q) ||
        (x.email || '').toLowerCase().includes(q)
      )
    }
    d.sort((a, b) => {
      let va = a[sort.col] ?? ''
      let vb = b[sort.col] ?? ''
      if (typeof va === 'string') va = va.toLowerCase()
      if (typeof vb === 'string') vb = vb.toLowerCase()
      return sort.dir === 'asc' ? (va > vb ? 1 : -1) : (va < vb ? 1 : -1)
    })
    return d
  }, [items, search, sort])

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

  // Stats
  const comContato = items.filter(x => x.contato || x.email || x.telefone).length
  const comCidade  = items.filter(x => x.cidade).length

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-text1 text-xl font-semibold">Empresas</h1>
          <p className="text-text2 text-sm mt-0.5">
            {loading ? 'Carregando...' : `${items.length} empresas cadastradas`}
          </p>
        </div>
        <button onClick={load} className="btn-secondary gap-1.5">
          <RefreshCw size={13} className={loading ? 'animate-spin' : ''} />
          Atualizar
        </button>
      </div>

      {/* KPIs */}
      {!loading && (
        <div className="grid grid-cols-3 gap-3">
          {[
            { label: 'Total',          value: items.length,   icon: Building2, color: 'text-text1', bg: 'bg-surface2' },
            { label: 'Com contato',    value: comContato,     icon: User,      color: 'text-blue',  bg: 'bg-blue/10' },
            { label: 'Com localidade', value: comCidade,      icon: MapPin,    color: 'text-green', bg: 'bg-green/10' },
          ].map(({ label, value, icon: Icon, color, bg }) => (
            <div key={label} className="card flex items-center gap-3">
              <div className={`w-8 h-8 rounded-lg ${bg} flex items-center justify-center shrink-0`}>
                <Icon size={15} className={color} />
              </div>
              <div>
                <div className={`text-lg font-bold ${color}`}>{value}</div>
                <div className="text-text2 text-xs">{label}</div>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Busca */}
      <div className="relative max-w-sm">
        <Search size={13} className="absolute left-3 top-1/2 -translate-y-1/2 text-text3" />
        <input
          value={search}
          onChange={e => { setSearch(e.target.value); setPage(0) }}
          placeholder="Buscar empresa, CNPJ, cidade, contato..."
          className="w-full bg-surface2 border border-border rounded-btn pl-8 pr-3 py-2 text-sm text-text1 placeholder:text-text3 focus:outline-none focus:border-blue/50 transition-colors"
        />
      </div>

      {/* Tabela */}
      <div className="card p-0 overflow-hidden">
        {loading ? (
          <div className="flex items-center justify-center h-40 gap-2 text-text3">
            <Loader2 size={16} className="animate-spin" /> Carregando empresas...
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="table-base">
              <thead>
                <tr className="bg-surface2/50">
                  <th className="cursor-pointer hover:text-text1" onClick={() => toggleSort('nome')}>
                    <div className="flex items-center gap-1"><Building2 size={12} /> Empresa <SortIcon col="nome" /></div>
                  </th>
                  <th>CNPJ</th>
                  <th className="cursor-pointer hover:text-text1" onClick={() => toggleSort('cidade')}>
                    <div className="flex items-center gap-1"><MapPin size={12} /> Cidade <SortIcon col="cidade" /></div>
                  </th>
                  <th className="cursor-pointer hover:text-text1" onClick={() => toggleSort('contato')}>
                    <div className="flex items-center gap-1"><User size={12} /> Contato <SortIcon col="contato" /></div>
                  </th>
                  <th><div className="flex items-center gap-1"><Mail size={12} /> E-mail</div></th>
                  <th><div className="flex items-center gap-1"><Phone size={12} /> Telefone</div></th>
                </tr>
              </thead>
              <tbody>
                {paginated.length === 0 ? (
                  <tr><td colSpan={6} className="text-center text-text3 py-10">Nenhuma empresa encontrada</td></tr>
                ) : paginated.map((emp, i) => (
                  <motion.tr
                    key={emp.id}
                    initial={{ opacity: 0 }}
                    animate={{ opacity: 1 }}
                    transition={{ delay: i * 0.01 }}
                    className="hover:bg-surface2/60 transition-colors"
                  >
                    <td>
                      <div className="font-medium text-text1 text-sm">{emp.nome || '—'}</div>
                      {emp.id && <div className="text-text3 text-[11px]">#{emp.id}</div>}
                    </td>
                    <td>
                      <span className="font-mono text-text2 text-xs">{emp.cnpj || <span className="text-text3">—</span>}</span>
                    </td>
                    <td>
                      {emp.cidade ? (
                        <span className="text-text2 text-sm">{emp.cidade}{emp.uf ? `, ${emp.uf}` : ''}</span>
                      ) : <span className="text-text3">—</span>}
                    </td>
                    <td>
                      <span className="text-text2 text-sm">{emp.contato || <span className="text-text3">—</span>}</span>
                    </td>
                    <td>
                      {emp.email ? (
                        <a href={`mailto:${emp.email}`} className="text-blue text-xs hover:underline">{emp.email}</a>
                      ) : <span className="text-text3">—</span>}
                    </td>
                    <td>
                      <span className="text-text2 text-xs tabular-nums">{emp.telefone || <span className="text-text3">—</span>}</span>
                    </td>
                  </motion.tr>
                ))}
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
