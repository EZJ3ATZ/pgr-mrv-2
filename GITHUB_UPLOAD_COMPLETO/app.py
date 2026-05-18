import os, re, shutil, zipfile, io, tempfile, random
from datetime import datetime
from flask import Flask, request, jsonify, send_file, render_template
import xml.etree.ElementTree as ET

try:
    from pdfminer.high_level import extract_text as pdf_extract
    PDF_OK = True
except ImportError:
    PDF_OK = False

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 10 * 1024 * 1024  # 10MB max

BASE_DIR   = os.path.dirname(os.path.abspath(__file__))
TPL_DIR    = os.path.join(BASE_DIR, 'tpl')
MODEL_DIR  = os.path.join(BASE_DIR, 'modelo_unpacked')

MESES_PT = {1:"Janeiro",2:"Fevereiro",3:"Março",4:"Abril",5:"Maio",6:"Junho",
            7:"Julho",8:"Agosto",9:"Setembro",10:"Outubro",11:"Novembro",12:"Dezembro"}

def mes_ano():
    d = datetime.now()
    return f"{MESES_PT[d.month]} / {d.year}"

# ── GHE ─────────────────────────────────────────────────────────────
# ════════════════════════════════════════════════════════════════════
# GHE_AGENTES
# Fonte confirmada com data: GHE Oregon - Residencial Oregon (20/02/2024)
# GHEs novos sem data confirmada (Mata das Borboletas): listados em GHE_SEM_DATA
# ════════════════════════════════════════════════════════════════════

# GHEs cujas medições não têm data confirmada — campo data aparece como ???
GHE_SEM_DATA = {
    "PAISAGISMO",
    "SERVICOS_GERAIS",
    "ASSIST_TEC_ELETRICA",
    "ASSIST_TEC_ESPEC",
    "MAQUINAS_PEQUENO_PORTE",
}

