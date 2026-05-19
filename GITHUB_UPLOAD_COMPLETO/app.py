import os, re, shutil, zipfile, io, tempfile, base64
from datetime import datetime
from flask import Flask, request, jsonify, send_file, render_template
import xml.etree.ElementTree as ET

try:
    from pdfminer.high_level import extract_text as pdf_extract
    PDF_OK = True
except ImportError:
    PDF_OK = False

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024  # 50MB max (fotos)

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
    # Ruído 84,47 Moderado | Poeira Madeira 0,40 | PNOS 0,23 | Posturas | Perfurocortantes | Queda — Parque Canoas 2025
    "CARPINTARIA":[
        ('ruido','84,47 dB(A)','Moderado',True,False),
        ('quant','Poeira de Madeira','Químico','1','0,5','0,40 mg/m³','Risco Baixo',False),
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
    # Ruído 88,48 Alto | Vibração AREN 0,86/VDVR 18,3 Moderado | PNOS 0,78 | Postura Sentada — Parque Canoas 2025
    "MAQUINAS_PEQUENO_PORTE":[
        ('ruido','88,48 dB(A)','Alto',True,True),
        ('quant','Poeira não Fibrogênica (PNOS-Respirável)','Químico','2.640','1.320','0,78 mg/m³','Risco Baixo',False),
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
    # Paisagismo — Ruído 64,79 Baixo | Posturas | Queda — Parque Canoas 2025 (sem PNOS medido)
    "PAISAGISMO":[
        ('ruido','64,79 dB(A)','Baixo',False,False),
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
    # Assistência Técnica Elétrica — Ruído 84,52 | PNOS 0,05 | Posturas | Eletricidade — Parque Canoas 2025
    "ASSIST_TEC_ELETRICA":[
        ('ruido','84,52 dB(A)','Moderado',True,False),
        ('quant','Poeira não Fibrogênica (PNOS-Respirável)','Químico','2.640','1.320','0,05 mg/m³','Risco Baixo',False),
        ('ergon','Posturas Incomodas','Risco Baixo'),
        ('acid','Eletricidade','Risco Baixo'),
    ],
    # Assistência Técnica Especializada — Ruído 84,52 | PNOS 0,05 | Posturas — Parque Canoas 2025
    "ASSIST_TEC_ESPEC":[
        ('ruido','84,52 dB(A)','Moderado',True,False),
        ('quant','Poeira não Fibrogênica (PNOS-Respirável)','Químico','2.640','1.320','0,05 mg/m³','Risco Baixo',False),
        ('ergon','Posturas Incomodas','Risco Baixo'),
    ],
}

CARGOS_SUGESTOES = sorted([
    "Ajudante Armador","Ajudante Eletricista","Ajudante Geral","Ajudante Pratico",
    "Almoxarife","Almoxarife Pleno","Analista Administrativo","Apontador",
    "Armador","Armador Pleno","Assistente Administrativo",
    "Assistente Tecnico Edificacoes","Assistente Tecnico Seguranca Trabalho",
    "Auxiliar Administrativo","Auxiliar Almoxarife","Auxiliar Armador",
    "Auxiliar Bombeiro Hidraulico","Auxiliar Carpinteiro",
    "Auxiliar de Limpeza","Auxiliar de Servicos Gerais",
    "Auxiliar Eletricista","Auxiliar Encanador","Auxiliar Engenharia",
    "Auxiliar Ferreiro","Auxiliar Limpeza","Auxiliar Montador",
    "Auxiliar Montador Formas Metalicas","Auxiliar Obras","Auxiliar Pedreiro",
    "Auxiliar Pintor","Auxiliar Producao","Auxiliar Seguranca Trabalho",
    "Auxiliar Tecnico Seguranca Trabalho","Azulejista",
    "Bombeiro Hidraulico","Bombeiro Pleno","Cabo Turma",
    "Carpinteiro","Carpinteiro Forma","Carpinteiro Pleno","Carpinteiro Polivalente",
    "Carpinteiro Serrador","Contra Mestre",
    "Eletricista","Eletricista Instalador Predial","Eletricista Pleno",
    "Eletricista Pos Entrega","Encanador",
    "Encarregado","Encarregado Acabamento","Encarregado Almoxarife",
    "Encarregado Armador","Encarregado Carpintaria","Encarregado de Obras",
    "Encarregado Eletrica","Encarregado Forma","Encarregado Geral",
    "Encarregado Geral Instalacoes","Encarregado Geral Obras",
    "Encarregado Hidraulica","Encarregado Instalacoes","Encarregado Obras Forma",
    "Encarregado Obras Instalacoes","Encarregado Turma",
    "Engenheiro","Engenheiro Junior","Engenheiro Pleno","Engenheiro Senior",
    "Estagiario","Faxineiro","Ferramenteiro","Ferreiro",
    "Gesseiro","Gesseiro Pleno","Guariteiro","Jardineiro","Ladrilheiro","Marceneiro",
    "Meio Oficial","Meio Oficial Armador","Meio Oficial Azulejista",
    "Meio Oficial Bombeiro","Meio Oficial Carpinteiro","Meio Oficial Eletricista",
    "Meio Oficial Encanador","Meio Oficial Ferreiro","Meio Oficial Gesseiro",
    "Meio Oficial Montador","Meio Oficial Montador Formas Metalicas",
    "Meio Oficial Pedreiro","Meio Oficial Pintor","Meio Oficial Pos Entrega",
    "Mestre Geral Obras","Mestre Obras",
    "Montador","Montador Andaimes","Montador de Forma Metalica",
    "Montador Esquadrias","Montador Formas Metalicas Pleno",
    "Oficial","Oficial Pleno","Oficial Polivalente",
    "Operador Betoneira","Operador Cremalheira","Operador Elevador Carga",
    "Operador Equipamentos","Operador Grua","Operador Guincho",
    "Operador Maquinas Geral","Operador Maquinas Leves","Operador Maquinas Pesadas",
    "Pedreiro","Pedreiro Acabamento","Pedreiro I","Pedreiro Pleno",
    "Pintor","Pintor Pleno","Porteiro","Profissional Lider",
    "Profissional Pos Entrega","Profissional Pos Entrega Polivalente",
    "Rejuntador","Serralheiro","Servente","Sinaleiro","Soldador",
    "Supervisor Instalacoes","Tecnico Ambiental","Tecnico Edificacoes",
    "Tecnico Instalacoes","Tecnico Seguranca Trabalho I","Tecnico Seguranca Trabalho II",
    "Topografo","Vigia","Vigia Noturno",
], key=str.lower)

def get_ghe(cargo):
    """Retorna o GHE do cargo (Mata das Borboletas) ou None se não reconhecido."""
    c = cargo.upper()
    # Acabamento (azulejista, ladrilheiro, pedreiro acabamento)
    if any(x in c for x in ["AZULEJ","LADRILH","PEDREIRO ACABAMENTO"]): return "ACABAMENTO"
    # Gesso/Rejunte
    if any(x in c for x in ["GESSEIRO","REJUNT"]): return "GESSO_REJUNTE"
    # Administrativo
    if any(x in c for x in ["ADMIN","ANALISTA","ENGENHEIRO","TOPOGRAFO","APONTADOR","APRENDIZ"]): return "ADMINISTRATIVO"
    # Almoxarifado (inclui ferramenteiro — mas Encarregado Almoxarife vai pra Supervisão)
    if "ENCARREGADO ALMOXARIFE" in c: return "SUPERVISAO"
    if any(x in c for x in ["ALMOXARIFE","FERRAMENTEIRO"]): return "ALMOXARIFADO"
    # Apoio Produção (técnicos, estagiários, segurança do trabalho)
    if any(x in c for x in ["TECNICO EDIF","TECNICO SEGUR","TECNICO AMBI","TECNICO INSTAL",
                              "ESTAGIARIO","ASSISTENTE TECNICO","AUXILIAR SEGUR",
                              "AUXILIAR TECNICO SEGUR"]): return "APOIO_PRODUCAO"
    # Armação (inclui ferreiro, ajudante armador)
    if any(x in c for x in ["ARMADOR","AUXILIAR ARMAD","AJUDANTE ARMAD","FERREIRO","ARMADOR PLENO"]): return "ARMACAO"
    # Carpintaria (inclui marceneiro, carpinteiro polivalente)
    if any(x in c for x in ["CARPINT","SERRAD","MARCENEIRO"]): return "CARPINTARIA"
    # Central Betoneira
    if "BETONEIRA" in c: return "CENTRAL_BETONEIRA"
    # Pós-entrega elétrica especificamente
    if "ELETRICISTA POS ENTREGA" in c: return "ASSIST_TEC_ELETRICA"
    # Profissional pós-entrega (polivalente ou especializado)
    if "POS ENTREGA" in c: return "ASSIST_TEC_ESPEC"
    # Elétrica (eletricista — não encarregado, não pós-entrega)
    if "ELETRIC" in c and "ENCARREGADO" not in c: return "ELETRICA"
    # Hidráulica (inclui encanador, bombeiro, auxiliar)
    if any(x in c for x in ["BOMBEIRO","ENCANADOR","HIDRAUL","AUXILIAR ENCANADOR",
                              "AUXILIAR BOMBEIRO","BOMBEIRO PLENO"]): return "HIDRAULICA"
    # Máquinas leves (pequeno porte) — deve vir ANTES de MAQUINAS_GERAL
    if any(x in c for x in ["MAQUINAS LEVE","MAQUINAS LEVES","MAQUINAS PEQUENO"]): return "MAQUINAS_PEQUENO_PORTE"
    # Máquinas grandes/pesadas
    if any(x in c for x in ["OPERADOR MAQUIN","MAQUINAS GERAL","MAQUINAS PESAD",
                              "OPERADOR EQUIP"]): return "MAQUINAS_GERAL"
    # Máquinas estacionárias (cremalheira, grua, guincho, sinaleiro, elevador)
    if any(x in c for x in ["CREMALHEIRA","GRUA","SINALEIRO","GUINCHO",
                              "ELEVADOR CARGA","ELEVADOR FORM"]): return "MAQUINAS_ESTAC"
    # Paisagismo
    if "JARDINEIRO" in c: return "PAISAGISMO"
    # Polivalente
    if "POLIVALENTE" in c: return "POLIVALENTE"
    # Pintura (inclui auxiliar pintor, pintor pleno)
    if any(x in c for x in ["PINTOR","AUXILIAR PINT"]): return "PINTURA"
    # Portaria (inclui guariteiro)
    if any(x in c for x in ["PORTEIRO","VIGIA","GUARITEIRO"]): return "PORTARIA"
    # Serralheria (inclui soldador)
    if any(x in c for x in ["SERRALHEIRO","SOLDADOR"]): return "SERRALHERIA"
    # Serviços Gerais (limpeza, faxina)
    if any(x in c for x in ["FAXINEIRO","AUXILIAR LIMPEZA","AUXILIAR SERVICOS GERAIS"]): return "SERVICOS_GERAIS"
    # Supervisão (encarregados, mestres, cabo turma, supervisor)
    if any(x in c for x in ["ENCARREGADO","MESTRE","PROFISSIONAL LIDER",
                              "SUPERVISOR","CABO TURMA"]): return "SUPERVISAO"
    # Estrutura Parede (montadores de formas metálicas)
    if any(x in c for x in ["MONTADOR FORMA","MONTADOR FORMAS","AUXILIAR MONTADOR",
                              "MEIO OFICIAL MONTADOR"]): return "ESTRUTURA_PAREDE"
    if c.strip() in ["MONTADOR", "MONTADOR DE FORMA METALICA"]: return "ESTRUTURA_PAREDE"
    # Estrutura Alvenaria (pedreiro, meio oficial, montador andaimes/esquadrias, oficial)
    if any(x in c for x in ["PEDREIRO","MEIO OFICIAL PEDREIRO","MEIO OFICIAL",
                              "AUXILIAR PEDREIRO","MONTADOR ANDAIME","MONTADOR ESQUADRIA",
                              "OFICIAL PLENO","PEDREIRO PLENO","PEDREIRO I"]): return "ESTRUTURA_ALVENARIA"
    if c.strip() == "OFICIAL": return "ESTRUTURA_ALVENARIA"
    # Operacional (servente, ajudante, auxiliar obras/produção)
    if any(x in c for x in ["SERVENTE","AJUDANTE GERAL","AJUDANTE PRATICO",
                              "AUXILIAR DE LIMPEZA","AUXILIAR DE SERVICO",
                              "AUXILIAR OBRAS","AUXILIAR PRODUCAO"]): return "OPERACIONAL"
    if "AJUDANTE" in c: return "OPERACIONAL"
    # Cargo não reconhecido
    return None

DESCRICOES = {
    "PEDREIRO":"Executar serviços de acabamento e reparos de blocos e superfícies concretadas, assentamento de tijolos, reboco e arremates de estruturas construídas, preparação de argamassa de diversos tipos, colocação de telhas, lajes pré-moldadas, pisos, azulejos, ferragens, manilhas, bancadas e peças sanitárias, de acordo com orientações e solicitações recebidas do superior imediato.",
    "MEIO OFICIAL PEDREIRO":"Auxiliar os oficiais na realização de obras de edificações de paredes, pisos, construções em alvenarias, concretagem, cimentados, revestimentos entre outros trabalhos da construção civil.",
    "MEIO OFICIAL":"Auxiliar os oficiais na realização de obras de edificações de paredes, pisos, construções em alvenarias, concretagem, cimentados e revestimentos.",
    "SERVENTE":"Realizar limpeza e organização do canteiro de obras, transporte de materiais e equipamentos, auxiliar as demais frentes de trabalho conforme solicitação do superior imediato.",
    "AUXILIAR DE LIMPEZA":"Realizar a limpeza e organização das instalações do canteiro de obras e áreas sociais, garantindo boas condições de higiene e segurança.",
    "AUXILIAR DE SERVICOS GERAIS":"Realizar atividades diversas de apoio ao canteiro de obras, incluindo limpeza, organização e suporte às atividades dos oficiais.",
    "AJUDANTE PRATICO":"Auxiliar nas atividades do canteiro de obras, transporte de materiais, limpeza e suporte às atividades dos oficiais.",
    "REJUNTADOR":"Realizar serviços de rejuntamento em pisos, paredes, azulejos e outros elementos de construção civil.",
    "PINTOR":"Realizar a pintura de superfícies internas e externas de edificações, preparar superfícies, aplicar tintas, vernizes e outros revestimentos conforme especificações técnicas.",
    "MEIO OFICIAL PINTOR":"Auxiliar os pintores na preparação e pintura de superfícies, mistura de materiais e aplicação de revestimentos.",
    "ARMADOR":"Realizar corte, dobragem e montagem de armações de aço para estruturas de concreto armado, seguindo projetos e especificações técnicas.",
    "AUXILIAR ARMADOR":"Auxiliar os armadores no corte, dobragem e montagem de armações de aço para estruturas de concreto armado.",
    "CARPINTEIRO":"Executar serviços de carpintaria em obras de construção civil, incluindo montagem de formas para concreto, andaimes e outras estruturas de madeira.",
    "MEIO OFICIAL CARPINTEIRO":"Auxiliar os carpinteiros na montagem de formas para concreto e outras estruturas de madeira.",
    "ELETRICISTA":"Realizar a instalação, manutenção e reparo de sistemas elétricos em obras de construção civil, seguindo as normas técnicas de segurança vigentes.",
    "MEIO OFICIAL ELETRICISTA":"Auxiliar os eletricistas nas atividades de instalação, manutenção e reparo de sistemas elétricos.",
    "GESSEIRO":"Executar serviços de revestimento com gesso, incluindo aplicação de reboco, massa corrida e outros acabamentos em paredes e tetos.",
    "AZULEJISTA":"Assentar azulejos, cerâmicas, pastilhas e outros revestimentos em pisos e paredes, seguindo projeto e especificações técnicas.",
    "BOMBEIRO HIDRAULICO":"Realizar instalação, manutenção e reparo de sistemas hidráulicos e de esgoto em obras de construção civil.",
    "MEIO OFICIAL BOMBEIRO":"Auxiliar os bombeiros hidráulicos nas instalações e manutenções de sistemas hidráulicos e sanitários.",
    "MONTADOR":"Montar e desmontar formas metálicas e estruturas para concretagem de paredes e lajes.",
    "MONTADOR DE FORMA METALICA":"Montar e desmontar formas metálicas para concretagem de paredes e lajes em construção civil.",
    "ENCARREGADO":"Supervisionar e coordenar as equipes de trabalho no canteiro de obras, garantindo prazos, qualidade e segurança.",
    "ENCARREGADO DE OBRAS":"Supervisionar e coordenar as equipes de trabalho no canteiro de obras, garantindo prazos, qualidade e segurança.",
    "ENCARREGADO ELETRICA":"Supervisionar e coordenar a equipe de elétrica no canteiro de obras, garantindo qualidade e segurança nas instalações.",
    "PORTEIRO":"Controlar o acesso de pessoas e veículos ao canteiro de obras, garantindo a segurança das instalações.",
    "VIGIA":"Realizar a vigilância e proteção do canteiro de obras durante os períodos determinados.",
    "TOPOGRAFO":"Realizar levantamentos topográficos, controle de nível e demarcação de pontos para execução de obras.",
    "AUXILIAR ADMINISTRATIVO":"Executar atividades administrativas de apoio às operações do canteiro de obras.",
    "ENGENHEIRO":"Planejar, coordenar e supervisionar as atividades de engenharia no canteiro de obras, garantindo qualidade técnica e segurança.",
}

def get_desc(cargo):
    for k, v in DESCRICOES.items():
        if cargo.upper() == k:
            return v
    return f"Executar as atividades inerentes ao cargo de {cargo} no canteiro de obras, conforme orientações do superior imediato e normas de segurança."

# ── Templates ────────────────────────────────────────────────────────
# ATENÇÃO: valores com espaço trailing replicam o formato exato do XML
ORIG = {
    "ruido_db":   "81,57 dB(A) ",          # tem espaço trailing no XML
    "pnos_agent": "Poeira não Fibrogênica (PNOS-Respirável)",
    "pnos_medi":  "0,10 mg/m³ ",           # tem espaço trailing no XML
    "pnos_lt":    "2.640",
    "pnos_na":    "1.320",
    "ergon1":     "Posturas",
    "ergon2":     "Incomodas",
    "acid1":      "Queda",
    "acid2":      "de",
    "acid3":      "Objetos",
    "cargo_desc": "Executar serviços de acabamento e reparos de blocos e superfícies concretadas, assentamento de tijolos, reboco e arremates de estruturas construídas, preparação de argamassa de diversos tipos, colocação de telhas, lajes pré-moldadas, pisos, azulejos, ferragens, manilhas, bancadas e peças sanitárias, de acordo com orientações e solicitações recebidas do superior imediato.",
    "ruido_fund": "Acima do nível de ação, conforme NR-09 da Portaria 3214/78 do M.T.E.",
    "ruido_key":  "Foi identificada a exposição ao agente ruído, sendo necessário.",
    "ruido_rec":  "Recomendamos o uso de protetor auditivo e a realização dos\u00a0exames médicos, audiometria, face o que estabelece a NR-07 (PCMSO) da Portaria 3214 do M.T.E., diante do nível de ruído ter ultrapassado o\u00a0limite de tolerância (LT)\u00a0de\u00a085 dB(A).",
    "pnos_key":   "Foi identificado a exposição às poeiras (PNOS) no canteiro de obra, sendo necessário:",
    "pnos_fund":  "Concentração abaixo do Nível de Ação. ", # tem espaço trailing no XML
    "data":       "Março / 2026",
}

def load_tpl(name):
    with open(os.path.join(TPL_DIR, name + '.xml'), 'r', encoding='utf-8') as f:
        return f.read()

def xs(s): return s.replace('&','&amp;').replace('<','&lt;').replace('>','&gt;')
def spacer(): return '\n    <w:p><w:pPr><w:spacing w:after="0" w:line="240" w:lineRule="auto"/></w:pPr></w:p>\n'

def _replace_run_text(xml, old_text, new_text):
    """Substitui texto dentro de um run XML, respeitando espaços trailing."""
    return xml.replace(f'>{old_text}<', f'>{new_text}<')

def adapt_ruido(db_val, nivel, acima_acao, acima_lt):
    t = load_tpl('ruido')
    # Substituir o valor de dB — o novo valor também precisa do espaço trailing
    t = _replace_run_text(t, ORIG["ruido_db"], db_val + ' ')
    if nivel == 'Baixo': t = t.replace('>Moderado<', '>Baixo<', 1)
    elif nivel == 'Alto': t = t.replace('>Moderado<', '>Alto<', 1)
    if acima_lt:
        t = t.replace(ORIG["ruido_fund"],
                      'Acima do Limite de Tolerância (LT) de 85 dB(A), conforme NR-15 Anexo 1 da Portaria 3214/78 do M.T.E.')
        t = t.replace(ORIG["ruido_rec"],
                      'Uso obrigatório de protetor auditivo (CA válido). Audiometria conforme NR-07 (PCMSO). Medidas de controle de engenharia na fonte geradora.')
    elif not acima_acao:
        t = t.replace(ORIG["ruido_fund"],
                      'Abaixo do nível de ação, conforme NR-09 da Portaria 3214/78 do M.T.E.')
        t = t.replace(ORIG["ruido_key"],
                      'O valor de ruído avaliado está abaixo do nível de ação. Manter monitoramento periódico.')
        t = t.replace(ORIG["ruido_rec"],
                      'Manter boas práticas de higiene ocupacional. Monitorar periodicamente os níveis de ruído.')
    return t

def adapt_quant(nome_ag, grupo, lt_val, na_val, medicao, nivel_risco, acima_na):
    t = load_tpl('pnos')
    t = t.replace(ORIG["pnos_agent"], nome_ag)
    t = _replace_run_text(t, ORIG["pnos_lt"], lt_val)
    t = _replace_run_text(t, ORIG["pnos_na"], na_val)
    # Medição também tem espaço trailing
    t = _replace_run_text(t, ORIG["pnos_medi"], medicao + ' ')
    n = nivel_risco.replace('Risco ', '')
    if n == 'Baixo': t = t.replace('>Moderado<', '>Baixo<', 1)
    elif n == 'Alto': t = t.replace('>Moderado<', '>Alto<', 1)
    if 'Madeira' in nome_ag:
        t = t.replace(ORIG["pnos_key"],
                      'Foi identificada a exposição à poeira de madeira no canteiro de obra, sendo necessário:')
    elif 'Total' in nome_ag:
        t = t.replace(ORIG["pnos_key"],
                      'Foi identificada a exposição às poeiras (PNOS-Total) no canteiro de obra, sendo necessário:')
    if acima_na:
        t = t.replace(ORIG["pnos_fund"], 'Concentração acima do Nível de Ação. Adotar medidas de controle. ')
    return t

def adapt_ergon(nome_ag, nivel_risco):
    t = load_tpl('ergonomico')
    n = nivel_risco.replace('Risco ', '')
    if nome_ag != 'Posturas Incomodas':
        t = _replace_run_text(t, ORIG["ergon1"], xs(nome_ag))
        t = _replace_run_text(t, ORIG["ergon2"], '')
    if n == 'Baixo': t = t.replace('>Moderado<', '>Baixo<', 1)
    elif n == 'Alto': t = t.replace('>Moderado<', '>Alto<', 1)
    return t

def adapt_acid(nome_ag, nivel_risco):
    t = load_tpl('acidente')
    n = nivel_risco.replace('Risco ', '')
    if nome_ag == 'Objetos Perfurocortantes':
        t = _replace_run_text(t, ORIG["acid1"], 'Objetos Perfurocortantes')
        t = _replace_run_text(t, ORIG["acid2"], '')
        t = _replace_run_text(t, ORIG["acid3"], '')
    elif nome_ag == 'Trabalho em Altura - NR35':
        t = _replace_run_text(t, ORIG["acid1"], 'Trabalho em Altura - NR35')
        t = _replace_run_text(t, ORIG["acid2"], '')
        t = _replace_run_text(t, ORIG["acid3"], '')
    elif nome_ag == 'Eletricidade':
        t = _replace_run_text(t, ORIG["acid1"], 'Eletricidade')
        t = _replace_run_text(t, ORIG["acid2"], '')
        t = _replace_run_text(t, ORIG["acid3"], '')
    if n == 'Baixo': t = t.replace('>Moderado<', '>Baixo<', 1)
    elif n == 'Alto': t = t.replace('>Moderado<', '>Alto<', 1)
    return t

def build_cargo_section(cargo, cidade, uf):
    """
    Constrói a seção de um cargo com seus riscos.
    - GHE_SEM_DATA: datas aparecem como ??? (medição sem data confirmada)
    - GHE reconhecido com data: usa 20/02/2024 (Oregon)
    """
    ghe = get_ghe(cargo)
    ghe_desconhecido = ghe is None
    if ghe is None:
        ghe = 'OPERACIONAL'  # fallback — endpoint já bloqueou antes de chegar aqui

    # Verificar se este GHE tem data de medição conhecida
    sem_data = ghe in GHE_SEM_DATA

    agentes = GHE_AGENTES.get(ghe, GHE_AGENTES['OPERACIONAL'])

    sc = load_tpl('setor_cargo')
    sc = sc.replace('Setor: Ribeirão das Neves - MG</w:t>', f'Setor: {cidade} - {uf}</w:t>')
    sc = sc.replace('RIBEIRÃO DAS NEVES - MG</w:t>', f'{cidade.upper()} - {uf.upper()}</w:t>')
    sc = sc.replace('>Cargo: Pedreiro<', f'>Cargo: {xs(cargo)}<')
    sc = sc.replace('>Cargo: Pedreiro</w:t>', f'>Cargo: {xs(cargo)}</w:t>')
    sc = sc.replace(ORIG["cargo_desc"], get_desc(cargo))

    titulo_texto = f'Especificação dos Riscos - Cargo: {xs(cargo)} '
    titulo = load_tpl('titulo_riscos').replace(
        'Especificação dos Riscos - Cargo: Pedreiro ', titulo_texto)

    risk = ''
    for ag in agentes:
        if ag[0] == 'ruido':
            _, db, nivel, aa, alt = ag
            tbl = adapt_ruido(db, nivel, aa, alt)
            if sem_data:
                # Data no XML está como <w:t>20/02/2024</w:t>
                tbl = tbl.replace('>20/02/2024<', '>??/??/???? - DATA PENDENTE<')
            risk += tbl + spacer()
        elif ag[0] == 'quant':
            _, nome_ag, grupo, lt, na, med, nr, acima = ag
            tbl = adapt_quant(nome_ag, grupo, lt, na, med, nr, acima)
            if sem_data:
                tbl = tbl.replace('>20/02/2024<', '>??/??/????<')
            risk += tbl + spacer()
        elif ag[0] == 'ergon':
            _, nome_ag, nr = ag
            risk += adapt_ergon(nome_ag, nr) + spacer()
        elif ag[0] == 'acid':
            _, nome_ag, nr = ag
            risk += adapt_acid(nome_ag, nr) + spacer()

    return sc + titulo + risk

def gerar_docx_bytes(nome, cnpj, rua, numero, complemento, cep, bairro, cidade, uf, cargos):
    data_atual = mes_ano()
    partes = [p for p in [rua, (f'Nº {numero}' if numero else ''), complemento] if p.strip()]
    endereco = ' '.join(partes) or 'A definir'
    part1 = load_tpl('part1')
    part3 = load_tpl('part3')
    def subst(t):
        t = t.replace('63.370.132 MARCIO DA SILVA', nome)
        t = t.replace('63.370.132/0001-25', cnpj)
        t = t.replace('Al Das Sibipurunas Nº 1137', endereco)
        t = t.replace('33.830-360', cep or 'A definir')
        t = t.replace('>Ribeirão das Neves<', f'>{cidade}<')
        t = t.replace('Vale das Acácias', bairro or 'A definir')
        t = t.replace('RIBEIRÃO DAS NEVES - MG</w:t>', f'{cidade.upper()} - {uf.upper()}</w:t>')
        t = t.replace('Setor: Ribeirão das Neves - MG</w:t>', f'Setor: {cidade} - {uf}</w:t>')
        t = t.replace('>Pedreiro<', f'>{cargos[0]}<')
        t = t.replace(f'>{ORIG["data"]}<', f'>{data_atual}<')
        return t
    p1 = subst(part1)
    p3 = subst(part3)
    # Índice dinâmico
    tpl_idx = load_tpl('indice_cargo')
    idx_novo = ''
    for i, cargo in enumerate(cargos):
        para = tpl_idx.replace('>Cargo: Pedreiro<', f'>Cargo: {xs(cargo)}<')
        new_id = '%08X' % (0x762D6EE3 + i + 1)
        para = para.replace('762D6EE3', new_id)
        idx_novo += para + '\n'
    if tpl_idx in p1:
        p1 = p1.replace(tpl_idx, idx_novo, 1)
    new_cargos = ''.join(build_cargo_section(c, cidade, uf) for c in cargos)
    new_xml = p1 + '\n' + new_cargos + '\n    ' + p3
    ET.fromstring(new_xml)  # valida
    # Empacotar em memória
    work_dir = tempfile.mkdtemp()
    try:
        shutil.copytree(MODEL_DIR, os.path.join(work_dir, 'doc'))
        with open(os.path.join(work_dir, 'doc', 'word', 'document.xml'), 'w', encoding='utf-8') as f:
            f.write(new_xml)
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
            for root, dirs, files in os.walk(os.path.join(work_dir, 'doc')):
                for file in files:
                    abs_path = os.path.join(root, file)
                    rel_path = os.path.relpath(abs_path, os.path.join(work_dir, 'doc'))
                    zf.write(abs_path, rel_path)
        buf.seek(0)
        return buf.getvalue()
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)

# ── Extração de PDF ───────────────────────────────────────────────
def extrair_pdf(file_bytes):
    dados = {"nome":"","cnpj":"","rua":"","numero":"","complemento":"","cep":"","bairro":"","cidade":"","uf":"MG","cargos":[]}
    if not PDF_OK:
        return dados
    try:
        buf = io.BytesIO(file_bytes)
        texto = pdf_extract(buf)
    except:
        return dados
    full = ' '.join(l.strip() for l in texto.split('\n') if l.strip())
    lines = [l.strip() for l in texto.split('\n') if l.strip()]
    cnpj_m = re.search(r'\d{2}[\.\s]?\d{3}[\.\s]?\d{3}[/\s]?\d{4}[-\s]?\d{2}', full)
    if cnpj_m: dados['cnpj'] = re.sub(r'\s','',cnpj_m.group())
    for i,l in enumerate(lines):
        if re.search(r'RAZ[ÃA]O SOCIAL|NOME EMPRES', l, re.I) and i+1<len(lines):
            dados['nome'] = lines[i+1].strip(); break
    if not dados['nome'] and cnpj_m:
        idx = full.find(cnpj_m.group())
        before = full[:idx].strip().split()
        if before: dados['nome'] = ' '.join(before[-8:]).strip()
    cep_m = re.search(r'\d{5}-?\d{3}', full)
    if cep_m: dados['cep'] = cep_m.group()
    uf_m = re.search(r'\b(MG|SP|RJ|ES|GO|BA|PR|SC|RS|DF|MT|MS|AM|PA|CE|PE|MA|RN|PB|AL|SE|PI|TO|RO|AC|RR|AP|GO)\b', full)
    if uf_m: dados['uf'] = uf_m.group()
    for i,l in enumerate(lines):
        if re.search(r'(RUA|AV\.|AVENIDA|LOGRADOURO|ENDERE)', l, re.I) and len(l) > 5:
            dados['rua'] = l; break
    for cargo in ["Pedreiro","Servente","Pintor","Meio Oficial Pintor","Armador","Eletricista","Carpinteiro","Gesseiro","Rejuntador","Azulejista","Bombeiro Hidraulico","Encarregado","Montador","Meio Oficial Pedreiro","Meio Oficial Eletricista","Auxiliar de Limpeza","Porteiro","Vigia","Topografo","Auxiliar Administrativo","Engenheiro"]:
        if re.search(re.escape(cargo), full, re.I):
            dados['cargos'].append(cargo)
    return dados

# ── Rotas ─────────────────────────────────────────────────────────
@app.route('/')
def index():
    return render_template('index.html', cargos_sugestoes=CARGOS_SUGESTOES)

@app.route('/extrair', methods=['POST'])
def extrair():
    if 'pdf' not in request.files:
        return jsonify({'erro': 'Nenhum arquivo enviado'}), 400
    f = request.files['pdf']
    if not f.filename.lower().endswith('.pdf'):
        return jsonify({'erro': 'Envie um arquivo PDF'}), 400
    dados = extrair_pdf(f.read())
    return jsonify(dados)

@app.route('/gerar', methods=['POST'])
def gerar():
    data = request.json
    nome   = data.get('nome','').strip()
    cnpj   = data.get('cnpj','').strip()
    rua    = data.get('rua','').strip()
    numero = data.get('numero','').strip()
    compl  = data.get('complemento','').strip()
    cep    = data.get('cep','').strip()
    bairro = data.get('bairro','').strip()
    cidade = data.get('cidade','').strip() or 'Belo Horizonte'
    uf     = data.get('uf','MG').strip().upper()
    cargos = [c.strip() for c in data.get('cargos',[]) if c.strip()]

    if not nome: return jsonify({'erro': 'Informe a Razão Social'}), 400
    if not cargos: return jsonify({'erro': 'Adicione pelo menos um cargo'}), 400

    # ── Verificar problemas ANTES de gerar ──────────────────────────
    alertas = []

    # 1. Cargos não reconhecidos no GHE
    desconhecidos = [c for c in cargos if get_ghe(c) is None]
    if desconhecidos:
        return jsonify({
            'aviso': True,
            'erro': f'Cargo(s) não reconhecido(s) no GHE MRV: {", ".join(desconhecidos)}. '
                    f'Verifique a grafia ou solicite o cadastro deste cargo.'
        }), 422

    # 2. Cargos com GHE sem data de medição confirmada
    sem_data = [c for c in cargos if get_ghe(c) in GHE_SEM_DATA]
    if sem_data:
        ghes_afetados = list(dict.fromkeys(get_ghe(c) for c in sem_data))
        alertas.append(f'Data de medição não confirmada para: {", ".join(sem_data)} '
                       f'(GHE: {", ".join(ghes_afetados)}). '
                       f'O campo de data aparecerá como ??? no PGR gerado.')

    # 3. Campos obrigatórios ausentes
    faltando = []
    if not cnpj: faltando.append('CNPJ')
    if not cep:  faltando.append('CEP')
    if not cidade or cidade == 'Belo Horizonte' and not data.get('cidade','').strip():
        faltando.append('Cidade')
    if faltando:
        alertas.append(f'Campos não preenchidos: {", ".join(faltando)}. '
                       f'O PGR será gerado com esses campos em branco.')

    try:
        docx_bytes = gerar_docx_bytes(nome,cnpj,rua,numero,compl,cep,bairro,cidade,uf,cargos)
        nome_safe = re.sub(r'[/\\:*?"<>|]','_',nome)
        filename = f"PGR - {nome_safe} - {mes_ano().replace(' / ','_')}.docx"

        # Se há alertas, retornar JSON com aviso + base64 do arquivo
        if alertas:
            import base64
            return jsonify({
                'aviso_gerado': True,
                'alertas': alertas,
                'filename': filename,
                'docx_b64': base64.b64encode(docx_bytes).decode()
            })

        return send_file(
            io.BytesIO(docx_bytes),
            as_attachment=True,
            download_name=filename,
            mimetype='application/vnd.openxmlformats-officedocument.wordprocessingml.document'
        )
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'erro': f'Erro interno: {str(e)}'}), 500

