# -*- coding: utf-8 -*-
"""Motor de leitura dos autos trabalhistas (modulo Pericias).

Duas responsabilidades, ambas sem Flask e sem banco — para poder rodar e testar
isoladamente:

1. FATIAR os autos. O PDF de "autos completos" do PJe traz indice de marcadores
   com o TIPO OFICIAL de cada peca, a data de juntada e a pagina inicial. Isso
   torna a separacao deterministica: nao se procura a peticao inicial, ela vem
   rotulada. Nenhuma IA participa desta etapa.

2. LER A ATA de audiencia — modalidade da pericia, perito nomeado e prazos.
   Aqui NAO existe formato unico: 2a VT Contagem escreve um bloco
   "TERMO DE PERICIA DE INSALUBRIDADE / PERICULOSIDADE / PPP" com tabela de
   datas absolutas; VT Nova Lima escreve prosa, defere DUAS pericias (contabil e
   insalubridade), nomeia dois peritos e da prazo relativo ("10 dias a contar de
   06/05/2026"). Por isso a saida desta funcao e SEMPRE um rascunho: traz
   `precisa_revisao` e a lista de `avisos`, e quem confirma e uma pessoa.
"""
import re
import unicodedata
from datetime import date, datetime, timedelta

try:
    import fitz  # PyMuPDF
except ImportError:  # pragma: no cover - ambiente sem PyMuPDF
    fitz = None


# ── Categorias canonicas de peca ───────────────────────────────────────
# O PJe usa dezenas de rotulos; o modulo so precisa saber o papel da peca no
# fluxo de analise (ata -> inicial -> contestacao -> documentacao tecnica).
CATEGORIAS = (
    'ata', 'inicial', 'contestacao', 'replica', 'quesitos', 'laudo',
    'doc_tecnico', 'ctps', 'jornada', 'financeiro', 'societario', 'processual', 'outro',
)

_MAPA_CATEGORIA = (
    # (padrao no tipo do PJe, categoria)
    (r'ata da audi|ata de audi',                                'ata'),
    (r'peti[çc][ãa]o inicial|reclama[çc][ãa]o trabalhista',     'inicial'),
    (r'contesta[çc][ãa]o',                                      'contestacao'),
    (r'r[ée]plica|impugna[çc][ãa]o [àa] contesta',              'replica'),
    (r'apresenta[çc][ãa]o de quesitos|quesitos',                'quesitos'),
    (r'laudo|prova emprestada|parecer t[ée]cnico',              'laudo'),
    (r'equipamento de prote[çc][ãa]o individual|\bepi\b|'
     r'atestado de sa[úu]de ocupacional|\baso\b|'
     r'perfil profissiogr|\bppp\b|ordem de servi[çc]o|'
     r'ficha de registro|regulamento interno|'
     r'programa de gerenciamento|\bpgr\b|\bltcat\b|\bpcmso\b',  'doc_tecnico'),
    (r'carteira de trabalho|\bctps\b|contrato de trabalho',     'ctps'),
    (r'cart[ãa]o de ponto|controle de frequ|jornada',           'jornada'),
    (r'contracheque|recibo de sal|\btrct\b|rescis[ãa]o|'
     r'fgts|recibo de f[ée]rias|dep[óo]sito judicial',          'financeiro'),
    (r'contrato social|\bcnpj\b|procura[çc][ãa]o|'
     r'substabelecimento|carta de preposi',                     'societario'),
    (r'despacho|intima[çc][ãa]o|certid[ãa]o|senten[çc]a|'
     r'mandado|habilita[çc][ãa]o|manifesta[çc][ãa]o|'
     r'sum[áa]rio|capa|conven[çc][ãa]o coletiva|\bcct\b',       'processual'),
)