GHE_AGENTES = {
    # Ruído 87,49 | PNOS-Resp 0,90 — Oregon 20/02/2024
    "ACABAMENTO":[
        ('ruido','87,49 dB(A)','Alto',True,True),
        ('quant','Poeira não Fibrogênica (PNOS-Respirável)','Químico','2.640','1.320','0,90 mg/m³','Risco Baixo',False),
        ('ergon','Posturas Incomodas','Risco Baixo'),
        ('acid','Queda de Objetos','Risco Moderado'),
    ],
    # Ruído 83,49 Moderado | PNOS-Total 2,66
    "GESSO_REJUNTE":[
        ('ruido','76,06 dB(A)','Baixo',False,False),
        ('quant','Poeira não Fibrogênica (PNOS-Total)','Químico','10','5','2,66 mg/m³','Risco Baixo',False),
        ('ergon','Posturas Incomodas','Risco Baixo'),
        ('acid','Queda de Objetos','Risco Moderado'),
    ],
    # Ruído 65,25 Baixo | Monotonia | Postura Sentada
    "ADMINISTRATIVO":[
        ('ruido','74,88 dB(A)','Baixo',False,False),
        ('ergon','Monotonia','Risco Baixo'),
        ('ergon','Postura Sentada','Risco Baixo'),
    ],
    # Ruído 73,84 Baixo | Posturas | Queda
    "ALMOXARIFADO":[
        ('ruido','83,57 dB(A)','Moderado',True,False),
        ('ergon','Posturas Incomodas','Risco Baixo'),
        ('acid','Queda de Objetos','Risco Moderado'),
    ],
    # Ruído 72,83 Baixo | PNOS 0,02 | Posturas | Queda
    "APOIO_PRODUCAO":[
        ('ruido','81,77 dB(A)','Moderado',True,False),
        ('quant','Poeira não Fibrogênica (PNOS-Respirável)','Químico','2.640','1.320','0,15 mg/m³','Risco Baixo',False),
        ('ergon','Posturas Incomodas','Risco Baixo'),
        ('acid','Queda de Objetos','Risco Moderado'),
    ],
    # Ruído 83,85 Moderado | PNOS 0,76 | Posturas | Perfurocortantes | Queda
    "ARMACAO":[
        ('ruido','88,98 dB(A)','Alto',True,True),
        ('quant','Poeira não Fibrogênica (PNOS-Respirável)','Químico','2.640','1.320','0,19 mg/m³','Risco Baixo',False),
        ('ergon','Posturas Incomodas','Risco Baixo'),
        ('acid','Objetos Perfurocortantes','Risco Baixo'),
        ('acid','Queda de Objetos','Risco Moderado'),
    ],
    # Ruído 83,15 Moderado | Poeira Madeira 0,37 | PNOS 0,74 | Posturas | Perfurocortantes | Queda
    "CARPINTARIA":[
        ('ruido','84,47 dB(A)','Moderado',True,False),
        ('quant','Poeira de Madeira','Químico','1','0,5','0,33 mg/m³','Risco Baixo',False),
        ('quant','Poeira não Fibrogênica (PNOS-Respirável)','Químico','2.640','1.320','0,23 mg/m³','Risco Baixo',False),
        ('ergon','Posturas Incomodas','Risco Baixo'),
        ('acid','Objetos Perfurocortantes','Risco Baixo'),
        ('acid','Queda de Objetos','Risco Moderado'),
    ],
    # Central Betoneira — mantido do Oregon (não aparece no Mata das Borboletas)
    "CENTRAL_BETONEIRA":[
        ('ruido','85,06 dB(A)','Alto',True,True),
        ('quant','Poeira não Fibrogênica (PNOS-Respirável)','Químico','2.640','1.320','0,62 mg/m³','Risco Baixo',False),
        ('ergon','Posturas Incomodas','Risco Baixo'),
    ],
    # Ruído 82,09 Moderado | PNOS 0,38 | Posturas | Queda
    "ELETRICA":[
        ('ruido','81,46 dB(A)','Moderado',True,False),
        ('quant','Poeira não Fibrogênica (PNOS-Respirável)','Químico','2.640','1.320','0,38 mg/m³','Risco Baixo',False),
        ('ergon','Posturas Incomodas','Risco Baixo'),
        ('acid','Queda de Objetos','Risco Moderado'),
    ],
    # Ruído 80,07 Moderado | PNOS 0,33 | Posturas | Queda
    "ESTRUTURA_ALVENARIA":[
        ('ruido','81,57 dB(A)','Moderado',True,False),
        ('quant','Poeira não Fibrogênica (PNOS-Respirável)','Químico','2.640','1.320','0,33 mg/m³','Risco Baixo',False),
        ('ergon','Posturas Incomodas','Risco Baixo'),
        ('acid','Queda de Objetos','Risco Moderado'),
    ],
    # Ruído 93,71 Alto | PNOS 0,39 | Esforço | Posturas | Queda | Altura NR35
    "ESTRUTURA_PAREDE":[
        ('ruido','96,63 dB(A)','Alto',True,True),
        ('quant','Poeira não Fibrogênica (PNOS-Respirável)','Químico','2.640','1.320','0,28 mg/m³','Risco Baixo',False),
        ('ergon','Esforço Físico Intenso','Risco Moderado'),
        ('ergon','Posturas Incomodas','Risco Baixo'),
        ('acid','Queda de Objetos','Risco Moderado'),
        ('acid','Trabalho em Altura - NR35','Risco Moderado'),
    ],
    # Ruído 82,79 Moderado | MEK 13mg | PNOS 0,83 | Posturas | Queda
    "HIDRAULICA":[
        ('ruido','81,90 dB(A)','Moderado',True,False),
        ('quant','Poeira não Fibrogênica (PNOS-Respirável)','Químico','2.640','1.320','0,28 mg/m³','Risco Baixo',False),
        ('ergon','Posturas Incomodas','Risco Baixo'),
        ('acid','Queda de Objetos','Risco Moderado'),
    ],
    # Ruído 81,09 Moderado | Vibração AREN 0,81 | Vibração VDVR 16,6 | PNOS 1,71 Moderado | Postura Sentada
    "MAQUINAS_GERAL":[
        ('ruido','87,79 dB(A)','Alto',True,True),
        ('quant','Poeira não Fibrogênica (PNOS-Respirável)','Químico','2.640','1.320','0,74 mg/m³','Risco Baixo',False),
        ('ergon','Postura Sentada','Risco Baixo'),
    ],
    # Ruído 76,78 Baixo | Vibração | PNOS 0,20 | Postura Sentada — NOVO
    "MAQUINAS_PEQUENO_PORTE":[
        ('ruido','76,78 dB(A)','Baixo',False,False),
        ('quant','Poeira não Fibrogênica (PNOS-Respirável)','Químico','2.640','1.320','0,20 mg/m³','Risco Baixo',False),
        ('ergon','Postura Sentada','Risco Baixo'),
    ],
    # Ruído 78,48 Baixo | PNOS 0,17 | Posturas | Queda
    "MAQUINAS_ESTAC":[
        ('ruido','81,51 dB(A)','Moderado',True,False),
        ('quant','Poeira não Fibrogênica (PNOS-Respirável)','Químico','2.640','1.320','0,07 mg/m³','Risco Baixo',False),
        ('ergon','Posturas Incomodas','Risco Baixo'),
        ('acid','Queda de Objetos','Risco Moderado'),
    ],
    # Ruído 70,43 Baixo | PNOS 1,87 Moderado | Esforço | Posturas | Queda
    "OPERACIONAL":[
        ('ruido','80,29 dB(A)','Moderado',True,False),
        ('quant','Poeira não Fibrogênica (PNOS-Respirável)','Químico','2.640','1.320','0,19 mg/m³','Risco Baixo',False),
        ('ergon','Esforço Físico Intenso','Risco Moderado'),
        ('ergon','Posturas Incomodas','Risco Baixo'),
        ('acid','Queda de Objetos','Risco Moderado'),
    ],
    # Paisagismo — NOVO: Ruído 68,94 Baixo | PNOS 0,10 | Posturas | Queda
    "PAISAGISMO":[
        ('ruido','68,94 dB(A)','Baixo',False,False),
        ('quant','Poeira não Fibrogênica (PNOS-Respirável)','Químico','2.640','1.320','0,10 mg/m³','Risco Baixo',False),
        ('ergon','Posturas Incomodas','Risco Baixo'),
        ('acid','Queda de Objetos','Risco Moderado'),
    ],
    # Ruído 77,60 Baixo | PNOS 0,47 | Posturas | Queda
    "PINTURA":[
        ('ruido','82,54 dB(A)','Moderado',True,False),
        ('quant','Poeira não Fibrogênica (PNOS-Respirável)','Químico','2.640','1.320','0,47 mg/m³','Risco Baixo',False),
        ('ergon','Posturas Incomodas','Risco Baixo'),
        ('acid','Queda de Objetos','Risco Moderado'),
    ],
    # Ruído 72,31 Baixo | PNOS 0,15 | Monotonia
    "PORTARIA":[
        ('ruido','68,66 dB(A)','Baixo',False,False),
        ('quant','Poeira não Fibrogênica (PNOS-Respirável)','Químico','2.640','1.320','0,10 mg/m³','Risco Baixo',False),
        ('ergon','Monotonia','Risco Baixo'),
    ],
    # Polivalente — mantido do Oregon
    "POLIVALENTE":[
        ('ruido','84,52 dB(A)','Moderado',True,False),
        ('quant','Poeira de Madeira','Químico','1','0,5','0,33 mg/m³','Risco Baixo',False),
        ('quant','Poeira não Fibrogênica (PNOS-Respirável)','Químico','2.640','1.320','0,05 mg/m³','Risco Baixo',False),
        ('ergon','Posturas Incomodas','Risco Baixo'),
        ('acid','Objetos Perfurocortantes','Risco Baixo'),
        ('acid','Queda de Objetos','Risco Moderado'),
    ],
    # Ruído 84,12 Moderado | PNOS 0,17 | Posturas | Perfurocortantes | Queda
    "SERRALHERIA":[
        ('ruido','82,64 dB(A)','Moderado',True,False),
        ('quant','Poeira não Fibrogênica (PNOS-Respirável)','Químico','2.640','1.320','0,19 mg/m³','Risco Baixo',False),
        ('ergon','Posturas Incomodas','Risco Baixo'),
        ('acid','Objetos Perfurocortantes','Risco Baixo'),
        ('acid','Queda de Objetos','Risco Moderado'),
    ],
    # Serviços Gerais — NOVO: sem ruído; químicos (limpeza) + Posturas
    "SERVICOS_GERAIS":[
        ('quant','Poeira não Fibrogênica (PNOS-Respirável)','Químico','2.640','1.320','0,12 mg/m³','Risco Baixo',False),
        ('ergon','Posturas Incomodas','Risco Baixo'),
    ],
    # Ruído 81,22 Moderado | PNOS 0,44 | Posturas | Queda
    "SUPERVISAO":[
        ('ruido','81,65 dB(A)','Moderado',True,False),
        ('quant','Poeira não Fibrogênica (PNOS-Respirável)','Químico','2.640','1.320','0,44 mg/m³','Risco Baixo',False),
        ('ergon','Posturas Incomodas','Risco Baixo'),
        ('acid','Queda de Objetos','Risco Moderado'),
    ],
    # Assist. Téc. Elétrica — NOVO (Mata das Borboletas)
    "ASSIST_TEC_ELETRICA":[
        ('ruido','70,36 dB(A)','Baixo',False,False),
        ('quant','Poeira não Fibrogênica (PNOS-Respirável)','Químico','2.640','1.320','0,10 mg/m³','Risco Baixo',False),
        ('ergon','Posturas Incomodas','Risco Baixo'),
        ('acid','Queda de Objetos','Risco Moderado'),
    ],
    # Assist. Téc. Especializado — NOVO (Mata das Borboletas)
    "ASSIST_TEC_ESPEC":[
        ('ruido','70,36 dB(A)','Baixo',False,False),
        ('quant','Poeira não Fibrogênica (PNOS-Respirável)','Químico','2.640','1.320','0,10 mg/m³','Risco Baixo',False),
        ('ergon','Posturas Incomodas','Risco Baixo'),
        ('acid','Queda de Objetos','Risco Moderado'),
    ],
}

