# -*- coding: utf-8 -*-
"""Resultado do laboratório como dado + divergência PDF × digitado.

Até 05/08/2026 a concentração só existia dentro do .docx. Agora cada resultado é
uma linha por (amostrador, agente, fonte): 'pdf' é o laudo do laboratório,
'digitado' é o que o técnico lançou ao gerar o laudo.

Regra escolhida pelo Matheus: divergindo, **nenhum vence** — as duas linhas ficam e
abre-se uma divergência na Camada de Consistência para uma pessoa decidir.
"""
import pytest

from controle.db import get_db, init_db
from controle.resultado_lab import (TOLERANCIA, base_ra, divergem,
                                    do_laudo_extraido, gravar, gravar_muitos,
                                    listar, migrar_de_ra_laudos, por_amostrador,
                                    ras_do_amostrador, separar_fracao, separar_valor)


def _limpar():
    from controle.lab_inbox import _ensure_ra_laudos
    with get_db() as c:
        _ensure_ra_laudos(c)      # criada sob demanda pelo backfill, não pelo init_db
        c.execute("DELETE FROM resultados_lab WHERE amostrador_cod LIKE 'ZZTEST%'")
        c.execute("DELETE FROM divergencias WHERE descricao LIKE '%ZZTEST%'")
        c.execute("DELETE FROM amostradores WHERE codigo LIKE 'ZZTEST%'")
        c.execute("DELETE FROM ra_laudos WHERE amostrador_cod LIKE 'ZZTEST%'")


@pytest.fixture(autouse=True)
def banco():
    init_db()
    _limpar()
    yield
    _limpar()


def _laudo(cod='ZZTEST01', agente='Tolueno', conc='47,28085 ppm', **extra):
    d = {'filtroNumero': cod, 'agente': agente, 'concentracao': conc,
         'ltNR15': '78', 'ltTWA': '20', 'trabalhador': 'FULANO DE TAL',
         'dataAnalise': '23/07/2026', 'ra_num': '81962595'}
    d.update(extra)
    return d


def _divergencias_abertas():
    with get_db() as c:
        return [dict(r) for r in c.execute(
            "SELECT tipo, severidade, descricao, entidade_tipo, entidade_id "
            "FROM divergencias WHERE tipo='resultado_lab_divergente' AND status='aberta' "
            "AND descricao LIKE '%ZZTEST%'").fetchall()]


# ── leitura do valor ───────────────────────────────────────────────────

@pytest.mark.parametrize('txt,esperado', [
    ('47,28085 ppm',   ('47,28085', 'ppm', 47.28085, False)),
    ('<0,007 ppm',     ('<0,007', 'ppm', 0.007, True)),
    ('0,00041 mg/m³',  ('0,00041', 'mg/m³', 0.00041, False)),
    ('1.234,56 mg/m³', ('1.234,56', 'mg/m³', 1234.56, False)),
    ('',               ('', '', None, False)),
])
def test_separar_valor(txt, esperado):
    assert separar_valor(txt) == esperado


def test_nao_detectado_e_marcado():
    """'<' é o não detectado do lab — some se guardar só o número."""
    _, _, num, nd = separar_valor('<0,017 mg/m³')
    assert num == 0.017 and nd is True


@pytest.mark.parametrize('entrada,esperado', [
    ('81962595', '81962595'), ('81962595-3', '81962595'), ('RA 81962595', '81962595'),
    ('', ''), (None, ''),
])
def test_base_do_ra(entrada, esperado):
    """O sufixo é a sequência do tubo; o laudo é chamado pela base."""
    assert base_ra(entrada) == esperado


def test_acha_por_ra_mesmo_com_sufixo_do_tubo():
    """Era o furo pego validando em prod: o RA ficava vazio e ?ra= não achava nada."""
    gravar(_laudo(cod='ZZTEST05', ra_num='81962595-3'), 'pdf')
    assert len(listar(ra_num='81962595')) == 1
    assert len(listar(ra_num='RA 81962595-3')) == 1
    assert listar(ra_num='99999999') == []


