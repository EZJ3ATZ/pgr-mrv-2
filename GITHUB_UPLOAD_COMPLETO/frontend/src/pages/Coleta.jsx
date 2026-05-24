import { useEffect, useState, useRef, useMemo } from 'react'
import FullCalendar from '@fullcalendar/react'
import dayGridPlugin from '@fullcalendar/daygrid'
import interactionPlugin from '@fullcalendar/interaction'
import ptBrLocale from '@fullcalendar/core/locales/pt-br'
import { Calendar, List, RefreshCw, MapPin, Clock, CheckCircle2, AlertTriangle, Loader2, Search } from 'lucide-react'

const STATUS_CORES = {
  aberta:       { cls: 'bg-blue-900/30 text-blue-400 border-blue-800/40',    label: 'Aberta',        cal: '#60a5fa' },
  em_andamento: { cls: 'bg-yellow-900/30 text-yellow-400 border-yellow-800/40', label: 'Em andamento', cal: '#fbbf24' },
  concluida:    { cls: 'bg-green-900/30 text-green-400 border-green-800/40',  label: 'Concluída',     cal: '#4ade80' },
  pendente:     { cls: 'bg-secondary text-muted-foreground border-border',    label: 'Pendente',      cal: '#71717a' },
}

function statusCfg(s) {
  if (!s) return STATUS_CORES.pendente
  const k = s.toLowerCase().replace(' ', '_')
  return STATUS_CORES[k] || STATUS_CORES.pendente
}

function StatusBadge({ status }) {
  const cfg = statusCfg(status)
  return (
    <span className={`inline-flex items-center text-xs px-1.5 py-0.5 rounded border font-medium ${cfg.cls}`}>
      {cfg.label}
    </span>
  )
}

function PrioridadeBadge({ p }) {
  if (!p) return null
  const upper = (p || '').toUpperCase()
  const cls = upper.includes('ALTA') ? 'text-red-400' : upper.includes('MEDIA') || upper.includes('MÉDIA') ? 'text-yellow-400' : 'text-muted-foreground'
  return <span className={`text-xs font-medium ${cls}`}>{p}</span>
}

