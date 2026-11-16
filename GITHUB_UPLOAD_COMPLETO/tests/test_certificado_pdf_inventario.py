# -*- coding: utf-8 -*-
"""Certificado de calibração em PDF no inventário.

Pedido do Matheus (16/11/2026), depois do Wesley no grupo Os Medidores: "Eu consegui
adicionar os aparelhos mas não os certificados de calibração. O PDF". O inventário só
guardava o NÚMERO do certificado; o PDF de aparelho novo dependia de alguém pôr as páginas
em static/certs, e o laudo químico saía sem o certificado.

O que estes testes travam:
  - o técnico anexa o PDF no equipamento e a lista passa a mostrar `cert_pdf`;
  - só entra PDF de verdade (arquivo que não é PDF, PDF quebrado e acessório são recusados);
  - enviar outro PDF TROCA o anterior (um certificado por equipamento);
  - qualquer pessoa logada abre o PDF; tirar o certificado é só do admin;
  - apagar o equipamento leva o certificado junto;
  - o laudo acha as páginas pelo nº de série (sem diferença de maiúscula ou espaço) e
    devolve lista vazia quando não há certificado, sem erro.
"""
import io

import pytest
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from app import app, _cert_paginas_do_inventario
from controle.db import get_db, init_db

MARCA = 'TESTCERT'
USUARIOS = {'tecnico': 'tecnico.certificado@ocupacional.com.br',
            'admin': 'admin.certificado@ocupacional.com.br'}


def _pdf(paginas=2, texto='Certificado de calibracao'):
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    for i in range(paginas):
        c.drawString(72, 760, f'{texto} - pagina {i + 1}')
        c.showPage()
    c.save()
    return buf.getvalue()


def _apaga_testes():
    with get_db() as conn:
        ids = [r['id'] for r in conn.execute(
            'SELECT id FROM equipamentos_inventario WHERE marca=?', (MARCA,)).fetchall()]
        for eid in ids:
            conn.execute('DELETE FROM equipamentos_certificados WHERE equipamento_id=?', (eid,))
        conn.execute('DELETE FROM equipamentos_inventario WHERE marca=?', (MARCA,))


@pytest.fixture(autouse=True)
def _ambiente():
    init_db()
    _apaga_testes()
    with get_db() as conn:
        for role, email in USUARIOS.items():
            conn.execute('DELETE FROM usuarios WHERE email=?', (email,))
            conn.execute("INSERT INTO usuarios (nome, email, senha_hash, role, ativo) "
                         "VALUES (?,?,?,?,1)", (f'Teste {role}', email, 'x', role))
    yield
    _apaga_testes()
    with get_db() as conn:
        for email in USUARIOS.values():
            conn.execute('DELETE FROM usuarios WHERE email=?', (email,))


def _cli(role):
    with get_db() as conn:
        uid = conn.execute('SELECT id FROM usuarios WHERE email=?',
                           (USUARIOS[role],)).fetchone()['id']
    cli = app.test_client()
    with cli.session_transaction() as s:
        s['_user_id'] = str(uid)
        s['_fresh'] = True
    return cli


def _cria(cli, **campos):
    campos.setdefault('marca', MARCA)
    r = cli.post('/controle/equipamentos', json=campos)
    assert r.status_code == 201, r.get_json()
    return r.get_json()['id']


def _envia(cli, eid, conteudo, nome='certificado.pdf'):
    return cli.post(f'/controle/equipamentos/{eid}/certificado',
                    data={'arquivo': (io.BytesIO(conteudo), nome)},
                    content_type='multipart/form-data')


def _na_lista(cli, eid):
    return next(e for e in cli.get('/controle/equipamentos').get_json() if e['id'] == eid)


