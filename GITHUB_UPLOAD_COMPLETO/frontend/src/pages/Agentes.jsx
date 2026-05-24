import { useState, useEffect, useMemo } from 'react'
import { Search, Filter } from 'lucide-react'

const TIPO_CORES = {
  'Ruído':       'bg-orange-900/30 text-orange-400 border-orange-800/40',
  'Químico':     'bg-purple-900/30 text-purple-400 border-purple-800/40',
  'Ergonômico':  'bg-blue-900/30 text-blue-400 border-blue-800/40',
  'Acidente':    'bg-yellow-900/30 text-yellow-400 border-yellow-800/40',
}

const NIVEL_CORES = {
  'Alto':          'text-red-400',
  'Moderado':      'text-yellow-400',
  'Baixo':         'text-green-400',
  'Risco Alto':    'text-red-400',
  'Risco Moderado':'text-yellow-400',
  'Risco Baixo':   'text-green-400',
}

function Badge({ tipo }) {
  const cls = TIPO_CORES[tipo] || 'bg-secondary text-muted-foreground border-border'
  return (
    <span className={`inline-flex text-xs px-1.5 py-0.5 rounded border font-medium ${cls}`}>
      {tipo}
    </span>
  )
}

function NivelText({ nivel }) {
  const cls = NIVEL_CORES[nivel] || 'text-muted-foreground'
  return <span className={`text-xs font-medium ${cls}`}>{nivel}</span>
}

export default function Agentes() {
  const [dados, setDados] = useState([])
  const [loading, setLoading] = useState(true)
  const [busca, setBusca] = useState('')
  const [filtroTipo, setFiltroTipo] = useState('Todos')
  const [filtroGhe, setFiltroGhe] = useState('Todos')

  useEffect(() => {
    fetch('/api/agentes')
      .then(r => r.json())
      .then(d => { setDados(d); setLoading(false) })
      .catch(() => setLoading(false))
  }, [])

  const ghes = useMemo(() => ['Todos', ...Array.from(new Set(dados.map(d => d.ghe))).sort()], [dados])
  const tipos = ['Todos', 'Ruído', 'Químico', 'Ergonômico', 'Acidente']

  const filtrados = useMemo(() => {
    return dados.filter(d => {
      if (filtroTipo !== 'Todos' && d.tipo !== filtroTipo) return false
      if (filtroGhe !== 'Todos' && d.ghe !== filtroGhe) return false
      if (busca) {
        const b = busca.toLowerCase()
        return d.ghe.toLowerCase().includes(b) ||
               d.agente.toLowerCase().includes(b) ||
               d.tipo.toLowerCase().includes(b)
      }
      return true
    })
  }, [dados, filtroTipo, filtroGhe, busca])

  // Stats
  const stats = useMemo(() => {
    const ghesUnicos = new Set(dados.map(d => d.ghe)).size
    const insalubres = dados.filter(d => d.insalubridade).length
    return { total: dados.length, ghes: ghesUnicos, insalubres }
  }, [dados])

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-foreground text-lg font-semibold">Agentes de Risco</h1>
          <p className="text-muted-foreground text-xs mt-0.5">GHEs e agentes mapeados para obras MRV</p>
        </div>
      </div>

      {/* Stats */}
      {!loading && (
        <div className="grid grid-cols-3 gap-3">
          <div className="bg-card border border-border rounded-lg p-3">
            <p className="text-xs text-muted-foreground">Total de Agentes</p>
            <p className="text-2xl font-semibold text-foreground mt-1">{stats.total}</p>
          </div>
          <div className="bg-card border border-border rounded-lg p-3">
            <p className="text-xs text-muted-foreground">GHEs Mapeados</p>
            <p className="text-2xl font-semibold text-foreground mt-1">{stats.ghes}</p>
          </div>
          <div className="bg-card border border-border rounded-lg p-3">
            <p className="text-xs text-muted-foreground">Com Insalubridade</p>
            <p className="text-2xl font-semibold text-orange-400 mt-1">{stats.insalubres}</p>
          </div>
        </div>
      )}

      {/* Filtros */}
      <div className="flex gap-2 flex-wrap">
        <div className="relative flex-1 min-w-48">
          <Search size={13} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-muted-foreground" />
          <input
            className="form-input pl-8"
            placeholder="Buscar GHE ou agente..."
            value={busca}
            onChange={e => setBusca(e.target.value)}
          />
        </div>
        <div className="flex items-center gap-1.5">
          <Filter size={13} className="text-muted-foreground" />
          <select className="form-input w-36" value={filtroTipo} onChange={e => setFiltroTipo(e.target.value)}>
            {tipos.map(t => <option key={t} value={t}>{t}</option>)}
          </select>
        </div>
        <select className="form-input w-52" value={filtroGhe} onChange={e => setFiltroGhe(e.target.value)}>
          {ghes.map(g => <option key={g} value={g}>{g === 'Todos' ? '— Todos os GHEs —' : g.replace(/_/g, ' ')}</option>)}
        </select>
      </div>

      {/* Tabela */}
      <div className="bg-card border border-border rounded-lg overflow-hidden">
        {loading ? (
          <div className="flex items-center justify-center h-32 text-muted-foreground text-sm">
            Carregando agentes...
          </div>
        ) : filtrados.length === 0 ? (
          <div className="flex items-center justify-center h-32 text-muted-foreground text-sm">
            Nenhum agente encontrado
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="table-base">
              <thead>
                <tr>
                  <th>GHE</th>
                  <th>Tipo</th>
                  <th>Agente</th>
                  <th>Nível/Risco</th>
                  <th>Valor</th>
                  <th>Insalubridade</th>
                </tr>
              </thead>
              <tbody>
                {filtrados.map((d, i) => (
                  <tr key={i} className="hover:bg-secondary/20 transition-colors">
                    <td>
                      <span className="font-medium text-foreground text-xs">
                        {d.ghe.replace(/_/g, ' ')}
                      </span>
                    </td>
                    <td><Badge tipo={d.tipo} /></td>
                    <td className="text-foreground max-w-xs">{d.agente}</td>
                    <td><NivelText nivel={d.nivel} /></td>
                    <td className="text-muted-foreground text-xs">
                      {d.tipo === 'Químico' && d.concentracao
                        ? <span>{d.concentracao} <span className="text-muted-foreground/60">(LT: {d.lt})</span></span>
                        : d.valor || '—'
                      }
                    </td>
                    <td>
                      {d.insalubridade
                        ? <span className="text-xs text-orange-400 font-medium">Sim</span>
                        : <span className="text-xs text-muted-foreground">—</span>
                      }
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {!loading && (
        <p className="text-xs text-muted-foreground">
          {filtrados.length} de {dados.length} agentes exibidos
        </p>
      )}
    </div>
  )
}