export default function Coleta() {
  const [demandas, setDemandas] = useState([])
  const [loading, setLoading] = useState(true)
  const [view, setView] = useState('lista')
  const [search, setSearch] = useState('')
  const [filtroStatus, setFiltroStatus] = useState('todos')
  const [selectedEvent, setSelectedEvent] = useState(null)
  const calendarRef = useRef(null)

  const load = () => {
    setLoading(true)
    fetch('/controle/demandas?limit=500')
      .then(r => r.json())
      .then(d => setDemandas(Array.isArray(d) ? d : d.items || []))
      .catch(() => {})
      .finally(() => setLoading(false))
  }
  useEffect(load, [])

  const filtered = useMemo(() => {
    let d = [...demandas]
    if (search) {
      const q = search.toLowerCase()
      d = d.filter(x =>
        (x.empresa_nome || '').toLowerCase().includes(q) ||
        (x.numero_os || '').toLowerCase().includes(q) ||
        (x.descricao || '').toLowerCase().includes(q)
      )
    }
    if (filtroStatus !== 'todos') {
      d = d.filter(x => statusCfg(x.status).label === filtroStatus)
    }
    // Ordenar: sem conclusão primeiro (pendentes/abertas), depois por prazo
    d.sort((a, b) => {
      const order = { aberta: 0, em_andamento: 1, pendente: 2, concluida: 3 }
      const sa = order[a.status?.toLowerCase().replace(' ','_')] ?? 2
      const sb = order[b.status?.toLowerCase().replace(' ','_')] ?? 2
      if (sa !== sb) return sa - sb
      if (a.prazo && b.prazo) return a.prazo.localeCompare(b.prazo)
      if (a.prazo) return -1
      if (b.prazo) return 1
      return 0
    })
    return d
  }, [demandas, search, filtroStatus])

  const statusOptions = useMemo(() => {
    const set = new Set(demandas.map(d => statusCfg(d.status).label))
    return ['todos', ...Array.from(set).sort()]
  }, [demandas])

  const events = demandas.filter(d => d.prazo).map(d => ({
    id: String(d.id),
    title: d.empresa_nome || `OS ${d.numero_os}`,
    date: d.prazo,
    backgroundColor: statusCfg(d.status).cal,
    borderColor: statusCfg(d.status).cal,
    textColor: '#0a0a0a',
    extendedProps: d,
  }))

  // Stats
  const stats = useMemo(() => ({
    total:   demandas.length,
    abertas: demandas.filter(d => statusCfg(d.status).label === 'Aberta').length,
    andamento: demandas.filter(d => statusCfg(d.status).label === 'Em andamento').length,
    comPrazo: demandas.filter(d => d.prazo).length,
  }), [demandas])

  return (
    <div className="space-y-4 h-full flex flex-col">
      {/* Header */}
      <div className="flex items-center justify-between shrink-0">
        <div>
          <h1 className="text-foreground text-lg font-semibold">Coleta de Campo</h1>
          <p className="text-muted-foreground text-xs mt-0.5">
            {loading ? 'Carregando...' : `${demandas.length} OS carregadas · ${stats.comPrazo} com prazo`}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <div className="flex rounded-md border border-border overflow-hidden text-xs">
            {[['lista', List, 'Lista'], ['calendario', Calendar, 'Calendário']].map(([v, Icon, label]) => (
              <button key={v} onClick={() => setView(v)}
                className={`flex items-center gap-1.5 px-3 py-1.5 transition-colors ${view === v ? 'bg-secondary text-foreground' : 'text-muted-foreground hover:bg-secondary/50'}`}>
                <Icon size={12} />{label}
              </button>
            ))}
          </div>
          <button onClick={load} className="btn-secondary">
            <RefreshCw size={13} className={loading ? 'animate-spin' : ''} /> Atualizar
          </button>
        </div>
      </div>

      {/* Stats */}
      {!loading && (
        <div className="grid grid-cols-4 gap-3 shrink-0">
          {[
            { label: 'Total OS',       value: stats.total,    color: 'text-foreground' },
            { label: 'Abertas',        value: stats.abertas,  color: 'text-blue-400' },
            { label: 'Em andamento',   value: stats.andamento, color: 'text-yellow-400' },
            { label: 'Com prazo',      value: stats.comPrazo, color: 'text-green-400' },
          ].map(({ label, value, color }) => (
            <div key={label} className="bg-card border border-border rounded-lg p-3">
              <p className="text-xs text-muted-foreground">{label}</p>
              <p className={`text-2xl font-semibold mt-1 ${color}`}>{value}</p>
            </div>
          ))}
        </div>
      )}

      {/* Vista Lista */}
      {view === 'lista' && (
        <>
          {/* Filtros */}
          <div className="flex gap-2 shrink-0">
            <div className="relative flex-1 max-w-sm">
              <Search size={13} className="absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground" />
              <input className="form-input pl-8" placeholder="Buscar empresa, OS..."
                value={search} onChange={e => setSearch(e.target.value)} />
            </div>
            <select className="form-input w-40" value={filtroStatus} onChange={e => setFiltroStatus(e.target.value)}>
              {statusOptions.map(s => <option key={s} value={s}>{s === 'todos' ? 'Todos os status' : s}</option>)}
            </select>
          </div>

          <div className="bg-card border border-border rounded-lg overflow-hidden flex-1">
            {loading ? (
              <div className="flex items-center justify-center h-40 gap-2 text-muted-foreground">
                <Loader2 size={16} className="animate-spin" /> Carregando OS...
              </div>
            ) : filtered.length === 0 ? (
              <div className="flex items-center justify-center h-40 text-muted-foreground text-sm">
                Nenhuma OS encontrada
              </div>
            ) : (
              <div className="overflow-x-auto">
                <table className="table-base">
                  <thead>
                    <tr>
                      <th>OS</th>
                      <th>Empresa</th>
                      <th>Status</th>
                      <th>Prioridade</th>
                      <th><div className="flex items-center gap-1"><Clock size={11}/> Prazo</div></th>
                      <th>Descrição</th>
                    </tr>
                  </thead>
                  <tbody>
                    {filtered.map(d => (
                      <tr key={d.id} className="hover:bg-secondary/20 transition-colors">
                        <td>
                          <span className="font-mono text-foreground font-medium text-xs">
                            {d.numero_os || `#${d.id}`}
                          </span>
                        </td>
                        <td>
                          <div className="flex items-center gap-1 text-foreground text-sm">
                            <MapPin size={11} className="text-muted-foreground shrink-0" />
                            {d.empresa_nome || <span className="text-muted-foreground/50">—</span>}
                          </div>
                        </td>
                        <td><StatusBadge status={d.status} /></td>
                        <td><PrioridadeBadge p={d.prioridade} /></td>
                        <td>
                          {d.prazo
                            ? <span className="text-xs text-foreground tabular-nums">{d.prazo}</span>
                            : <span className="text-muted-foreground/50 text-xs">—</span>
                          }
                        </td>
                        <td>
                          <span className="text-muted-foreground text-xs max-w-xs truncate block" title={d.descricao}>
                            {d.descricao || '—'}
                          </span>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </>
      )}

      {/* Vista Calendário */}
      {view === 'calendario' && (
        <div className="bg-card border border-border rounded-lg p-4 flex-1 min-h-[500px] [&_.fc]:text-sm [&_.fc-toolbar-title]:text-foreground [&_.fc-toolbar-title]:font-semibold [&_.fc-button]:bg-secondary [&_.fc-button]:border-border [&_.fc-button]:text-muted-foreground [&_.fc-button:hover]:text-foreground [&_.fc-button-active]:bg-accent [&_.fc-daygrid-day-number]:text-muted-foreground [&_.fc-col-header-cell-cushion]:text-muted-foreground [&_.fc-day-today]:bg-blue-500/10 [&_.fc-scrollgrid]:border-border [&_.fc-scrollgrid-sync-table]:border-border">
          {loading ? (
            <div className="flex items-center justify-center h-40 gap-2 text-muted-foreground">
              <Loader2 size={16} className="animate-spin" /> Carregando...
            </div>
          ) : (
            <FullCalendar
              ref={calendarRef}
              plugins={[dayGridPlugin, interactionPlugin]}
              initialView="dayGridMonth"
              locale={ptBrLocale}
              events={events}
              headerToolbar={{ left: 'prev,next today', center: 'title', right: 'dayGridMonth,dayGridWeek' }}
              height="auto"
              eventClick={({ event }) => setSelectedEvent(event.extendedProps)}
              eventContent={({ event }) => (
                <div className="px-1 py-0.5 text-[10px] font-medium truncate">
                  {event.title}
                </div>
              )}
            />
          )}
          {selectedEvent && (
            <div className="mt-3 p-3 bg-secondary/50 border border-border rounded-lg text-sm">
              <div className="flex items-center justify-between mb-1">
                <span className="font-semibold text-foreground">{selectedEvent.empresa_nome}</span>
                <StatusBadge status={selectedEvent.status} />
              </div>
              {selectedEvent.numero_os && <p className="text-xs text-muted-foreground">OS: {selectedEvent.numero_os}</p>}
              {selectedEvent.descricao && <p className="text-xs text-muted-foreground mt-1">{selectedEvent.descricao}</p>}
            </div>
          )}
          {events.length === 0 && !loading && (
            <p className="text-center text-muted-foreground text-xs mt-4">
              Nenhuma OS com prazo definido para exibir no calendário.
            </p>
          )}
        </div>
      )}
    </div>
  )
}
