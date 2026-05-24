import { useState, useRef } from 'react'
import { Plus, X, FileDown, AlertTriangle, CheckCircle2, Loader2, Image, ChevronDown, ChevronUp } from 'lucide-react'

const GRAUS = ['1','2','3','4']
const TECNICOS = [
  { value: 'kelly',   label: 'Kelly Elissama Firmino' },
  { value: 'helbert', label: 'Helbert Gonçalves de Oliveira' },
  { value: 'matheus', label: 'Matheus Costa' },
]
const UFS = ['MG','SP','RJ','ES','GO','BA','PR','SC','RS','DF','MT','MS','PA']

function newAval() {
  return {
    cargo: '', setor: '', trabalhador: '',
    dataColeta: '', horaInicio: '', horaFim: '', jornada: '8h',
    serie: '', fontes: '', atividades: '',
    controleColetivo: 'N.A.', epi: 'Protetor Auditivo',
    lavgQ3: '', nenQ3: '', doseQ3: '',
    twaQ5: '', lavgQ5: '', doseQ5: '',
    neQ5: '', nenQ5: '',
    tabelaImg: null, histogramaImg: null, certImgs: [],
    expanded: true,
  }
}

function imgToB64(file) {
  return new Promise((res, rej) => {
    const r = new FileReader()
    r.onload = e => res(e.target.result)
    r.onerror = rej
    r.readAsDataURL(file)
  })
}

function ConclusaoTag({ lavgQ3 }) {
  const v = parseFloat(String(lavgQ3).replace(',', '.'))
  if (isNaN(v) || lavgQ3 === '') return null
  if (v >= 85) return <span className="text-xs px-2 py-0.5 rounded bg-red-900/40 text-red-400 font-medium">⚠ Acima do Limite (≥85 dB)</span>
  if (v >= 80) return <span className="text-xs px-2 py-0.5 rounded bg-yellow-900/40 text-yellow-400 font-medium">⚡ Nível de Ação (80–84 dB)</span>
  return <span className="text-xs px-2 py-0.5 rounded bg-green-900/40 text-green-400 font-medium">✓ Dentro do Limite (&lt;80 dB)</span>
}

