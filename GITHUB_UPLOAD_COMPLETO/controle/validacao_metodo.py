# -*- coding: utf-8 -*-
"""Confere a coleta contra a guia de métodos, antes de a amostra ir ao laboratório.

Ponto de controle pedido pelo Matheus em 31/07/2026: depois que o tubo sai para o
laboratório não tem volta, então vazão, volume e tipo de amostrador têm que ser
conferidos contra o método enquanto dá para refazer.

Prova de valor na coleta real da Destak (5 amostradores): o `TCP2912AV3` foi
coletado a 0,201 L/min quando o NIOSH 1403 admite até 0,2 — fora por 0,001, meio
por cento. A bomba já tinha sido calibrada em 0,20127, acima do máximo antes de
começar. É o tipo de desvio que ninguém vê a olho nu e que o laboratório pode
contestar depois.

A guia (`guia_metodos.json`, 374 agentes / 512 métodos) traz por método: faixa de
vazão, faixa de volume, código do método e o tipo de amostrador compatível.

**O mesmo agente pode ser medido de mais de uma forma** (Matheus, 03/08/2026), e
isso aparece em dois eixos que se confundiam num só:

1. **Métodos diferentes para o mesmo agente** — 116 dos 374 agentes. Benzeno tem
   4 entradas (NIOSH 1501 em TCP, OSHA 1005 em TCP e 2 PASSIVO SKC em OVM). Quem
   escolhe é o técnico na tela; o amostrador usado só faz a pré-seleção.
2. **Regimes diferentes DENTRO do mesmo método** — TWA, STEL, vapores. 20
   métodos escrevem os dois no mesmo campo (`'*TWA: 240L *STEL: 30L'`), e ler
   tudo junto fundia as faixas: virava 30 a 240 L, que reprova um TWA de 248 L e
   aprova um STEL de 200 L. O regime sai da DURAÇÃO da coleta.

Limite de valor único (`'1 L/MIN'`, 101 métodos) é alvo de calibração, não teto:
até ±5% de desvio vira ATENÇÃO, não reprovação. Faixa declarada (`'0,02 A 0,2'`)
continua sendo limite duro.
"""
import re
import logging

log = logging.getLogger(__name__)

# Veredictos possíveis
OK = 'ok'                # dentro do método
ATENCAO = 'atencao'      # fora do valor nominal, dentro da tolerância de calibração
FORA = 'fora'            # algum parâmetro fora da faixa
SEM_METODO = 'sem_metodo'  # agente não está na guia (374 de 468 do catálogo do lab)
SEM_DADO = 'sem_dado'    # falta vazão/tempo para conferir

# Desvio tolerado sobre limite de VALOR ÚNICO (não sobre faixa declarada).
TOLERANCIA_NOMINAL = 0.05

# Regimes de amostragem que a guia rotula dentro do campo de vazão/volume.
_ROTULOS = r'TWA|STEL|TETO|CEILING|VAPORES(?:\s+E\s+MISTURAS)?'
_RE_ROTULO = re.compile(r'(' + _ROTULOS + r')', re.I)
# '(240MIN)' é a duração do regime, não um limite de faixa — sai antes de medir.
# Não pega o 'MIN' de 'L/MIN' porque ali o número não encosta no MIN.
_RE_DURACAO = re.compile(r'\(?\s*(\d{1,4})\s*MIN(?:UTOS)?\b\s*\)?', re.I)


def num_br(texto):
    """Número em pt-BR: ponto é MILHAR, vírgula é decimal.

    '1.000' → 1000.0 · '0,02' → 0.02 · '2,5' → 2.5

    Tratar o ponto como decimal lia "45 A 1.000 L" como faixa 1–45 e reprovava
    coleta boa: 120,6 L aparecia 3× acima do limite quando está dentro.
    """
    s = str(texto or '').strip()
    if not s:
        return None
    try:
        if ',' in s:                                  # vírgula = decimal
            return float(s.replace('.', '').replace(',', '.'))
        if re.match(r'^\d{1,3}(\.\d{3})+$', s):       # 1.000 / 12.500 = milhar
            return float(s.replace('.', ''))
        return float(s)
    except ValueError:
        return None


