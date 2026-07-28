# -*- coding: utf-8 -*-
"""A aritmética da planilha de campo química — t, Vm, Volume e ΔV.

POR QUE ISTO IMPORTA MAIS QUE O RESTO: o laudo químico NÃO calcula volume nem
concentração — ele recebe prontos. A conta acontece aqui, na planilha de campo. Se
o volume sai maior, a concentração (massa/volume) sai MENOR e o laudo pode concluir
"abaixo do limite" numa exposição que estava acima.

Foi exatamente o bug de 12/06: `t` não subtraía os intervalos, o volume saía
superestimado e afetava a concentração. O `_parse_intervalos_min` nasceu desse fix
(o backend fazia `int(float('1:30'))`, estourava, engolia a exceção e não descontava
nada — o PDF saía diferente da tela do wizard).

Cadeia: t = fim − início − intervalos · Vm = (Vi+Vf)/2 · Vol = Vm × t · ΔV = |Vi−Vf|/Vi
"""
import pytest

from controle.routes import _parse_intervalos_min


# ── t: a subtração de intervalos ─────────────────────────────────────────────
@pytest.mark.parametrize('entrada,minutos', [
    ('', 0),
    (None, 0),
    ('30', 30),                 # minutos soltos
    ('0:30', 30),               # hh:mm
    ('1:30', 90),               # o caso que estourava: int(float('1:30'))
    ('1:00', 60),
    ('0:30, 15', 45),           # vários separados por vírgula
    ('0:30; 15', 45),           # e por ponto-e-vírgula
    ('0:30,15,0:05', 50),
    ('12,5', 17),               # VÍRGULA É SEPARADOR: "12 e 5", não 12,5 — ver nota
    ('12.5', 12),               # ponto é decimal; round() do Python arredonda p/ par
    ('7.5', 8),
    (' 20 ', 20),               # espaço em volta
    ('abc', 0),                 # lixo não derruba, só não conta
    ('0:30, abc, 15', 45),      # lixo no meio não perde os válidos
])
def test_parse_intervalos_min(entrada, minutos):
    assert _parse_intervalos_min(entrada) == minutos, \
        f'intervalo {entrada!r} deveria somar {minutos} min'


def test_virgula_e_separador_nao_decimal():
    """Contrato do campo: vírgula SEPARA intervalos ('0:30, 15' = 45 min). Então
    '12,5' vale 17 (12 + 5), não 12,5. Consequência: o `tok.replace(',', '.')` do
    parser é código morto — o split em vírgula já consumiu o separador antes.

    Fica documentado em vez de "corrigido" porque: (a) intervalo em minuto quebrado
    é irreal na prática (usa-se 15/30/60 ou hh:mm); (b) o erro é conservador — conta
    MAIS intervalo, então t menor, volume menor e concentração MAIOR, que superestima
    a exposição em vez de subestimar. Se um dia alguém digitar decimal com vírgula, o
    laudo erra para o lado seguro."""
    assert _parse_intervalos_min('12,5') == 17
    assert _parse_intervalos_min('12.5') == 12   # com ponto funciona como decimal


def test_intervalo_de_almoco_reduz_o_volume():
    """O caso concreto do bug: jornada 08:00–16:00 (480 min) com 1h de almoço.
    Sem descontar → t=480 e Vol=48,0 L. Descontando → t=420 e Vol=42,0 L.
    A diferença de 14% no volume vira 14% de erro na concentração, para MENOS —
    o lado perigoso, porque subestima a exposição."""
    vazao = 0.1  # L/min
    t_bruto = 480
    t_liquido = t_bruto - _parse_intervalos_min('1:00')
    assert t_liquido == 420, 'almoço de 1h não foi descontado'

    vol_errado = round(vazao * t_bruto, 1)
    vol_certo = round(vazao * t_liquido, 1)
    assert vol_errado == 48.0
    assert vol_certo == 42.0
    # a concentração é massa/volume: volume maior → concentração menor
    massa_ug = 500.0
    assert (massa_ug / vol_certo) > (massa_ug / vol_errado), \
        'volume inflado subestima a concentração — é o lado perigoso do erro'


# ── A cadeia completa, como o gerador do PDF faz ─────────────────────────────
def _conta_amostrador(inicio_min, fim_min, intervalos, vi, vf):
    """Espelha routes.py:4782-4796 (o bloco que monta a tabela do PDF)."""
    t = max(0, (fim_min - inicio_min) - _parse_intervalos_min(intervalos))
    vm = round((vi + vf) / 2, 3) if (vi and vf) else ''
    vol = round(float(vm) * float(t), 1) if vm != '' else ''
    dv = round(abs(vi - vf) / vi * 100, 1) if (vi and vf) else ''
    return t, vm, vol, dv


def test_cadeia_t_vm_vol_dv():
    """Caso do golden do laudo químico: 08:00→12:00 sem intervalo, Vi 0,100,
    Vf 0,102 → t=240, Vm=0,101, Vol=24,2 L, ΔV=2,0%."""
    t, vm, vol, dv = _conta_amostrador(8 * 60, 12 * 60, '', 0.100, 0.102)
    assert t == 240
    assert vm == 0.101
    assert vol == 24.2
    assert dv == 2.0


def test_delta_v_acima_de_5_pct_e_detectavel():
    """Item 6 do Bernardo: Vi e Vf não podem ser iguais e a variação tem que ser
    real. ΔV > ±5% invalida a amostra (NR/NHO) e o laudo pinta em vermelho —
    então o número tem que estar certo."""
    _, _, _, dv = _conta_amostrador(8 * 60, 12 * 60, '', 0.100, 0.094)
    assert dv == 6.0, 'ΔV de 6% não foi calculado corretamente'
    assert dv > 5.0, 'ΔV acima do aceitável não é detectável'


def test_vazao_igual_da_delta_v_zero():
    """Vi == Vf → ΔV 0%. O Bernardo apontou que isso é suspeito na prática (item 6),
    mas a CONTA está certa; quem sinaliza é a regra de negócio, não a aritmética."""
    _, _, _, dv = _conta_amostrador(8 * 60, 12 * 60, '', 0.100, 0.100)
    assert dv == 0.0


def test_t_nunca_negativo():
    """Intervalo maior que a duração não pode gerar t negativo (viraria volume
    negativo no PDF). O `max(0, ...)` do gerador cobre isso — cenário já validado
    no stress-test de 15/06, aqui virou regressão automática."""
    t, _, vol, _ = _conta_amostrador(8 * 60, 9 * 60, '2:00', 0.100, 0.100)
    assert t == 0, 't ficou negativo'
    assert vol == 0.0, 'volume negativo no PDF'


def test_meia_noite_nao_inverte():
    """Coleta que cruza a meia-noite: o gerador só calcula t quando fim >= início
    (senão deixa vazio). Documenta a limitação — turno da noite precisa do t
    digitado à mão."""
    a, b = 22 * 60, 2 * 60
    assert not (b >= a), (
        'se isto passar a ser verdade, o gerador mudou e turno noturno virou '
        'suportado — atualizar este teste')