# ── CARGOS_SUGESTOES ────────────────────────────────────────────────
CARGOS_SUGESTOES = [
    "Ajudante de Obras","Ajudante em Geral","Almoxarife",
    "Analista Administrativo","Analista de Segurança do Trabalho",
    "Armador de Estruturas de Concreto","Armador de Ferragens",
    "Assistente Técnico Elétrico","Assistente Técnico Especializado",
    "Auxiliar Administrativo","Auxiliar de Limpeza","Auxiliar de Serviços Gerais",
    "Azulejista","Bombeiro Hidráulico","Carpinteiro",
    "Carpinteiro de Forma","Carpinteiro de Telhado",
    "Encanador","Encarregado de Obras","Encarregado de Produção",
    "Engenheiro Civil","Engenheiro de Segurança do Trabalho",
    "Estagiário de Engenharia","Estagiário de Segurança do Trabalho",
    "Gesseiro","Ladrilheiro","Mestre de Obras",
    "Meio Oficial Carpinteiro","Meio Oficial Eletricista",
    "Meio Oficial Pedreiro","Meio Oficial Pintor",
    "Meio Oficial Serralheiro","Montador de Estruturas Metálicas",
    "Motorista","Oficial Eletricista","Operador de Betoneira",
    "Operador de Cremalheira","Operador de Grua",
    "Operador de Máquinas em Geral","Operador de Miniescavadeira",
    "Operador de Retroescavadeira","Paisagista","Pedreiro",
    "Pintor de Obras","Polivalente","Porteiro","Recepcionista",
    "Rejuntador","Serralheiro","Servente de Obras","Sinaleiro",
    "Técnico de Segurança do Trabalho","Técnico em Edificações",
    "Topógrafo","Vigia",
]

# ── LABELS_PAT ──────────────────────────────────────────────────────
_LABELS_PAT = re.compile(
    r'^(?:RAZÃO SOCIAL|NOME EMPRESARIAL|NOME:|DENOMINAÇÃO|CNPJ|CPF|NIRE|'
    r'ENDEREÇO|LOGRADOURO|NÚMERO|COMPLEMENTO|BAIRRO|MUNICÍPIO|CIDADE|'
    r'CEP|UF|ESTADO|ATIVIDADE|PORTE|SITUAÇÃO|NATUREZA|DATA|'
    r'CAPITAL|SÓCIO|QUADRO|RESPONSÁVEL|REGISTRO|INSCRIÇÃO|'
    r'TELEFONE|EMAIL|FAX|CONTATO)',
    re.I
)

def _label_value(lines, label_pat):
    """Return the line *after* the first line matching label_pat."""
    for i, ln in enumerate(lines):
        if re.search(label_pat, ln, re.I):
            for j in range(i + 1, min(i + 4, len(lines))):
                v = lines[j].strip()
                if v and not _LABELS_PAT.match(v):
                    return v
    return ''