# ── achar o RA de um tubo sem tocar na caixa de e-mail ─────────────────

def test_ras_do_amostrador_responde_do_banco():
    """É o que tira a busca por código dos 45 s: o RA já está gravado."""
    gravar(_laudo(cod='ZZTEST06', ra_num='81962595-2'), 'pdf')
    assert ras_do_amostrador('ZZTEST06') == ['81962595']
    assert ras_do_amostrador('  zztest06 ') == ['81962595']   # normaliza a digitação


def test_ras_do_amostrador_vale_para_tubo_fora_do_inventario():
    """O caso que importa: IEC488V não está no inventário e mesmo assim tem RA."""
    r = gravar(_laudo(cod='ZZTEST07', ra_num='81962595'), 'pdf')
    assert r['amostrador_id'] is None
    assert ras_do_amostrador('ZZTEST07') == ['81962595']


def test_ras_do_amostrador_vazio_quando_nao_sabemos():
    """Vazio manda o endpoint varrer a caixa — não pode devolver lista falsa."""
    assert ras_do_amostrador('ZZTEST99') == []
    assert ras_do_amostrador('') == []


def test_ras_do_amostrador_junta_varios_ras_sem_repetir():
    gravar(_laudo(cod='ZZTEST08', agente='Tolueno', ra_num='81962595'), 'pdf')
    gravar(_laudo(cod='ZZTEST08', agente='Etanol', ra_num='81962595'), 'pdf')
    gravar(_laudo(cod='ZZTEST08', agente='Xileno', ra_num='81970001'), 'pdf')
    assert ras_do_amostrador('ZZTEST08') == ['81962595', '81970001']


# ── gravação ───────────────────────────────────────────────────────────

def test_grava_o_que_o_laudo_diz():
    r = gravar(_laudo(), 'pdf', 'RA do lab')
    assert r['gravado'] and 'divergencia' not in r
    linhas = listar(amostrador='ZZTEST01')
    assert len(linhas) == 1
    assert linhas[0]['fonte'] == 'pdf' and linhas[0]['valor_txt'] == '47,28085'
    assert linhas[0]['unidade'] == 'ppm' and linhas[0]['lt_nr15'] == '78'


def test_laudo_sem_concentracao_nao_grava_linha_vazia():
    r = gravar(_laudo(conc=''), 'pdf')
    assert not r['gravado'] and 'concentração' in r['motivo']
    assert listar(amostrador='ZZTEST01') == []


def test_sem_codigo_de_amostrador_nao_grava():
    """Sem o tubo não há chave: gravar viraria resultado órfão."""
    assert not gravar(_laudo(cod=''), 'pdf')['gravado']


def test_regravar_a_mesma_fonte_atualiza_em_vez_de_duplicar():
    gravar(_laudo(conc='47,28 ppm'), 'pdf')
    gravar(_laudo(conc='47,90 ppm'), 'pdf')
    linhas = listar(amostrador='ZZTEST01')
    assert len(linhas) == 1 and linhas[0]['valor_txt'] == '47,90'


def test_agente_com_caixa_e_espaco_diferentes_e_o_mesmo_agente():
    gravar(_laudo(agente='Tolueno'), 'pdf')
    gravar(_laudo(agente='  TOLUENO  '), 'digitado')
    d = por_amostrador('ZZTEST01')
    assert len(d['pdf']) == 1 and len(d['digitado']) == 1
    assert not d['divergentes']            # mesmo valor, só a grafia mudou


# ── divergência ────────────────────────────────────────────────────────

def test_valor_diferente_abre_divergencia_e_guarda_os_dois():
    gravar(_laudo(conc='47,28085 ppm'), 'pdf', 'RA 81962595')
    r = gravar(_laudo(conc='4,728 ppm'), 'digitado', 'laudo químico · Matheus')
    assert 'divergencia' in r
    d = por_amostrador('ZZTEST01')
    assert len(d['pdf']) == 1 and len(d['digitado']) == 1      # nenhum sobrescrito
    assert d['divergentes'] and 'Tolueno' in d['divergentes'][0]['agente']
    abertas = _divergencias_abertas()
    assert len(abertas) == 1
    assert abertas[0]['severidade'] == 'alto'
    assert '47,28085' in abertas[0]['descricao'] and '4,728' in abertas[0]['descricao']


