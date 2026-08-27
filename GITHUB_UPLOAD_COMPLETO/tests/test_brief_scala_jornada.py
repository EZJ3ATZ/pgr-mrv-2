# -*- coding: utf-8 -*-
"""Brief & Scala pela jornada REAL (pedido do Bernardo, 26/08/2026).

Antes de 27/08/2026 o fator era **0,88 chumbado** — a conta de 44 h semanais —
aplicado a toda avaliação, e o laudo assinado declarava "jornada de 44 horas
semanais" mesmo para empresa em escala 12x36. Numa jornada de 12 h o modelo
diário dá 0,50: o documento podia assinar REGULAR uma exposição acima do limite
corrigido.

O que estes testes travam:
  1. a fórmula (semanal e diária) bate com a conta feita à mão;
  2. o campo livre de jornada é lido nos formatos que a tela produz;
  3. 🔴 **a mudança nunca afrouxa o limite** — sem jornada SEMANAL declarada, o
     fator jamais passa de 0,88. Sem essa trava, `08:00` (o valor mais comum do
     campo) daria FR = 1 e o laudo sairia menos restritivo do que era antes;
  4. jornada ilegível repete o comportamento antigo;
  5. a tela e o laudo continuam usando a MESMA conta.
"""
import pytest

from app import (BS_FATOR_PADRAO, _classificar_quimico, _fator_brief_scala,
                 _jornada_horas, app)


# ── 1. a fórmula ──────────────────────────────────────────────────────

def test_formula_semanal_bate_com_a_conta_a_mao():
    # FR = (40/Hsr) × ((168 − Hsr)/128)
    fr, _, _ = _fator_brief_scala('44 horas semanais')
    assert fr == pytest.approx((40 / 44) * ((168 - 44) / 128), abs=1e-6)
    assert fr == pytest.approx(0.8807, abs=1e-4)


def test_formula_diaria_bate_com_a_conta_a_mao():
    # FR = (8/Hd) × ((24 − Hd)/16). Escala 12x36 → 0,50, o caso que motivou tudo.
    fr, _, _ = _fator_brief_scala('12x36')
    assert fr == pytest.approx((8 / 12) * ((24 - 12) / 16), abs=1e-6)
    assert fr == pytest.approx(0.50, abs=1e-6)


def test_jornada_de_12h_derruba_o_limite_pela_metade():
    """O caso concreto: LT-TWA 20 ppm. Com 0,88 o corrigido era 17,6 e uma
    concentração de 15 passava. Com a jornada real de 12 h o corrigido é 10,0 e
    a mesma medição reprova."""
    ev = {'concentracao': '15', 'ltNR15': '78', 'ltTWA': '20 ppm'}
    antes = _classificar_quimico(dict(ev))                      # sem jornada → 0,88
    depois = _classificar_quimico(dict(ev, jornada='12x36'))
    assert antes['acgih'][0] == pytest.approx(17.6, abs=1e-3)
    assert antes['acgih'][2] is True                            # passava
    assert depois['acgih'][0] == pytest.approx(10.0, abs=1e-3)
    assert depois['acgih'][2] is False                          # reprova
    assert depois['ok_geral'] is False


# ── 2. leitura do campo livre ─────────────────────────────────────────

@pytest.mark.parametrize('txt,horas,base', [
    ('09:00H',            9.0,  'dia'),      # o padrão que a tela grava
    ('08:00',             8.0,  'dia'),
    ('12x36',            12.0,  'dia'),
    ('12 x 36',          12.0,  'dia'),
    ('8h/dia',            8.0,  'dia'),
    ('10 horas',         10.0,  'dia'),      # número baixo sem palavra → dia
    ('44h semanais',     44.0,  'semana'),
    ('44 h/sem',         44.0,  'semana'),
    ('36 horas semanais', 36.0, 'semana'),
    ('44',               44.0,  'semana'),   # número alto sem palavra → semana
    ('24x48',            56.0,  'semana'),   # plantão: 24 h a cada 72 h
])
def test_le_os_formatos_que_a_tela_produz(txt, horas, base):
    h, b = _jornada_horas(txt)
    assert (h, b) == (pytest.approx(horas, abs=1e-6), base)


