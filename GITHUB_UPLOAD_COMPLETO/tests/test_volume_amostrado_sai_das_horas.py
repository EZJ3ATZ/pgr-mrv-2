# -*- coding: utf-8 -*-
"""`tempo_min` e `volume_l` param de nascer zerados na coleta química.

Medido no banco de produção em 08/09/2026: **39 de 39** linhas de
`coletas_quimico_amostr` com `tempo_min = 0` e `volume_l = 0`, enquanto
`hora_inicio`, `hora_final` e `vazao_media` estavam preenchidos nas 39.

Causa: `nvCqRenderAmostrTable` (templates/index.html) calcula `t` e
`vol = Vm × t` em variável local dentro do `map` que desenha a tabela. O
número aparece na célula e nunca é escrito no objeto que sobe, então o backend
recebia `tempo_min` ausente e gravava 0 — e `volume_L = vazão × 0`.

Por que importa: o volume é justamente o número que o guia de métodos cobra
(a sílica pede 400 a 1000 L, foi a reclamação do Helbert em 08/09). A cadeia de
custódia e o `validar_coleta` contornavam recalculando de vazão × horas, com
comentário no código dizendo que a coluna estava zerada; quem lesse a coluna
crua num relatório novo receberia zero sem desconfiar.
"""
from controle.db import get_db, init_db, save_coleta_quimico, _minutos_amostrados


def _seed():
    init_db()
    with get_db() as conn:
        cur = conn.execute("INSERT INTO empresas (nome) VALUES ('ZVOLUME TESTE')")
        eid = cur.lastrowid
        cur = conn.execute(
            "INSERT INTO demandas (empresa_id, numero_os, status) "
            "VALUES (?, 'ZOS-VOL-1', 'em_andamento')", (eid,))
        return cur.lastrowid


def _gravar(dem, **am):
    base = {'id_amostrador': 'ZVOL01', 'tipo_amostrador': 'PVC',
            'vazao_inicial': 2.5, 'vazao_final': 2.5}
    base.update(am)
    cid = save_coleta_quimico({
        'demanda_id': dem, 'data_coleta': '2026-09-08',
        'substancias': 'Sílica Cristalina', 'nome_funcionario': 'Fulano',
        'status': 'concluida', 'amostradores': [base],
    })
    with get_db() as conn:
        r = conn.execute(
            'SELECT tempo_min, volume_L, vazao_media FROM coletas_quimico_amostr '
            'WHERE coleta_id=? ORDER BY seq LIMIT 1', (cid,)).fetchone()
    d = dict(r) if hasattr(r, 'keys') else {'tempo_min': r[0], 'volume_L': r[1],
                                            'vazao_media': r[2]}
    return d


def test_volume_sai_das_horas_quando_a_tela_nao_manda_o_tempo():
    """O caso real: a tela manda hora e vazão, não manda tempo_min."""
    dem = _seed()
    d = _gravar(dem, hora_inicio='09:10', hora_final='11:50')
    assert d['tempo_min'] == 160, d          # 2h40
    assert round(d['volume_L'], 1) == 400.0, d   # 2,5 L/min x 160 min


def test_intervalo_e_descontado_igual_a_tela():
    """A tela desconta 'hh:mm' separados por vírgula ou ponto e vírgula."""
    dem = _seed()
    d = _gravar(dem, hora_inicio='08:00', hora_final='12:00', intervalos='0:30')
    assert d['tempo_min'] == 210, d          # 240 - 30
    d = _gravar(dem, hora_inicio='08:00', hora_final='12:00', intervalos='0:15;0:15')
    assert d['tempo_min'] == 210, d


def test_tempo_que_o_cliente_manda_continua_vencendo():
    """Aqui é rede de segurança, não substituição do que a tela calculou."""
    dem = _seed()
    d = _gravar(dem, hora_inicio='09:10', hora_final='11:50', tempo_min=120)
    assert d['tempo_min'] == 120, d
    assert round(d['volume_L'], 1) == 300.0, d


def test_sem_hora_nenhuma_fica_zero_e_nao_inventa():
    dem = _seed()
    d = _gravar(dem, hora_inicio='', hora_final='')
    assert d['tempo_min'] == 0, d
    assert d['volume_L'] == 0, d


def test_amostragem_que_atravessa_a_meia_noite():
    """`minutos_entre` já vira o dia; aqui só garanto que a coleta usa isso."""
    assert _minutos_amostrados(
        {'hora_inicio': '22:00', 'hora_final': '02:00'}) == 240


def test_hora_invertida_nao_gera_tempo_negativo():
    """Intervalo maior que a jornada não pode virar volume negativo."""
    assert _minutos_amostrados(
        {'hora_inicio': '08:00', 'hora_final': '09:00', 'intervalos': '2:00'}) == 0