def test_nao_detectado_contra_valor_medido_e_divergencia():
    """'<0,007' e '0,007' dão o mesmo número e conclusões opostas."""
    gravar(_laudo(conc='<0,007 ppm'), 'pdf')
    r = gravar(_laudo(conc='0,007 ppm'), 'digitado')
    assert 'divergencia' in r and 'NÃO DETECTADO' in r['divergencia']


def test_arredondamento_de_transcricao_nao_vira_divergencia():
    gravar(_laudo(conc='47,28085 ppm'), 'pdf')
    r = gravar(_laudo(conc='47,281 ppm'), 'digitado')     # 0,001% de diferença
    assert 'divergencia' not in r
    assert not _divergencias_abertas()


def test_diferenca_acima_da_tolerancia_marca():
    base = 100.0
    acima = base * (1 + TOLERANCIA * 3)
    gravar(_laudo(conc=f'{base:.4f} ppm'.replace('.', ',')), 'pdf')
    r = gravar(_laudo(conc=f'{acima:.4f} ppm'.replace('.', ',')), 'digitado')
    assert 'divergencia' in r


def test_divergencia_nao_duplica_ao_regravar():
    gravar(_laudo(conc='47,28085 ppm'), 'pdf')
    gravar(_laudo(conc='4,728 ppm'), 'digitado')
    gravar(_laudo(conc='4,728 ppm'), 'digitado')      # técnico gera o laudo 2x
    assert len(_divergencias_abertas()) == 1


def test_divergencia_reaberta_mostra_os_numeros_de_agora():
    """Corrigir pela metade não pode deixar a divergência falando do valor antigo."""
    gravar(_laudo(conc='47,28085 ppm'), 'pdf')
    gravar(_laudo(conc='4,728 ppm'), 'digitado')
    gravar(_laudo(conc='40,0 ppm'), 'digitado')       # técnico corrigiu, ainda diverge
    abertas = _divergencias_abertas()
    assert len(abertas) == 1
    assert '40,0' in abertas[0]['descricao'] and '4,728' not in abertas[0]['descricao']


def test_ancora_a_divergencia_no_amostrador_quando_ele_existe():
    """Sem inventário a âncora é a linha do resultado — se fosse NULL o dedupe
    de salvar_divergencias falharia e abriria divergência a cada gravação."""
    with get_db() as c:
        c.execute("INSERT INTO amostradores (codigo, tipo, status, arquivado) "
                  "VALUES ('ZZTEST09','TCP','laboratorio',0)")
    gravar(_laudo(cod='ZZTEST09', conc='10,0 ppm'), 'pdf')
    gravar(_laudo(cod='ZZTEST09', conc='99,0 ppm'), 'digitado')
    abertas = _divergencias_abertas()
    assert len(abertas) == 1 and abertas[0]['entidade_tipo'] == 'amostrador'
    assert abertas[0]['entidade_id'] is not None


def test_sem_numero_dos_dois_lados_nao_inventa_divergencia():
    a = {'valor_num': None, 'valor_txt': 'ilegível', 'nao_detectado': 0}
    b = {'valor_num': 5.0, 'valor_txt': '5,0', 'nao_detectado': 0}
    assert divergem(a, b) == (False, '')


# ── histórico do backfill (formato do parse_ra_pdf) ────────────────────

@pytest.mark.parametrize('unidade,esperado', [
    ('mg/m³ (I)', ('mg/m³', 'Inalável')),
    ('mg/m³ ( R )', ('mg/m³', 'Respirável')),
    ('mg/m³ (T)', ('mg/m³', 'Torácica')),
    ('ppm', ('ppm', '')),
    ('', ('', '')),
])
def test_separar_fracao(unidade, esperado):
    """O backfill casa a unidade por prefixo, então a fração vem colada nela."""
    assert separar_fracao(unidade) == esperado


