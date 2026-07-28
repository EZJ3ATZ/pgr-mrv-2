# -*- coding: utf-8 -*-
"""Golden tests dos geradores de documento (os 4 laudos DOCX).

O QUE ISTO PEGA: qualquer mudança no texto/número do documento gerado. Editar o
template no Word, mexer num `replace`, trocar uma fórmula — tudo aparece como diff
antes de chegar em cliente. Era o ponto cego: os geradores produzem o papel que vai
pra fiscalização e não tinham teste nenhum.

O QUE ISTO NÃO PEGA: número errado que já estava errado quando o gabarito nasceu.
Golden congela o comportamento ATUAL, não a verdade. Por isso os defeitos conhecidos
estão marcados com `xfail(strict=True)` mais abaixo — eles falham de propósito e,
quando alguém consertar, o pytest avisa que é hora de tirar o marcador.

REGERAR OS GABARITOS (só quando a mudança for intencional e conferida):
    cd GITHUB_UPLOAD_COMPLETO
    ATUALIZAR_GOLDENS=1 py -m pytest tests/test_laudos_golden.py -q
Depois LER O DIFF antes de commitar. Regerar sem ler é o mesmo que não ter teste.
"""
import os
import pathlib

import pytest

from tests.laudos_casos import (CALOR, EMPRESA, PGR, QUIMICO, RUIDO,
                                docx_texto, normalizar)

GOLDENS = pathlib.Path(__file__).parent / 'goldens'
ATUALIZAR = os.environ.get('ATUALIZAR_GOLDENS') == '1'


def _gera(qual):
    """Chama o gerador direto (sem HTTP) e devolve o texto normalizado."""
    import app as A
    if qual == 'quimico':
        blob = A.gerar_quimico_bytes(QUIMICO)
    elif qual == 'ruido':
        blob = A.gerar_ruido_bytes(RUIDO)
    elif qual == 'calor':
        blob = A.gerar_calor_bytes(CALOR)
    elif qual == 'pgr':
        blob = A.gerar_docx_bytes(
            PGR['nome'], PGR['cnpj'], PGR['rua'], PGR['numero'], PGR['complemento'],
            PGR['cep'], PGR['bairro'], PGR['cidade'], PGR['uf'], PGR['cargos'],
            cnae=PGR['cnae'], descricao_cnae=PGR['descricao_cnae'],
            grau_risco=PGR['grau_risco'])
    else:
        raise AssertionError(f'gerador desconhecido: {qual}')
    return normalizar(docx_texto(blob))


@pytest.mark.parametrize('qual', ['quimico', 'ruido', 'calor', 'pgr'])
def test_documento_bate_com_o_gabarito(qual):
    atual = _gera(qual)
    alvo = GOLDENS / f'{qual}.golden.txt'

    if ATUALIZAR or not alvo.exists():
        GOLDENS.mkdir(exist_ok=True)
        alvo.write_text(atual, encoding='utf-8')
        pytest.skip(f'gabarito de {qual} (re)gerado em {alvo.name} — CONFERIR o diff')

    esperado = alvo.read_text(encoding='utf-8')
    if atual == esperado:
        return

    # Diff legível: primeira linha divergente, com contexto.
    la, le = atual.split('\n'), esperado.split('\n')
    for i in range(max(len(la), len(le))):
        a = la[i] if i < len(la) else '<fim do documento>'
        e = le[i] if i < len(le) else '<fim do gabarito>'
        if a != e:
            ctx = '\n'.join(f'    {n + 1}: {le[n]}' for n in range(max(0, i - 2), i))
            pytest.fail(
                f'{qual}: documento mudou na linha {i + 1}.\n'
                f'  contexto:\n{ctx}\n'
                f'  gabarito: {e!r}\n'
                f'  gerado:   {a!r}\n'
                f'  ({len(le)} linhas no gabarito, {len(la)} no gerado)\n'
                f'  Se a mudança é intencional: ATUALIZAR_GOLDENS=1 py -m pytest '
                f'tests/test_laudos_golden.py -q')


# ── Asserções nos números que já deram bug ───────────────────────────────────
# Golden pega qualquer mudança, mas com mensagem genérica. Estas asserções dizem
# QUAL invariante quebrou — cada uma corresponde a um bug real já corrigido.

def test_quimico_traz_a_concentracao_e_os_limites():
    """Regressão 12/06: título mostrava o funcionário em vez da substância, e a
    célula da data de coleta recebia a data de análise."""
    t = _gera('quimico')
    assert 'Tolueno' in t, 'substância ausente no laudo'
    assert '18,5' in t, 'concentração ausente'
    assert '78' in t, 'LT NR-15 ausente'
    assert '10/06/2026' in t, 'data de COLETA ausente'
    assert '20/06/2026' in t, 'data de ANÁLISE ausente'