# ── PDF EXTRACTION ───────────────────────────────────────────────────
def extrair_dados_pdf(pdf_bytes: bytes) -> dict:
    if not PDF_OK:
        return {}
    try:
        text = pdf_extract(io.BytesIO(pdf_bytes))
    except Exception:
        return {}

    lines = [l.strip() for l in text.splitlines() if l.strip()]
    full  = ' '.join(lines)

    # ── CNPJ ────────────────────────────────────────────────────────
    cnpj_m = re.search(r'\d{2}[.\s]?\d{3}[.\s]?\d{3}[/\s]?\d{4}[-\s]?\d{2}', full)
    cnpj   = cnpj_m.group(0).strip() if cnpj_m else ''

    # ── RAZÃO SOCIAL ────────────────────────────────────────────────
    nome = ''
    # 1) Tenta label explícito (apenas 2 linhas para não capturar rua)
    for label in ('RAZÃO SOCIAL', 'NOME EMPRESARIAL', 'NOME:',
                  'DENOMINAÇÃO', 'EMPRESA:'):
        for i, ln in enumerate(lines):
            if label.upper() in ln.upper():
                for j in range(i + 1, min(i + 3, len(lines))):
                    v = lines[j].strip()
                    if v and not _LABELS_PAT.match(v):
                        nome = v; break
            if nome:
                break
        if nome:
            break

    # 2) Fallback: última linha acima do CNPJ que não seja label
    if not nome and cnpj:
        cnpj_idx = next((i for i, l in enumerate(lines) if cnpj[:8] in l), -1)
        if cnpj_idx > 0:
            for j in range(cnpj_idx - 1, max(-1, cnpj_idx - 5), -1):
                v = lines[j].strip()
                # strip leading doc numbers (CPF/CNPJ-like digits)
                v = re.sub(r'^[\d./-]{8,}\s*', '', v)
                if v and not _LABELS_PAT.match(v) and len(v) > 3:
                    nome = v; break

    # ── ENDEREÇO ────────────────────────────────────────────────────
    rua = numero = complemento = bairro = cidade = cep = uf = ''

    # CEP
    cep_m = re.search(r'\d{2}[.]?\d{3}[-.]\d{3}', full)
    cep = cep_m.group(0) if cep_m else ''

    # Logradouro — linha seguinte ao label ENDEREÇO/LOGRADOURO
    rua_raw = _label_value(lines,
        r'ENDERE[ÇC]O|LOGRADOURO')
    if rua_raw:
        parts = re.split(r',\s*', rua_raw, maxsplit=1)
        rua = parts[0].strip()
        if len(parts) > 1:
            rest = parts[1].strip()
            sub = re.split(r'[,\s-]+', rest, maxsplit=1)
            numero = sub[0].strip()
            if len(sub) > 1:
                complemento = sub[1].strip()

    # Número — label NÚMERO/NUMERO
    if not numero:
        n = _label_value(lines, r'N[ÚU]MERO|\bNO\b')
        if n:
            numero = re.split(r'[,\s]', n)[0]

    # Complemento — label COMPLEMENTO/COMPL
    if not complemento:
        complemento = _label_value(lines, r'COMPLEMENTO|COMPL\b')

    # Bairro — label BAIRRO/DISTRITO
    bairro = _label_value(lines, r'BAIRRO|DISTRITO')

    # Cidade — busca linha a linha para evitar match guloso
    for ln in lines:
        m = re.search(
            r'(?:MUNIC[IÍ]PIO|CIDADE)[:\s]+([A-ZÀ-Ú][A-Za-zÀ-ú ]+)',
            ln, re.I)
        if m:
            cidade = m.group(1).strip(); break
    if not cidade:
        for ln in lines:
            m = re.search(
                r'([A-ZÀ-Ú][A-Za-zÀ-ú ]+)\s*/\s*([A-Z]{2})',
                ln)
            if m:
                cidade = m.group(1).strip(); break
    if not cidade:
        for ln in lines:
            m = re.search(
                r'([A-ZÀ-Ú][A-Za-zÀ-ú ]+)\s*[-–]\s*([A-Z]{2})\b',
                ln)
            if m:
                cidade = m.group(1).strip(); break

    # UF
    uf_m = re.search(r'\b(MG|SP|RJ|ES|GO|BA|PR|SC|RS|DF|MT|MS|PA|AM|CE|PE|PB|RN|MA|PI|AL|SE|TO|AC|RO|RR|AP)\b', full)
    uf = uf_m.group(1) if uf_m else 'MG'

    # ── CARGOS ────────────────────────────────────────────────────
    # Sort longest first to prevent shorter substrings blocking longer matches
    cargos_sorted = sorted(CARGOS_SUGESTOES, key=len, reverse=True)
    cargos_encontrados = []
    remaining = full
    for cargo in cargos_sorted:
        if cargo.lower() in remaining.lower():
            idx = remaining.lower().find(cargo.lower())
            cargos_encontrados.append(cargo)
            # consume the matched text to prevent substring duplicates
            remaining = remaining[:idx] + remaining[idx + len(cargo):]
    cargos_encontrados = list(dict.fromkeys(cargos_encontrados))

    return {
        'nome': nome, 'cnpj': cnpj,
        'rua': rua, 'numero': numero, 'complemento': complemento,
        'cep': cep, 'bairro': bairro, 'cidade': cidade, 'uf': uf,
        'cargos': cargos_encontrados,
    }

# ── XML HELPERS ──────────────────────────────────────────────────────
NS = {
    'w' : 'http://schemas.openxmlformats.org/wordprocessingml/2006/main',
    'w14': 'http://schemas.microsoft.com/office/word/2010/wordml',
    'r' : 'http://schemas.openxmlformats.org/officeDocument/2006/relationships',
}
for pfx, uri in NS.items():
    ET.register_namespace(pfx, uri)