def _numeros(texto):
    return [n for n in (num_br(x) for x in re.findall(r'\d+(?:[.,]\d+)*', str(texto or '')))
            if n is not None]


def faixa(texto):
    """(mínimo, máximo) do texto da guia. (None, None) quando não dá para ler.

    Formas reais encontradas:
      '0,02 A 0,2 L/MIN'                    → (0.02, 0.2)
      '45 A 1.000 L'                        → (45.0, 1000.0)
      'MÁXIMO 6 L'                          → (0.0, 6.0)
      '1,7 NYLON OU 2,0 SKC OU 2,5 ALUMÍNIO' → (1.7, 2.5)   ← vazão por material
    """
    t = str(texto or '')
    if not t.strip():
        return None, None
    nums = _numeros(t)
    if not nums:
        return None, None
    if re.search(r'M[ÁA]XIMO|AT[ÉE]', t, re.I) and len(nums) == 1:
        return 0.0, nums[0]
    if re.search(r'M[ÍI]NIMO', t, re.I) and len(nums) == 1:
        return nums[0], None
    if len(nums) == 1:
        return nums[0], nums[0]
    return min(nums), max(nums)


def nominal(texto):
    """True quando a guia declara UM valor, não uma faixa ('1 L/MIN', '240L').

    Valor único é alvo de calibração: a bomba fecha em 1,037 e reprovar por 3,7%
    é mais rígido que o próprio laboratório. Faixa ('0,02 A 0,2'), 'MÁXIMO' e
    'MÍNIMO' declaram limite de verdade e continuam duros.
    """
    t = str(texto or '')
    if re.search(r'\bA\b|\bE\b|[–—]|M[ÁA]XIMO|M[ÍI]NIMO|AT[ÉE]', t, re.I):
        return False
    return len(_numeros(t)) == 1


def _rotulo_canon(s):
    s = str(s or '').upper()
    if 'STEL' in s:
        return 'STEL'
    if 'TWA' in s:
        return 'TWA'
    if 'TETO' in s or 'CEILING' in s:
        return 'TETO'
    if 'VAPORES' in s:
        return 'VAPORES'
    return ''


def blocos_regime(texto):
    """Um bloco por regime de amostragem declarado no campo.

    A guia escreve os regimes no MESMO campo:
      '*TWA: 1L/MIN (240MIN) *VAPORES E MISTURAS 2L/MIN (120MIN) *STEL: 2L/MIN (15MIN)'
    → TWA(1, 240min) · VAPORES(2, 120min) · STEL(2, 15min)

    Bloco sem rótulo antes do primeiro rótulo vale como TWA — a guia escreve o
    valor de jornada sem nome e rotula só a exceção (ex.: Cloro, '* 240L ...
    STEL: 30L'). Rótulo sem número é qualificador, não regime ('(VAPORES E
    MISTURAS)' colado no volume TWA do peróxido de hidrogênio).
    """
    t = str(texto or '')
    if not t.strip():
        return []
    partes = _RE_ROTULO.split(t)           # [pré, rótulo, corpo, rótulo, corpo…]
    crus = [(_rotulo_canon(partes[i]), partes[i + 1])
            for i in range(1, len(partes) - 1, 2)]
    if not crus:
        return [{'regime': '', 'texto': _limpar(_RE_DURACAO.sub(' ', t)) or t.strip(),
                 'minutos': _duracao(t)}]
    if _numeros(_RE_DURACAO.sub(' ', partes[0])):
        crus.insert(0, ('TWA', partes[0]))
    out = []
    for regime, corpo in crus:
        limpo = _RE_DURACAO.sub(' ', corpo)
        if not _numeros(limpo):
            continue
        out.append({'regime': regime, 'minutos': _duracao(corpo),
                    'texto': _limpar(limpo)})
    return out