def test_quimico_nao_traz_residuo_do_template():
    """Regressão 12/06: vazão média fixa 0,19550, bomba 'Inlite Ventuspro' e o CNAE
    da empresa do template vazavam para todo laudo.

    Nota: 'Ventuspro' AINDA aparece no laudo — de propósito, na seção III
    (metodologia), que lista os tipos de bomba mais comuns. Ali é prosa descritiva,
    não valor da avaliação. Por isso o teste não proíbe a palavra: exige que a bomba
    REAL da avaliação esteja presente, que é o invariante que o bug violava.
    """
    t = _gera('quimico')
    assert '0,19550' not in t, 'vazão média fixa do template reapareceu'
    assert 'SKC AirChek' in t, 'bomba da avaliação ausente'
    assert 'A63555' in t, 'nº de série da bomba da avaliação ausente'
    assert 'Planos de saúde' not in t, 'descrição CNAE do template reapareceu'
    assert '65.50-2-00' not in t, 'CNAE do template reapareceu'


def test_quimico_resumo_e_conclusao_nao_se_contradizem():
    """O bug de 28/07: o quadro resumo (seção IX) dizia "18,5 (REGULAR)" enquanto a
    conclusão (seção VI) dizia IRREGULAR pela ACGIH — o LT-TWA de 20 ppm corrigido
    pela Brief & Scala cai para 17,6 e a concentração de 18,5 passa. Quem lia só o
    resumo via REGULAR numa exposição acima do limite corrigido.

    As duas seções agora dividem `_classificar_quimico`. Este teste é a trava: se
    alguém mexer numa e não na outra, quebra aqui."""
    t = _gera('quimico')
    corpo_acgih_irregular = 'a concentração ultrapassa o limite corrigido, situação IRREGULAR' in t
    resumo_acgih_irregular = 'ACGIH: IRREGULAR' in t
    assert corpo_acgih_irregular == resumo_acgih_irregular, (
        f'seção VI e IX discordam sobre a ACGIH: corpo diz irregular='
        f'{corpo_acgih_irregular}, resumo diz irregular={resumo_acgih_irregular}')
    # e o resumo tem que nomear as DUAS normas, não um veredicto solto
    assert 'NR-15: REGULAR' in t, 'resumo não nomeia o veredicto da NR-15'
    assert 'ACGIH: IRREGULAR' in t, 'resumo não nomeia o veredicto da ACGIH'


@pytest.mark.parametrize('conc,ltnr15,lttwa,esperado_nr15,esperado_acgih', [
    ('18,5', '78', '20',  True,  False),   # o caso real: NR-15 passa, ACGIH (17,6) não
    ('10,0', '78', '20',  True,  True),    # abaixo dos dois
    ('90,0', '78', '20',  False, False),   # acima dos dois
    ('17,6', '78', '20',  True,  False),   # exatamente no LT corrigido → não é "< LT"
    ('17,5', '78', '20',  True,  True),    # logo abaixo do corrigido
])
def test_classificador_quimico_por_limite(conc, ltnr15, lttwa, esperado_nr15, esperado_acgih):
    """A conta do Brief & Scala (LT-TWA × 0,88) e a comparação por limite, isoladas."""
    from app import _classificar_quimico
    r = _classificar_quimico({'concentracao': conc, 'ltNR15': ltnr15, 'ltTWA': lttwa})
    assert r['nr15'][1] is esperado_nr15, f'NR-15 errado p/ {conc} vs {ltnr15}'
    assert r['acgih'][2] is esperado_acgih, (
        f'ACGIH errado p/ {conc} vs {lttwa}×0,88={float(lttwa) * 0.88}')
    assert r['ok_geral'] is (esperado_nr15 and esperado_acgih)


def test_classificador_nao_detectado_e_regular():
    """"<" ou N.D. = abaixo do limite de detecção, logo abaixo de qualquer LT (#9)."""
    from app import _classificar_quimico
    for conc in ('< 0,5', 'N.D.', 'ND', ''):
        r = _classificar_quimico({'concentracao': conc, 'ltNR15': '78', 'ltTWA': '20'})
        assert r['nd'] is True and r['ok_geral'] is True, f'{conc!r} deveria ser regular'