def _new_para_id() -> str:
    return '%08X' % random.randrange(0x10000000, 0xFFFFFFFF)

def _uniquify_ids(xml_str: str) -> str:
    """Replace every w14:paraId and w14:textId with a fresh random hex value."""
    def _rep(m):
        return m.group(1) + _new_para_id() + m.group(3)
    xml_str = re.sub(
        r'(w14:paraId=")([0-9A-Fa-f]{8})(")', _rep, xml_str)
    xml_str = re.sub(
        r'(w14:textId=")([0-9A-Fa-f]{8})(")', _rep, xml_str)
    return xml_str

def _tag(ns, local):
    return '{%s}%s' % (NS[ns], local)

def _set_text(para, new_text):
    ns_w = NS['w']
    runs = para.findall(_tag('w','r'))
    if not runs:
        r = ET.SubElement(para, _tag('w','r'))
        t = ET.SubElement(r, _tag('w','t'))
        t.text = new_text
        return
    first = runs[0]
    t = first.find(_tag('w','t'))
    if t is None:
        t = ET.SubElement(first, _tag('w','t'))
    t.text = new_text
    if len(new_text) > 0 and (new_text[0] == ' ' or new_text[-1] == ' '):
        t.set('{http://www.w3.org/XML/1998/namespace}space', 'preserve')
    for r in runs[1:]:
        para.remove(r)

def _find_para_with(body, keyword):
    for p in body.iter(_tag('w','p')):
        txt = ''.join((t.text or '') for t in p.iter(_tag('w','t')))
        if keyword in txt:
            return p
    return None

def _para_text(p):
    return ''.join((t.text or '') for t in p.iter(_tag('w','t')))

# ── DOCX BUILDER (PGR) ───────────────────────────────────────────────
RRENS = '{http://schemas.openxmlformats.org/package/2006/relationships}'
RELS_CONTENT = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1"
    Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument"
    Target="word/document.xml"/>
  <Relationship Id="rId2"
    Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties"
    Target="docProps/core.xml"/>
  <Relationship Id="rId3"
    Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties"
    Target="docProps/app.xml"/>