@app.route('/ghe/<cargo>')
def ghe_info(cargo):
    ghe = get_ghe(cargo)
    if ghe is None:
        return jsonify({'ghe': 'NÃO_RECONHECIDO', 'agentes': 0, 'aviso': True})
    agentes = GHE_AGENTES.get(ghe, [])
    return jsonify({'ghe': ghe, 'agentes': len(agentes)})

# ── Laudo de Calor ────────────────────────────────────────────────────

def get_limite_nr15(m_medio):
    T = [(100,33.7),(102,33.6),(104,33.5),(106,33.4),(108,33.3),(110,33.2),
         (112,33.1),(115,33.0),(117,32.9),(119,32.8),(122,32.7),(124,32.6),
         (127,32.5),(129,32.4),(132,32.3),(135,32.2),(137,32.1),(140,32.0),
         (143,31.9),(146,31.8),(149,31.7),(152,31.6),(155,31.5),(158,31.4),
         (161,31.3),(165,31.2),(168,31.1),(171,31.0),(175,30.9),(178,30.8),
         (182,30.7),(186,30.6),(189,30.5),(193,30.4),(197,30.3),(201,30.2),
         (205,30.1),(209,30.0),(214,29.9),(218,29.8),(222,29.7),(227,29.6),
         (231,29.5),(236,29.4),(241,29.3),(246,29.2),(251,29.1),(256,29.0),
         (261,28.9),(266,28.8),(272,28.7),(277,28.6),(283,28.5),(289,28.4),
         (294,28.3),(300,28.2),(306,28.1),(313,28.0),(319,27.9),(325,27.8),
         (332,27.7),(339,27.6),(346,27.5)]
    if m_medio <= 100: return 33.7
    if m_medio >= 346: return 27.5
    for i in range(len(T)-1):
        m1,i1 = T[i]; m2,i2 = T[i+1]
        if m1 <= m_medio <= m2:
            return round(i1 + (i2-i1)*(m_medio-m1)/(m2-m1), 1)
    return 30.0

