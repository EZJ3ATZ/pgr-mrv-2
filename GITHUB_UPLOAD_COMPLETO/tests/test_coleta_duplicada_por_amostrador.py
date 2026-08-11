# -*- coding: utf-8 -*-
"""_coleta_duplicada: dois trabalhadores no MESMO agente, no mesmo dia, é rotina.

Caso real (Wesley, 11/08/2026 — LPC Construções, OS 6549851, visita de 10/08):
o planejamento tinha 6 tubos de "Poeira Respirável + Sílica LivreCristalina" em
6 funcionários diferentes. A trava de duplicidade olhava só OS + data +
substância, então as coletas 2..6 foram RECUSADAS: os amostradores nunca saíram
do estoque, a cadeia de custódia não tinha o que puxar e o técnico via
"✅ Medição salva!". No banco de produção sobrou 1 coleta de 6.

Regra correta: o discriminador é o AMOSTRADOR (identifica fisicamente a
amostra); sem ele, o funcionário; sem os dois, mantém a trava antiga.
"""
from controle import routes as routes_mod
from controle.db import get_db, init_db, save_coleta_quimico


def _seed_demanda(os_num):
    init_db()
    with get_db() as conn:
        conn.execute("DELETE FROM demandas WHERE numero_os=?", (os_num,))
        emp = conn.execute("INSERT INTO empresas (nome) VALUES ('LPC TESTE')").lastrowid
        cur = conn.execute(
            "INSERT INTO demandas (titulo, numero_os, status, empresa_id) "
            "VALUES ('OS dup', ?, 'aberta', ?)", (os_num, emp))
        return cur.lastrowid


POEIRA = 'Poeira Respirável + Sílica LivreCristalina'


def _gravar(dem, func, cod, data='2026-08-10', subst=POEIRA):
    return save_coleta_quimico({
        'demanda_id': dem, 'data_coleta': data, 'substancias': subst,
        'nome_funcionario': func, 'status': 'concluida',
        'amostradores': [{'id_amostrador': cod, 'tipo_amostrador': 'PVC'}],
    })


def _dup(dem, func, cod, data='2026-08-10', subst=POEIRA):
    return routes_mod._coleta_duplicada(
        'quimico', dem, data, subst, func,
        [{'id_amostrador': cod, 'tipo_amostrador': 'PVC'}] if cod else [])


# ── o bug do Wesley ───────────────────────────────────────────────────

def test_mesmo_agente_em_outro_funcionario_e_outro_tubo_nao_e_duplicada():
    dem = _seed_demanda('DUP1')
    _gravar(dem, 'Noé Costa', 'PVC12V96')
    assert _dup(dem, 'José da Silva', 'PVC24V96') is False


def test_os_seis_tubos_de_poeira_da_lpc_entram_todos():
    dem = _seed_demanda('DUP2')
    tubos = [('Noé Costa', 'PVC12V96'), ('Meio Oficial', 'PVC24V96'),
             ('Op. Guincho', 'PVC21V96'), ('Pedreiro I', 'PVC28V96'),
             ('Op. Betoneira', 'PVC07V96'), ('Armador', 'PVC30V96')]
    for func, cod in tubos:
        assert _dup(dem, func, cod) is False, f'{cod} recusado indevidamente'
        _gravar(dem, func, cod)
    with get_db() as conn:
        n = conn.execute("SELECT COUNT(*) c FROM coletas_quimico WHERE demanda_id=?",
                         (dem,)).fetchone()
    assert (n['c'] if hasattr(n, 'keys') else n[0]) == 6


# ── a trava que tem de continuar de pé ────────────────────────────────

def test_mesmo_tubo_de_novo_e_duplicada():
    dem = _seed_demanda('DUP3')
    _gravar(dem, 'Noé Costa', 'PVC12V96')
    assert _dup(dem, 'Noé Costa', 'PVC12V96') is True


def test_mesmo_tubo_com_grafia_diferente_e_duplicada():
    dem = _seed_demanda('DUP4')
    _gravar(dem, 'Noé Costa', 'PVC12V96')
    assert _dup(dem, 'outro nome qualquer', ' pvc12v96 ') is True


def test_sem_amostrador_dos_dois_lados_o_funcionario_decide():
    dem = _seed_demanda('DUP5')
    _gravar(dem, 'Noé Costa', '')
    assert _dup(dem, 'noé  costa', '') is True     # mesmo nome → duplicada
    assert _dup(dem, 'José da Silva', '') is False  # nome diferente → passa


def test_sem_amostrador_e_sem_nome_mantem_a_trava_antiga():
    dem = _seed_demanda('DUP6')
    _gravar(dem, '', '')
    assert _dup(dem, '', '') is True


def test_substancia_diferente_nunca_foi_duplicada():
    dem = _seed_demanda('DUP7')
    _gravar(dem, 'Noé Costa', 'PVC12V96')
    assert _dup(dem, 'Noé Costa', 'EC98028A', subst='Ferro, Óxido (Fe2O3)') is False


def test_outra_data_nao_e_duplicada():
    dem = _seed_demanda('DUP8')
    _gravar(dem, 'Noé Costa', 'PVC12V96')
    assert _dup(dem, 'Noé Costa', 'PVC12V96', data='2026-08-11') is False


def test_sem_os_nao_bloqueia():
    assert routes_mod._coleta_duplicada('quimico', None, '2026-08-10', POEIRA,
                                        'Noé Costa', []) is False


# ── ruído e vibração seguem 1 planilha por visita ─────────────────────

def test_ruido_continua_uma_por_visita():
    dem = _seed_demanda('DUP9')
    from controle.db import save_coleta_ruido
    save_coleta_ruido({'demanda_id': dem, 'data_coleta': '2026-08-10',
                       'status': 'concluida'})
    assert routes_mod._coleta_duplicada('ruido', dem, '2026-08-10') is True


def test_vibracao_continua_uma_por_visita():
    dem = _seed_demanda('DUP10')
    from controle.db import save_coleta_outros
    save_coleta_outros({'tipo': 'vibracao', 'demanda_id': dem,
                        'data_coleta': '2026-08-10', 'status': 'concluida'})
    assert routes_mod._coleta_duplicada('vibracao', dem, '2026-08-10') is True
    assert routes_mod._coleta_duplicada('calor', dem, '2026-08-10') is False
