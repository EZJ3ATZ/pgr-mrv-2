import { useState, useEffect, useRef, useCallback, useMemo } from 'react'
import { AgGridReact } from 'ag-grid-react'
import { Search, RefreshCw, Download, Filter } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Skeleton } from '@/components/ui/skeleton'

// ── Cell Renderers simples (fora do componente) ────────────────────────
function StatusCell(p) {
  const v = p.value || 'pendente'
  const map = {
    aberta:       { label: 'Aberta',        color: '#60a5fa' },
    em_andamento: { label: 'Em andamento',  color: '#fbbf24' },
    concluida:    { label: 'Concluída',     color: '#4ade80' },
    pendente:     { label: 'Pendente',      color: '#71717a' },
  }
  const s = map[v] || map.pendente
  return (
    <span style={{
      display: 'inline-flex', alignItems: 'center', gap: 4,
      padding: '1px 8px', borderRadius: 4,
      fontSize: 11, fontWeight: 500,
      color: s.color, border: `1px solid ${s.color}40`,
      background: `${s.color}18`,
    }}>
      {s.label}
    </span>
  )
}

function PrioCell(p) {
  const map = { urgente: '#f87171', alta: '#fb923c', media: '#71717a', média: '#71717a', baixa: '#71717a' }
  const label = { urgente: 'Urgente', alta: 'Alta', media: 'Média', média: 'Média', baixa: 'Baixa' }
  const key = (p.value || '').toLowerCase()
  const color = map[key] || '#71717a'
  return (
    <span style={{
      display: 'inline-flex', padding: '1px 8px', borderRadius: 4,
      fontSize: 11, fontWeight: 500,
      color, border: `1px solid ${color}40`, background: `${color}18`,
    }}>
      {label[key] || p.value || '—'}
    </span>
  )
}

function DiasCell(p) {
  if (p.value == null) return <span style={{ color: '#52525b', fontSize: 12 }}>—</span>
  const color = p.value > 180 ? '#f87171' : p.value > 60 ? '#fbbf24' : '#71717a'
  return <span style={{ color, fontSize: 12, fontFamily: 'monospace', fontWeight: 500 }}>{p.value}d</span>
}

