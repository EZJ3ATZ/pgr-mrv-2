import { useState, useRef } from 'react'
import { Plus, X, Thermometer, FileDown, AlertTriangle, CheckCircle2, Loader2, Image } from 'lucide-react'

const UFS = ['MG','SP','RJ','ES','GO','BA','PR','SC','RS','DF','MT','MS','PA']
const GRS = ['1','2','3','4']

const ATIVIDADES = [
  { label: 'Trabalho Leve – Sentado, movimentos moderados com braços e tronco', M: 117 },
  { label: 'Trabalho Leve – Sentado, movimentos moderados com braços e pernas', M: 148 },
  { label: 'Trabalho Leve – De pé, trabalho leve em máquina ou bancada', M: 117 },
  { label: 'Trabalho Moderado – Sentado, movimentos vigorosos com braços e pernas', M: 188 },
  { label: 'Trabalho Moderado – De pé, com os braços e tronco', M: 198 },
  { label: 'Trabalho Moderado – Em pé, inclinado, carregando pesos moderados', M: 207 },
  { label: 'Trabalho Moderado – Caminhando, carregando pesos leves', M: 221 },
  { label: 'Trabalho Pesado – Trabalho intenso', M: 290 },
  { label: 'Trabalho Muito Pesado – Trabalho muito intenso', M: 407 },
]

const VESTIMENTAS = [
  'Uniforme de Trabalho (0)',
  'Avental de algodão (0)',
  'Uniforme completo (0)',
  'Macacão (0)',
  'Jaqueta e calça (5)',
  'Macacão de SMS descartável (0,5)',
  'Uniforme de SMS duplo (1,0)',
  'Conjunto de capuz de SMS (1,0)',
]

function novoPonto() {
  return {
    local: '', tempo: 60,
    tbn: '', tbs: '', tg: '',
    atividade: ATIVIDADES[4].label, M: 198,
  }
}

function novoSetor() {
  return { nome: '', horario: '', vestimenta: VESTIMENTAS[0], pontos: [novoPonto()] }
}

function calcIbutg(tbn, tg, interno = true) {
  const n = +tbn, g = +tg
  if (!n || !g) return null
  return interno ? (0.7 * n + 0.3 * g) : (0.7 * n + 0.1 * n + 0.2 * g) // NR-15 An.3
}

function getLimite(M) {
  if (M <= 175) return 30.5
  if (M <= 250) return 28.0
  if (M <= 360) return 25.5
  if (M <= 465) return 22.5
  return 20.0
}