def _limpar(texto):
    """Texto de faixa apresentável: uma linha, sem os separadores da guia.

    Sem isso a tela mostrava '240L\\n(' — o parêntese sobra quando o
    qualificador '(VAPORES E MISTURAS)' é separado do valor do TWA.
    """
    t = re.sub(r'\s+', ' ', str(texto or '')).strip(' :|*()-–—')
    return re.sub(r'^[A-Z]?\)\s*:?\s*', '', t)     # 'STEL(C):2L/MIN' → '2L/MIN'


def _duracao(texto):
    m = _RE_DURACAO.search(str(texto or ''))
    return int(m.group(1)) if m else None


def regime_da_coleta(duracao_min, blocos):
    """Qual regime a coleta representa, pela DURAÇÃO (decisão de 03/08/2026).

    Vale a duração que a guia declara por regime ('TWA: 1L/MIN (240MIN)'); sem
    isso, ≤20 min = STEL e ≥120 min = TWA. Fora dessas pistas assume TWA, que é
    o padrão da guia — e a tela declara o regime assumido, para o técnico ver
    contra o que está sendo conferido.
    """
    rotulados = [b for b in blocos if b.get('regime')]
    if not rotulados:
        return ''
    if duracao_min:
        com_min = [b for b in rotulados if b.get('minutos')]
        if com_min:
            return min(com_min, key=lambda b: abs(b['minutos'] - duracao_min))['regime']
        alvo = 'STEL' if duracao_min <= 20 else ('TWA' if duracao_min >= 120 else '')
        for b in rotulados:
            if b['regime'] == alvo:
                return alvo
    for b in rotulados:
        if b['regime'] == 'TWA':
            return 'TWA'
    return rotulados[0]['regime']


def _bloco_do_regime(blocos, regime):
    """O bloco do regime pedido; senão o sem rótulo; senão o primeiro."""
    if not blocos:
        return None
    if regime:
        for b in blocos:
            if b['regime'] == regime:
                return b
    for b in blocos:
        if not b['regime']:
            return b
    return blocos[0]


def passivo(metodo):
    """Método passivo (crachá OVM): sem bomba, a guia grava vazão e volume '0'.

    77 dos 512 métodos são assim. Conferir vazão contra '0' reprovava qualquer
    coleta; aqui vazão e volume simplesmente não se aplicam.
    """
    def _zero(x):
        return str(x or '').strip().replace(',', '.') in ('', '0', '0.0')
    return bool(metodo) and _zero(metodo.get('vazao')) and _zero(metodo.get('volume'))


def minutos_entre(hora_ini, hora_fim):
    """Duração em minutos. Vira o dia quando o fim é menor que o início."""
    def _p(x):
        m = re.match(r'^\s*(\d{1,2})[:h](\d{2})', str(x or ''))
        if not m:
            return None
        h, mi = int(m.group(1)), int(m.group(2))
        return h * 60 + mi if 0 <= h <= 23 and 0 <= mi <= 59 else None
    a, b = _p(hora_ini), _p(hora_fim)
    if a is None or b is None:
        return None
    dur = b - a
    if dur < 0:
        dur += 24 * 60
    return dur or None


def _dentro(valor, lo, hi):
    """None em lo/hi = limite não declarado, não reprova."""
    if valor is None:
        return None
    if lo is not None and valor < lo:
        return False
    if hi is not None and valor > hi:
        return False
    return True


