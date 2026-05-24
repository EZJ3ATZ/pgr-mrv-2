import { useEffect, useState, useRef } from 'react'
import FullCalendar from '@fullcalendar/react'
import dayGridPlugin from '@fullcalendar/daygrid'
import timeGridPlugin from '@fullcalendar/timegrid'
import interactionPlugin from '@fullcalendar/interaction'
import ptBrLocale from '@fullcalendar/core/locales/pt-br'
import { motion } from 'framer-motion'
import { Calendar, MapPin, Clock } from 'lucide-react'

const STATUS_COLORS = {
  aberta:       '#60a5fa',
  em_andamento: '#fbbf24',
  concluida:    '#4ade80',
  pendente:     '#52525b',
}

export default function Coleta() {
  const [demandas, setDemandas] = useState([])
  const [loading, setLoading]   = useState(true)
  const [selected, setSelected] = useState(null)
  const calendarRef = useRef(null)

  useEffect(() => {
    fetch('/controle/demandas?limit=500')
      .then(r => r.json())
      .then(d => {
        const arr = Array.isArray(d) ? d : d.items || []
        setDemandas(arr)
      })
      .finally(() => setLoading(false))
  }, [])

  // Converter demandas com prazo em eventos do calendário
  const events = demandas
    .filter(d => d.prazo)
    .map(d => ({
      id:    String(d.id),
      title: d.empresa_nome || `OS ${d.numero_os}`,
      date:  d.prazo,
      extendedProps: { status: d.status, os: d.numero_os, desc: d.descricao, prioridade: d.prioridade },
      backgroundColor: STATUS_COLORS[d.status] || '#52525b',
      borderColor:     STATUS_COLORS[d.status] || '#52525b',
      textColor:       '#0a0a0a',
    }))

  return (
    <motion.div
      className="flex flex-col gap-4 h-full"
      initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.18 }}
    >
      {/* Header */}
      <div className="flex items-center justify-between shrink-0">
        <div>
          <h1 className="text-foreground text-lg font-semibold">Coleta de Campo</h1>
          <p className="text-muted-foreground text-xs mt-0.5">
            {loading ? 'Carregando...' : `${events.length} OS com prazo definido`}
          </p>
        </div>
        <div className="flex gap-3 text-xs text-muted-foreground">
          {Object.entries({ aberta: 'Aberta', em_andamento: 'Em andamento', concluida: 'Concluída', pendente: 'Pendente' }).map(([k, v]) => (
            <span key={k} className="flex items-center gap-1.5">
              <span className="w-2 h-2 rounded-full" style={{ background: STATUS_COLORS[k] }} />
              {v}
            </span>
          ))}
        </div>
      </div>

      <div className="flex gap-4 flex-1 min-h-0">
        {/* Calendário */}
        <div className="flex-1 min-h-0 bg-card border border-border rounded-lg overflow-hidden p-3 fullcalendar-dark">
          {!loading && (
            <FullCalendar
              ref={calendarRef}
              plugins={[dayGridPlugin, timeGridPlugin, interactionPlugin]}
              initialView="dayGridMonth"
              locale={ptBrLocale}
              events={events}
              height="100%"
              headerToolbar={{
                left:   'prev,next today',
                center: 'title',
                right:  'dayGridMonth,timeGridWeek',
              }}
              eventClick={({ event }) => setSelected(event)}
              eventDidMount={({ el }) => {
                el.style.fontSize = '11px'
                el.style.borderRadius = '3px'
                el.style.fontWeight = '500'
              }}
              dayMaxEvents={3}
              moreLinkContent={({ num }) => `+${num} mais`}
            />
          )}
        </div>

        {/* Painel lateral — detalhes do evento selecionado */}
        <div className="w-64 shrink-0 flex flex-col gap-3">
          {selected ? (
            <motion.div
              key={selected.id}
              initial={{ opacity: 0, x: 10 }} animate={{ opacity: 1, x: 0 }} transition={{ duration: 0.15 }}
              className="bg-card border border-border rounded-lg p-4 space-y-3"
            >
              <div className="flex items-start gap-2">
                <div className="w-2.5 h-2.5 rounded-full mt-1 shrink-0"
                     style={{ background: selected.backgroundColor }} />
                <div>
                  <p className="text-foreground text-sm font-medium leading-tight">{selected.title}</p>
                  <p className="text-muted-foreground text-[10px] mt-0.5">OS {selected.extendedProps.os}</p>
                </div>
              </div>
              <div className="space-y-2 text-xs text-muted-foreground">
                <div className="flex items-center gap-2">
                  <Clock size={11} />
                  <span>Prazo: {selected.startStr}</span>
                </div>
                <div className="flex items-center gap-2">
                  <MapPin size={11} />
                  <span>Prioridade: {selected.extendedProps.prioridade || '—'}</span>
                </div>
                <div className="flex items-center gap-2">
                  <Calendar size={11} />
                  <span>Status: {selected.extendedProps.status}</span>
                </div>
              </div>
              {selected.extendedProps.desc && (
                <p className="text-[11px] text-muted-foreground border-t border-border pt-2 leading-relaxed line-clamp-4">
                  {selected.extendedProps.desc}
                </p>
              )}
              <button
                onClick={() => setSelected(null)}
                className="text-[10px] text-muted-foreground hover:text-foreground transition-colors"
              >
                Fechar ×
              </button>
            </motion.div>
          ) : (
            <div className="bg-card border border-border rounded-lg p-4 text-center text-muted-foreground text-xs">
              <Calendar size={24} className="mx-auto mb-2 opacity-30" />
              Clique em um evento para ver detalhes
            </div>
          )}

          {/* Resumo do mês */}
          <div className="bg-card border border-border rounded-lg p-4 space-y-2">
            <p className="text-[10px] uppercase tracking-wider text-muted-foreground/50 font-semibold">Resumo</p>
            {Object.entries(STATUS_COLORS).map(([k, color]) => {
              const count = events.filter(e => e.extendedProps?.status === k).length
              return count > 0 ? (
                <div key={k} className="flex items-center justify-between text-xs">
                  <span className="flex items-center gap-1.5 text-muted-foreground">
                    <span className="w-1.5 h-1.5 rounded-full" style={{ background: color }} />
                    {k === 'em_andamento' ? 'Em andamento' : k.charAt(0).toUpperCase() + k.slice(1)}
                  </span>
                  <span className="font-semibold text-foreground tabular-nums">{count}</span>
                </div>
              ) : null
            })}
          </div>
        </div>
      </div>

      {/* CSS override para tema dark */}
      <style>{`
        .fullcalendar-dark .fc {
          --fc-border-color: #27272a;
          --fc-button-bg-color: #18181b;
          --fc-button-border-color: #27272a;
          --fc-button-hover-bg-color: #27272a;
          --fc-button-hover-border-color: #3f3f46;
          --fc-button-active-bg-color: #3f3f46;
          --fc-button-text-color: #a1a1aa;
          --fc-today-bg-color: #1c1c1e;
          --fc-neutral-bg-color: #18181b;
          --fc-page-bg-color: transparent;
          --fc-non-business-color: #18181b;
          color: #e4e4e7;
          font-size: 12px;
        }
        .fullcalendar-dark .fc-toolbar-title { font-size: 14px; font-weight: 600; color: #e4e4e7; }
        .fullcalendar-dark .fc-col-header-cell { font-size: 11px; font-weight: 500; color: #71717a; }
        .fullcalendar-dark .fc-daygrid-day-number { font-size: 11px; color: #71717a; }
        .fullcalendar-dark .fc-day-today .fc-daygrid-day-number { color: #60a5fa; font-weight: 700; }
        .fullcalendar-dark .fc-button { font-size: 11px !important; padding: 3px 8px !important; border-radius: 5px !important; }
        .fullcalendar-dark .fc-more-link { font-size: 10px; color: #71717a; }
      `}</style>
    </motion.div>
  )
}