# ── Modalidades de pericia ─────────────────────────────────────────────
# NOSSAS = as que a Ocupacional executa. FORA_ESCOPO existe para o modulo
# reconhecer e DESCARTAR: um processo pode deferir pericia contabil e
# insalubridade na mesma ata, e a contabil nao gera trabalho para a engenharia.
MODALIDADES = {
    'insalubridade':  r'insalubr',
    'periculosidade': r'pericul',
    'ergonomia':      r'ergonom|nr-?\s*17|ergon[ôo]mic',
    'acidente':       r'acidente do trabalho|acidente de trabalho|din[âa]mica do? acidente',
    'ppp':            r'\bppp\b|perfil profissiogr',
    'acumulo_funcao': r'ac[úu]mulo de fun[çc][ãa]o|ac[úu]mulo de cargo',
}
MODALIDADES_FORA_ESCOPO = {
    'contabil':     r'pericial cont[áa]bil|pericia cont[áa]bil|cont[áa]bil',
    'medica':       r'pericia m[ée]dica|pericial m[ée]dica',
    'grafotecnica': r'grafot[ée]cnic',
    'engenharia_civil': r'pericia de engenharia civil',
}
MODALIDADE_LABEL = {
    'insalubridade': 'Insalubridade',
    'periculosidade': 'Periculosidade',
    'ergonomia': 'Ergonomia',
    'acidente': 'Acidente do Trabalho / Dinâmica de Acidente',
    'ppp': 'PPP',
    'acumulo_funcao': 'Acúmulo de função',
    'contabil': 'Contábil (fora do escopo)',
    'medica': 'Médica (fora do escopo)',
    'grafotecnica': 'Grafotécnica (fora do escopo)',
    'engenharia_civil': 'Engenharia civil (fora do escopo)',
}

# Prazos que interessam. A ordem e a de leitura na ata.
PRAZOS = (
    ('prazo_quesitos',    'Prazo para apresentar quesitos e assistente técnico'),
    ('diligencia_ini',    'Início da janela da diligência'),
    ('diligencia_fim',    'Fim da janela da diligência'),
    ('prazo_laudo',       'Entrega do laudo pelo perito'),
    ('vista_ini',         'Início da vista às partes'),
    ('vista_fim',         'Fim da vista às partes'),
    ('esclarec_ini',      'Início do prazo de esclarecimentos'),
    ('esclarec_fim',      'Fim do prazo de esclarecimentos'),
    ('audiencia_instrucao', 'Audiência de instrução'),
)

_MESES = {
    'janeiro': 1, 'fevereiro': 2, 'marco': 3, 'março': 3, 'abril': 4, 'maio': 5,
    'junho': 6, 'julho': 7, 'agosto': 8, 'setembro': 9, 'outubro': 10,
    'novembro': 11, 'dezembro': 12,
}

_RE_CNJ = re.compile(r'\b(\d{7}-\d{2}\.\d{4}\.5\.\d{2}\.\d{4})\b')
_RE_DATA = re.compile(r'\b(\d{1,2})/(\d{1,2})/(\d{4})\b')
_RE_DATA_LONGA = re.compile(r'\b(\d{1,2})\s+de\s+([a-zç]+)\s+de\s+(\d{4})\b', re.I)
# A comarca nunca contem digito nem atravessa a linha — "[^\n\d]" evita colar o
# numero do processo no nome da cidade ("Santa Luzia ATOrd 0010580-...").
_RE_VARA = re.compile(r'(\d{1,2})[ªa°]?\s*Vara do Trabalho de\s+([^\n\d]{2,40})', re.I)
_RE_VARA_UNICA = re.compile(r'\bVara do Trabalho de\s+([^\n\d]{2,40})', re.I)
_RE_TRT = re.compile(r'TRIBUNAL REGIONAL DO TRABALHO DA\s+(\d{1,2})[ªa°]?\s*REGI[ÃA]O', re.I)


def _sem_acento(s):
    return ''.join(c for c in unicodedata.normalize('NFKD', s or '') if not unicodedata.combining(c))


def _linha_unica(texto):
    """Junta o texto num paragrafo continuo. O PJe quebra linha no meio da
    frase ("17/07\n/2026", "prazo comum de 10\ndias"), o que arrebenta
    qualquer regex aplicado linha a linha."""
    t = re.sub(r'-\n', '', texto or '')
    t = re.sub(r'\s*\n\s*', ' ', t)
    t = re.sub(r'[ \t]{2,}', ' ', t)
    # Data partida pela quebra de linha vira "17/07 /2026" depois do join.
    # Sem colar as barras, todo prazo escrito no fim da linha se perde.
    t = re.sub(r'(\d)\s*/\s*(\d)', r'\1/\2', t)
    return t.strip()