def test_tecnico_anexa_o_pdf_e_a_lista_mostra():
    cli = _cli('tecnico')
    eid = _cria(cli, tipo='bomba', modelo='BDX-II', numero_serie='TC-0001')
    assert _na_lista(cli, eid)['cert_pdf'] is None

    r = _envia(cli, eid, _pdf(2), 'Certificado BDX TC-0001.pdf')
    assert r.status_code == 201, r.get_json()
    assert r.get_json() == {'ok': True, 'nome': 'Certificado BDX TC-0001.pdf', 'paginas': 2}

    cert = _na_lista(cli, eid)['cert_pdf']
    assert cert['nome'] == 'Certificado BDX TC-0001.pdf'
    assert cert['paginas'] == 2
    assert cert['tamanho'] > 500

    aberto = cli.get(f'/controle/equipamentos/{eid}/certificado')
    assert aberto.status_code == 200
    assert aberto.mimetype == 'application/pdf'
    assert aberto.data.startswith(b'%PDF')
    assert 'attachment' not in aberto.headers.get('Content-Disposition', '')


def test_so_entra_pdf_de_verdade():
    cli = _cli('tecnico')
    eid = _cria(cli, tipo='bomba', modelo='BDX-II', numero_serie='TC-0002')

    r = _envia(cli, eid, b'\xff\xd8\xff\xe0 isto e um jpeg', 'foto.jpg')
    assert r.status_code == 400 and 'PDF' in r.get_json()['erro']

    r = _envia(cli, eid, b'%PDF-1.4 arquivo cortado no meio', 'quebrado.pdf')
    assert r.status_code == 400 and 'abrir' in r.get_json()['erro']

    r = cli.post(f'/controle/equipamentos/{eid}/certificado', data={}, content_type='multipart/form-data')
    assert r.status_code == 400

    assert _na_lista(cli, eid)['cert_pdf'] is None
    assert cli.get(f'/controle/equipamentos/{eid}/certificado').status_code == 404


def test_acessorio_e_item_inexistente_sao_recusados():
    cli = _cli('tecnico')
    acess = _cria(cli, tipo='mangueira', observacao='Mangueira de teste do certificado', quantidade=3)
    assert _envia(cli, acess, _pdf(1)).status_code == 400
    assert _envia(cli, 99999999, _pdf(1)).status_code == 404


def test_enviar_outro_pdf_troca_o_anterior():
    cli = _cli('tecnico')
    eid = _cria(cli, tipo='bomba', modelo='BDX-II', numero_serie='TC-0003')
    assert _envia(cli, eid, _pdf(1), 'antigo.pdf').status_code == 201
    assert _envia(cli, eid, _pdf(3), 'novo.pdf').status_code == 201

    cert = _na_lista(cli, eid)['cert_pdf']
    assert (cert['nome'], cert['paginas']) == ('novo.pdf', 3)
    with get_db() as conn:
        n = conn.execute('SELECT COUNT(*) AS c FROM equipamentos_certificados WHERE equipamento_id=?',
                         (eid,)).fetchone()['c']
    assert n == 1


def test_tirar_o_certificado_e_so_do_admin_e_apagar_o_equipamento_leva_junto():
    tec, adm = _cli('tecnico'), _cli('admin')
    eid = _cria(tec, tipo='bomba', modelo='BDX-II', numero_serie='TC-0004')
    assert _envia(tec, eid, _pdf(1)).status_code == 201

    assert tec.delete(f'/controle/equipamentos/{eid}/certificado').status_code == 403
    assert _na_lista(tec, eid)['cert_pdf'] is not None

    assert adm.delete(f'/controle/equipamentos/{eid}/certificado').status_code == 200
    assert _na_lista(tec, eid)['cert_pdf'] is None
    assert adm.delete(f'/controle/equipamentos/{eid}/certificado').status_code == 404

    assert _envia(tec, eid, _pdf(1)).status_code == 201
    assert adm.delete(f'/controle/equipamentos/{eid}').status_code == 200
    with get_db() as conn:
        n = conn.execute('SELECT COUNT(*) AS c FROM equipamentos_certificados WHERE equipamento_id=?',
                         (eid,)).fetchone()['c']
    assert n == 0


