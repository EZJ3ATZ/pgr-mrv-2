import { useState, useEffect, useRef } from 'react'
import { Upload, Plus, X, FileDown, AlertTriangle, CheckCircle2, Loader2 } from 'lucide-react'

const UFS = ['MG','SP','RJ','ES','GO','BA','PR','SC','RS','DF','MT','MS','PA']

export default function GeradorPGR() {
  const [cargosOpcoes, setCargosOpcoes] = useState([])
  const [form, setForm] = useState({
    nome: '', cnpj: '', cep: '', rua: '', numero: '', complemento: '',
    bairro: '', cidade: '', uf: 'MG',
  })
  const [cargos, setCargos] = useState([])
  const [cargoInput, setCargoInput] = useState('')
  const [uploading, setUploading] = useState(false)
  const [gerando, setGerando] = useState(false)
  const [status, setStatus] = useState(null) // { tipo: 'erro'|'aviso'|'ok', msg, alertas, filename, b64 }
  const [pdfNome, setPdfNome] = useState('')
  const fileRef = useRef()

  useEffect(() => {
    fetch('/api/cargos').then(r => r.json()).then(setCargosOpcoes).catch(() => {})
  }, [])

  function set(k, v) { setForm(f => ({ ...f, [k]: v })) }

  // ── Upload PDF ──────────────────────────────────────────────────────
  async function handlePdf(file) {
    if (!file) return
    setPdfNome(file.name)
    setUploading(true)
    setStatus(null)
    const fd = new FormData()
    fd.append('pdf', file)
    try {
      const r = await fetch('/extrair', { method: 'POST', body: fd })
      const d = await r.json()
      setForm(f => ({
        ...f,
        nome:         d.nome         || f.nome,
        cnpj:         d.cnpj         || f.cnpj,
        cep:          d.cep          || f.cep,
        rua:          d.rua          || f.rua,
        numero:       d.numero       || f.numero,
        complemento:  d.complemento  || f.complemento,
        bairro:       d.bairro       || f.bairro,
        cidade:       d.cidade       || f.cidade,
        uf:           d.uf           || f.uf,
      }))
      if (d.cargos?.length) {
        setCargos(prev => {
          const todos = [...prev, ...d.cargos.filter(c => !prev.includes(c))]
          return todos
        })
      }
    } catch {
      setStatus({ tipo: 'erro', msg: 'Erro ao processar PDF.' })
    } finally {
      setUploading(false)
    }
  }

  function addCargo() {
    const c = cargoInput.trim()
    if (!c || cargos.includes(c)) return
    setCargos(p => [...p, c])
    setCargoInput('')
  }

  function removeCargo(c) { setCargos(p => p.filter(x => x !== c)) }

  function selectCargo(v) {
    if (!v || cargos.includes(v)) return
    setCargos(p => [...p, v])
  }

  // ── Gerar PGR ───────────────────────────────────────────────────────
  async function gerarPGR() {
    setStatus(null)
    if (!form.nome.trim()) { setStatus({ tipo: 'erro', msg: 'Informe a Razão Social.' }); return }
    if (cargos.length === 0) { setStatus({ tipo: 'erro', msg: 'Adicione pelo menos um cargo.' }); return }
    setGerando(true)
    try {
      const r = await fetch('/gerar', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ...form, cargos }),
      })

      if (r.status === 422) {
        const d = await r.json()
        setStatus({ tipo: 'aviso', msg: d.erro })
        return
      }

      if (!r.ok) {
        const d = await r.json().catch(() => ({}))
        setStatus({ tipo: 'erro', msg: d.erro || 'Erro ao gerar PGR.' })
        return
      }

      const ct = r.headers.get('Content-Type') || ''
      if (ct.includes('json')) {
        const d = await r.json()
        if (d.aviso_gerado) {
          // Baixar mesmo com aviso
          const bytes = Uint8Array.from(atob(d.docx_b64), c => c.charCodeAt(0))
          const blob = new Blob([bytes], { type: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document' })
          const url = URL.createObjectURL(blob)
          const a = document.createElement('a'); a.href = url; a.download = d.filename; a.click()
          URL.revokeObjectURL(url)
          setStatus({ tipo: 'aviso_gerado', alertas: d.alertas, filename: d.filename })
        }
      } else {
        const blob = await r.blob()
        const cd = r.headers.get('Content-Disposition') || ''
        const fn = cd.match(/filename="?([^"]+)"?/)?.[1] || 'PGR.docx'
        const url = URL.createObjectURL(blob)
        const a = document.createElement('a'); a.href = url; a.download = fn; a.click()
        URL.revokeObjectURL(url)
        setStatus({ tipo: 'ok', msg: `${fn} gerado com sucesso!` })
      }
    } catch {
      setStatus({ tipo: 'erro', msg: 'Erro de conexão.' })
    } finally {
      setGerando(false)
    }
  }

  return (
    <div className="space-y-4 max-w-3xl">
      <div>
        <h1 className="text-foreground text-lg font-semibold">Programa de Gerenciamento de Riscos</h1>
        <p className="text-muted-foreground text-xs mt-0.5">Preencha os dados da empresa, adicione os cargos e clique em Gerar PGR.</p>
      </div>

      {/* Upload PDF */}
      <div className="bg-card border border-border rounded-lg p-4">
        <div className="flex items-center gap-2 mb-3">
          <Upload size={14} className="text-muted-foreground" />
          <span className="text-sm font-semibold text-foreground">Importar PDF da Empresa</span>
          <span className="text-xs text-muted-foreground ml-1">— extração automática de dados</span>
        </div>
        <div
          className="border-2 border-dashed border-border rounded-lg p-6 text-center cursor-pointer hover:border-blue-500/50 transition-colors"
          onClick={() => fileRef.current?.click()}
          onDragOver={e => e.preventDefault()}
          onDrop={e => { e.preventDefault(); handlePdf(e.dataTransfer.files[0]) }}
        >
          {uploading
            ? <div className="flex items-center justify-center gap-2 text-muted-foreground text-sm"><Loader2 size={16} className="animate-spin" /> Extraindo dados...</div>
            : pdfNome
              ? <p className="text-sm text-blue-400">{pdfNome}</p>
              : <>
                  <Upload size={20} className="mx-auto mb-2 text-muted-foreground" />
                  <p className="text-sm text-foreground font-medium">Clique para selecionar o PDF</p>
                  <p className="text-xs text-muted-foreground mt-1">ou arraste aqui · PDF até 10 MB</p>
                </>
          }
        </div>
        <input ref={fileRef} type="file" accept=".pdf" className="hidden" onChange={e => handlePdf(e.target.files[0])} />
        <p className="text-xs text-muted-foreground mt-2">Dados extraídos são sugestão — confirme antes de gerar.</p>
      </div>

      {/* Dados da Empresa */}
      <div className="bg-card border border-border rounded-lg p-4 space-y-3">
        <p className="text-sm font-semibold text-foreground">Dados da Empresa</p>
        <div>
          <label className="text-xs text-muted-foreground">Razão Social / Nome</label>
          <input className="form-input mt-1" placeholder="Nome completo da empresa ou MEI"
            value={form.nome} onChange={e => set('nome', e.target.value)} />
        </div>
        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className="text-xs text-muted-foreground">CNPJ</label>
            <input className="form-input mt-1" placeholder="00.000.000/0001-00"
              value={form.cnpj} onChange={e => set('cnpj', e.target.value)} />
          </div>
          <div>
            <label className="text-xs text-muted-foreground">CEP</label>
            <input className="form-input mt-1" placeholder="00000-000"
              value={form.cep} onChange={e => set('cep', e.target.value)} />
          </div>
        </div>
        <div className="grid grid-cols-3 gap-3">
          <div className="col-span-2">
            <label className="text-xs text-muted-foreground">Logradouro</label>
            <input className="form-input mt-1" placeholder="Rua / Avenida"
              value={form.rua} onChange={e => set('rua', e.target.value)} />
          </div>
          <div>
            <label className="text-xs text-muted-foreground">Número</label>
            <input className="form-input mt-1" placeholder="Nº"
              value={form.numero} onChange={e => set('numero', e.target.value)} />
          </div>
        </div>
        <div className="grid grid-cols-3 gap-3">
          <div>
            <label className="text-xs text-muted-foreground">Complemento</label>
            <input className="form-input mt-1" placeholder="Apto, Bloco..."
              value={form.complemento} onChange={e => set('complemento', e.target.value)} />
          </div>
          <div>
            <label className="text-xs text-muted-foreground">Bairro</label>
            <input className="form-input mt-1" placeholder="Bairro"
              value={form.bairro} onChange={e => set('bairro', e.target.value)} />
          </div>
          <div>
            <label className="text-xs text-muted-foreground">Cidade</label>
            <input className="form-input mt-1" placeholder="Cidade"
              value={form.cidade} onChange={e => set('cidade', e.target.value)} />
          </div>
        </div>
        <div className="w-24">
          <label className="text-xs text-muted-foreground">UF</label>
          <select className="form-input mt-1" value={form.uf} onChange={e => set('uf', e.target.value)}>
            {UFS.map(u => <option key={u} value={u}>{u}</option>)}
          </select>
        </div>
      </div>

      {/* Cargos */}
      <div className="bg-card border border-border rounded-lg p-4 space-y-3">
        <p className="text-sm font-semibold text-foreground">Cargos da Empresa</p>
        <p className="text-xs text-muted-foreground">O GHE e os agentes de risco são preenchidos automaticamente</p>
        <div className="flex gap-2">
          <input
            className="form-input flex-1"
            placeholder="Digite o cargo..."
            value={cargoInput}
            onChange={e => setCargoInput(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && addCargo()}
          />
          <select className="form-input w-56" onChange={e => { selectCargo(e.target.value); e.target.value = '' }}>
            <option value="">— Selecionar da lista —</option>
            {cargosOpcoes.map(c => <option key={c} value={c}>{c}</option>)}
          </select>
          <button
            className="flex items-center gap-1.5 px-3 py-1.5 bg-blue-600 hover:bg-blue-700 text-white text-sm rounded-md transition-colors"
            onClick={addCargo}
          >
            <Plus size={14} /> Adicionar
          </button>
        </div>

        {cargos.length > 0 && (
          <div className="space-y-1.5 mt-2">
            {cargos.map(c => (
              <div key={c} className="flex items-center justify-between bg-secondary/50 border border-border rounded-md px-3 py-1.5">
                <span className="text-sm text-foreground">{c}</span>
                <button onClick={() => removeCargo(c)} className="text-muted-foreground hover:text-red-400 transition-colors">
                  <X size={13} />
                </button>
              </div>
            ))}
          </div>
        )}
        {cargos.length === 0 && (
          <p className="text-xs text-muted-foreground italic">Nenhum cargo adicionado.</p>
        )}
      </div>

      {/* Status */}
      {status && (
        <div className={`flex gap-2 p-3 rounded-lg border text-sm ${
          status.tipo === 'ok' ? 'bg-green-950/30 border-green-800/40 text-green-400' :
          status.tipo === 'erro' ? 'bg-red-950/30 border-red-800/40 text-red-400' :
          'bg-yellow-950/30 border-yellow-800/40 text-yellow-400'
        }`}>
          {status.tipo === 'ok' ? <CheckCircle2 size={16} className="shrink-0 mt-0.5" /> : <AlertTriangle size={16} className="shrink-0 mt-0.5" />}
          <div>
            {status.msg && <p>{status.msg}</p>}
            {status.alertas?.map((a, i) => <p key={i}>{a}</p>)}
            {status.tipo === 'aviso_gerado' && <p className="mt-1 font-medium">Arquivo gerado com avisos — verifique os pontos acima.</p>}
          </div>
        </div>
      )}

      {/* Botão Gerar */}
      <button
        className="flex items-center gap-2 px-6 py-3 bg-green-600 hover:bg-green-700 disabled:opacity-50 text-white font-semibold rounded-lg transition-colors w-full justify-center"
        onClick={gerarPGR}
        disabled={gerando}
      >
        {gerando ? <Loader2 size={17} className="animate-spin" /> : <FileDown size={17} />}
        {gerando ? 'Gerando...' : 'Gerar PGR'}
      </button>
    </div>
  )
}