def _iso(d):
    return d.strftime('%Y-%m-%d') if d else None


def _data_de(trecho):
    """Primeira data encontrada no trecho, em ISO. Aceita 12/06/2026 e
    '3 de junho de 2026'. Devolve None se nao houver data valida."""
    m = _RE_DATA.search(trecho or '')
    if m:
        try:
            return _iso(date(int(m.group(3)), int(m.group(2)), int(m.group(1))))
        except ValueError:
            return None
    m = _RE_DATA_LONGA.search(trecho or '')
    if m:
        mes = _MESES.get(_sem_acento(m.group(2)).lower())
        if mes:
            try:
                return _iso(date(int(m.group(3)), mes, int(m.group(1))))
            except ValueError:
                return None
    return None


def _mais_dias_uteis(d, n):
    """Soma n dias uteis (ignora sabado e domingo; nao conhece feriado)."""
    atual, faltam = d, n
    while faltam > 0:
        atual += timedelta(days=1)
        if atual.weekday() < 5:
            faltam -= 1
    return atual


def categoria_da_peca(tipo_pje):
    """Categoria canonica a partir do rotulo do PJe."""
    t = (tipo_pje or '').lower()
    for padrao, cat in _MAPA_CATEGORIA:
        if re.search(padrao, t):
            return cat
    return 'outro'


# ── 1. Fatiamento dos autos ────────────────────────────────────────────

# Categorias cujo texto sai INTEGRAL. As demais saem por amostra — ver
# _monta_peca(). E a lista de peças que o fluxo de analise da pericia le:
# ata -> inicial -> contestacao -> documentacao tecnica.
CATEGORIAS_COM_TEXTO = frozenset((
    'ata', 'inicial', 'contestacao', 'replica', 'quesitos', 'laudo',
    'doc_tecnico', 'ctps',
))


def fatiar_autos(caminho_ou_bytes, guardar_texto=True,
                 categorias_com_texto=CATEGORIAS_COM_TEXTO):
    """Abre o PDF dos autos e devolve dict com cabecalho e lista de pecas.

    Cada peca: {ordem, tipo_pje, categoria, data_juntada, pag_ini, pag_fim,
    paginas, chars, densidade, escaneada, texto_integral, texto}.

    `escaneada` = densidade abaixo de 320 chars/pagina. Nos autos reais o PJe
    estampa cabecalho e assinatura eletronica em toda pagina (120-300 chars),
    entao pagina digitalizada nunca vem com zero — o corte por densidade e o
    que separa peca nativa de peca que ainda vai precisar de OCR.
    """
    if fitz is None:
        raise RuntimeError('PyMuPDF (pymupdf) nao esta instalado')

    if isinstance(caminho_ou_bytes, (bytes, bytearray)):
        doc = fitz.open(stream=bytes(caminho_ou_bytes), filetype='pdf')
    else:
        doc = fitz.open(caminho_ou_bytes)

    try:
        meta = doc.metadata or {}
        toc = doc.get_toc() or []

        faixas = []
        if toc:
            for i, item in enumerate(toc):
                pag = max(1, item[2])
                fim = toc[i + 1][2] - 1 if i + 1 < len(toc) else doc.page_count
                faixas.append((item[1], pag, max(pag, fim)))
        else:
            # Sem indice: trata os autos como peca unica. Nao adivinha corte —
            # melhor entregar o texto inteiro e sinalizar do que fatiar errado.
            faixas.append(('Autos (PDF sem índice de peças)', 1, doc.page_count))

        pecas = [_monta_peca(doc, t, ini, fim, guardar_texto, categorias_com_texto)
                 for t, ini, fim in faixas]

        cabecalho = _cabecalho(meta, _texto_paginas(doc, 1, min(3, doc.page_count)), pecas)
        cabecalho['total_paginas'] = doc.page_count
        cabecalho['tem_indice'] = bool(toc)
        nativas = sum(p['paginas'] for p in pecas if not p['escaneada'])
        cabecalho['paginas_nativas'] = nativas
        cabecalho['paginas_escaneadas'] = doc.page_count - nativas
        # Peca amostrada tem a densidade medida em 3 paginas, nao em todas — a
        # contagem nativo/escaneado dela e estimativa. A UI precisa dizer "≈".
        cabecalho['paginas_estimadas'] = any(not p['texto_integral'] for p in pecas)
        return {'cabecalho': cabecalho, 'pecas': pecas}
    finally:
        doc.close()