def test_sem_login_nao_anexa_nem_abre():
    eid = _cria(_cli('tecnico'), tipo='bomba', modelo='BDX-II', numero_serie='TC-0005')
    anon = app.test_client()
    assert _envia(anon, eid, _pdf(1)).status_code in (302, 401, 403)
    assert anon.get(f'/controle/equipamentos/{eid}/certificado').status_code in (302, 401, 403)


def test_laudo_acha_as_paginas_pelo_numero_de_serie():
    cli = _cli('tecnico')
    eid = _cria(cli, tipo='bomba', modelo='BDX-II', numero_serie='TC-0006-Ab')
    assert _cert_paginas_do_inventario('TC-0006-Ab') == []

    assert _envia(cli, eid, _pdf(3)).status_code == 201
    paginas = _cert_paginas_do_inventario('  tc-0006-AB ')
    assert len(paginas) == 3
    assert all(p.startswith('data:image/jpeg;base64,') for p in paginas)

    assert _cert_paginas_do_inventario('SERIE-QUE-NAO-EXISTE') == []
    assert _cert_paginas_do_inventario('') == []
    assert _cert_paginas_do_inventario(None) == []


def _imagens_do_laudo(blob):
    import zipfile
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        return sorted(n for n in z.namelist() if n.startswith('word/media/q'))


def test_laudo_quimico_com_bomba_nova_leva_o_certificado_do_inventario():
    """Bomba que não está nos certificados fixos de static/certs: o laudo químico sai com as
    páginas do PDF anexado no inventário. Sem o PDF, sai como saía antes (sem certificado)."""
    import copy
    import app as A
    from tests.laudos_casos import QUIMICO

    caso = copy.deepcopy(QUIMICO)
    caso['config'].update({'bomba': 'bdx', 'bombaSN': 'TC-LAUDO-01'})
    caso['avaliacoes'][0].update({'bomba': 'bdx', 'bombaSN': 'TC-LAUDO-01'})

    cli = _cli('tecnico')
    eid = _cria(cli, tipo='bomba', modelo='BDX-II', numero_serie='TC-LAUDO-01')
    sem = _imagens_do_laudo(A.gerar_quimico_bytes(caso))

    assert _envia(cli, eid, _pdf(2)).status_code == 201
    com = _imagens_do_laudo(A.gerar_quimico_bytes(caso))
    assert len(com) == len(sem) + 2, (sem, com)


def test_bomba_com_certificado_fixo_continua_usando_o_fixo():
    """Série que já tem páginas em static/certs não troca de fonte nem duplica o certificado,
    mesmo que alguém anexe um PDF para ela no inventário."""
    import copy
    import app as A
    from tests.laudos_casos import QUIMICO

    import os
    # só vale série cujas páginas EXISTEM no disco: o mapa tem séries sem arquivo
    # (ex.: 20230702024), e para essas o certificado do inventário é justamente o que entra.
    serie = next(s for s in A._PUMP_CERT_PAGES['bdx']
                 if os.path.exists(os.path.join(A.CERTS_DIR, f'cert_bdx_{s}_p1.jpg')))
    caso = copy.deepcopy(QUIMICO)
    caso['config'].update({'bomba': 'bdx', 'bombaSN': serie})
    caso['avaliacoes'][0].update({'bomba': 'bdx', 'bombaSN': serie})
    antes = _imagens_do_laudo(A.gerar_quimico_bytes(caso))
    assert antes, 'o laudo deveria levar as páginas fixas desta série'

    cli = _cli('tecnico')
    with get_db() as conn:  # a série fixa pode já existir no inventário do seed: anexa nela
        row = conn.execute('SELECT id FROM equipamentos_inventario WHERE numero_serie=?', (serie,)).fetchone()
    eid = row['id'] if row else _cria(cli, tipo='bomba', modelo='BDX-II', numero_serie=serie)
    try:
        assert _envia(cli, eid, _pdf(3)).status_code == 201
        depois = _imagens_do_laudo(A.gerar_quimico_bytes(caso))
        assert len(depois) == len(antes)
    finally:
        with get_db() as conn:
            conn.execute('DELETE FROM equipamentos_certificados WHERE equipamento_id=?', (eid,))