export default function LaudoCalor() {
  const [empresa, setEmpresa] = useState({
    razaoSocial: '', cnpj: '', cep: '', endereco: '', bairro: '',
    cidade: 'Belo Horizonte', uf: 'MG', cnae: '', grauRisco: '3',
    descricaoCnae: '', contato: '', telefone: '', email: '', logo: null,
  })
  const [aval, setAval] = useState({
    dataAvaliacao: '', cidadeCarta: 'BELO HORIZONTE, MAIO DE 2026',
    equipamento: 'Net.Temp – Chrompack Smart TEMP | S/N: IBU0000000209',
    certNo: '180.646', dataCalib: '24/03/2026', artNumero: '',
  })
  const [setores, setSetores] = useState([novoSetor()])
  const [gerando, setGerando] = useState(false)
  const [status, setStatus] = useState(null)
  const logoRef = useRef()

  function setEmp(k, v) { setEmpresa(e => ({ ...e, [k]: v })) }
  function setAv(k, v)  { setAval(a => ({ ...a, [k]: v })) }

  // ── Setores ──────────────────────────────────────────────────────────
  function addSetor() { setSetores(s => [...s, novoSetor()]) }
  function remSetor(si) { setSetores(s => s.filter((_, i) => i !== si)) }
  function updSetor(si, k, v) {
    setSetores(s => s.map((x, i) => i === si ? { ...x, [k]: v } : x))
  }
  function addPonto(si) {
    setSetores(s => s.map((x, i) => i === si ? { ...x, pontos: [...x.pontos, novoPonto()] } : x))
  }
  function remPonto(si, pi) {
    setSetores(s => s.map((x, i) => i === si ? { ...x, pontos: x.pontos.filter((_, j) => j !== pi) } : x))
  }
  function updPonto(si, pi, k, v) {
    setSetores(s => s.map((x, i) => i !== si ? x : {
      ...x,
      pontos: x.pontos.map((p, j) => j !== pi ? p : {
        ...p, [k]: k === 'tempo' || k === 'M' ? +v : v,
        ...(k === 'atividade' ? { M: ATIVIDADES.find(a => a.label === v)?.M ?? p.M } : {}),
      }),
    }))
  }

  // ── Logo ─────────────────────────────────────────────────────────────
  function selecionarLogo(file) {
    if (!file) return
    const r = new FileReader()
    r.onload = e => setEmpresa(emp => ({ ...emp, logo: e.target.result }))
    r.readAsDataURL(file)
  }

  // ── Gerar ────────────────────────────────────────────────────────────
  async function gerar() {
    setStatus(null)
    if (!empresa.razaoSocial.trim()) { setStatus({ tipo: 'erro', msg: 'Informe a Razão Social.' }); return }
    if (!setores.length) { setStatus({ tipo: 'erro', msg: 'Adicione pelo menos um setor.' }); return }
    const semPontos = setores.some(s => !s.pontos.length || s.pontos.some(p => !p.tbn || !p.tg))
    if (semPontos) { setStatus({ tipo: 'erro', msg: 'Preencha Tbn e Tg em todos os pontos.' }); return }

    setGerando(true)
    try {
      const payload = {
        empresa: { ...empresa },
        avaliacao: { ...aval },
        setores: setores.map(s => ({
          nome: s.nome, horario: s.horario, vestimenta: s.vestimenta,
          pontos: s.pontos.map(p => ({
            local: p.local, tempo: +p.tempo,
            tbn: +p.tbn, tbs: +p.tbs || 0, tg: +p.tg,
            atividade: p.atividade, M: +p.M,
            fotos: [null, null, null],
          })),
        })),
      }
      const r = await fetch('/gerar_calor', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      })
      if (!r.ok) { const e = await r.json().catch(() => ({})); setStatus({ tipo: 'erro', msg: e.erro || 'Erro.' }); return }
      const blob = await r.blob()
      const cd = r.headers.get('Content-Disposition') || ''
      const fn = cd.match(/filename="?([^"]+)"?/)?.[1] || `Laudo de Calor - ${empresa.razaoSocial}.docx`
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a'); a.href = url; a.download = fn; a.click()
      URL.revokeObjectURL(url)
      setStatus({ tipo: 'ok', msg: `${fn} gerado com sucesso!` })
    } catch { setStatus({ tipo: 'erro', msg: 'Erro de conexão.' }) }
    finally { setGerando(false) }
  }

  return (
    <div className="space-y-4 max-w-3xl">
      <div>
        <h1 className="text-foreground text-lg font-semibold">Laudo de Calor — NR-15 Anexo 3</h1>
        <p className="text-muted-foreground text-xs mt-0.5">Preencha os dados, adicione os setores e gere o laudo.</p>
      </div>

      {/* Dados da Empresa */}
      <div className="bg-card border border-border rounded-lg p-4 space-y-3">
        <p className="text-sm font-semibold text-foreground flex items-center gap-2"><Thermometer size={14} className="text-orange-400" /> Dados da Empresa</p>
        <div><label className="text-xs text-muted-foreground">Razão Social</label>
          <input className="form-input mt-1" placeholder="Nome completo da empresa" value={empresa.razaoSocial} onChange={e => setEmp('razaoSocial', e.target.value)} />
        </div>
        <div className="grid grid-cols-2 gap-3">
          <div><label className="text-xs text-muted-foreground">CNPJ</label>
            <input className="form-input mt-1" placeholder="00.000.000/0001-00" value={empresa.cnpj} onChange={e => setEmp('cnpj', e.target.value)} />
          </div>
          <div><label className="text-xs text-muted-foreground">CEP</label>
            <input className="form-input mt-1" placeholder="00000-000" value={empresa.cep} onChange={e => setEmp('cep', e.target.value)} />
          </div>
        </div>
        <div className="grid grid-cols-3 gap-3">
          <div className="col-span-2"><label className="text-xs text-muted-foreground">Endereço</label>
            <input className="form-input mt-1" placeholder="Rua / Avenida, Nº" value={empresa.endereco} onChange={e => setEmp('endereco', e.target.value)} />
          </div>
          <div><label className="text-xs text-muted-foreground">Bairro</label>
            <input className="form-input mt-1" placeholder="Bairro" value={empresa.bairro} onChange={e => setEmp('bairro', e.target.value)} />
          </div>
        </div>
        <div className="grid grid-cols-3 gap-3">
          <div><label className="text-xs text-muted-foreground">Cidade</label>
            <input className="form-input mt-1" placeholder="Cidade" value={empresa.cidade} onChange={e => setEmp('cidade', e.target.value)} />
          </div>
          <div><label className="text-xs text-muted-foreground">UF</label>
            <select className="form-input mt-1" value={empresa.uf} onChange={e => setEmp('uf', e.target.value)}>
              {UFS.map(u => <option key={u}>{u}</option>)}
            </select>
          </div>
          <div><label className="text-xs text-muted-foreground">Grau de Risco</label>
            <select className="form-input mt-1" value={empresa.grauRisco} onChange={e => setEmp('grauRisco', e.target.value)}>
              {GRS.map(g => <option key={g}>{g}</option>)}
            </select>
          </div>
        </div>
        <div className="grid grid-cols-2 gap-3">
          <div><label className="text-xs text-muted-foreground">CNAE</label>
            <input className="form-input mt-1" placeholder="00.00-0/00" value={empresa.cnae} onChange={e => setEmp('cnae', e.target.value)} />
          </div>
          <div><label className="text-xs text-muted-foreground">Descrição CNAE</label>
            <input className="form-input mt-1" placeholder="Atividade econômica" value={empresa.descricaoCnae} onChange={e => setEmp('descricaoCnae', e.target.value)} />
          </div>
        </div>
        <div className="grid grid-cols-3 gap-3">
          <div><label className="text-xs text-muted-foreground">Contato</label>
            <input className="form-input mt-1" placeholder="Nome" value={empresa.contato} onChange={e => setEmp('contato', e.target.value)} />
          </div>
          <div><label className="text-xs text-muted-foreground">Telefone</label>
            <input className="form-input mt-1" placeholder="(00) 00000-0000" value={empresa.telefone} onChange={e => setEmp('telefone', e.target.value)} />
          </div>
          <div><label className="text-xs text-muted-foreground">E-mail</label>
            <input className="form-input mt-1" placeholder="email@empresa.com" value={empresa.email} onChange={e => setEmp('email', e.target.value)} />
          </div>
        </div>
        {/* Logo */}
        <div>
          <label className="text-xs text-muted-foreground">Logo da Empresa <span className="text-muted-foreground/50">(opcional)</span></label>
          <div className="flex items-center gap-3 mt-1">
            {empresa.logo && <img src={empresa.logo} className="h-10 rounded border border-border bg-white p-1 object-contain" alt="logo" />}
            <button className="btn-secondary gap-1.5" onClick={() => logoRef.current?.click()}>
              <Image size={12} /> {empresa.logo ? 'Trocar logo' : 'Selecionar logo (PNG/JPG)'}
            </button>
            {empresa.logo && <button className="text-xs text-muted-foreground hover:text-red-400" onClick={() => setEmpresa(e => ({ ...e, logo: null }))}>remover</button>}
          </div>
          <input ref={logoRef} type="file" accept="image/*" className="hidden" onChange={e => selecionarLogo(e.target.files[0])} />
        </div>
      </div>

      {/* Dados da Avaliação */}
      <div className="bg-card border border-border rounded-lg p-4 space-y-3">
        <p className="text-sm font-semibold text-foreground">Dados da Avaliação</p>
        <div className="grid grid-cols-2 gap-3">
          <div><label className="text-xs text-muted-foreground">Data da Avaliação</label>
            <input className="form-input mt-1" placeholder="dd/mm/aaaa" value={aval.dataAvaliacao} onChange={e => setAv('dataAvaliacao', e.target.value)} />
          </div>
          <div><label className="text-xs text-muted-foreground">Cidade e Mês/Ano (carta)</label>
            <input className="form-input mt-1" value={aval.cidadeCarta} onChange={e => setAv('cidadeCarta', e.target.value)} />
          </div>
        </div>
        <div><label className="text-xs text-muted-foreground">Equipamento Utilizado</label>
          <input className="form-input mt-1" value={aval.equipamento} onChange={e => setAv('equipamento', e.target.value)} />
        </div>
        <div className="grid grid-cols-2 gap-3">
          <div><label className="text-xs text-muted-foreground">Certificado de Calibração Nº</label>
            <input className="form-input mt-1" value={aval.certNo} onChange={e => setAv('certNo', e.target.value)} />
          </div>
          <div><label className="text-xs text-muted-foreground">Data de Calibração</label>
            <input className="form-input mt-1" value={aval.dataCalib} onChange={e => setAv('dataCalib', e.target.value)} />
          </div>
        </div>
        <div><label className="text-xs text-muted-foreground">Número ART <span className="text-muted-foreground/50">(opcional)</span></label>
          <input className="form-input mt-1" placeholder="Ex: 2026123456789" value={aval.artNumero} onChange={e => setAv('artNumero', e.target.value)} />
        </div>
      </div>

      {/* Setores */}
      <div className="bg-card border border-border rounded-lg p-4 space-y-3">
        <div className="flex items-center justify-between">
          <p className="text-sm font-semibold text-foreground">Setores de Medição</p>
          <button className="btn-secondary gap-1.5 text-xs" onClick={addSetor}><Plus size={12} /> Setor</button>
        </div>

        {setores.length === 0 && (
          <p className="text-xs text-muted-foreground text-center py-4">Clique em "+ Setor" para adicionar o primeiro setor.</p>
        )}

        {setores.map((s, si) => (
          <div key={si} className="border border-border/60 rounded-lg p-3 space-y-3 bg-secondary/20">
            <div className="flex items-center justify-between">
              <p className="text-xs font-semibold text-muted-foreground uppercase tracking-wider">Setor {si + 1}</p>
              <button onClick={() => remSetor(si)} className="text-muted-foreground hover:text-red-400 transition-colors"><X size={13} /></button>
            </div>
            <div className="grid grid-cols-3 gap-2">
              <div><label className="text-xs text-muted-foreground">Nome do setor</label>
                <input className="form-input mt-1" placeholder="Ex: PADARIA" value={s.nome} onChange={e => updSetor(si, 'nome', e.target.value)} />
              </div>
              <div><label className="text-xs text-muted-foreground">Horário</label>
                <input className="form-input mt-1" placeholder="Ex: 07h às 16h" value={s.horario} onChange={e => updSetor(si, 'horario', e.target.value)} />
              </div>
              <div><label className="text-xs text-muted-foreground">Vestimenta</label>
                <select className="form-input mt-1" value={s.vestimenta} onChange={e => updSetor(si, 'vestimenta', e.target.value)}>
                  {VESTIMENTAS.map(v => <option key={v}>{v}</option>)}
                </select>
              </div>
            </div>

            {/* Pontos de medição */}
            <div className="space-y-2">
              {s.pontos.map((p, pi) => {
                const ibutg = calcIbutg(p.tbn, p.tg)
                const limite = getLimite(p.M)
                const ok = ibutg !== null && ibutg <= limite
                return (
                  <div key={pi} className="border border-border/40 rounded-md p-2.5 bg-card space-y-2">
                    <div className="flex items-center justify-between">
                      <span className="text-xs text-muted-foreground font-medium">Ponto {pi + 1}</span>
                      {s.pontos.length > 1 && (
                        <button onClick={() => remPonto(si, pi)} className="text-muted-foreground hover:text-red-400"><X size={11} /></button>
                      )}
                    </div>
                    <div className="grid grid-cols-4 gap-2">
                      <div><label className="text-xs text-muted-foreground">Local</label>
                        <input className="form-input mt-1" placeholder="Local" value={p.local} onChange={e => updPonto(si, pi, 'local', e.target.value)} />
                      </div>
                      <div><label className="text-xs text-muted-foreground">Tbn (°C)</label>
                        <input className="form-input mt-1" type="number" step="0.1" placeholder="28.5" value={p.tbn} onChange={e => updPonto(si, pi, 'tbn', e.target.value)} />
                      </div>
                      <div><label className="text-xs text-muted-foreground">Tg (°C)</label>
                        <input className="form-input mt-1" type="number" step="0.1" placeholder="31.0" value={p.tg} onChange={e => updPonto(si, pi, 'tg', e.target.value)} />
                      </div>
                      <div><label className="text-xs text-muted-foreground">Tempo (min)</label>
                        <input className="form-input mt-1" type="number" min="1" value={p.tempo} onChange={e => updPonto(si, pi, 'tempo', e.target.value)} />
                      </div>
                    </div>
                    <div className="grid grid-cols-2 gap-2">
                      <div><label className="text-xs text-muted-foreground">Atividade</label>
                        <select className="form-input mt-1" value={p.atividade} onChange={e => updPonto(si, pi, 'atividade', e.target.value)}>
                          {ATIVIDADES.map(a => <option key={a.label} value={a.label}>{a.label} ({a.M}W)</option>)}
                        </select>
                      </div>
                      <div><label className="text-xs text-muted-foreground">Taxa Metabólica (W)</label>
                        <input className="form-input mt-1" type="number" value={p.M} onChange={e => updPonto(si, pi, 'M', e.target.value)} />
                      </div>
                    </div>
                    {ibutg !== null && (
                      <div className={`flex gap-4 px-2.5 py-1.5 rounded text-xs font-medium ${ok ? 'bg-green-950/30 text-green-400 border border-green-800/30' : 'bg-red-950/30 text-red-400 border border-red-800/30'}`}>
                        <span>IBUTG = {ibutg.toFixed(1)} ºC</span>
                        <span>Limite = {limite} ºC</span>
                        <span>{ok ? '✔ ACEITÁVEL' : '✘ ACIMA DO LIMITE'}</span>
                      </div>
                    )}
                  </div>
                )
              })}
              <button className="text-xs text-muted-foreground hover:text-foreground flex items-center gap-1 transition-colors" onClick={() => addPonto(si)}>
                <Plus size={11} /> Adicionar ponto de medição
              </button>
            </div>
          </div>
        ))}
      </div>

      {/* Status */}
      {status && (
        <div className={`flex gap-2 p-3 rounded-lg border text-sm ${status.tipo === 'ok' ? 'bg-green-950/30 border-green-800/40 text-green-400' : 'bg-red-950/30 border-red-800/40 text-red-400'}`}>
          {status.tipo === 'ok' ? <CheckCircle2 size={16} className="shrink-0 mt-0.5" /> : <AlertTriangle size={16} className="shrink-0 mt-0.5" />}
          <p>{status.msg}</p>
        </div>
      )}

      <button
        className="flex items-center gap-2 px-6 py-3 bg-orange-600 hover:bg-orange-700 disabled:opacity-50 text-white font-semibold rounded-lg transition-colors w-full justify-center"
        onClick={gerar} disabled={gerando}
      >
        {gerando ? <Loader2 size={17} className="animate-spin" /> : <FileDown size={17} />}
        {gerando ? 'Gerando...' : 'Gerar Laudo de Calor'}
      </button>
    </div>
  )
}
