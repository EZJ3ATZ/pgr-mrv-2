# -*- coding: utf-8 -*-
"""Login Microsoft (registro SSO-Medicoes) ao lado do login por senha.

Pedido do Bernardo (e-mail "Alinhamento perfil de acesso dos sistemas internos",
25/08/2026): o sistema de medições entra no login pelo Azure, como os outros
sistemas internos. Entra AO LADO da senha: técnico em campo não pode ficar
trancado do lado de fora no dia da troca.

O que estes testes travam:
  - sem as variáveis SSO_*, nada muda: sem botão, e a rota nova volta ao login;
  - com elas, o botão aparece e manda para o tenant e o client id certos;
  - o retorno exige o `state` que a própria sessão gerou (sem isso, qualquer
    link forjado logaria a vítima na conta de outra pessoa);
  - a Microsoft só AUTENTICA: quem entra é quem tem cadastro ativo em
    `usuarios`. E-mail sem cadastro não vira conta nova — foi o furo da
    Cobrança em 23/09, onde login Microsoft + autocadastro deixavam as 258
    pessoas do tenant entrarem sozinhas;
  - conta de outro tenant ou token emitido para outro app é recusada.
"""
import pytest

import controle.auth as auth_mod
from app import app
from controle.db import get_db, init_db

TENANT = '953ea640-963d-4af5-844c-03f21ebc048f'
CLIENT = '2875e663-8902-4b93-a0e0-01e007641fd2'
EMAIL_ATIVO = 'tecnico.sso@ocupacional.com.br'
EMAIL_INATIVO = 'inativo.sso@ocupacional.com.br'


@pytest.fixture(autouse=True)
def _usuarios():
    init_db()
    with get_db() as conn:
        for e in (EMAIL_ATIVO, EMAIL_INATIVO):
            conn.execute('DELETE FROM usuarios WHERE email=?', (e,))
        conn.execute("INSERT INTO usuarios (nome, email, senha_hash, ativo) VALUES (?,?,?,1)",
                     ('Tecnico SSO', EMAIL_ATIVO, 'x'))
        conn.execute("INSERT INTO usuarios (nome, email, senha_hash, ativo) VALUES (?,?,?,0)",
                     ('Inativo SSO', EMAIL_INATIVO, 'x'))
    yield
    with get_db() as conn:
        for e in (EMAIL_ATIVO, EMAIL_INATIVO):
            conn.execute('DELETE FROM usuarios WHERE email=?', (e,))


@pytest.fixture
def sso_ligado(monkeypatch):
    monkeypatch.setenv('SSO_CLIENT_ID', CLIENT)
    monkeypatch.setenv('SSO_CLIENT_SECRET', 'segredo-de-teste')
    monkeypatch.setenv('SSO_TENANT_ID', TENANT)
    monkeypatch.delenv('SSO_REDIRECT_URI', raising=False)


@pytest.fixture
def sso_desligado(monkeypatch):
    for v in ('SSO_CLIENT_ID', 'SSO_CLIENT_SECRET', 'SSO_TENANT_ID', 'SSO_REDIRECT_URI'):
        monkeypatch.delenv(v, raising=False)


def _claims(email, tid=TENANT, aud=CLIENT):
    return {'tid': tid, 'aud': aud, 'preferred_username': email, 'name': 'Pessoa'}


def _retorno(monkeypatch, claims, state_sessao='abc', state_url='abc'):
    monkeypatch.setattr(auth_mod, '_trocar_codigo', lambda cfg, code: claims)
    with app.test_client() as cli:
        with cli.session_transaction() as s:
            s['sso_state'] = state_sessao
        r = cli.get(f'/auth/gate-callback?code=codigo&state={state_url}')
        with cli.session_transaction() as s:
            logado = s.get('_user_id')
    return r, logado


def _uid(email):
    with get_db() as conn:
        return str(conn.execute('SELECT id FROM usuarios WHERE email=?', (email,)).fetchone()['id'])