def _avaliar(campo, valor, unidade, bloco):
    """Confere um valor contra o bloco de regime escolhido.

    `ok=None` = sem como conferir. `tolerancia=True` = fora do valor nominal mas
    dentro de ±5% — desvio de calibração de bomba, atenção e não reprovação.
    """
    texto = (bloco or {}).get('texto') or ''
    lo, hi = faixa(texto)
    item = {'campo': campo, 'valor': valor, 'unidade': unidade,
            'min': lo, 'max': hi, 'faixa_texto': texto,
            'regime': (bloco or {}).get('regime') or '',
            'ok': _dentro(valor, lo, hi), 'tolerancia': False, 'desvio': None}
    if item['ok'] is False and valor and nominal(texto):
        alvo = lo if lo else hi
        if alvo:
            desvio = abs(valor - alvo) / alvo
            item['desvio'] = round(desvio, 4)
            if desvio <= TOLERANCIA_NOMINAL:
                item['ok'], item['tolerancia'] = True, True
    return item


def validar_coleta(metodo, vazao=None, volume=None, tipo_amostrador=None,
                   tempo_min=None, hora_inicio=None, hora_final=None):
    """Confere uma coleta contra UM método da guia, no regime da duração dela.

    `volume` é usado se vier; senão sai de vazão × duração (`volume_l` e
    `tempo_min` estão zerados em 100% dos registros, mas as horas são gravadas).

    Devolve dict com veredicto, problemas (reprovam), avisos (não reprovam) e os
    valores conferidos — para a tela mostrar o porquê, não só a cor.
    """
    if not metodo:
        return {'veredicto': SEM_METODO, 'problemas': [], 'avisos': [],
                'metodo_cod': '', 'regime': '', 'passivo': False, 'itens': []}

    dur = tempo_min if (tempo_min or 0) > 0 else minutos_entre(hora_inicio, hora_final)
    if volume in (None, 0, '') and vazao and dur:
        try:
            volume = round(float(vazao) * float(dur), 4)
        except (TypeError, ValueError):
            volume = None

    try:
        vz = float(vazao) if vazao not in (None, '') else None
    except (TypeError, ValueError):
        vz = None
    try:
        vl = float(volume) if volume not in (None, '') else None
    except (TypeError, ValueError):
        vl = None

    eh_passivo = passivo(metodo)
    bl_v = [] if eh_passivo else blocos_regime(metodo.get('vazao'))
    bl_l = [] if eh_passivo else blocos_regime(metodo.get('volume'))
    # O regime sai da vazão (é onde a guia declara a duração de cada um) e vale
    # também para o volume: conferir vazão de STEL contra volume de TWA não faz
    # sentido físico.
    regime = regime_da_coleta(dur, bl_v) or regime_da_coleta(dur, bl_l)

    itens, problemas, avisos = [], [], []

    for campo, valor, unidade, blocos in (('vazão', vz, 'L/min', bl_v),
                                          ('volume', vl, 'L', bl_l)):
        if eh_passivo:
            itens.append({'campo': campo, 'valor': valor, 'unidade': unidade,
                          'min': None, 'max': None, 'ok': None,
                          'tolerancia': False, 'desvio': None, 'regime': '',
                          'faixa_texto': 'não se aplica (método passivo)'})
            continue
        item = _avaliar(campo, valor, unidade, _bloco_do_regime(blocos, regime))
        itens.append(item)
        alvo = f"{item['faixa_texto'] or '—'}"
        if item['regime']:
            alvo += f" ({item['regime']})"
        if item['ok'] is False:
            problemas.append(f"{campo} {_fmt(valor)} {unidade} fora de {alvo}")
        elif item['tolerancia']:
            avisos.append(f"{campo} {_fmt(valor)} {unidade} é "
                          f"{_pct(item['desvio'])} do nominal {alvo} — "
                          f"dentro dos {int(TOLERANCIA_NOMINAL * 100)}% de calibração")

    ok_t = _tipo_confere(tipo_amostrador, metodo.get('amostradorCod'))
    tipos_guia = _tipos_do_metodo(metodo.get('amostradorCod'))
    itens.append({'campo': 'amostrador', 'valor': tipo_amostrador or '',
                  'esperado': ', '.join(tipos_guia), 'ok': ok_t,
                  'tolerancia': False, 'desvio': None, 'regime': ''})
    if ok_t is False:
        problemas.append(
            f"amostrador {tipo_amostrador} não é o do método ({', '.join(tipos_guia)})")

    if problemas:
        ver = FORA
    elif all(i.get('ok') is None for i in itens):
        ver = SEM_DADO
    elif avisos:
        ver = ATENCAO
    else:
        ver = OK
    return {'veredicto': ver, 'problemas': problemas, 'avisos': avisos,
            'metodo_cod': metodo.get('metodoCod') or '',
            'metodo_desc': metodo.get('metodoDesc') or '',
            'regime': regime, 'passivo': eh_passivo,
            'volume_calculado': vl, 'duracao_min': dur, 'itens': itens}