def _laudo_backfill(cod='ZZTEST10', ra='81963184'):
    """Formato que o parse_ra_pdf (backfill) produz — outro do gerador."""
    return {'amostrador': cod, 'ra_num': ra, 'funcionario': 'ROBERTO CARLOS',
            'resultados': [
                {'agente': 'Cromo metálico, como Cr(0)', 'unidade': 'mg/m³ (I)',
                 'resultado': '0,00041'},
                {'agente': 'Tolueno', 'unidade': 'ppm', 'resultado': '47,28085'},
                {'agente': 'Limite não aplicável', 'unidade': 'ppm', 'resultado': '-'},
            ]}


def test_converte_o_formato_do_backfill():
    d = do_laudo_extraido(_laudo_backfill())
    assert len(d) == 2                       # a linha com '-' não é medição
    assert d[0]['concentracao'] == '0,00041 mg/m³' and d[0]['fracao'] == 'Inalável'
    assert d[1]['concentracao'] == '47,28085 ppm' and not d[1]['fracao']
    assert all(x['filtroNumero'] == 'ZZTEST10' and x['ra_num'] == '81963184' for x in d)


def _semear_ra_laudos(cod, ra, resultados):
    import json
    with get_db() as c:
        c.execute("INSERT INTO ra_laudos (amostrador_cod, ra_num, funcionario, resultados) "
                  "VALUES (?,?,?,?)", (cod, ra, 'ROBERTO CARLOS',
                                       json.dumps(resultados, ensure_ascii=False)))


def test_migracao_traz_o_historico_do_backfill():
    _semear_ra_laudos('ZZTEST11', '81963184',
                      [{'agente': 'Tolueno', 'unidade': 'ppm', 'resultado': '12,5'}])
    r = migrar_de_ra_laudos()
    assert r['gravados'] >= 1
    linhas = listar(amostrador='ZZTEST11')
    assert len(linhas) == 1
    assert linhas[0]['valor_txt'] == '12,5' and linhas[0]['ra_num'] == '81963184'
    assert linhas[0]['fonte'] == 'pdf' and 'migrado' in linhas[0]['origem']


def test_migracao_nao_sobrescreve_o_laudo_lido_completo():
    """O JSON do backfill não tem limites; sobrescrever perderia LT NR-15/ACGIH."""
    gravar(_laudo(cod='ZZTEST12', agente='Tolueno', conc='47,28085 ppm'), 'pdf', 'PDF completo')
    _semear_ra_laudos('ZZTEST12', '81963184',
                      [{'agente': 'Tolueno', 'unidade': 'ppm', 'resultado': '99,9'}])
    migrar_de_ra_laudos()
    linhas = listar(amostrador='ZZTEST12')
    assert len(linhas) == 1
    assert linhas[0]['valor_txt'] == '47,28085'      # preservado
    assert linhas[0]['lt_nr15'] == '78'              # limite continua lá


def test_migracao_e_idempotente():
    _semear_ra_laudos('ZZTEST13', '81963184',
                      [{'agente': 'Etanol', 'unidade': 'ppm', 'resultado': '20,3'}])
    a = migrar_de_ra_laudos()
    b = migrar_de_ra_laudos()
    assert a['gravados'] >= 1 and b['gravados'] == 0
    assert b['preservados'] >= 1
    assert len(listar(amostrador='ZZTEST13')) == 1


# ── lote ───────────────────────────────────────────────────────────────

def test_gravar_muitos_conta_e_reporta():
    r = gravar_muitos([
        _laudo(cod='ZZTEST02', agente='Etanol', conc='20,3295 ppm'),
        _laudo(cod='ZZTEST03', agente='Cromo metálico, como Cr(0)',
               conc='0,00041 mg/m³', fracao='Inalável'),
        _laudo(cod='ZZTEST04', conc=''),          # sem valor → ignorado
    ], 'pdf', 'caixa do lab')
    assert r['gravados'] == 2 and r['ignorados'] == 1
    assert por_amostrador('ZZTEST03')['pdf'][0]['fracao'] == 'Inalável'