def test_desligado_nao_mostra_botao_nem_abre_rota(sso_desligado):
    with app.test_client() as cli:
        tela = cli.get('/auth/login')
        assert tela.status_code == 200
        assert 'Entrar com Microsoft' not in tela.get_data(as_text=True)
        r = cli.get('/auth/microsoft')
        assert r.status_code == 302 and r.headers['Location'].endswith('/auth/login')
        r = cli.get('/auth/gate-callback?code=x&state=y')
        assert r.status_code == 302 and r.headers['Location'].endswith('/auth/login')


def test_ligado_mostra_botao_e_manda_para_o_tenant(sso_ligado):
    with app.test_client() as cli:
        tela = cli.get('/auth/login').get_data(as_text=True)
        assert 'Entrar com Microsoft' in tela
        assert 'name="senha"' in tela, 'a senha continua ao lado do botão'
        r = cli.get('/auth/microsoft')
        with cli.session_transaction() as s:
            state = s.get('sso_state')
    destino = r.headers['Location']
    assert r.status_code == 302
    assert destino.startswith(f'https://login.microsoftonline.com/{TENANT}/oauth2/v2.0/authorize?')
    assert f'client_id={CLIENT}' in destino
    assert state and f'state={state}' in destino
    assert 'redirect_uri=https%3A%2F%2Fmedicoes-ocupacional.up.railway.app%2Fauth%2Fgate-callback' in destino


def test_retorno_com_cadastro_ativo_entra(sso_ligado, monkeypatch):
    r, logado = _retorno(monkeypatch, _claims('Tecnico.SSO@ocupacional.com.br'))
    assert r.status_code == 302
    assert logado == _uid(EMAIL_ATIVO), 'e-mail casa sem diferenciar maiúscula'


def test_retorno_sem_cadastro_nao_cria_conta(sso_ligado, monkeypatch):
    with get_db() as conn:
        antes = conn.execute('SELECT COUNT(*) AS n FROM usuarios').fetchone()['n']
    r, logado = _retorno(monkeypatch, _claims('qualquer.pessoa@ocupacional.com.br'))
    with get_db() as conn:
        depois = conn.execute('SELECT COUNT(*) AS n FROM usuarios').fetchone()['n']
    assert r.status_code == 403
    assert logado is None
    assert depois == antes
    assert 'ainda não tem acesso liberado' in r.get_data(as_text=True)


def test_retorno_de_cadastro_inativo_nao_entra(sso_ligado, monkeypatch):
    r, logado = _retorno(monkeypatch, _claims(EMAIL_INATIVO))
    assert r.status_code == 403 and logado is None


def test_state_diferente_do_da_sessao_recusa(sso_ligado, monkeypatch):
    r, logado = _retorno(monkeypatch, _claims(EMAIL_ATIVO), state_sessao='abc', state_url='forjado')
    assert r.status_code == 400 and logado is None


def test_sem_state_na_sessao_recusa(sso_ligado, monkeypatch):
    monkeypatch.setattr(auth_mod, '_trocar_codigo', lambda cfg, code: _claims(EMAIL_ATIVO))
    with app.test_client() as cli:
        r = cli.get('/auth/gate-callback?code=codigo&state=abc')
        with cli.session_transaction() as s:
            logado = s.get('_user_id')
    assert r.status_code == 400 and logado is None


@pytest.mark.parametrize('claims', [
    _claims(EMAIL_ATIVO, tid='00000000-0000-0000-0000-000000000000'),
    _claims(EMAIL_ATIVO, aud='outro-app'),
])
def test_outro_tenant_ou_outro_app_recusa(sso_ligado, monkeypatch, claims):
    r, logado = _retorno(monkeypatch, claims)
    assert r.status_code == 403 and logado is None


def test_falha_na_troca_do_codigo_nao_loga(sso_ligado, monkeypatch):
    r, logado = _retorno(monkeypatch, None)
    assert r.status_code == 502 and logado is None


def test_payload_jwt_le_as_claims():
    import base64
    import json
    corpo = base64.urlsafe_b64encode(json.dumps({'tid': TENANT}).encode()).decode().rstrip('=')
    assert auth_mod._payload_jwt(f'h.{corpo}.s') == {'tid': TENANT}
    assert auth_mod._payload_jwt('lixo') is None