def test_classificador_stel_so_conta_ate_15min():
    """TLV-STEL é limite de curta duração: só se aplica quando a coleta tem <= 15 min
    (item 2 do Bernardo — não exibir STEL quando não se aplica)."""
    from app import _classificar_quimico
    base = {'concentracao': '50', 'ltNR15': '78', 'ltSTEL': '40'}
    assert _classificar_quimico({**base, 'tempoColeta': '10'})['stel'] is not None
    assert _classificar_quimico({**base, 'tempoColeta': '240'})['stel'] is None, \
        'STEL aplicado a coleta de 4h — não é limite de curta duração'


def test_ruido_quadro_resumo_traz_TODAS_as_avaliacoes():
    """Regressão 12/06: quadro resumo não era populado e mantinha linha fantasma."""
    t = _gera('ruido')
    assert 'Operador de Prensa' in t, 'avaliação 1 ausente do quadro'
    assert 'Conferente' in t, 'avaliação 2 ausente do quadro'
    assert '86,4' in t and '79,2' in t, 'valores Lavg ausentes'
    assert 'Coordenador de base' not in t, 'linha fantasma do template reapareceu'
    assert 'NOME DA EMPRESA' not in t, 'placeholder da carta não foi substituído'


def test_ruido_nao_mistura_as_duas_grandezas_sem_rotulo():
    """Crítica ABHO 09/07 item 6: Lavg (NR-15, Q=5) e NEN (NHO-01, Q=3) são
    grandezas diferentes. Se as duas aparecem, o rótulo tem que aparecer também."""
    t = _gera('ruido')
    if '86,4' in t and '89,2' in t:
        assert ('NR-15' in t or 'NR 15' in t), 'Q5 sem citar NR-15'
        assert ('NHO' in t), 'Q3 sem citar NHO-01'


def test_calor_usa_o_quadro_vigente_e_nao_o_truncado():
    """Regressão a4e23b5: `_NR15_QUADRO1` truncava em 346 W e o limite saía
    permissivo demais. M=350 W do caso cai exatamente depois do corte."""
    from app import get_limite_nr15
    assert get_limite_nr15(350) is not None, 'M=350 W sem limite (quadro truncado?)'
    assert get_limite_nr15(350) < get_limite_nr15(150), \
        'limite não cai quando a taxa metabólica sobe — quadro invertido'
    t = _gera('calor')
    assert 'FORNO' in t and 'ALMOXARIFADO' in t, 'setor ausente do laudo'
    assert 'Boca do forno' in t, 'ponto de medição ausente do laudo'
    # A CONCLUSÃO (gerada) tem que citar NR-15. O texto de capa do template ainda
    # cita NR-09 — defeito separado, registrado no xfail no fim do arquivo.
    assert 'na NR-15, para uma taxa de metabolismo' in t, \
        'conclusão gerada não cita NR-15'


def test_calor_conclusao_bate_com_a_media_ponderada():
    """A conclusão usa M médio e IBUTG médio PONDERADOS PELO TEMPO de cada ponto —
    não o pior ponto. Caso FORNO: M (350·30 + 250·30)/60 = 300 W;
    IBUTG (28,3·30 + 25,8·30)/60 = 27,05 → 27,1. Se a ponderação quebrar, o laudo
    conclui conforme/não-conforme pelo número errado."""
    t = _gera('calor')
    assert 'metabolismo média de 300 W' in t, 'M médio ponderado do setor FORNO mudou'
    assert '27,1' in t, 'IBUTG médio ponderado do setor FORNO mudou'
    assert 'metabolismo média de 150 W' in t, 'M do setor ALMOXARIFADO mudou'


def test_calor_ibutg_por_ponto_bate_com_a_formula():
    """A fórmula do IBUTG é a conta que sustenta o laudo. Confere ponto a ponto
    contra o valor calculado à mão (céu aberto usa tbs, interno não).

    Regressão dupla: `_ibutg_ponto` ignorava o tbs (auditoria 05/07) e a escolha
    céu aberto/coberto é inferida do preenchimento do tbs (decisão do Matheus,
    convenção da planilha de campo — crítica ABHO 09/07 item 7)."""
    from app import _ibutg_ponto
    from tests.laudos_casos import CALOR, CALOR_IBUTG_ESPERADO
    for setor in CALOR['setores']:
        for p in setor['pontos']:
            obtido = round(_ibutg_ponto(p)[0], 1)
            esperado = CALOR_IBUTG_ESPERADO[p['local']]
            assert abs(obtido - esperado) < 0.05, (
                f'{p["local"]}: IBUTG {obtido} != {esperado} esperado '
                f'(tbn={p["tbn"]} tbs={p["tbs"] or "vazio"} tg={p["tg"]})')


