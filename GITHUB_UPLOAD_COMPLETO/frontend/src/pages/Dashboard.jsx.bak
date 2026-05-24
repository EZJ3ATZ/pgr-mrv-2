import { motion } from 'framer-motion'
import {
  Package, FlaskConical, ClipboardList, Building2,
  TrendingUp, AlertTriangle, CheckCircle2, Clock, ArrowRight
} from 'lucide-react'
import {
  AreaChart, Area, XAxis, YAxis, Tooltip, ResponsiveContainer,
  PieChart, Pie, Cell, Legend
} from 'recharts'
import { useStats } from '../hooks/useStats'

// Animação de entrada para cards
const fadeUp = {
  hidden: { opacity: 0, y: 16 },
  show:   { opacity: 1, y: 0 },
}

const stagger = {
  show: { transition: { staggerChildren: 0.07 } },
}

// Dados mock de produtividade (meses)
const prodData = [
  { mes: 'Jan', medicoes: 18, laudos: 5 },
  { mes: 'Fev', medicoes: 24, laudos: 8 },
  { mes: 'Mar', medicoes: 31, laudos: 11 },
  { mes: 'Abr', medicoes: 27, laudos: 9 },
  { mes: 'Mai', medicoes: 38, laudos: 14 },
]

export default function Dashboard() {
  const { data: stats, loading } = useStats()

  const kpis = [
    {
      label: 'Medições realizadas',
      value: stats?.medicoes_realizadas ?? '—',
      sub: 'total histórico',
      icon: CheckCircle2,
      color: 'text-green',
      bg: 'bg-green/10',
    },
    {
      label: 'Amostradores em estoque',
      value: stats?.estoque ?? '—',
      sub: 'disponíveis',
      icon: Package,
      color: 'text-blue',
      bg: 'bg-blue/10',
    },
    {
      label: 'Demandas abertas',
      value: stats?.demandas_pendentes ?? '—',
      sub: 'pendentes de coleta',
      icon: ClipboardList,
      color: 'text-yellow',
      bg: 'bg-yellow/10',
    },
    {
      label: 'No laboratório',
      value: stats?.laboratorio ?? '—',
      sub: 'aguardando resultado',
      icon: FlaskConical,
      color: 'text-purple',
      bg: 'bg-purple/10',
    },
    {
      label: 'Empresas ativas',
      value: stats?.empresas_ativas ?? '—',
      sub: 'com OS registradas',
      icon: Building2,
      color: 'text-text1',
      bg: 'bg-surface2',
    },
    {
      label: 'Medições pendentes',
      value: stats?.medicoes_pendentes ?? '—',
      sub: 'a executar',
      icon: Clock,
      color: 'text-red',
      bg: 'bg-red/10',
    },
  ]

  // Dados do pie chart de amostradores
  const pieData = stats ? [
    { name: 'Estoque',    value: stats.estoque,    color: '#388bfd' },
    { name: 'Laboratório', value: stats.laboratorio, color: '#bc8cff' },
    { name: 'Reservados', value: stats.reservados,  color: '#d29922' },
    { name: 'Devolvidos', value: stats.devolvidos,  color: '#3fb950' },
  ].filter(d => d.value > 0) : []

  return (
    <div className="space-y-6">
      {/* Título */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-text1 text-xl font-semibold">Dashboard</h1>
          <p className="text-text2 text-sm mt-0.5">Visão geral operacional</p>
        </div>
        <div className="flex items-center gap-2 text-xs text-text2">
          <TrendingUp size={13} className="text-green" />
          Atualizado agora
        </div>
      </div>

      {/* KPI Grid */}
      <motion.div
        variants={stagger}
        initial="hidden"
        animate="show"
        className="grid grid-cols-3 gap-4"
      >
        {kpis.map((kpi) => (
          <motion.div key={kpi.label} variants={fadeUp}>
            <KpiCard {...kpi} loading={loading} />
          </motion.div>
        ))}
      </motion.div>

      {/* Charts row */}
      <div className="grid grid-cols-3 gap-4">
        {/* Produtividade — área chart */}
        <motion.div
          variants={fadeUp}
          initial="hidden"
          animate="show"
          className="card col-span-2"
        >
          <div className="flex items-center justify-between mb-4">
            <div>
              <div className="text-text1 font-semibold text-sm">Produtividade</div>
              <div className="text-text2 text-xs">Medições e laudos por mês</div>
            </div>
          </div>
          <ResponsiveContainer width="100%" height={180}>
            <AreaChart data={prodData} margin={{ top: 0, right: 0, left: -20, bottom: 0 }}>
              <defs>
                <linearGradient id="gMed" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%"  stopColor="#3fb950" stopOpacity={0.25} />
                  <stop offset="95%" stopColor="#3fb950" stopOpacity={0} />
                </linearGradient>
                <linearGradient id="gLaud" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%"  stopColor="#388bfd" stopOpacity={0.25} />
                  <stop offset="95%" stopColor="#388bfd" stopOpacity={0} />
                </linearGradient>
              </defs>
              <XAxis dataKey="mes" tick={{ fill: '#8b949e', fontSize: 11 }} axisLine={false} tickLine={false} />
              <YAxis tick={{ fill: '#8b949e', fontSize: 11 }} axisLine={false} tickLine={false} />
              <Tooltip
                contentStyle={{ background: '#161b22', border: '1px solid #30363d', borderRadius: 8, fontSize: 12 }}
                labelStyle={{ color: '#e6edf3' }}
              />
              <Area type="monotone" dataKey="medicoes" name="Medições" stroke="#3fb950" fill="url(#gMed)" strokeWidth={2} dot={false} />
              <Area type="monotone" dataKey="laudos"   name="Laudos"   stroke="#388bfd" fill="url(#gLaud)" strokeWidth={2} dot={false} />
            </AreaChart>
          </ResponsiveContainer>
        </motion.div>

        {/* Amostradores — pie chart */}
        <motion.div
          variants={fadeUp}
          initial="hidden"
          animate="show"
          className="card"
        >
          <div className="text-text1 font-semibold text-sm mb-1">Amostradores</div>
          <div className="text-text2 text-xs mb-3">Distribuição por status</div>
          {pieData.length > 0 ? (
            <ResponsiveContainer width="100%" height={180}>
              <PieChart>
                <Pie
                  data={pieData}
                  cx="50%"
                  cy="50%"
                  innerRadius={45}
                  outerRadius={70}
                  paddingAngle={3}
                  dataKey="value"
                >
                  {pieData.map((entry) => (
                    <Cell key={entry.name} fill={entry.color} />
                  ))}
                </Pie>
                <Tooltip
                  contentStyle={{ background: '#161b22', border: '1px solid #30363d', borderRadius: 8, fontSize: 12 }}
                />
                <Legend
                  iconType="circle"
                  iconSize={8}
                  formatter={(value) => <span style={{ color: '#8b949e', fontSize: 11 }}>{value}</span>}
                />
              </PieChart>
            </ResponsiveContainer>
          ) : (
            <div className="h-[180px] flex items-center justify-center text-text3 text-sm">
              Carregando...
            </div>
          )}
        </motion.div>
      </div>

      {/* Alertas */}
      {stats?.venc_urgente > 0 && (
        <motion.div variants={fadeUp} initial="hidden" animate="show">
          <div className="card border-red/30 bg-red/5 flex items-center gap-3">
            <AlertTriangle size={16} className="text-red shrink-0" />
            <div className="text-sm text-text1">
              <span className="font-semibold text-red">{stats.venc_urgente}</span> amostrador(es) com vencimento urgente no laboratório
            </div>
            <button className="ml-auto flex items-center gap-1 text-xs text-red hover:underline">
              Ver <ArrowRight size={12} />
            </button>
          </div>
        </motion.div>
      )}
    </div>
  )
}

function KpiCard({ label, value, sub, icon: Icon, color, bg, loading }) {
  return (
    <div className="kpi-card group">
      <div className="flex items-start justify-between">
        <div className={`w-8 h-8 rounded-lg ${bg} flex items-center justify-center`}>
          <Icon size={15} className={color} />
        </div>
      </div>
      <div className={`text-2xl font-bold mt-2 ${color} ${loading ? 'opacity-30' : ''}`}>
        {loading ? '···' : value}
      </div>
      <div className="text-text1 text-xs font-medium">{label}</div>
      <div className="text-text3 text-[11px]">{sub}</div>
    </div>
  )
}