export default function LaudoRuido() {
  const [empresa, setEmpresa] = useState({
    razaoSocial: '', logo: '', cnpj: '', endereco: '', cep: '',
    bairro: '', cidade: 'Belo Horizonte', uf: 'MG',
    cnae: '', grauRisco: '2', descricaoCnae: '',
    responsavel: '', telefone: '', email: '',
  })
  const [tecnico, setTecnico] = useState('kelly')
  const [dataLaudo, setDataLaudo] = useState('')
  const [avaliacoes, setAvaliacoes] = useState([newAval()])
  const [gerando, setGerando] = useState(false)
  const [status, setStatus] = useState(null)
  const logoRef = useRef()

  function setEmp(k, v) { setEmpresa(e => ({ ...e, [k]: v })) }

  function setAval(i, k, v) {
    setAvaliacoes(a => a.map((x, idx) => idx === i ? { ...x, [k]: v } : x))
  }

  function addAval() { setAvaliacoes(a => [...a, newAval()]) }
  function removeAval(i) { setAvaliacoes(a => a.filter((_, idx) => idx !== i)) }
  function toggleAval(i) { setAvaliacoes(a => a.map((x, idx) => idx === i ? { ...x, expanded: !x.expanded } : x)) }

  async function handleLogo(file) {
    if (!file) return
    const b64 = await imgToB64(file)
    setEmp('logo', b64)
  }

  async function handleImg(i, field, file) {
    if (!file) return
    const b64 = await imgToB64(file)
    setAval(i, field, b64)
  }

  async function handleCertImg(i, file) {
    if (!file) return
    const b64 = await imgToB64(file)
    setAvaliacoes(a => a.map((x, idx) => idx === i ? { ...x, certImgs: [...x.certImgs, b64] } : x))
  }

  function removeCertImg(i, ci) {
    setAvaliacoes(a => a.map((x, idx) => idx === i ? { ...x, certImgs: x.certImgs.filter((_, j) => j !== ci) } : x))
  }

  async function gerar() {
    setStatus(null)
    if (!empresa.razaoSocial.trim()) { setStatus({ tipo: 'erro', msg: 'Informe a Razão Social.' }); return }
    if (avaliacoes.length === 0) { setStatus({ tipo: 'erro', msg: 'Adicione pelo menos uma avaliação.' }); return }
    setGerando(true)
    try {
      const payload = {
        empresa: { ...empresa },
        tecnico,
        dataLaudo,
        avaliacoes: avaliacoes.map(({ expanded, ...av }) => av),
      }
      const r = await fetch('/gerar-ruido', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      })
      if (!r.ok) {
        const d = await r.json().catch(() => ({}))
        setStatus({ tipo: 'erro', msg: d.erro || `Erro ${r.status}` })
        return
      }
      const blob = await r.blob()
      const cd = r.headers.get('Content-Disposition') || ''
      const fn = cd.match(/filename="?([^"]+)"?/)?.[1] || 'Laudo_Ruido.docx'
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a'); a.href = url; a.download = fn; a.click()
      URL.revokeObjectURL(url)
      setStatus({ tipo: 'ok', msg: `${fn} gerado com sucesso!` })
    } catch {
      setStatus({ tipo: 'erro', msg: 'Erro de conexão.' })
    } finally {
      setGerando(false)
    }
  }

  return (
    <div className="space-y-4 max-w-4xl">
      <div>
        <h1 className="text-foreground text-lg font-semibold">Laudo de Ruído — NR-15 Anexo 1</h1>
        <p className="text-muted-foreground text-xs mt-0.5">Preencha os dados, adicione as avaliações e gere o laudo.</p>
      </div>

      {/* Dados da Empresa */}
      <div className="bg-card border border-border rounded-lg p-4 space-y-3">
        <p className="text-sm font-semibold text-foreground">Dados da Empresa</p>
        <div>
          <label className="text-xs text-muted-foreground">Razão Social</label>
          <input className="form-input mt-1" placeholder="Nome completo da empresa"
            value={empresa.razaoSocial} onChange={e => setEmp('razaoSocial', e.target.value)} />
        </div>
        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className="text-xs text-muted-foreground">CNPJ</label>
            <input className="form-input mt-1" placeholder="00.000.000/0001-00"
              value={empresa.cnpj} onChange={e => setEmp('cnpj', e.target.value)} />
          </div>
          <div>
            <label className="text-xs text-muted-foreground">CEP</label>
            <input className="form-input mt-1" placeholder="00000-000"
              value={empresa.cep} onChange={e => setEmp('cep', e.target.value)} />
          </div>
        </div>
        <div className="grid grid-cols-3 gap-3">
          <div className="col-span-2">
            <label className="text-xs text-muted-foreground">Endereço (Rua, Nº)</label>
            <input className="form-input mt-1" placeholder="Rua / Avenida, Nº"
              value={empresa.endereco} onChange={e => setEmp('endereco', e.target.value)} />
          </div>
          <div>
            <label className="text-xs text-muted-foreground">Bairro</label>
            <input className="form-input mt-1" placeholder="Bairro"
              value={empresa.bairro} onChange={e => setEmp('bairro', e.target.value)} />
          </div>
        </div>
        <div className="grid grid-cols-3 gap-3">
          <div>
            <label className="text-xs text-muted-foreground">Cidade</label>
            <input className="form-input mt-1" placeholder="Cidade"
              value={empresa.cidade} onChange={e => setEmp('cidade', e.target.value)} />
          </div>
          <div>
            <label className="text-xs text-muted-foreground">UF</label>
            <select className="form-input mt-1" value={empresa.uf} onChange={e => setEmp('uf', e.target.value)}>
              {UFS.map(u => <option key={u} value={u}>{u}</option>)}
            </select>
          </div>
          <div>
            <label className="text-xs text-muted-foreground">Grau de Risco</label>
            <select className="form-input mt-1" value={empresa.grauRisco} onChange={e => setEmp('grauRisco', e.target.value)}>
              {GRAUS.map(g => <option key={g} value={g}>{g}</option>)}
            </select>
          </div>
        </div>
        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className="text-xs text-muted-foreground">CNAE</label>
            <input className="form-input mt-1" placeholder="00.00-0/00"
              value={empresa.cnae} onChange={e => setEmp('cnae', e.target.value)} />
          </div>
          <div>
            <label className="text-xs text-muted-foreground">Descrição CNAE</label>
            <input className="form-input mt-1" placeholder="Atividade econômica"
              value={empresa.descricaoCnae} onChange={e => setEmp('descricaoCnae', e.target.value)} />
          </div>
        </div>
        <div className="grid grid-cols-3 gap-3">
          <div>
            <label className="text-xs text-muted-foreground">Responsável</label>
            <input className="form-input mt-1" placeholder="Nome do responsável"
              value={empresa.responsavel} onChange={e => setEmp('responsavel', e.target.value)} />
          </div>
          <div>
            <label className="text-xs text-muted-foreground">Telefone</label>
            <input className="form-input mt-1" placeholder="(00) 00000-0000"
              value={empresa.telefone} onChange={e => setEmp('telefone', e.target.value)} />
          </div>
          <div>
            <label className="text-xs text-muted-foreground">E-mail</label>
            <input className="form-input mt-1" placeholder="email@empresa.com"
              value={empresa.email} onChange={e => setEmp('email', e.target.value)} />
          </div>
        </div>
        <div>
          <label className="text-xs text-muted-foreground">Logo da Empresa <span className="text-muted-foreground/60">(opcional)</span></label>
          <button className="btn-secondary mt-1" onClick={() => logoRef.current?.click()}>
            <Image size={13} />
            {empresa.logo ? 'Logo carregado ✓' : 'Selecionar logo (PNG/JPG)'}
          </button>
          <input ref={logoRef} type="file" accept="image/*" className="hidden"
            onChange={e => handleLogo(e.target.files[0])} />
        </div>
      </div>

      {/* Config do Laudo */}
      <div className="bg-card border border-border rounded-lg p-4 space-y-3">
        <p className="text-sm font-semibold text-foreground">Configurações do Laudo</p>
        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className="text-xs text-muted-foreground">Data do Laudo</label>
            <input className="form-input mt-1" placeholder="dd/mm/aaaa"
              value={dataLaudo} onChange={e => setDataLaudo(e.target.value)} />
          </div>
          <div>
            <label className="text-xs text-muted-foreground">Técnico Responsável</label>
            <select className="form-input mt-1" value={tecnico} onChange={e => setTecnico(e.target.value)}>
              {TECNICOS.map(t => <option key={t.value} value={t.value}>{t.label}</option>)}
            </select>
          </div>
        </div>
      </div>

      {/* Avaliações */}
      <div className="space-y-3">
        <div className="flex items-center justify-between">
          <p className="text-sm font-semibold text-foreground">Avaliações de Ruído</p>
          <button className="flex items-center gap-1.5 px-3 py-1.5 bg-blue-600 hover:bg-blue-700 text-white text-sm rounded-md transition-colors"
            onClick={addAval}>
            <Plus size={14} /> Adicionar Avaliação
          </button>
        </div>

        {avaliacoes.map((av, i) => (
          <div key={i} className="bg-card border border-border rounded-lg overflow-hidden">
            {/* Header */}
            <div className="flex items-center justify-between px-4 py-2.5 bg-secondary/30 border-b border-border">
              <button className="flex items-center gap-2 text-sm font-medium text-foreground" onClick={() => toggleAval(i)}>
                {av.expanded ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
                Avaliação {String(i+1).padStart(2,'0')} {av.cargo ? `— ${av.cargo}` : ''}
                {av.lavgQ3 && <ConclusaoTag lavgQ3={av.lavgQ3} />}
              </button>
              {avaliacoes.length > 1 && (
                <button onClick={() => removeAval(i)} className="text-muted-foreground hover:text-red-400 transition-colors">
                  <X size={14} />
                </button>
              )}
            </div>

            {av.expanded && (
              <div className="p-4 space-y-3">
                {/* Identificação */}
                <div className="grid grid-cols-3 gap-3">
                  <div>
                    <label className="text-xs text-muted-foreground">Cargo</label>
                    <input className="form-input mt-1" placeholder="Ex: Pedreiro"
                      value={av.cargo} onChange={e => setAval(i, 'cargo', e.target.value)} />
                  </div>
                  <div>
                    <label className="text-xs text-muted-foreground">Setor</label>
                    <input className="form-input mt-1" placeholder="Ex: Produção"
                      value={av.setor} onChange={e => setAval(i, 'setor', e.target.value)} />
                  </div>
                  <div>
                    <label className="text-xs text-muted-foreground">Funcionário(a)</label>
                    <input className="form-input mt-1" placeholder="Nome do trabalhador"
                      value={av.trabalhador} onChange={e => setAval(i, 'trabalhador', e.target.value)} />
                  </div>
                </div>

                {/* Datas e horários */}
                <div className="grid grid-cols-4 gap-3">
                  <div>
                    <label className="text-xs text-muted-foreground">Data Coleta</label>
                    <input className="form-input mt-1" placeholder="dd/mm/aaaa"
                      value={av.dataColeta} onChange={e => setAval(i, 'dataColeta', e.target.value)} />
                  </div>
                  <div>
                    <label className="text-xs text-muted-foreground">Hora Início</label>
                    <input className="form-input mt-1" placeholder="08:00"
                      value={av.horaInicio} onChange={e => setAval(i, 'horaInicio', e.target.value)} />
                  </div>
                  <div>
                    <label className="text-xs text-muted-foreground">Hora Fim</label>
                    <input className="form-input mt-1" placeholder="17:00"
                      value={av.horaFim} onChange={e => setAval(i, 'horaFim', e.target.value)} />
                  </div>
                  <div>
                    <label className="text-xs text-muted-foreground">Jornada</label>
                    <input className="form-input mt-1" placeholder="8h"
                      value={av.jornada} onChange={e => setAval(i, 'jornada', e.target.value)} />
                  </div>
                </div>

                {/* Equipamento e fontes */}
                <div className="grid grid-cols-2 gap-3">
                  <div>
                    <label className="text-xs text-muted-foreground">Nº Série Dosímetro</label>
                    <input className="form-input mt-1" placeholder="Ex: DSM-0001"
                      value={av.serie} onChange={e => setAval(i, 'serie', e.target.value)} />
                  </div>
                  <div>
                    <label className="text-xs text-muted-foreground">EPI Utilizado</label>
                    <input className="form-input mt-1" placeholder="Protetor Auditivo"
                      value={av.epi} onChange={e => setAval(i, 'epi', e.target.value)} />
                  </div>
                </div>
                <div>
                  <label className="text-xs text-muted-foreground">Fonte(s) Geradora(s) de Ruído</label>
                  <input className="form-input mt-1" placeholder="Ex: Compressores, Ferramentas pneumáticas"
                    value={av.fontes} onChange={e => setAval(i, 'fontes', e.target.value)} />
                </div>
                <div>
                  <label className="text-xs text-muted-foreground">Descrição das Atividades</label>
                  <textarea className="form-input mt-1" rows={2} placeholder="Descreva as atividades realizadas durante a medição"
                    value={av.atividades} onChange={e => setAval(i, 'atividades', e.target.value)} />
                </div>
                <div>
                  <label className="text-xs text-muted-foreground">Medidas de Controle Coletivo</label>
                  <input className="form-input mt-1" placeholder="N.A."
                    value={av.controleColetivo} onChange={e => setAval(i, 'controleColetivo', e.target.value)} />
                </div>

                {/* Resultados */}
                <div className="border border-border/50 rounded-md p-3 space-y-3">
                  <p className="text-xs font-semibold text-muted-foreground uppercase tracking-wider">Resultados da Dosimetria</p>

                  {/* Q=3 */}
                  <div>
                    <p className="text-xs text-blue-400 font-medium mb-1.5">Q = 3 dB / NHO-01 — Exposição Ocupacional</p>
                    <div className="grid grid-cols-3 gap-3">
                      <div>
                        <label className="text-xs text-muted-foreground">LAVG dB(A)</label>
                        <input className="form-input mt-1" placeholder="0.0"
                          value={av.lavgQ3} onChange={e => setAval(i, 'lavgQ3', e.target.value)} />
                      </div>
                      <div>
                        <label className="text-xs text-muted-foreground">NEN dB</label>
                        <input className="form-input mt-1" placeholder="0.0"
                          value={av.nenQ3} onChange={e => setAval(i, 'nenQ3', e.target.value)} />
                      </div>
                      <div>
                        <label className="text-xs text-muted-foreground">DOSE %</label>
                        <input className="form-input mt-1" placeholder="0.0"
                          value={av.doseQ3} onChange={e => setAval(i, 'doseQ3', e.target.value)} />
                      </div>
                    </div>
                  </div>

                  {/* Q=5 */}
                  <div>
                    <p className="text-xs text-yellow-400 font-medium mb-1.5">Q = 5 dB / NR-15 — Legislação Trabalhista</p>
                    <div className="grid grid-cols-3 gap-3">
                      <div>
                        <label className="text-xs text-muted-foreground">TWA dB(A)</label>
                        <input className="form-input mt-1" placeholder="0.0"
                          value={av.twaQ5} onChange={e => setAval(i, 'twaQ5', e.target.value)} />
                      </div>
                      <div>
                        <label className="text-xs text-muted-foreground">LAVG dB(A)</label>
                        <input className="form-input mt-1" placeholder="0.0"
                          value={av.lavgQ5} onChange={e => setAval(i, 'lavgQ5', e.target.value)} />
                      </div>
                      <div>
                        <label className="text-xs text-muted-foreground">DOSE %</label>
                        <input className="form-input mt-1" placeholder="0.0"
                          value={av.doseQ5} onChange={e => setAval(i, 'doseQ5', e.target.value)} />
                      </div>
                    </div>
                  </div>

                  {/* Q=5* INSS */}
                  <div>
                    <p className="text-xs text-purple-400 font-medium mb-1.5">Q = 5* dB / NEN INSS — Legislação Previdenciária</p>
                    <div className="grid grid-cols-2 gap-3">
                      <div>
                        <label className="text-xs text-muted-foreground">NE dB</label>
                        <input className="form-input mt-1" placeholder="0.0"
                          value={av.neQ5} onChange={e => setAval(i, 'neQ5', e.target.value)} />
                      </div>
                      <div>
                        <label className="text-xs text-muted-foreground">NEN dB</label>
                        <input className="form-input mt-1" placeholder="0.0"
                          value={av.nenQ5} onChange={e => setAval(i, 'nenQ5', e.target.value)} />
                      </div>
                    </div>
                  </div>
                </div>

                {/* Imagens */}
                <div className="border border-border/50 rounded-md p-3 space-y-3">
                  <p className="text-xs font-semibold text-muted-foreground uppercase tracking-wider">Imagens do Dosímetro <span className="normal-case font-normal">(opcional)</span></p>
                  <div className="grid grid-cols-2 gap-3">
                    <div>
                      <label className="text-xs text-muted-foreground">Tabela de Resultados</label>
                      <label className="btn-secondary mt-1 cursor-pointer">
                        <Image size={13} />
                        {av.tabelaImg ? 'Tabela carregada ✓' : 'Selecionar imagem'}
                        <input type="file" accept="image/*" className="hidden"
                          onChange={e => handleImg(i, 'tabelaImg', e.target.files[0])} />
                      </label>
                    </div>
                    <div>
                      <label className="text-xs text-muted-foreground">Histograma</label>
                      <label className="btn-secondary mt-1 cursor-pointer">
                        <Image size={13} />
                        {av.histogramaImg ? 'Histograma carregado ✓' : 'Selecionar imagem'}
                        <input type="file" accept="image/*" className="hidden"
                          onChange={e => handleImg(i, 'histogramaImg', e.target.files[0])} />
                      </label>
                    </div>
                  </div>
                  <div>
                    <label className="text-xs text-muted-foreground">Certificados de Calibração</label>
                    <label className="btn-secondary mt-1 cursor-pointer">
                      <Plus size={13} />
                      Adicionar certificado
                      <input type="file" accept="image/*" className="hidden"
                        onChange={e => handleCertImg(i, e.target.files[0])} />
                    </label>
                    {av.certImgs.length > 0 && (
                      <div className="flex flex-wrap gap-1.5 mt-2">
                        {av.certImgs.map((_, ci) => (
                          <div key={ci} className="flex items-center gap-1 px-2 py-1 bg-secondary rounded text-xs text-muted-foreground">
                            Cert. {ci+1}
                            <button onClick={() => removeCertImg(i, ci)} className="hover:text-red-400">
                              <X size={11} />
                            </button>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                </div>
              </div>
            )}
          </div>
        ))}
      </div>

      {/* Status */}
      {status && (
        <div className={`flex gap-2 p-3 rounded-lg border text-sm ${
          status.tipo === 'ok' ? 'bg-green-950/30 border-green-800/40 text-green-400' :
          'bg-red-950/30 border-red-800/40 text-red-400'
        }`}>
          {status.tipo === 'ok' ? <CheckCircle2 size={16} className="shrink-0 mt-0.5" /> : <AlertTriangle size={16} className="shrink-0 mt-0.5" />}
          <p>{status.msg}</p>
        </div>
      )}

      {/* Botão */}
      <button
        className="flex items-center gap-2 px-6 py-3 bg-green-600 hover:bg-green-700 disabled:opacity-50 text-white font-semibold rounded-lg transition-colors w-full justify-center"
        onClick={gerar}
        disabled={gerando}
      >
        {gerando ? <Loader2 size={17} className="animate-spin" /> : <FileDown size={17} />}
        {gerando ? 'Gerando...' : 'Gerar Laudo de Ruído'}
      </button>
    </div>
  )
}