</Relationships>'''


def _build_docx(nome, cnpj, rua, numero, complemento, cep, bairro,
                cidade, uf, cargos_list) -> bytes:
    cargo_ghe_list = []
    for cargo in cargos_list:
        c = cargo.upper()
        if 'ELETRIC' in c and 'ENCARREGADO' not in c:
            ghe = 'ELETRICA'
        elif 'POLIVALENTE' in c:
            ghe = 'POLIVALENTE'
        elif 'OPERADOR MAQUIN' in c or 'MAQUINAS GERAL' in c:
            ghe = 'MAQUINAS_GERAL'
        elif 'CREMALHEIRA' in c or 'GRUA' in c or 'SINALEIRO' in c:
            ghe = 'MAQUINAS_ESTAC'
        elif any(x in c for x in ('SERVENTE','AJUDANTE','AUXILIAR DE LIMP','AUXILIAR DE SERVICO')):
            ghe = 'OPERACIONAL'
        elif 'AZULEJ' in c or 'LADRILH' in c:
            ghe = 'ACABAMENTO'
        elif 'GESSEIRO' in c or 'REJUNT' in c:
            ghe = 'GESSO_REJUNTE'
        elif any(x in c for x in ('ENGENHEIRO','TOPOGRAFO','ANALISTA')):
            ghe = 'ADMINISTRATIVO'
        elif 'ALMOXARIFE' in c:
            ghe = 'ALMOXARIFADO'
        elif 'ESTAGIARIO' in c or 'ESTAGIÁRIO' in c:
            ghe = 'APOIO_PRODUCAO'
        elif 'ARMADOR' in c:
            ghe = 'ARMACAO'
        elif 'CARPINT' in c:
            ghe = 'CARPINTARIA'
        elif 'BETONEIRA' in c:
            ghe = 'CENTRAL_BETONEIRA'
        elif 'MONTADOR' in c:
            ghe = 'ESTRUTURA_PAREDE'
        elif 'BOMBEIRO' in c or 'ENCANADOR' in c:
            ghe = 'HIDRAULICA'
        elif 'PINTOR' in c:
            ghe = 'PINTURA'
        elif 'PORTEIRO' in c or 'VIGIA' in c:
            ghe = 'PORTARIA'
        elif 'SERRALHEIRO' in c:
            ghe = 'SERRALHERIA'
        elif 'ENCARREGADO' in c or 'MESTRE' in c:
            ghe = 'SUPERVISAO'
        elif 'PEDREIRO' in c:
            ghe = 'ESTRUTURA_ALVENARIA'
        elif 'PAISAGIST' in c:
            ghe = 'PAISAGISMO'
        elif 'SERVICOS GERAIS' in c or 'SERVIÇOS GERAIS' in c:
            ghe = 'SERVICOS_GERAIS'
        elif 'ASSIST' in c and 'ELETRIC' in c:
            ghe = 'ASSIST_TEC_ELETRICA'
        elif 'ASSIST' in c and ('ESPEC' in c or 'TEC' in c):
            ghe = 'ASSIST_TEC_ESPEC'
        elif 'MOTORISTA' in c:
            ghe = 'MAQUINAS_GERAL'
        elif 'MEIO OFICIAL' in c:
            ghe = 'ESTRUTURA_ALVENARIA'
        else:
            ghe = 'OPERACIONAL'
        cargo_ghe_list.append((cargo, ghe))

    tpl_files = {}
    for root_dir, dirs, files in os.walk(MODEL_DIR):
        for fname in files:
            full_path = os.path.join(root_dir, fname)
            rel = os.path.relpath(full_path, MODEL_DIR).replace(os.sep, '/')
            with open(full_path, 'rb') as fh:
                tpl_files[rel] = fh.read()

    if 'word/document.xml' not in tpl_files:
        raise RuntimeError('Template corrompido: word/document.xml ausente')

    doc_xml = tpl_files['word/document.xml'].decode('utf-8')

    doc_xml = doc_xml.replace('{{EMPRESA}}', nome or '')
    doc_xml = doc_xml.replace('{{CNPJ}}',   cnpj or '')
    end_full = ' '.join(filter(None, [rua, numero, complemento]))
    doc_xml = doc_xml.replace('{{ENDERECO}}', end_full)
    doc_xml = doc_xml.replace('{{BAIRRO}}',   bairro or '')
    doc_xml = doc_xml.replace('{{CIDADE}}',   cidade or 'Belo Horizonte')
    doc_xml = doc_xml.replace('{{CEP}}',      cep or '')
    doc_xml = doc_xml.replace('{{UF}}',       uf or 'MG')
    doc_xml = doc_xml.replace('{{MES_ANO}}',  mes_ano())

    tree   = ET.fromstring(doc_xml.encode('utf-8'))
    body   = tree.find('.//' + _tag('w','body'))

    anchor = _find_para_with(body, '{{CARGO_BLOCO}}')
    if anchor is None:
        raise RuntimeError('Marcador {{CARGO_BLOCO}} não encontrado no template')

    anchor_idx = list(body).index(anchor)
    body.remove(anchor)

    GHE_SECTION_TPL = {}
    for fname, content in tpl_files.items():
        if fname.startswith('tpl/'):
            key = fname[4:].replace('.xml','').upper()
            GHE_SECTION_TPL[key] = content.decode('utf-8')

    alertas = []
    insert_pos = anchor_idx

    for cargo, ghe in cargo_ghe_list:
        section_xml = GHE_SECTION_TPL.get(ghe)
        if not section_xml:
            alertas.append(f"{cargo} → GHE '{ghe}' sem template")
            continue

        agentes = GHE_AGENTES.get(ghe, [])
        data_med = '???' if ghe in GHE_SEM_DATA else '20/02/2024'

        section_xml = section_xml.replace('{{CARGO}}', cargo)
        section_xml = section_xml.replace('{{GHE}}',   ghe.replace('_',' '))
        section_xml = section_xml.replace('{{DATA_MEDICAO}}', data_med)
        section_xml = _uniquify_ids(section_xml)

        section_tree = ET.fromstring(section_xml.encode('utf-8'))
        cargo_body   = section_tree.find('.//' + _tag('w','body'))
        elements     = list(cargo_body) if cargo_body is not None else list(section_tree)

        for el in elements:
            body.insert(insert_pos, el)
            insert_pos += 1

    new_xml = ET.tostring(tree, encoding='unicode', xml_declaration=False)
    new_xml = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n' + new_xml
    tpl_files['word/document.xml'] = new_xml.encode('utf-8')

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        if '_rels/.rels' not in tpl_files:
            zf.writestr('_rels/.rels', RELS_CONTENT)
        for rel_path, data in tpl_files.items():
            zf.writestr(rel_path, data)
    buf.seek(0)

    if alertas:
        import base64
        return None, alertas, base64.b64encode(buf.read()).decode()
    return buf.read(), [], None


# ── ROUTES ───────────────────────────────────────────────────────────
@app.route('/')
def index():
    return render_template('index.html',
                           cargos_sugestoes=CARGOS_SUGESTOES)


@app.route('/extrair', methods=['POST'])
def extrair():
    if 'pdf' not in request.files:
        return jsonify({'erro': 'Nenhum arquivo enviado'}), 400
    pdf_bytes = request.files['pdf'].read()
    return jsonify(extrair_dados_pdf(pdf_bytes))


@app.route('/gerar', methods=['POST'])
def gerar():
    d = request.json or {}
    nome   = d.get('nome','').strip()
    cnpj   = d.get('cnpj','')
    rua    = d.get('rua','')
    numero = d.get('numero','')
    complemento = d.get('complemento','')
    cep    = d.get('cep','')
    bairro = d.get('bairro','')
    cidade = d.get('cidade','Belo Horizonte')
    uf     = d.get('uf','MG')
    cargos_list = d.get('cargos', [])

    if not nome:
        return jsonify({'erro': 'Nome da empresa é obrigatório'}), 400
    if not cargos_list:
        return jsonify({'erro': 'Adicione pelo menos um cargo'}), 400

    try:
        result, alertas, b64 = _build_docx(
            nome, cnpj, rua, numero, complemento, cep, bairro, cidade, uf, cargos_list)

        nome_safe = re.sub(r'[/\\:*?"<>|]','_', nome)
        filename  = f"PGR - {nome_safe} - {mes_ano().replace(' / ','_')}.docx"

        if alertas:
            return jsonify({
                'aviso_gerado': True,
                'alertas': alertas,
                'docx_b64': b64,
                'filename': filename,
            })

        return send_file(
            io.BytesIO(result),
            as_attachment=True,
            download_name=filename,
            mimetype='application/vnd.openxmlformats-officedocument.wordprocessingml.document'
        )
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({'erro': f'Erro interno: {str(e)}'}), 500


# ── LAUDO DE CALOR ───────────────────────────────────────────────────
CALOR_MODEL_DIR = os.path.join(BASE_DIR, 'modelo_laudo_calor')

NR15_TABLE = [
    (100,33.7),(102,33.6),(104,33.5),(106,33.4),(108,33.3),(110,33.2),(112,33.1),(115,33.0),
    (117,32.9),(119,32.8),(122,32.7),(124,32.6),(127,32.5),(129,32.4),(132,32.3),(135,32.2),
    (137,32.1),(140,32.0),(143,31.9),(146,31.8),(149,31.7),(152,31.6),(155,31.5),(158,31.4),
    (161,31.3),(165,31.2),(168,31.1),(171,31.0),(175,30.9),(178,30.8),(182,30.7),(186,30.6),
    (189,30.5),(193,30.4),(197,30.3),(201,30.2),(205,30.1),(209,30.0),(214,29.9),(218,29.8),
    (222,29.7),(227,29.6),(231,29.5),(236,29.4),(241,29.3),(246,29.2),(251,29.1),(256,29.0),
    (261,28.9),(266,28.8),(272,28.7),(277,28.6),(283,28.5),(289,28.4),(294,28.3),(300,28.2),
    (306,28.1),(313,28.0),(319,27.9),(325,27.8),(332,27.7),(339,27.6),(346,27.5)
]


def _nr15_limite(m: float) -> float:
    if m <= NR15_TABLE[0][0]:
        return NR15_TABLE[0][1]
    if m >= NR15_TABLE[-1][0]:
        return NR15_TABLE[-1][1]
    for i in range(len(NR15_TABLE) - 1):
        m1, l1 = NR15_TABLE[i]
        m2, l2 = NR15_TABLE[i + 1]
        if m1 <= m <= m2:
            return round(l1 + (l2 - l1) * (m - m1) / (m2 - m1), 1)
    return NR15_TABLE[-1][1]


def _calor_row(tpl_row_xml: str, local: str, tempo: int,
               tbn: float, tbs: float, tg: float,
               ibutg: float, atividade: str, M: int) -> str:
    """Clone a template table row XML and fill measurement values."""
    row = tpl_row_xml
    row = row.replace('{{LOCAL}}',     local)
    row = row.replace('{{TEMPO}}',     str(tempo))
    row = row.replace('{{TBN}}',       f'{tbn:.1f}')
    row = row.replace('{{TBS}}',       f'{tbs:.1f}')
    row = row.replace('{{TG}}',        f'{tg:.1f}')
    row = row.replace('{{IBUTG}}',     f'{ibutg:.2f}')
    row = row.replace('{{ATIVIDADE}}', atividade)
    row = row.replace('{{M}}',         str(M))
    return _uniquify_ids(row)


def gerar_laudo_calor_bytes(empresa: dict, avaliacao: dict, setores: list) -> bytes:
    """
    Read modelo_laudo_calor/template_ocupacional.docx, fill placeholders,
    duplicate the evaluation block per sector, and return the new DOCX bytes.
    """
    tpl_path = os.path.join(CALOR_MODEL_DIR, 'template_ocupacional.docx')
    if not os.path.exists(tpl_path):
        raise RuntimeError('Template Laudo de Calor não encontrado: ' + tpl_path)

    with zipfile.ZipFile(tpl_path, 'r') as zin:
        tpl_files = {name: zin.read(name) for name in zin.namelist()}

    doc_xml = tpl_files['word/document.xml'].decode('utf-8')

    # ── empresa placeholders ────────────────────────────────────────
    doc_xml = doc_xml.replace('{{RAZAO_SOCIAL}}',   empresa.get('razaoSocial',''))
    doc_xml = doc_xml.replace('{{CNPJ}}',           empresa.get('cnpj',''))
    doc_xml = doc_xml.replace('{{ENDERECO}}',        empresa.get('endereco',''))
    doc_xml = doc_xml.replace('{{CEP}}',            empresa.get('cep',''))
    doc_xml = doc_xml.replace('{{CIDADE}}',         empresa.get('cidade',''))
    doc_xml = doc_xml.replace('{{BAIRRO}}',         empresa.get('bairro',''))
    doc_xml = doc_xml.replace('{{UF}}',             empresa.get('uf',''))
    doc_xml = doc_xml.replace('{{CNAE}}',           empresa.get('cnae',''))
    doc_xml = doc_xml.replace('{{CNAE_DESC}}',      empresa.get('descricaoCnae',''))
    doc_xml = doc_xml.replace('{{GRAU_RISCO}}',     str(empresa.get('grauRisco','')))
    doc_xml = doc_xml.replace('{{CONTATO}}',        empresa.get('contato',''))
    doc_xml = doc_xml.replace('{{TELEFONE}}',       empresa.get('telefone',''))
    doc_xml = doc_xml.replace('{{EMAIL}}',          empresa.get('email',''))

    # ── avaliacao placeholders ──────────────────────────────────────
    doc_xml = doc_xml.replace('{{DATA_AVALIACAO}}', avaliacao.get('dataAvaliacao',''))
    doc_xml = doc_xml.replace('{{CIDADE_CARTA}}',   avaliacao.get('cidadeCarta',''))
    doc_xml = doc_xml.replace('{{EQUIPAMENTO}}',    avaliacao.get('equipamento',''))
    doc_xml = doc_xml.replace('{{CERT_NO}}',        avaliacao.get('certNo',''))
    doc_xml = doc_xml.replace('{{DATA_CALIB}}',     avaliacao.get('dataCalib',''))

    tree = ET.fromstring(doc_xml.encode('utf-8'))
    body = tree.find('.//' + _tag('w', 'body'))

    # ── find the sector block anchor and table row template ─────────
    bloco_start = _find_para_with(body, '{{SETOR_BLOCO_START}}')
    bloco_end   = _find_para_with(body, '{{SETOR_BLOCO_END}}')
    if bloco_start is None or bloco_end is None:
        raise RuntimeError('Marcadores {{SETOR_BLOCO_START}} / {{SETOR_BLOCO_END}} não encontrados')

    children = list(body)
    i_start  = children.index(bloco_start)
    i_end    = children.index(bloco_end)

    # The elements between markers form the template block for one sector
    bloco_elements = children[i_start + 1 : i_end]

    # Find the template table row ({{LOCAL}} marker)
    tpl_row_xml = None
    tpl_tbl     = None
    tpl_tbl_row = None
    for el in bloco_elements:
        ns_w_tbl = _tag('w', 'tbl')
        if el.tag == ns_w_tbl:
            for tr in el.findall(_tag('w', 'tr')):
                row_txt = ''.join(t.text or '' for t in tr.iter(_tag('w', 't')))
                if '{{LOCAL}}' in row_txt:
                    tpl_row_xml = ET.tostring(tr, encoding='unicode')
                    tpl_tbl     = el
                    tpl_tbl_row = tr
                    break
        if tpl_row_xml:
            break

    # ── build new body content ──────────────────────────────────────
    # Remove marker paragraphs and the original template block
    for el in [bloco_start, bloco_end] + bloco_elements:
        body.remove(el)

    insert_pos = i_start
    page_break_xml = (
        '<w:p xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        '<w:r><w:br w:type="page"/></w:r></w:p>'
    )

    for s_idx, setor in enumerate(setores):
        pontos = setor.get('pontos', [])
        total_t = sum(p['tempo'] for p in pontos) or 1
        ibutg_medio = sum((0.7*p['tbn'] + 0.3*p['tg']) * p['tempo'] for p in pontos) / total_t
        m_medio     = sum(p['M'] * p['tempo'] for p in pontos) / total_t
        limite      = _nr15_limite(m_medio)
        status_txt  = 'ACEITÁVEL' if ibutg_medio <= limite else 'ATENÇÃO'

        import copy
        setor_elements = copy.deepcopy(bloco_elements)

        # Fill sector-level placeholders in text
        for el in setor_elements:
            for t_el in el.iter(_tag('w', 't')):
                if t_el.text:
                    t_el.text = (t_el.text
                        .replace('{{SETOR_NOME}}',    setor.get('nome',''))
                        .replace('{{HORARIO}}',       setor.get('horario',''))
                        .replace('{{VESTIMENTA}}',    setor.get('vestimenta',''))
                        .replace('{{IBUTG_MEDIO}}',   f'{ibutg_medio:.1f}')
                        .replace('{{LIMITE_NR15}}',   f'{limite:.1f}')
                        .replace('{{STATUS}}',        status_txt)
                    )

        # Fill measurement table rows
        if tpl_row_xml and tpl_tbl is not None:
            new_tbl = None
            for el in setor_elements:
                if el.tag == _tag('w', 'tbl'):
                    tbl_txt = ''.join(t.text or '' for t in el.iter(_tag('w', 't')))
                    if '{{LOCAL}}' in tbl_txt:
                        new_tbl = el; break
            if new_tbl is not None:
                # Remove the template row
                for tr in list(new_tbl):
                    tr_txt = ''.join(t.text or '' for t in tr.iter(_tag('w', 't')))
                    if '{{LOCAL}}' in tr_txt:
                        new_tbl.remove(tr); break
                # Append one row per measurement point
                for p in pontos:
                    ibutg = 0.7 * p['tbn'] + 0.3 * p['tg']
                    row_xml = _calor_row(
                        tpl_row_xml,
                        p.get('local',''), p.get('tempo',0),
                        p['tbn'], p['tbs'], p['tg'], ibutg,
                        p.get('atividade',''), p.get('M',0)
                    )
                    new_tbl.append(ET.fromstring(row_xml.encode('utf-8')))

        # Uniquify IDs in the whole block
        block_xml = ''.join(ET.tostring(el, encoding='unicode') for el in setor_elements)
        block_xml = _uniquify_ids(block_xml)

        # Wrap in a dummy doc to parse back
        wrapper = ET.fromstring(
            f'<root xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            f'{block_xml}</root>'
        )
        for el in list(wrapper):
            body.insert(insert_pos, el)
            insert_pos += 1

        # Page break between sectors
        if s_idx < len(setores) - 1:
            pb = ET.fromstring(page_break_xml.encode('utf-8'))
            body.insert(insert_pos, pb)
            insert_pos += 1

    # ── serialise ───────────────────────────────────────────────────
    new_xml = ET.tostring(tree, encoding='unicode', xml_declaration=False)
    new_xml = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n' + new_xml
    tpl_files['word/document.xml'] = new_xml.encode('utf-8')

    if '_rels/.rels' not in tpl_files:
        tpl_files['_rels/.rels'] = RELS_CONTENT.encode('utf-8')

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zout:
        for rel_path, data in tpl_files.items():
            zout.writestr(rel_path, data)
    buf.seek(0)
    return buf.read()


@app.route('/gerar_calor', methods=['POST'])
def gerar_calor():
    d       = request.json or {}
    empresa  = d.get('empresa', {})
    avaliacao = d.get('avaliacao', {})
    setores  = d.get('setores', [])

    if not empresa.get('razaoSocial','').strip():
        return jsonify({'erro': 'Razão Social é obrigatória'}), 400
    if not setores:
        return jsonify({'erro': 'Informe pelo menos um setor'}), 400

    try:
        docx_bytes = gerar_laudo_calor_bytes(empresa, avaliacao, setores)
        nome = empresa.get('razaoSocial','Empresa')
        nome_safe = re.sub(r'[/\\:*?"<>|]','_', nome)
        filename = f"Laudo de Calor - {nome_safe} - {mes_ano().replace(' / ','_')}.docx"
        return send_file(
            io.BytesIO(docx_bytes),
            as_attachment=True,
            download_name=filename,
            mimetype='application/vnd.openxmlformats-officedocument.wordprocessingml.document'
        )
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({'erro': f'Erro interno: {str(e)}'}), 500


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)