@pytest.mark.parametrize('txt', [
    '',                  # em branco
    '   ',
    'turno A',           # sem número
    '6x1',               # escala de DIAS, não diz carga horária
    '5x2',
    '08:00 às 17:00',    # FAIXA de horário: não dá para descontar intervalo
    '07:00 as 16:00',
])
def test_nao_chuta_quando_nao_da_para_ler(txt):
    assert _jornada_horas(txt) == (None, None)


# ── 3. 🔴 a trava: nunca afrouxar ─────────────────────────────────────

@pytest.mark.parametrize('txt', [
    '', 'turno A', '6x1', '08:00 às 17:00',           # ilegíveis
    '08:00', '8h/dia', '07:00', '06:00', '09:00H',    # jornada diária normal/curta
    '12x36', '10 horas',                              # estendidas
])
def test_sem_jornada_semanal_declarada_o_fator_nunca_passa_de_088(txt):
    """A regressão que esta trava impede: o modelo diário dá FR > 1 para turno de
    8 h ou menos. Aplicar isso relaxaria o Limite de Tolerância acima do que o
    gerador vinha assinando — e em documento assinado, calado."""
    fr, rotulo, _ = _fator_brief_scala(txt)
    assert fr <= BS_FATOR_PADRAO + 1e-9, f'{txt!r} afrouxou o limite: FR={fr} ({rotulo})'


def test_jornada_semanal_curta_declarada_dispensa_a_reducao():
    """36 h/semana é MENOS que as 40 h da ACGIH: não há jornada estendida para
    corrigir. Aqui a informação é explícita sobre a semana, então o teto de 1,0
    vale e o limite fica o da própria ACGIH."""
    fr, rotulo, leu = _fator_brief_scala('36 horas semanais')
    assert fr == 1.0 and leu is True
    assert 'sem redução aplicável' in rotulo


def test_o_fator_nunca_sai_da_faixa_util():
    for txt in ['', '12x36', '24x48', '44 horas semanais', '16 horas', '23 horas',
                '167 horas semanais', '0 horas', 'turno A', '09:00H']:
        fr, _, _ = _fator_brief_scala(txt)
        assert 0 < fr <= 1.0, f'{txt!r} produziu fator fora da faixa: {fr}'


# ── 4. fallback ───────────────────────────────────────────────────────

def test_jornada_ilegivel_repete_o_comportamento_antigo():
    fr, rotulo, leu = _fator_brief_scala('turno A')
    assert fr == BS_FATOR_PADRAO
    assert leu is False
    assert '44 horas semanais' in rotulo and 'padrão' in rotulo


def test_avaliacao_sem_campo_jornada_nao_quebra():
    r = _classificar_quimico({'concentracao': '18,5', 'ltNR15': '78', 'ltTWA': '20'})
    assert r['acgih'][0] == pytest.approx(17.6, abs=1e-3)
    assert r['bs']['fator'] == BS_FATOR_PADRAO and r['bs']['leu_jornada'] is False


# ── 5. tela e laudo dividem a conta ───────────────────────────────────

def test_a_tela_recebe_o_mesmo_veredicto_do_laudo():
    """A grade do lab manda `jornada` para /quimico/classificar desde 27/08/2026.
    Sem isso o backend caía no padrão e o badge discordava do documento em 12x36."""
    app.config['TESTING'] = True
    app.config['LOGIN_DISABLED'] = True
    c = app.test_client()
    ev = {'concentracao': '15 ppm', 'ltNR15': '78 ppm', 'ltTWA': '20 ppm',
          'jornada': '12x36'}
    r = c.post('/quimico/classificar', json={'avaliacoes': [ev]})
    assert r.status_code == 200
    (tela,) = r.get_json()['resultados']
    laudo = _classificar_quimico(ev)
    assert tela['txt'] == ('REGULAR' if laudo['ok_geral'] else 'IRREGULAR')
    assert tela['txt'] == 'IRREGULAR'      # a mesma medição passava com 0,88


def test_o_laudo_declara_a_jornada_que_usou():
    """O documento é assinado: tem de dizer de qual jornada saiu o limite, em vez
    de afirmar 44 h semanais para qualquer empresa."""
    _, rot12, _ = _fator_brief_scala('12x36')
    _, rot44, _ = _fator_brief_scala('44 horas semanais')
    assert '12 horas diárias' in rot12
    assert '44 horas semanais' in rot44
    assert rot12 != rot44