def _fx(v):
    try: return str(round(float(v),2)).replace('.',',')
    except: return str(v)

def _xe(s):
    import html as _h
    return _h.escape(str(s))

def gerar_calor_bytes(d):
    emp  = d.get('empresa',{})
    aval = d.get('avaliacao',{})
    sets = d.get('setores',[])

    tpl = os.path.join(BASE_DIR,'template_calor.docx')
    with open(tpl,'rb') as f: raw = f.read()
    zin = zipfile.ZipFile(io.BytesIO(raw))
    xml = zin.read('word/document.xml').decode('utf-8')
    rels_xml = zin.read('word/_rels/document.xml.rels').decode('utf-8')

    # ── Company replacements (Dahana template) ───────────────────────
    razao = emp.get('razaoSocial','')
    xml = xml.replace('COMERCIAL DAHANA LTDA', _xe(razao))
    xml = xml.replace('Av. General Olímpio Mourão Filho, 717', _xe(emp.get('endereco','')))
    xml = xml.replace('00.070.509/0034-79', _xe(emp.get('cnpj','')))
    xml = xml.replace('31710-690', _xe(emp.get('cep','')))
    xml = xml.replace('>Planalto<', f'>{_xe(emp.get("bairro",""))}<')
    xml = xml.replace('>Belo Horizonte<', f'>{_xe(emp.get("cidade","Belo Horizonte"))}<')
    xml = xml.replace('>MG<', f'>{_xe(emp.get("uf","MG"))}<')
    xml = xml.replace('47.11-3-02', _xe(emp.get('cnae','')))
    xml = xml.replace('Comércio varejista de mercadorias em geral, com predominância de produtos alimentícios - supermercados', _xe(emp.get('descricaoCnae','')))
    xml = xml.replace('>Thais Taveira<', f'>{_xe(emp.get("contato",""))}<')
    xml = xml.replace('(31)3359-3389', _xe(emp.get('telefone','')))
    xml = xml.replace('thais.conde@supernosso.com.br', _xe(emp.get('email','')))
    xml = xml.replace('BELO HORIZONTE, MAIO DE 2026.', _xe(aval.get('cidadeCarta','BELO HORIZONTE, MAIO DE 2026')) + '.')
    equip_old = 'Net.Temp – Chrompack Smart TEMP | S/N: IBU0000000209 | Calibração: 24/03/2026 | Certificado Nº 180.646'
    xml = xml.replace(equip_old, _xe(aval.get('equipamento', equip_old)))

    # ── Sector block template ────────────────────────────────────────
    calor2_poses = [m.start() for m in re.finditer('w:val="CALOR2"', xml)]
    if not calor2_poses: raise ValueError("CALOR2 style not found in template")
    sec_start = xml.rfind('<w:p ', 0, calor2_poses[0])
    cert_idx  = xml.find('CERTIFICADO DE CALIBRA', sec_start)
    tbl_end   = xml.rfind('</w:tbl>', sec_start, cert_idx) + len('</w:tbl>')

    # Use first sector as template
    if len(calor2_poses) > 1:
        sec_tpl = xml[sec_start : xml.rfind('<w:p ', 0, calor2_poses[1])]
    else:
        sec_tpl = xml[sec_start:tbl_end]

    tbl_open      = sec_tpl.find('<w:tbl>')
    calor2_para   = sec_tpl[:tbl_open]
    tbl_in_sec    = sec_tpl[tbl_open:]
    first_tr_pos  = tbl_in_sec.find('<w:tr')
    tbl_prefix    = tbl_in_sec[:first_tr_pos]  # <w:tbl> + tblPr + tblGrid
    tpl_rows      = re.findall(r'<w:tr[ >].*?</w:tr>', tbl_in_sec, re.DOTALL)

    row_hdr        = tpl_rows[0]   # header
    row_data_tpl   = tpl_rows[2]   # "Fritadeira" – clean data row
    row_ibutg_tpl  = tpl_rows[4]   # IBUTG médio
    row_act1_tpl   = tpl_rows[5]   # Atividade 01 (com labels "Tipo" e "Taxa M")
    row_actn_tpl   = tpl_rows[6]   # Atividade 02 (continuation)
    row_mmed_tpl   = tpl_rows[8]   # M média
    row_hor_tpl    = tpl_rows[9]   # Horário / vestimenta
    row_conc_hdr   = tpl_rows[10]  # CONCLUSÃO header
    row_conc_tpl   = tpl_rows[11]  # Conclusion text

    # ── Photo support ────────────────────────────────────────────────
    extra_rels  = []
    extra_media = {}
    _img_ctr    = [31]
    _iid_ctr    = [20]

    def _add_image(b64_str):
        _img_ctr[0] += 1; _iid_ctr[0] += 1
        rid = f'rId{_img_ctr[0]}'; iid = _iid_ctr[0]
        if b64_str.startswith('data:'):
            hdr, data = b64_str.split(',', 1)
            ext = 'jpeg' if ('jpeg' in hdr or 'jpg' in hdr) else 'png'
        else:
            data, ext = b64_str, 'jpeg'
        fname = f'media/calor_foto_{_img_ctr[0]}.{ext}'
        extra_rels.append(
            f'<Relationship Id="{rid}" '
            f'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" '
            f'Target="{fname}"/>')
        extra_media[f'word/{fname}'] = base64.b64decode(data)
        return rid, iid

    def _photo_cell(rid, iid, caption, w, span, pid):
        CX, CY = 2251710, 1689000
        ah = f'{pid:08X}'; eh = f'{(pid^0xABCD):08X}'
        gs = f'<w:gridSpan w:val="{span}"/>' if span > 1 else ''
        return (
            f'<w:tc><w:tcPr><w:tcW w:w="{w}" w:type="dxa"/>{gs}'
            f'<w:tcBorders><w:top w:val="single" w:sz="4" w:space="0" w:color="auto"/>'
            f'<w:left w:val="single" w:sz="4" w:space="0" w:color="auto"/>'
            f'<w:bottom w:val="single" w:sz="4" w:space="0" w:color="auto"/>'
            f'<w:right w:val="single" w:sz="4" w:space="0" w:color="auto"/></w:tcBorders>'
            f'<w:shd w:val="clear" w:color="auto" w:fill="FFFFFF" w:themeFill="background1"/>'
            f'<w:vAlign w:val="center"/></w:tcPr>'
            f'<w:p w14:paraId="{ah}" w14:textId="77777777">'
            f'<w:pPr><w:pStyle w:val="CORPODETEXTO"/><w:ind w:firstLine="0"/><w:jc w:val="center"/></w:pPr>'
            f'<w:r><w:drawing>'
            f'<wp:inline distT="0" distB="0" distL="0" distR="0" wp14:anchorId="{ah}" wp14:editId="{eh}">'
            f'<wp:extent cx="{CX}" cy="{CY}"/><wp:effectExtent l="0" t="0" r="0" b="0"/>'
            f'<wp:docPr id="{iid}" name="Foto {iid}"/>'
            f'<wp:cNvGraphicFramePr><a:graphicFrameLocks xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" noChangeAspect="1"/></wp:cNvGraphicFramePr>'
            f'<a:graphic xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
            f'<a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/picture">'
            f'<pic:pic xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture">'
            f'<pic:nvPicPr><pic:cNvPr id="{iid}" name="foto_{iid}.jpeg"/><pic:cNvPicPr/></pic:nvPicPr>'
            f'<pic:blipFill><a:blip r:embed="{rid}">'
            f'<a:extLst><a:ext uri="{{28A0092B-C50C-407E-A947-70E740481C1C}}">'
            f'<a14:useLocalDpi xmlns:a14="http://schemas.microsoft.com/office/drawing/2010/main" val="0"/>'
            f'</a:ext></a:extLst></a:blip>'
            f'<a:stretch><a:fillRect/></a:stretch></pic:blipFill>'
            f'<pic:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="{CX}" cy="{CY}"/></a:xfrm>'
            f'<a:prstGeom prst="rect"><a:avLst/></a:prstGeom></pic:spPr>'
            f'</pic:pic></a:graphicData></a:graphic></wp:inline></w:drawing></w:r>'
            f'<w:r><w:t>{_xe(caption)}</w:t></w:r></w:p></w:tc>'
        )

    def _empty_cell(caption, w, span, pid):
        ah = f'{pid:08X}'
        gs = f'<w:gridSpan w:val="{span}"/>' if span > 1 else ''
        return (
            f'<w:tc><w:tcPr><w:tcW w:w="{w}" w:type="dxa"/>{gs}'
            f'<w:tcBorders><w:top w:val="single" w:sz="4" w:space="0" w:color="auto"/>'
            f'<w:left w:val="single" w:sz="4" w:space="0" w:color="auto"/>'
            f'<w:bottom w:val="single" w:sz="4" w:space="0" w:color="auto"/>'
            f'<w:right w:val="single" w:sz="4" w:space="0" w:color="auto"/></w:tcBorders>'
            f'<w:shd w:val="clear" w:color="auto" w:fill="FFFFFF" w:themeFill="background1"/>'
            f'<w:vAlign w:val="center"/></w:tcPr>'
            f'<w:p w14:paraId="{ah}" w14:textId="77777777">'
            f'<w:pPr><w:pStyle w:val="CORPODETEXTO"/><w:ind w:firstLine="0"/><w:jc w:val="center"/></w:pPr>'
            f'<w:r><w:t>{_xe(caption)}</w:t></w:r></w:p></w:tc>'
        )

    # Cell widths & gridSpans for photo rows (10-column table, total 11055 DXA)
    _photo_specs = {
        1: [(11055, 10)],
        2: [(3968, 5), (7087, 5)],
        3: [(3683, 4), (3686, 4), (3686, 2)],
    }

    def _photo_rows(si, pontos):
        # Each ponto has up to 3 fotos → one photo row per ponto (3 cells)
        rows = []
        spc = _photo_specs[3]  # always 3 cells: (3683,4), (3686,4), (3686,2)
        for pi, p in enumerate(pontos):
            fotos = p.get('fotos') or []
            # Pad/trim to exactly 3 slots
            fotos = (list(fotos) + [None, None, None])[:3]
            if not any(fotos): continue  # skip row if no photos at all
            cap = p.get('local', '')
            cells = []
            for fi, b64 in enumerate(fotos):
                cw, gs = spc[fi]
                pid = (si * 0x100000 + pi * 0x1000 + fi + 0x500000) & 0xFFFFFFF
                if b64:
                    rid, iid = _add_image(b64)
                    cells.append(_photo_cell(rid, iid, cap, cw, gs, pid))
                else:
                    cells.append(_empty_cell('', cw, gs, pid))
            tr_pid = (si * 0x100000 + pi * 0x1000 + 0x600000) & 0xFFFFFFF
            rows.append(
                f'<w:tr w14:paraId="{tr_pid:08X}" w14:textId="77777777">'
                f'<w:trPr><w:cantSplit/><w:trHeight w:val="3500"/></w:trPr>'
                + ''.join(cells) + '</w:tr>')
        return rows

    # ── Row generators ───────────────────────────────────────────────
    def _uid(base_hex, si, offset):
        return f'{(int(base_hex, 16) + si*0x10000 + offset) & 0xFFFFFFFF:08X}'

    def _bump_ids(r, si, offset=0):
        return re.sub(r'(w14:paraId=")([0-9A-Fa-f]{8})',
                      lambda m: m.group(1)+_uid(m.group(2), si, offset), r)

    def make_data_row(pi, si, p):
        tbn = float(p.get('tbn',0)); tbs = float(p.get('tbs',0)); tg = float(p.get('tg',0))
        ibutg = round(0.7*tbn + 0.3*tg, 1)
        formula = f'IBUTG = (0,7 x {_fx(tbn)}) + (0,3 x {_fx(tg)})'
        r = _bump_ids(row_data_tpl, si, (pi+1)*0x100)
        r = r.replace('>Padaria – Fritadeira<', f'>{_xe(p.get("local",f"Ponto {pi+1}"))}<')
        r = r.replace('>15<', f'>{int(float(p.get("tempo",60)))}<')
        r = r.replace('>21,0<', f'>{_fx(tbn)}<')
        r = r.replace('>25,3<', f'>{_fx(tbs)}<')
        r = r.replace('>26,3<', f'>{_fx(tg)}<')
        r = r.replace('IBUTG = (0,7 x 21,0) + (0,3 x 26,3)', formula)
        r = r.replace('>22,6<', f'>{_fx(ibutg)}<')
        return r

    def make_act_row(pi, si, p):
        ativ = p.get('atividade','Trabalho Moderado – De p\xe9, com os bra\xe7os e tronco')
        M = round(float(p.get('M',198)))
        if pi == 0:
            r = _bump_ids(row_act1_tpl, si, 0x8000)
            r = r.replace(' Trabalho Moderado – De p\xe9, com os bra\xe7os e tronco',
                          f' {_xe(ativ)}')
            r = r.replace('>198 W<', f'>{M} W<')
        else:
            r = _bump_ids(row_actn_tpl, si, (pi+1)*0x8000)
            r = r.replace('>Atividade 02:<', f'>Atividade {pi+1:02d}:<')
            r = r.replace(' Trabalho Moderado – De p\xe9, com os bra\xe7os e tronco',
                          f' {_xe(ativ)}')
            r = r.replace('>198<', f'>{M}<')
        return r

    # ── Sector assembly ──────────────────────────────────────────────
    sector_blocks = []
    for si, setor in enumerate(sets):
        nome_s    = setor.get('nome', f'SETOR {si+1}')
        horario   = setor.get('horario','')
        vestimenta = setor.get('vestimenta','Uniforme de Trabalho (0)')
        pontos    = setor.get('pontos',[])

        total_t = sum(float(p.get('tempo',60)) for p in pontos) or 1
        ibutg_m = round(sum((0.7*float(p.get('tbn',0))+0.3*float(p.get('tg',0)))*float(p.get('tempo',60)) for p in pontos)/total_t, 1)
        m_med   = sum(float(p.get('M',198))*float(p.get('tempo',60)) for p in pontos)/total_t
        limite  = get_limite_nr15(m_med)
        ok      = ibutg_m <= limite

        c1 = (f'O limite de tolerância para exposição ao calor, segundo o Quadro Nº 1, do Anexo Nº 3, '
              f'na NR-09, para uma taxa de metabolismo média de {round(m_med)} W é de {_fx(limite)} IBUTG.')
        c2 = (f'O IBUTG médio encontrado foi de {_fx(ibutg_m)} ºC, ' +
              ('não ultrapassando o limite de tolerância.' if ok else 'ultrapassando o limite de tolerância.'))

        # CALOR2 title paragraph
        cp = _bump_ids(calor2_para, si)
        cp = re.sub(r'<w:t>[^<]*Avalia[^<]*</w:t>',
                    f'<w:t>Avaliação {si+1:02d} – Departamento: {_xe(nome_s)}</w:t>', cp)

        # Build rows
        data_rows = ''.join(make_data_row(pi, si, p) for pi, p in enumerate(pontos))

        r_ibutg = _bump_ids(row_ibutg_tpl, si, 0x1000)
        r_ibutg = re.sub(r'IBUTG \(M[eé]dio\) = [0-9,]+ ºC',
                         f'IBUTG (Médio) = {_fx(ibutg_m)} ºC', r_ibutg)

        act_rows = ''.join(make_act_row(pi, si, p) for pi, p in enumerate(pontos))

        r_mmed = _bump_ids(row_mmed_tpl, si, 0x2000)
        r_mmed = r_mmed.replace('>198 W<', f'>{round(m_med)} W<')

        r_hor = _bump_ids(row_hor_tpl, si, 0x3000)
        r_hor = r_hor.replace('>08:50 – 10:01<', f'>{_xe(horario)}<')
        r_hor = r_hor.replace('>Uniforme de Trabalho (0)<', f'>{_xe(vestimenta)}<')

        r_conc = _bump_ids(row_conc_tpl, si, 0x4000)
        r_conc = r_conc.replace(
            'O limite de tolerância para exposição ao calor, segundo o Quadro Nº 1, do Anexo Nº 3, na NR-09, para uma taxa de metabolismo média de 198 W é de 30,2 IBUTG.',
            _xe(c1))
        r_conc = r_conc.replace(
            'O IBUTG médio encontrado foi de 21,7 ºC, não ultrapassando o limite de tolerância.',
            _xe(c2))

        photo_rows = _photo_rows(si, pontos)

        tbl_xml = (tbl_prefix + row_hdr + data_rows + r_ibutg + act_rows +
                   r_mmed + r_hor + row_conc_hdr + r_conc +
                   ''.join(photo_rows) + '</w:tbl>')

        pb = ('<w:p><w:pPr><w:pStyle w:val="CORPODETEXTO"/></w:pPr>'
              '<w:r><w:rPr><w:noProof/></w:rPr><w:br w:type="page"/></w:r></w:p>') if si > 0 else ''
        sector_blocks.append(pb + cp + tbl_xml)

    xml = xml[:sec_start] + ''.join(sector_blocks) + xml[tbl_end:]

    # Inject new image relationships
    if extra_rels:
        rels_xml = rels_xml.replace('</Relationships>',
                                    '\n'.join(extra_rels) + '</Relationships>')

    zout = io.BytesIO()
    with zipfile.ZipFile(zout,'w',zipfile.ZIP_DEFLATED) as zw:
        for item in zin.infolist():
            if item.filename == 'word/document.xml':
                zw.writestr(item, xml.encode('utf-8'))
            elif item.filename == 'word/_rels/document.xml.rels':
                zw.writestr(item, rels_xml.encode('utf-8'))
            else:
                zw.writestr(item, zin.read(item.filename))
        for path, data in extra_media.items():
            zw.writestr(path, data)
    zin.close()
    return zout.getvalue()

@app.route('/gerar_calor', methods=['POST'])
def gerar_calor():
    data = request.json
    if not data or not data.get('empresa',{}).get('razaoSocial','').strip():
        return jsonify({'erro': 'Informe a Razão Social'}), 400
    if not data.get('setores'):
        return jsonify({'erro': 'Adicione pelo menos um setor'}), 400
    try:
        docx_bytes = gerar_calor_bytes(data)
        nome = data['empresa']['razaoSocial']
        nome_safe = re.sub(r'[/\\:*?"<>|]','_', nome)
        filename = f"Laudo de Calor - {nome_safe} - {mes_ano().replace(' / ','_')}.docx"
        return send_file(io.BytesIO(docx_bytes), as_attachment=True,
                         download_name=filename,
                         mimetype='application/vnd.openxmlformats-officedocument.wordprocessingml.document')
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({'erro': f'Erro interno: {str(e)}'}), 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
