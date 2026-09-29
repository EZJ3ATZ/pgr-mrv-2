# -*- coding: utf-8 -*-
"""Login SÓ pela Microsoft (registro SSO-Medicoes). Não existe usuário e senha.

Pedido do Bernardo (e-mail "Alinhamento perfil de acesso dos sistemas internos",
25/08/2026): o sistema de medições entra no login pelo Azure, como os outros
sistemas internos. Em 25/09 o botão entrou ao lado da senha; em 28/09 o Matheus
fechou a regra para todo sistema da frente: "o login tem que ser só pela
Microsoft. Não deve existir login e senha."

O que estes testes travam:
  - a tela de login não tem campo de senha, esteja a Microsoft configurada ou não;
  - POST de senha (formulário antigo, página guardada offline) não loga ninguém,
    mesmo com a senha certa;
  - autocadastro, troca e recuperação de senha só devolvem para a tela de login:
    não criam conta, não criam token, não gravam senha;
  - com as variáveis SSO_*, o botão manda para o tenant e o client id certos;
  - o retorno exige o `state` que a própria sessão gerou (sem isso, qualquer
    link forjado logaria a vítima na conta de outra pessoa);
  - a Microsoft só AUTENTICA: quem entra é quem tem cadastro ativo em
    `usuarios`. E-mail sem cadastro não vira conta nova — foi o furo da
    Cobrança em 23/09, onde login Microsoft + autocadastro deixavam as 258
    pessoas do tenant entrarem sozinhas;
  - conta de outro tenant ou token emitido para outro app é recusada;
  - quem cadastra é o admin, só com e-mail corporativo e sem senha;
  - o admin do sistema é a conta Microsoft do Matheus, não a caixa engenharia19@.
"""
import pytest
from werkzeug.security import generate_password_hash

import controle.auth as auth_mod
from app import app
from controle.db import get_db, init_db, row_to_dict, _admin_na_conta_microsoft, ADMIN_EMAIL

TENANT = '953ea640-963d-4af5-844c-03f21ebc048f'
CLIENT = '2875e663-8902-4b93-a0e0-01e007641fd2'
EMAIL_ATIVO = 'tecnico.sso@ocupacional.com.br'
EMAIL_INATIVO = 'inativo.sso@ocupacional.com.br'
EMAIL_ADMIN_TESTE = 'admin.sso@ocupacional.com.br'
EMAIL_NOVO = 'novo.sso@ocupacional.com.br'
ENG19 = 'engenharia19@ocupacional.com.br'
SENHA_CERTA = 'senha-certa-123'
TODOS = (EMAIL_ATIVO, EMAIL_INATIVO, EMAIL_ADMIN_TESTE, EMAIL_NOVO)


@pytest.fixture(autouse=True)
def _usuarios():
    init_db()
    with get_db() as conn:
        for e in TODOS:
            conn.execute('DELETE FROM usuarios WHERE email=?', (e,))
        # senha de verdade no hash: prova que nem a senha CERTA entra mais
        conn.execute("INSERT INTO usuarios (nome, email, senha_hash, ativo) VALUES (?,?,?,1)",
                     ('Tecnico SSO', EMAIL_ATIVO, generate_password_hash(SENHA_CERTA)))
        conn.execute("INSERT INTO usuarios (nome, email, senha_hash, ativo) VALUES (?,?,?,0)",
                     ('Inativo SSO', EMAIL_INATIVO, 'x'))
        conn.execute("INSERT INTO usuarios (nome, email, senha_hash, role, ativo) VALUES (?,?,?,?,1)",
                     ('Admin SSO', EMAIL_ADMIN_TESTE, '', 'admin'))
    yield
    with get_db() as conn:
        for e in TODOS:
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


def _linha(email):
    with get_db() as conn:
        r = conn.execute('SELECT * FROM usuarios WHERE lower(email)=?', (email.lower(),)).fetchone()
    return row_to_dict(r) if r else None


def _uid(email):
    return str(_linha(email)['id'])


def _logado_como(cli, email):
    with cli.session_transaction() as s:
        s['_user_id'] = _uid(email)
        s['_fresh'] = True
    return cli


# ── tela e senha ──────────────────────────────────────────────────────

@pytest.mark.parametrize('ligado', [True, False])
def test_tela_de_login_nao_tem_campo_de_senha(monkeypatch, ligado):
    if ligado:
        monkeypatch.setenv('SSO_CLIENT_ID', CLIENT)
        monkeypatch.setenv('SSO_CLIENT_SECRET', 'segredo-de-teste')
        monkeypatch.setenv('SSO_TENANT_ID', TENANT)
    else:
        for v in ('SSO_CLIENT_ID', 'SSO_CLIENT_SECRET', 'SSO_TENANT_ID'):
            monkeypatch.delenv(v, raising=False)
    with app.test_client() as cli:
        tela = cli.get('/auth/login')
    html = tela.get_data(as_text=True)
    assert tela.status_code == 200
    assert 'name="senha"' not in html and '<form' not in html
    assert 'Esqueci minha senha' not in html and 'Criar conta' not in html
    assert ('Entrar com Microsoft' in html) == ligado
    if not ligado:
        assert 'não está configurado' in html, 'sem o SSO a tela diz por que ninguém entra'


def test_senha_certa_nao_entra_mais(sso_ligado):
    with app.test_client() as cli:
        r = cli.post('/auth/login', data={'email': EMAIL_ATIVO, 'senha': SENHA_CERTA})
        with cli.session_transaction() as s:
            logado = s.get('_user_id')
    assert r.status_code == 400
    assert logado is None
    assert 'login por senha foi desligado' in r.get_data(as_text=True)