def test_pgr_clona_uma_linha_por_cargo():
    """Regressão 12/06: `replace` único fazia a tabela Setor/Cargo listar só o 1º."""
    t = _gera('pgr')
    for cargo in PGR['cargos']:
        assert cargo in t, f'cargo {cargo} ausente do PGR (tabela não clonou?)'


def test_pgr_nao_vaza_a_empresa_de_referencia_do_template():
    """O template do PGR foi montado sobre uma empresa real. Nome, CNPJ, endereço
    e cidade dela não podem sobrar no documento de outro cliente."""
    t = _gera('pgr')
    assert 'MARCIO DA SILVA' not in t, 'razão social do template vazou'
    assert '63.370.132' not in t, 'CNPJ do template vazou'
    assert 'Sibipurunas' not in t, 'endereço do template vazou'
    assert 'Ribeirão das Neves' not in t, 'cidade do template vazou'


def test_nenhum_documento_vaza_placeholder_ou_erro():
    """Varredura barata que o harness de 15/06 já fazia nos PDFs: nada de None,
    undefined, NaN, {{ }} ou traceback dentro do documento assinado."""
    for qual in ('quimico', 'ruido', 'calor', 'pgr'):
        t = _gera(qual)
        for ruim in ('undefined', 'NaN', '{{', 'Traceback', 'None None'):
            assert ruim not in t, f'{qual}: documento contém {ruim!r}'


# ── Defeitos CONHECIDOS e ainda não corrigidos ───────────────────────────────
# `strict=True` de propósito: enquanto o bug existe o teste falha-como-esperado
# (xfail, verde); quando alguém consertar, vira XPASS e o pytest ACUSA — aí é hora
# de apagar o marcador. É assim que o defeito não é esquecido nem vira gabarito.

def test_calor_respeita_o_grau_de_risco_da_empresa():
    """Corrigido em 28/07: `gerar_calor_bytes` não substituía `grauRisco` e o laudo
    saía sempre com "Grau de Risco 2" — valor do template (comércio varejista) —
    qualquer que fosse a empresa. Químico e ruído já substituíam."""
    t = _gera('calor')
    i = t.find('Grau de Risco')
    assert i >= 0, 'rótulo "Grau de Risco" desapareceu da capa do laudo de calor'
    trecho = t[i:i + 40]
    assert EMPRESA['grauRisco'] in trecho, \
        f'grau de risco da empresa ({EMPRESA["grauRisco"]}) não está em {trecho!r}'


@pytest.mark.xfail(strict=True, reason=(
    'ACHADO 28/07: a CARTA do laudo de calor (prosa do template_calor.docx, nunca '
    'substituída) diz "NR-15 e NR-09, através de seu Anexo 3, da Portaria 3218/78", e '
    'outra linha diz "Norma Regulamentadora n°09 ou n°15". A CONCLUSÃO gerada cita '
    'NR-15 corretamente e a conta está certa — o problema é só o texto fixo. Duas '
    'coisas p/ o Matheus confirmar (é domínio dele): (a) o limite de calor vem da '
    'NR-15 Anexo 3, então citar NR-09 na carta parece errado; (b) "Portaria 3218/78" '
    'parece typo de 3214/78 — o gerador de ruído usa 3214. Corrigir = editar o '
    'template no Word (4 ocorrências de NR-09) ou fazer replace no gerador.'))
def test_calor_carta_nao_deveria_citar_NR09_nem_portaria_3218():
    t = _gera('calor')
    assert 'NR-09' not in t, 'carta do laudo de calor cita NR-09'
    assert '3218/78' not in t, 'carta cita Portaria 3218/78 (typo de 3214/78?)'


@pytest.mark.xfail(strict=True, reason=(
    'ACHADO 28/07 (severidade baixa): em `_ibutg_ponto` o `tbs` tem try/except mas '
    '`tbn` e `tg` não — valor não-numérico derruba o laudo de calor com 500. Pela UI '
    'não acontece (input type=number + `+p.tbn` no envio, index.html:7298); o risco é '
    'via API/mobile/importação. Os geradores de PDF já foram endurecidos assim em '
    '15/06 (`_sanitize_rl`); o caminho DOCX não foi.'))
def test_calor_deveria_aguentar_tbn_nao_numerico_sem_500():
    from app import _ibutg_ponto
    ibutg, _ = _ibutg_ponto({'tbn': '25,0', 'tbs': '', 'tg': '30,0'})
    assert ibutg >= 0


