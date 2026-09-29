# -*- coding: utf-8 -*-
"""Autenticação de usuários — login só pela Microsoft, logout."""
import base64
import json
import os
import secrets
import urllib.parse
import urllib.request
from flask import Blueprint, request, redirect, url_for, render_template, session
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user

from .db import get_db, row_to_dict, registrar_evento

auth_bp = Blueprint('auth', __name__, url_prefix='/auth')
login_manager = LoginManager()
login_manager.login_view = 'auth.login'
login_manager.login_message = ''


class User(UserMixin):
    def __init__(self, id, nome, email, registro_mte, role):
        self.id = str(id)
        self.nome = nome
        self.email = email
        self.registro_mte = registro_mte or ''
        self.role = role or 'tecnico'


@login_manager.user_loader
def load_user(user_id):
    try:
        with get_db() as conn:
            row = conn.execute(
                'SELECT * FROM usuarios WHERE id=? AND ativo=1', (user_id,)
            ).fetchone()
        if row:
            d = row_to_dict(row)
            return User(d['id'], d['nome'], d['email'],
                        d.get('registro_mte', ''), d.get('role', 'tecnico'))
    except Exception:
        pass
    return None


# ── Login SÓ pela Microsoft (registro SSO-Medicoes no Entra) ──────────
# Regra do Matheus (28/09/2026) para todo sistema da frente do Azure: não existe
# usuário e senha. Login por senha, autocadastro, troca e recuperação de senha
# saíram; as rotas antigas só devolvem para a tela de login.
# Registro PRÓPRIO de login, separado do app do Graph (AZURE_*), que tem
# permissão de aplicação e não serve para identificar pessoa.
# Variáveis (Railway) — sem as três primeiras ninguém entra, e a tela avisa:
#   SSO_CLIENT_ID      client id do registro SSO-Medicoes
#   SSO_CLIENT_SECRET  segredo do registro
#   SSO_TENANT_ID      GUID do tenant (não o domínio: é comparado com a claim tid)
#   SSO_REDIRECT_URI   padrão abaixo; tem de ser idêntico ao cadastrado no registro
# A Microsoft só autentica. Quem ENTRA é quem tem cadastro ativo em `usuarios`
# (o admin cadastra o e-mail em /controle/admin/usuarios): e-mail sem cadastro
# não vira conta nova (lição da Cobrança, 23/09, onde login Microsoft +
# autocadastro davam acesso a qualquer um do tenant).
_SSO_REDIRECT_PADRAO = 'https://medicoes-ocupacional.up.railway.app/auth/gate-callback'


def _sso_cfg():
    """Lida a cada chamada (e não no import) para o teste poder ligar e desligar."""
    return {
        'client_id': os.environ.get('SSO_CLIENT_ID', '').strip(),
        'secret':    os.environ.get('SSO_CLIENT_SECRET', '').strip(),
        'tenant':    os.environ.get('SSO_TENANT_ID', '').strip(),
        'redirect':  os.environ.get('SSO_REDIRECT_URI', '').strip() or _SSO_REDIRECT_PADRAO,
    }


def sso_ativo():
    c = _sso_cfg()
    return bool(c['client_id'] and c['secret'] and c['tenant'])


@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('index'))
    if request.method == 'POST':
        # formulário de senha antigo (aba aberta, página guardada offline): não
        # confere senha nenhuma
        return _tela_login('O login por senha foi desligado. Use Entrar com Microsoft.', 400)
    return render_template('login.html', erro=None, sso=sso_ativo())


def _payload_jwt(token):
    """Claims do id_token. Sem checar assinatura: o token vem direto do endpoint
    de token da Microsoft, por TLS, em troca do nosso código + segredo (OIDC
    Core 3.1.3.7). tid e aud são conferidos por quem chama."""
    try:
        seg = token.split('.')[1]
        seg += '=' * (-len(seg) % 4)
        return json.loads(base64.urlsafe_b64decode(seg.encode()).decode())
    except Exception:
        return None