def test_rotas_de_senha_so_devolvem_para_o_login(sso_ligado):
    with get_db() as conn:
        tokens_antes = conn.execute('SELECT COUNT(*) AS n FROM password_reset_tokens').fetchone()['n']
    with app.test_client() as cli:
        r1 = cli.post('/auth/register', data={'nome': 'Novo', 'email': EMAIL_NOVO, 'senha': 'abc123'})
        r2 = cli.post('/auth/esqueci-senha', data={'email': EMAIL_ATIVO})
        r3 = cli.post('/auth/reset-senha/qualquer', data={'nova_senha': 'nova123', 'confirma': 'nova123'})
    for r in (r1, r2, r3):
        assert r.status_code == 302 and r.headers['Location'].endswith('/auth/login')
    assert _linha(EMAIL_NOVO) is None, 'autocadastro nao pode criar conta'
    with get_db() as conn:
        tokens_depois = conn.execute('SELECT COUNT(*) AS n FROM password_reset_tokens').fetchone()['n']
    assert tokens_depois == tokens_antes, 'recuperacao de senha nao pode gerar link'


def test_trocar_senha_nao_grava_nada(sso_ligado):
    hash_antes = _linha(EMAIL_ATIVO)['senha_hash']
    with app.test_client() as cli:
        _logado_como(cli, EMAIL_ATIVO)
        r = cli.post('/auth/alterar-senha', data={'senha_atual': SENHA_CERTA,
                                                   'nova_senha': 'outra123', 'confirma': 'outra123'})
    assert r.status_code == 302
    assert _linha(EMAIL_ATIVO)['senha_hash'] == hash_antes


# ── Microsoft ─────────────────────────────────────────────────────────

def test_desligado_rota_microsoft_volta_ao_login(sso_desligado):
    with app.test_client() as cli:
        r = cli.get('/auth/microsoft')
        assert r.status_code == 302 and r.headers['Location'].endswith('/auth/login')
        r = cli.get('/auth/gate-callback?code=x&state=y')
        assert r.status_code == 302 and r.headers['Location'].endswith('/auth/login')


def test_ligado_manda_para_o_tenant(sso_ligado):
    with app.test_client() as cli:
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


# ── cadastro pelo admin ───────────────────────────────────────────────

def test_admin_cadastra_so_com_email_corporativo(sso_ligado, monkeypatch):
    with app.test_client() as cli:
        _logado_como(cli, EMAIL_ADMIN_TESTE)
        r = cli.post('/controle/admin/usuarios', json={'nome': 'Novo', 'email': EMAIL_NOVO.upper()})
        assert r.status_code == 201, r.get_data(as_text=True)
        assert cli.post('/controle/admin/usuarios',
                        json={'nome': 'Novo', 'email': EMAIL_NOVO}).status_code == 409
        assert cli.post('/controle/admin/usuarios',
                        json={'nome': 'Fora', 'email': 'fora@gmail.com'}).status_code == 400
        assert cli.post('/controle/admin/usuarios',
                        json={'nome': 'X', 'email': 'x@ocupacional.com.br', 'role': 'dono'}).status_code == 400
    novo = _linha(EMAIL_NOVO)
    assert novo['ativo'] == 1 and novo['role'] == 'tecnico' and novo['senha_hash'] == ''
    # e o cadastrado entra pela Microsoft
    r, logado = _retorno(monkeypatch, _claims(EMAIL_NOVO))
    assert logado == str(novo['id'])


def test_tecnico_nao_cadastra_usuario(sso_ligado):
    with app.test_client() as cli:
        _logado_como(cli, EMAIL_ATIVO)
        r = cli.post('/controle/admin/usuarios', json={'nome': 'Novo', 'email': EMAIL_NOVO})
    assert r.status_code == 403
    assert _linha(EMAIL_NOVO) is None


# ── admin do sistema ──────────────────────────────────────────────────

def test_admin_sai_da_caixa_engenharia19_para_a_conta_microsoft():
    with get_db() as conn:
        conn.execute('DELETE FROM usuarios WHERE lower(email) IN (?,?)', (ENG19, ADMIN_EMAIL))
        conn.execute("INSERT INTO usuarios (nome, email, senha_hash, role, ativo) "
                     "VALUES ('Matheus', ?, 'x', 'tecnico', 0)", (ENG19,))
        uid = conn.execute('SELECT id FROM usuarios WHERE email=?', (ENG19,)).fetchone()['id']
        _admin_na_conta_microsoft(conn)
        _admin_na_conta_microsoft(conn)   # idempotente
    assert _linha(ENG19) is None, 'a caixa operacional nao pode continuar sendo admin'
    admin = _linha(ADMIN_EMAIL)
    assert admin['id'] == uid, 'a mesma linha muda de e-mail: historico e sessao ficam'
    assert admin['role'] == 'admin' and admin['ativo'] == 1


def test_admin_nao_move_se_a_conta_microsoft_ja_existe():
    with get_db() as conn:
        conn.execute('DELETE FROM usuarios WHERE lower(email) IN (?,?)', (ENG19, ADMIN_EMAIL))
        conn.execute("INSERT INTO usuarios (nome, email, senha_hash, role, ativo) "
                     "VALUES ('Caixa', ?, 'x', 'tecnico', 1)", (ENG19,))
        conn.execute("INSERT INTO usuarios (nome, email, senha_hash, role, ativo) "
                     "VALUES ('Matheus', ?, '', 'tecnico', 0)", (ADMIN_EMAIL,))
        _admin_na_conta_microsoft(conn)   # a coluna e UNIQUE: nao pode estourar
        conn.execute('DELETE FROM usuarios WHERE lower(email)=?', (ENG19,))
    admin = _linha(ADMIN_EMAIL)
    assert admin['role'] == 'admin' and admin['ativo'] == 1
