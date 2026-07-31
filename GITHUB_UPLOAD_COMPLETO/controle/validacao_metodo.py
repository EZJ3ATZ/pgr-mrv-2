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

A guia (`guia_metodos.json`, 374 agentes) traz por método: faixa de vazão, faixa
de volume, código do método e o tipo de amostrador compatível.
"""
import re
import logging

log = logging.getLogger(__name__)

# Veredictos possíveis
OK = 'ok'                # dentro do método
FORA = 'fora'            # algum parâmetro fora da faixa
SEM_METODO = 'sem_metodo'  # agente não está na guia (374 de 468 do catálogo do lab)
SEM_DADO = 'sem_dado'    # falta vazão/tempo para conferir


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
    nums = [n for n in (num_br(x) for x in re.findall(r'\d+(?:[.,]\d+)*', t))
            if n is not None]
    if not nums:
        return None, None
    if re.search(r'M[ÁA]XIMO|AT[ÉE]', t, re.I) and len(nums) == 1:
        return 0.0, nums[0]
    if re.search(r'M[ÍI]NIMO', t, re.I) and len(nums) == 1:
        return nums[0], None
    if len(nums) == 1:
        return nums[0], nums[0]
    return min(nums), max(nums)


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


def validar_coleta(metodo, vazao=None, volume=None, tipo_amostrador=None,
                   tempo_min=None, hora_inicio=None, hora_final=None):
    """Confere uma coleta contra UM método da guia.

    `volume` é usado se vier; senão sai de vazão × duração (`volume_l` e
    `tempo_min` estão zerados em 100% dos registros, mas as horas são gravadas).

    Devolve dict com veredicto, lista de problemas e os valores conferidos —
    para a tela mostrar o porquê, não só a cor.
    """
    if not metodo:
        return {'veredicto': SEM_METODO, 'problemas': [],
                'metodo_cod': '', 'itens': []}

    dur = tempo_min if (tempo_min or 0) > 0 else minutos_entre(hora_inicio, hora_final)
    if volume in (None, 0, '') and vazao and dur:
        try:
            volume = round(float(vazao) * float(dur), 4)
        except (TypeError, ValueError):
            volume = None

    lo_v, hi_v = faixa(metodo.get('vazao'))
    lo_l, hi_l = faixa(metodo.get('volume'))
    try:
        vz = float(vazao) if vazao not in (None, '') else None
    except (TypeError, ValueError):
        vz = None
    try:
        vl = float(volume) if volume not in (None, '') else None
    except (TypeError, ValueError):
        vl = None

    itens, problemas = [], []

    ok_v = _dentro(vz, lo_v, hi_v)
    itens.append({'campo': 'vazão', 'valor': vz, 'unidade': 'L/min',
                  'min': lo_v, 'max': hi_v, 'ok': ok_v,
                  'faixa_texto': metodo.get('vazao') or ''})
    if ok_v is False:
        problemas.append(
            f"vazão {_fmt(vz)} L/min fora de {metodo.get('vazao') or '—'}")

    ok_l = _dentro(vl, lo_l, hi_l)
    itens.append({'campo': 'volume', 'valor': vl, 'unidade': 'L',
                  'min': lo_l, 'max': hi_l, 'ok': ok_l,
                  'faixa_texto': metodo.get('volume') or ''})
    if ok_l is False:
        problemas.append(
            f"volume {_fmt(vl)} L fora de {metodo.get('volume') or '—'}")

    ok_t = _tipo_confere(tipo_amostrador, metodo.get('amostradorCod'))
    tipos_guia = _tipos_do_metodo(metodo.get('amostradorCod'))
    itens.append({'campo': 'amostrador', 'valor': tipo_amostrador or '',
                  'esperado': ', '.join(tipos_guia), 'ok': ok_t})
    if ok_t is False:
        problemas.append(
            f"amostrador {tipo_amostrador} não é o do método ({', '.join(tipos_guia)})")

    if problemas:
        ver = FORA
    elif all(i.get('ok') is None for i in itens):
        ver = SEM_DADO
    else:
        ver = OK
    return {'veredicto': ver, 'problemas': problemas,
            'metodo_cod': metodo.get('metodoCod') or '',
            'metodo_desc': metodo.get('metodoDesc') or '',
            'volume_calculado': vl, 'duracao_min': dur, 'itens': itens}


def _fmt(v):
    if v is None:
        return '—'
    s = f'{float(v):.4f}'.rstrip('0').rstrip('.')
    return s.replace('.', ',')


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