def _texto_paginas(doc, pag_ini, pag_fim):
    return '\n'.join((doc[i].get_text('text') or '')
                     for i in range(pag_ini - 1, min(pag_fim, doc.page_count)))


def _monta_peca(doc, titulo, pag_ini, pag_fim, guardar_texto, categorias_com_texto):
    paginas = pag_fim - pag_ini + 1
    # "12. 29/05/2026 - Contestacao - 209ac60" -> ordem, data, tipo
    m = re.match(r'^\s*(\d+)\.\s*(\d{2}/\d{2}/\d{4})\s*-\s*(.+?)'
                 r'(?:\s*-\s*[0-9a-f]{6,})?\s*$', titulo.strip())
    if m:
        ordem, data_j, tipo = int(m.group(1)), _data_de(m.group(2)), m.group(3).strip()
    else:
        ordem, data_j, tipo = None, _data_de(titulo), re.sub(r'\s*-\s*[0-9a-f]{6,}$', '',
                                                            titulo.strip())
    categoria = categoria_da_peca(tipo)

    # Custo: autos de 723 paginas levariam o request inteiro para extrair texto
    # de tudo, e 92% desse volume e CCT, contracheque e cartao de ponto — que o
    # fluxo da pericia nao le. Peca relevante sai integral; o resto sai por
    # AMOSTRA (3 paginas), suficiente para medir densidade e identificar a peca.
    # Quem precisar do texto completo de uma peca amostrada pede sob demanda.
    integral = (not guardar_texto) or categoria in categorias_com_texto
    if integral:
        corpo = _texto_paginas(doc, pag_ini, pag_fim).strip()
        base_paginas = paginas
    else:
        amostra = sorted({pag_ini, (pag_ini + pag_fim) // 2, pag_fim})
        corpo = '\n'.join((doc[p - 1].get_text('text') or '') for p in amostra).strip()
        base_paginas = len(amostra)

    densidade = round(len(corpo) / max(1, base_paginas))
    return {
        'ordem': ordem,
        'tipo_pje': tipo,
        'categoria': categoria,
        'data_juntada': data_j,
        'pag_ini': pag_ini,
        'pag_fim': pag_fim,
        'paginas': paginas,
        'chars': len(corpo) if integral else None,
        'densidade': densidade,
        'escaneada': densidade < 320,
        'texto_integral': bool(integral and guardar_texto),
        'texto': (corpo if guardar_texto else ''),
    }


def _limpa_comarca(s):
    """'BELO HORIZONTE // MG' -> 'BELO HORIZONTE'; tira pontuacao e UF solta."""
    c = re.split(r'\s*[/|,]{1,2}\s*', (s or '').strip())[0]
    return re.sub(r'\s{2,}', ' ', c).strip(' .,;-') or None


def _vara_comarca(texto):
    """Numero da vara e comarca. Vara numerada tem prioridade sobre vara unica."""
    m = _RE_VARA.search(texto or '')
    if m:
        return m.group(1) + 'ª', _limpa_comarca(m.group(2))
    m = _RE_VARA_UNICA.search(texto or '')
    if m:
        return None, _limpa_comarca(m.group(1))
    return None, None


def _cabecalho(meta, inicio, pecas=None):
    """Numero CNJ, partes, vara e regional. Vem do metadata do PJe quando
    existe (title/subject) e cai para o texto das primeiras paginas."""
    titulo = meta.get('title') or ''
    assunto = meta.get('subject') or ''

    numero = None
    for fonte in (titulo, assunto, inicio):
        m = _RE_CNJ.search(fonte or '')
        if m:
            numero = m.group(1)
            break

    reclamante = reclamada = None
    m = re.search(r'AUTOR[^:]*:\s*(.+?)(?:;|$)', assunto, re.I)
    if m:
        reclamante = m.group(1).strip()
    m = re.search(r'R[ÉE]U[^:]*:\s*(.+?)(?:;|$)', assunto, re.I)
    if m:
        reclamada = m.group(1).strip()
    if not reclamante:
        m = re.search(r'RECLAMANTE\s*:?\s*(.+)', inicio)
        if m:
            reclamante = m.group(1).strip()
    if not reclamada:
        m = re.search(r'RECLAMAD[OA]\s*(?:\(A\))?\s*:?\s*(.+)', inicio)
        if m:
            reclamada = m.group(1).strip()

    vara, comarca = _vara_comarca(inicio)
    m = _RE_TRT.search(inicio)
    trt = m.group(1) + 'ª' if m else (numero.split('.')[3] if numero else None)

    rito = None
    if re.search(r'sumar[íi]ssimo', titulo, re.I):
        rito = 'Sumaríssimo'
    elif re.search(r'ordin[áa]ri', titulo, re.I):
        rito = 'Ordinário'

    return {
        'numero_cnj': numero, 'reclamante': reclamante, 'reclamada': reclamada,
        'vara': vara, 'comarca': comarca, 'trt': trt, 'rito': rito,
        'assunto_pje': (meta.get('keywords') or '').strip() or None,
    }


# ── 2. Leitura da ata ──────────────────────────────────────────────────

# Palavras que NUNCA fazem parte do nome de um perito. Servem de freio: o texto
# do PJe cola o rodape de assinatura logo depois do nome ("Pericles Maurilio
# Correa Documento assinado eletronicamente por ..."), e sem esse corte o nome
# capturado estoura o limite de tamanho e a extracao devolve vazio.
_STOP_NOME = {
    'documento', 'documentos', 'fls', 'processo', 'numero', 'termo', 'perito',
    'perita', 'peritos', 'assinado', 'assinada', 'eletronicamente', 'intime',
    'intimem', 'intimacao', 'devera', 'deverao', 'prazo', 'prazos', 'data',
    'vista', 'audiencia', 'nomeado', 'nomeada', 'que', 'para', 'com', 'sem',
    'os', 'as', 'ao', 'na', 'no', 'em', 'por', 'sob', 'apresentar', 'laudo',
    'autos', 'partes', 'parte', 'juizo', 'vara', 'poder', 'justica', 'tribunal',
    'honorarios', 'indagadas', 'deferida', 'deferido', 'defiro', 'nomeando',
    'engenheiro', 'engenheira', 'medico', 'medica', 'crea', 'crm', 'oab', 'cpf',
    'qual', 'quais', 'fica', 'ficam', 'tomar', 'carga', 'encargo', 'reclamante',
    'reclamada', 'reclamado', 'valor', 'conciliacao', 'presente', 'presentes',
}
# Conectores aceitos no meio do nome, nunca no fim.
_CONECTOR_NOME = {'de', 'da', 'do', 'das', 'dos', 'e'}


def _limpa_nome(bruto):
    """Reduz o trecho capturado a um nome de pessoa plausivel: no maximo 5
    palavras, cortando na primeira palavra de parada. Devolve '' se o que
    sobrou nao parecer nome (menos de 2 palavras)."""
    tokens = re.split(r'\s+', re.sub(r'[.,;:]+', ' ', bruto or '').strip())
    nome = []
    for tk in tokens:
        base = _sem_acento(tk).lower()
        if not base:
            continue
        if base in _STOP_NOME:
            break
        if base not in _CONECTOR_NOME and not tk[:1].isupper():
            break
        nome.append(tk)
        if len(nome) >= 5:
            break
    while nome and _sem_acento(nome[-1]).lower() in _CONECTOR_NOME:
        nome.pop()
    return ' '.join(nome) if len(nome) >= 2 else ''


def _peritos(plano):
    """Nomes de peritos nomeados. Cobre os tres jeitos vistos nos autos reais:
      'Perito Nomeado:  Pericles Maurilio Correa'
      'Nomeado(a) como perito(a) o(a) , Sr(a). ANA PAULA MARTINS TRISTAO'
      'nomeando-se para o encargo o , Sr(a). IVAN PEREIRA DE SOUZA'
    O PJe insere virgula e quebra de linha entre o rotulo e o nome, por isso o
    separador tolera pontuacao solta.
    """
    achados = []
    padroes = (
        r'Perito\s*(?:Oficial\s*)?Nomead[oa]\s*:?\s*[,\s]*(.{5,90})',
        r'(?:nomead[oa]|nomeando-se|nomeio)[^.]{0,80}?Sr\(?a?\)?\.?\s*(.{5,90})',
        r'perit[oa]\s*\(?a?\)?\s*(?:o|a)?\s*,?\s*Sr\(?a?\)?\.?\s*(.{5,90})',
    )
    for p in padroes:
        for m in re.finditer(p, plano, re.I):
            nome = _limpa_nome(m.group(1))
            if nome and nome.upper() not in [a.upper() for a in achados]:
                achados.append(nome)
    return achados


def _modalidades(plano):
    """Modalidades deferidas na ata, separadas entre nossas e fora do escopo.

    Procura primeiro nos gatilhos de deferimento ("TERMO DE PERICIA DE ...",
    "deferida prova pericial de ...", "apuracao de ..."). Se nenhum gatilho
    casar, cai para varredura no texto todo — mais ruidoso, e por isso baixa a
    confianca e gera aviso.
    """
    gatilhos = [
        r'TERMO DE PER[ÍI]CIA[^\n\.]{0,160}',
        r'(?:defer(?:ida|ido|e-se|indo)|defiro)[^\.]{0,160}',
        r'pericial[^\.]{0,120}',
        r'per[íi]cia (?:destinada )?[àa]?\s*(?:apura[çc][ãa]o d[eoa]|para)[^\.]{0,120}',
    ]
    escopo = ' '.join(m.group(0) for g in gatilhos for m in re.finditer(g, plano, re.I))
    usou_fallback = not escopo.strip()
    alvo = escopo if not usou_fallback else plano

    nossas = [k for k, pat in MODALIDADES.items() if re.search(pat, alvo, re.I)]
    fora = [k for k, pat in MODALIDADES_FORA_ESCOPO.items() if re.search(pat, alvo, re.I)]
    # "pericia contabil" contem "contabil", mas so vale como fora-de-escopo se
    # o termo aparecer junto de pericia/prova pericial — evita marcar por causa
    # de "documento contabil" citado de passagem.
    fora = [f for f in fora if re.search(r'peric', alvo, re.I)]
    return nossas, fora, usou_fallback


def _prazos(plano):
    """Prazos da ata. Aceita data absoluta e prazo relativo em dias."""
    prazos, avisos = {}, []

    # (chave, padrao ancorado no rotulo; a data vem depois do rotulo)
    absolutos = (
        ('prazo_quesitos',
         r'(?:prazo para )?apresenta[çc][ãa]o de quesitos[^:]{0,120}:\s*([^\.;]{0,60})'),
        ('prazo_quesitos',
         r'quesitos[^:]{0,60}:\s*(\d{1,2}/\d{1,2}/\d{4})'),
        ('diligencia_ini',
         r'dilig[êe]ncia pericial[^:]{0,80}:\s*(\d{1,2}/\d{1,2}/\d{4})'),
        ('diligencia_fim',
         r'dilig[êe]ncia pericial[^:]{0,80}:\s*\d{1,2}/\d{1,2}/\d{4}\s*(?:at[ée]|a)\s*(\d{1,2}/\d{1,2}/\d{4})'),
        ('prazo_laudo',
         r'(?:entrega|apresenta[çc][ãa]o) do laudo[^:]{0,120}:\s*([^\.;]{0,60})'),
        ('prazo_laudo',
         r'apresentar laudo at[ée] o dia\s*(\d{1,2}/\d{1,2}/\d{4})'),
        ('vista_ini',
         r'[Vv]ista [àa]s partes do laudo[^:]{0,90}:\s*(\d{1,2}/\d{1,2}/\d{4})'),
        ('vista_fim',
         r'[Vv]ista [àa]s partes do laudo[^:]{0,90}:\s*\d{1,2}/\d{1,2}/\d{4}\s*(?:at[ée]|a)\s*(\d{1,2}/\d{1,2}/\d{4})'),
        ('esclarec_ini',
         r'esclarecimentos pel[oa]\s*\(?a?\)?\s*perit[oa][^:]{0,60}:\s*(\d{1,2}/\d{1,2}/\d{4})'),
        ('esclarec_fim',
         r'esclarecimentos pel[oa]\s*\(?a?\)?\s*perit[oa][^:]{0,60}:\s*\d{1,2}/\d{1,2}/\d{4}\s*(?:at[ée]|a)\s*(\d{1,2}/\d{1,2}/\d{4})'),
        ('audiencia_instrucao',
         r'[Aa]udi[êe]ncia de instru[çc][ãa]o[^\.]{0,60}?(\d{1,2}/\d{1,2}/\d{4})'),
    )
    for chave, padrao in absolutos:
        if prazos.get(chave):
            continue
        m = re.search(padrao, plano)
        if m:
            d = _data_de(m.group(1))
            if d:
                prazos[chave] = {'data': d, 'origem': 'ata', 'base': 'absoluta'}

    # Prazo relativo: "prazo comum de 10 dias, a contar de 06/05/2026".
    # Guarda as duas leituras (corridos e uteis) porque a ata nao diz qual, e
    # errar aqui e perder o prazo — quem decide e a pessoa que revisa.
    m = re.search(r'quesitos[^\.]{0,200}?prazo\s+(?:comum\s+)?de\s+(\d{1,3})\s*dias'
                  r'(\s+[úu]teis)?[^\.]{0,40}?a contar de\s*(\d{1,2}/\d{1,2}/\d{4})',
                  plano, re.I) or \
        re.search(r'prazo\s+(?:comum\s+)?de\s+(\d{1,3})\s*dias(\s+[úu]teis)?'
                  r'[^\.]{0,60}?a contar de\s*(\d{1,2}/\d{1,2}/\d{4})[^\.]{0,120}?quesitos',
                  plano, re.I)
    if m and not prazos.get('prazo_quesitos'):
        n, uteis, base_iso = int(m.group(1)), bool(m.group(2)), _data_de(m.group(3))
        if base_iso:
            base = datetime.strptime(base_iso, '%Y-%m-%d').date()
            corridos, dias_uteis = base + timedelta(days=n), _mais_dias_uteis(base, n)
            prazos['prazo_quesitos'] = {
                'data': _iso(dias_uteis if uteis else corridos),
                'origem': 'ata', 'base': 'relativa',
                'detalhe': '%d dias%s a contar de %s' % (n, ' úteis' if uteis else '',
                                                         base.strftime('%d/%m/%Y')),
                'alternativa_corridos': _iso(corridos),
                'alternativa_uteis': _iso(dias_uteis),
            }
            avisos.append(
                'O prazo de quesitos veio relativo (%s). Sistema calculou %s; '
                'a outra leitura é %s. Conferir antes de usar como prazo.'
                % (prazos['prazo_quesitos']['detalhe'],
                   corridos.strftime('%d/%m/%Y') if not uteis else dias_uteis.strftime('%d/%m/%Y'),
                   dias_uteis.strftime('%d/%m/%Y') if not uteis else corridos.strftime('%d/%m/%Y'))
            )
    return prazos, avisos


def ler_ata(texto_ata):
    """Le a ata e devolve rascunho para revisao humana.

    Saida: {modalidades, modalidades_fora_escopo, peritos, prazos, juiz,
    data_audiencia, confianca (0-100), precisa_revisao (sempre True),
    avisos[]}.

    `precisa_revisao` e True por principio: a ata nao tem formato unico entre
    regionais e um prazo lido errado significa preclusao. O numero da
    confianca serve para PRIORIZAR a fila de revisao, nao para dispensa-la.
    """
    plano = _linha_unica(texto_ata or '')
    avisos = []

    nossas, fora, usou_fallback = _modalidades(plano)
    if usou_fallback and nossas:
        avisos.append('Nenhum termo de deferimento reconhecido; a modalidade foi '
                      'inferida do texto inteiro da ata. Confirmar.')
    if not nossas:
        avisos.append('Não foi possível identificar a modalidade da perícia na ata.')
    if fora:
        avisos.append('A ata também defere perícia de outra especialidade (%s), '
                      'que não é da engenharia.' % ', '.join(MODALIDADE_LABEL[f] for f in fora))

    peritos = _peritos(plano)
    if not peritos:
        avisos.append('Perito nomeado não identificado na ata.')
    elif len(peritos) > 1:
        avisos.append('A ata nomeia mais de um perito (%s). Conferir qual responde '
                      'pela perícia da engenharia.' % '; '.join(peritos))

    prazos, avisos_prazo = _prazos(plano)
    avisos.extend(avisos_prazo)
    if not prazos.get('prazo_quesitos'):
        avisos.append('Prazo de quesitos não localizado — é o prazo que preclui. '
                      'Preencher à mão.')

    m = re.search(r'(?:Exmo\(a\)\.?\s*Sr\(a\)\.?\s*)?Ju[íi]z(?:a)?\s+d[oe]\s+Trabalho\s+'
                  r'([A-ZÀ-Ú][A-ZÀ-Ú\s]{5,60})', plano)
    juiz = re.sub(r'\s{2,}', ' ', m.group(1)).strip() if m else None
    if not juiz:
        m = re.search(r'sob a dire[çc][ãa]o d[oe]\(?a?\)?[^,]{0,40}?'
                      r'([A-ZÀ-Ú][A-ZÀ-Ú\s]{8,60})', plano)
        juiz = re.sub(r'\s{2,}', ' ', m.group(1)).strip() if m else None

    m = re.search(r'Em\s+(\d{1,2}\s+de\s+[a-zç]+\s+de\s+\d{4})', plano, re.I)
    data_audiencia = _data_de(m.group(1)) if m else None

    # Confianca: o que a ata entregou de fato, ponderado pelo que importa.
    pontos = 0
    if nossas:
        pontos += 30 if not usou_fallback else 15
    if peritos:
        pontos += 20 if len(peritos) == 1 else 10
    if prazos.get('prazo_quesitos'):
        pontos += 30 if prazos['prazo_quesitos'].get('base') == 'absoluta' else 15
    if prazos.get('prazo_laudo'):
        pontos += 10
    if data_audiencia:
        pontos += 10

    return {
        'modalidades': nossas,
        'modalidades_fora_escopo': fora,
        'peritos': peritos,
        'perito_nomeado': peritos[0] if len(peritos) == 1 else None,
        'prazos': prazos,
        'juiz': juiz,
        'data_audiencia': data_audiencia,
        'confianca': min(100, pontos),
        'precisa_revisao': True,
        'avisos': avisos,
    }


def analisar_autos(caminho_ou_bytes):
    """Fatia os autos e ja le a ata mais recente. Retorno pronto para gravar."""
    dados = fatiar_autos(caminho_ou_bytes)
    atas = [p for p in dados['pecas'] if p['categoria'] == 'ata']
    # Mais de uma audiencia no processo: vale a ata mais nova, e as anteriores
    # ficam registradas como pecas normais.
    ata = sorted(atas, key=lambda p: (p['data_juntada'] or '', p['pag_ini']))[-1] if atas else None
    dados['ata_pagina'] = ata['pag_ini'] if ata else None
    # A capa do PJe as vezes traz so "Vara do Trabalho de X" sem o numero; o
    # cabecalho da ata traz a vara completa. Completa o que faltou.
    if ata:
        vara, comarca = _vara_comarca(ata['texto'][:1200])
        dados['cabecalho']['vara'] = dados['cabecalho'].get('vara') or vara
        dados['cabecalho']['comarca'] = dados['cabecalho'].get('comarca') or comarca
    dados['extracao'] = ler_ata(ata['texto']) if ata else {
        'modalidades': [], 'modalidades_fora_escopo': [], 'peritos': [],
        'perito_nomeado': None, 'prazos': {}, 'juiz': None, 'data_audiencia': None,
        'confianca': 0, 'precisa_revisao': True,
        'avisos': ['Nenhuma ata de audiência encontrada nos autos.'],
    }
    return dados