def _trocar_codigo(cfg, code):
    """Troca o código do retorno pelo id_token. Devolve as claims ou None."""
    corpo = urllib.parse.urlencode({
        'grant_type':    'authorization_code',
        'code':          code,
        'redirect_uri':  cfg['redirect'],
        'client_id':     cfg['client_id'],
        'client_secret': cfg['secret'],
        'scope':         'openid profile email',
    }).encode()
    req = urllib.request.Request(
        f"https://login.microsoftonline.com/{cfg['tenant']}/oauth2/v2.0/token",
        data=corpo, method='POST',
        headers={'Content-Type': 'application/x-www-form-urlencoded'})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            tok = json.loads(r.read().decode())
    except Exception as e:
        # nunca logar o código nem o corpo: carregam credencial
        print(f'[auth] login Microsoft: troca do código falhou ({type(e).__name__})')
        return None
    return _payload_jwt(tok.get('id_token', ''))


def _tela_login(erro, status):
    return render_template('login.html', erro=erro, sso=sso_ativo()), status


@auth_bp.route('/microsoft')
def microsoft():
    if not sso_ativo():
        return redirect(url_for('auth.login'))
    if current_user.is_authenticated:
        return redirect(url_for('index'))
    cfg = _sso_cfg()
    state = secrets.token_urlsafe(24)
    session['sso_state'] = state
    q = urllib.parse.urlencode({
        'client_id':     cfg['client_id'],
        'response_type': 'code',
        'redirect_uri':  cfg['redirect'],
        'response_mode': 'query',
        'scope':         'openid profile email',
        'state':         state,
    })
    return redirect(f"https://login.microsoftonline.com/{cfg['tenant']}/oauth2/v2.0/authorize?{q}")


@auth_bp.route('/gate-callback')
def gate_callback():
    if not sso_ativo():
        return redirect(url_for('auth.login'))
    if request.args.get('error'):
        return _tela_login('O login pela Microsoft foi cancelado ou recusado.', 400)

    code = request.args.get('code', '')
    state = request.args.get('state', '')
    esperado = session.pop('sso_state', None)
    if not code or not esperado or not secrets.compare_digest(state, esperado):
        return _tela_login('O login expirou. Clique em Entrar com Microsoft de novo.', 400)

    cfg = _sso_cfg()
    claims = _trocar_codigo(cfg, code)
    if not claims:
        return _tela_login('Não deu para confirmar o login na Microsoft. Tente de novo.', 502)
    if claims.get('tid') != cfg['tenant'] or claims.get('aud') != cfg['client_id']:
        return _tela_login('Esta conta não é da Ocupacional.', 403)

    email = (claims.get('preferred_username') or claims.get('upn')
             or claims.get('email') or '').strip().lower()
    if not email:
        return _tela_login('A Microsoft não informou o e-mail da conta.', 403)

    with get_db() as conn:
        row = conn.execute(
            'SELECT * FROM usuarios WHERE lower(email)=? AND ativo=1', (email,)
        ).fetchone()
    if not row:
        registrar_evento('login_negado', f'Microsoft: {email} sem cadastro ativo',
                         usuario=email, ip=request.remote_addr)
        return _tela_login(f'A conta {email} ainda não tem acesso liberado. '
                           'Peça ao administrador para cadastrar o seu e-mail.', 403)

    d = row_to_dict(row)
    user = User(d['id'], d['nome'], d['email'],
                d.get('registro_mte', ''), d.get('role', 'tecnico'))
    login_user(user, remember=True)
    registrar_evento('login', f'{user.nome} ({email}) via Microsoft',
                     usuario=user.nome, ip=request.remote_addr)
    return redirect(url_for('index'))


@auth_bp.route('/logout')
@login_required
def logout():
    registrar_evento('logout', current_user.nome, usuario=current_user.nome, ip=request.remote_addr)
    logout_user()
    return redirect(url_for('auth.login'))


# ── Rotas de senha de antes do login só-Microsoft ─────────────────────
# Ficam só para link antigo, favorito ou e-mail de redefinição ainda na caixa de
# alguém cair na tela de login em vez de dar 404. Não criam conta, não mandam
# e-mail e não gravam senha.
@auth_bp.route('/register', methods=['GET', 'POST'])
def register():
    return redirect(url_for('auth.login'))


@auth_bp.route('/alterar-senha', methods=['GET', 'POST'])
def alterar_senha():
    return redirect(url_for('index') if current_user.is_authenticated else url_for('auth.login'))


@auth_bp.route('/esqueci-senha', methods=['GET', 'POST'])
def esqueci_senha():
    return redirect(url_for('auth.login'))


@auth_bp.route('/reset-senha/<token>', methods=['GET', 'POST'])
def reset_senha(token):
    return redirect(url_for('auth.login'))