def _fmt(v):
    if v is None:
        return '—'
    s = f'{float(v):.4f}'.rstrip('0').rstrip('.')
    return s.replace('.', ',')


def _pct(desvio):
    if not desvio:
        return '0%'
    return f"{desvio * 100:.1f}".replace('.', ',') + '% acima'


def _tipos_do_metodo(amostrador_cod):
    """Siglas de tipo em 'SKC 226-01 (TCP*****)' → ['TCP']."""
    if not amostrador_cod:
        return []
    s = str(amostrador_cod).upper()
    tipos = {m for m in re.findall(r'\(([A-Z][A-Z0-9]+)\*+\)', s)}
    if not tipos:
        for parte in re.split(r'\s+E\s+|\s*,\s*|\s*/\s*|\s+OU\s+', s):
            p = parte.strip()
            if re.fullmatch(r'[A-Z]{2,5}\d?', p):
                tipos.add(p)
    return sorted(tipos)


def _tipo_confere(tipo_usado, amostrador_cod):
    """None quando não há como comparar — não reprova por falta de dado.

    O campo `tipo_amostrador` da coleta às vezes guarda o texto completo do
    método ('SKC 225-5 (EC*****)') e às vezes só a sigla ('TCP'); comparar
    direto dava falso positivo, então procura a sigla dentro do texto.
    """
    tipos = _tipos_do_metodo(amostrador_cod)
    usado = str(tipo_usado or '').upper().strip()
    if not tipos or not usado:
        return None
    return any(re.search(r'(?<![A-Z])' + re.escape(t) + r'(?![A-Z])', usado)
               for t in tipos)


def chave_metodo(m):
    """Identidade do método na guia. metodoCod sozinho repete (Benzeno tem duas
    entradas 'PASSIVO SKC', de nomes diferentes) — o amostrador desempata."""
    if not m:
        return ''
    return f"{m.get('metodoCod') or ''}|{m.get('amostradorCod') or ''}"


def melhor_metodo(metodos, tipo_amostrador=None):
    """Escolhe o método a aplicar quando o agente tem vários.

    Prefere o compatível com o amostrador que foi usado de fato — senão a
    conferência reprovaria por comparar com o método errado.
    """
    if not metodos:
        return None
    if tipo_amostrador:
        for m in metodos:
            if _tipo_confere(tipo_amostrador, m.get('amostradorCod')):
                return m
    return metodos[0]


def escolher_metodo(metodos, chave=None, tipo_amostrador=None):
    """O método que o TÉCNICO escolheu na tela; sem escolha, a pré-seleção.

    116 dos 374 agentes têm mais de um método. Antes caía calado no primeiro da
    lista e conferia contra um método que ninguém escolheu.
    """
    if chave:
        for m in metodos or []:
            if chave_metodo(m) == chave:
                return m
    return melhor_metodo(metodos, tipo_amostrador)


def escolha_incerta(metodos, tipo_amostrador=None):
    """True quando há mais de um método e o amostrador usado não decide qual.

    Não reprova — só chama o técnico para confirmar no select, em vez de a tela
    exibir um método escolhido no escuro.
    """
    if not metodos or len(metodos) < 2:
        return False
    casam = [m for m in metodos if _tipo_confere(tipo_amostrador, m.get('amostradorCod'))]
    return len(casam) != 1