export default function Demandas() {
  const gridRef = useRef(null)
  const [demandas, setDemandas] = useState([])
  const [loading, setLoading] = useState(true)
  const [search, setSearch] = useState('')
  const [statusFilter, setStatusFilter] = useState('todos')

  const load = useCallback(() => {
    setLoading(true)
    fetch('/controle/demandas?limit=500')
      .then(r => r.json())
      .then(d => { setDemandas(Array.isArray(d) ? d : d.items || []); setLoading(false) })
      .catch(() => setLoading(false))
  }, [])

  useEffect(() => { load() }, [load])

  // Quick filter (busca global da AG Grid)
  useEffect(() => {
    gridRef.current?.api?.setGridOption('quickFilterText', search)
  }, [search])

  const filtered = useMemo(() => {
    if (statusFilter === 'todos') return demandas
    return demandas.filter(d => d.status === statusFilter)
  }, [demandas, statusFilter])

  const counts = useMemo(() => {
    const c = { aberta: 0, em_andamento: 0, concluida: 0, pendente: 0 }
    demandas.forEach(d => { if (d.status in c) c[d.status]++ })
    return c
  }, [demandas])

  const columnDefs = useMemo(() => [
    {
      field: 'empresa_nome', headerName: 'Empresa', flex: 2, minWidth: 180,
      filter: 'agTextColumnFilter', floatingFilter: true,
    },
    {
      field: 'numero_os', headerName: 'OS', width: 90,
      filter: 'agTextColumnFilter', floatingFilter: true,
      cellStyle: { fontFamily: 'monospace', fontSize: 12, color: '#71717a' },
    },
    {
      field: 'status', headerName: 'Status', width: 140,
      cellRenderer: StatusCell,
      filter: 'agSetColumnFilter', floatingFilter: true,
    },
    {
      field: 'prioridade', headerName: 'Prioridade', width: 110,
      cellRenderer: PrioCell,
      filter: 'agSetColumnFilter', floatingFilter: true,
    },
    {
      field: 'dias_aberta', headerName: 'Aberta há', width: 100,
      cellRenderer: DiasCell,
      filter: 'agNumberColumnFilter', floatingFilter: true,
      sort: 'desc',
    },
    {
      field: 'prazo', headerName: 'Prazo', width: 120,
      filter: 'agDateColumnFilter', floatingFilter: true,
      cellStyle: { fontSize: 12 },
    },
    {
      field: 'descricao', headerName: 'Descrição', flex: 1, minWidth: 160,
      filter: 'agTextColumnFilter', floatingFilter: true,
      cellStyle: { fontSize: 12, color: '#71717a' },
    },
  ], [])

  const defaultColDef = useMemo(() => ({
    sortable: true, resizable: true,
  }), [])

  const onExport = useCallback(() => {
    gridRef.current?.api?.exportDataAsCsv({
      fileName: `demandas_${new Date().toISOString().split('T')[0]}.csv`,
    })
  }, [])

  const statusPills = [
    { key: 'todos',        label: `Todas (${demandas.length})` },
    { key: 'aberta',       label: `Abertas (${counts.aberta})` },
    { key: 'em_andamento', label: `Em andamento (${counts.em_andamento})` },
    { key: 'concluida',    label: `Concluídas (${counts.concluida})` },
    { key: 'pendente',     label: `Pendentes (${counts.pendente})` },
  ]

  return (
    <div className="flex flex-col gap-3 h-full">
      <div className="flex items-center justify-between shrink-0">
        <div>
          <h1 className="text-foreground text-lg font-semibold">Demandas / OS</h1>
          <p className="text-muted-foreground text-xs mt-0.5">
            {loading ? 'Carregando...' : `${demandas.length} ordens de serviço`}
          </p>
        </div>
        <div className="flex gap-2">
          <Button variant="outline" size="sm" onClick={onExport} className="h-8 text-xs gap-1.5">
            <Download size={12} /> Exportar CSV
          </Button>
          <Button variant="outline" size="sm" onClick={load} className="h-8 text-xs gap-1.5">
            <RefreshCw size={12} className={loading ? 'animate-spin' : ''} /> Atualizar
          </Button>
        </div>
      </div>

      <div className="flex gap-1 flex-wrap shrink-0">
        {statusPills.map(({ key, label }) => (
          <button key={key} onClick={() => setStatusFilter(key)}
            className={`px-3 py-1 rounded text-xs font-medium border transition-colors ${
              statusFilter === key
                ? 'bg-secondary text-foreground border-border'
                : 'text-muted-foreground border-transparent hover:border-border hover:text-foreground'
            }`}>{label}</button>
        ))}
      </div>

      <div className="relative shrink-0">
        <Search size={13} className="absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground" />
        <Input value={search} onChange={e => setSearch(e.target.value)}
          placeholder="Busca rápida em todas as colunas..."
          className="pl-8 h-8 text-xs bg-card border-border max-w-xs" />
      </div>

      <div className="flex-1 min-h-0 rounded-lg overflow-hidden border border-border">
        {loading ? (
          <div className="p-4 space-y-2 bg-card h-full">
            {[...Array(10)].map((_, i) => <Skeleton key={i} className="h-10 w-full" />)}
          </div>
        ) : (
          <AgGridReact
            ref={gridRef}
            rowData={filtered}
            columnDefs={columnDefs}
            defaultColDef={defaultColDef}
            pagination={true}
            paginationPageSize={25}
            paginationPageSizeSelector={[25, 50, 100]}
            animateRows={true}
            getRowId={p => String(p.data.id)}
            onGridReady={p => p.api.sizeColumnsToFit()}
            style={{ height: '100%' }}
          />
        )}
      </div>
    </div>
  )
}