def test_pgr_respeita_cnae_descricao_e_grau_do_cadastro():
    """Corrigido em 28/07: o PGR não substituía CNAE, descrição do CNAE nem grau de
    risco, então todo documento saía com os da empresa de referência do template
    (43.99-1-03 / "Obras de alvenaria" / grau 03). Decisão do Matheus: puxar do
    CADASTRO da empresa (`db.dados_cadastro_empresa`), já que o form não pede."""
    t = _gera('pgr')
    assert '43.99-1-03' not in t, 'CNAE do template ainda no PGR'
    assert 'Obras de alvenaria' not in t, 'descrição CNAE do template ainda no PGR'
    assert PGR['cnae'] in t, 'CNAE do cadastro não chegou ao PGR'
    assert PGR['descricao_cnae'] in t, 'descrição do CNAE do cadastro não chegou'
    # grau '4' do cadastro sai como '04' (o template usa 2 dígitos) e o '03' do
    # template não pode sobrar em NENHUM dos dois lugares: capa e linha do
    # treinamento de CIPA, cujo dimensionamento depende do grau.
    assert 'Grau de Risco 04' in t, 'grau do cadastro não chegou à linha da CIPA'
    assert 'Grau de Risco 03' not in t, 'grau do template sobrou no PGR'
    assert '\n04\n' in ('\n' + t + '\n'), 'grau do cadastro não chegou à capa'


def test_pgr_grau_de_1_digito_ganha_zero_a_esquerda():
    """O template escreve o grau com 2 dígitos ('03'). Cadastro com '3' tem de sair
    '03' para o documento não misturar duas tipografias."""
    import app as A
    blob = A.gerar_docx_bytes(
        PGR['nome'], PGR['cnpj'], PGR['rua'], PGR['numero'], PGR['complemento'],
        PGR['cep'], PGR['bairro'], PGR['cidade'], PGR['uf'], PGR['cargos'],
        cnae='11.11-1-11', descricao_cnae='X', grau_risco='3')
    t = normalizar(docx_texto(blob))
    assert 'Grau de Risco 03' in t, "grau '3' deveria sair como '03'"


def test_pgr_marca_faltante_com_interrogacao_em_vez_do_template():
    """Cadastro sem os dados NÃO pode cair no valor do template (que é de outra
    empresa). Vira '???', a convenção que o PGR já usa para medição sem data
    confirmada — em branco no meio da capa passaria batido."""
    import app as A
    blob = A.gerar_docx_bytes(
        PGR['nome'], PGR['cnpj'], PGR['rua'], PGR['numero'], PGR['complemento'],
        PGR['cep'], PGR['bairro'], PGR['cidade'], PGR['uf'], PGR['cargos'],
        cnae='', descricao_cnae='', grau_risco='')
    t = normalizar(docx_texto(blob))
    assert '43.99-1-03' not in t, 'sem CNAE no cadastro, vazou o do template'
    assert 'Obras de alvenaria' not in t, 'sem descrição, vazou a do template'
    assert 'Grau de Risco 03' not in t, 'sem grau no cadastro, vazou o do template'
    assert '???' in t, 'campo ausente deveria aparecer como ???'


def test_dados_cadastro_empresa_casa_por_cnpj_e_por_nome():
    """A busca tem de usar a MESMA precedência do `enriquecer_empresa` (CNPJ, depois
    nome exato) — senão o caminho laudo→cadastro e o cadastro→laudo discordam sobre
    qual empresa é qual."""
    from controle.db import dados_cadastro_empresa, get_db, init_db
    init_db()
    with get_db() as conn:
        conn.execute("DELETE FROM empresas WHERE nome LIKE 'CADASTRO TESTE%'")
        conn.execute("INSERT INTO empresas (nome, cnpj, cnae, descricao_cnae, grau_risco) "
                     "VALUES ('CADASTRO TESTE A', '99.888.777/0001-66', '25.11-0-00', "
                     "'Estruturas metalicas', '3')")
        conn.execute("INSERT INTO empresas (nome, cnae, grau_risco) "
                     "VALUES ('CADASTRO TESTE B', '10.20-3-00', '4')")

    por_cnpj = dados_cadastro_empresa('nome que nao existe', '99.888.777/0001-66')
    assert por_cnpj['cnae'] == '25.11-0-00' and por_cnpj['grau_risco'] == '3', \
        'CNPJ deveria ter precedência sobre o nome'

    por_nome = dados_cadastro_empresa('cadastro teste b')   # case-insensitive
    assert por_nome['cnae'] == '10.20-3-00' and por_nome['grau_risco'] == '4'
    assert por_nome['descricao_cnae'] == '', 'campo ausente deveria vir vazio, não None'

    nada = dados_cadastro_empresa('EMPRESA QUE NAO EXISTE', '')
    assert nada == {'id': None, 'cnae': '', 'descricao_cnae': '', 'grau_risco': ''}
