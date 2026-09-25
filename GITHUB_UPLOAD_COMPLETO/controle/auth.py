# -*- coding: utf-8 -*-
"""Autenticação de usuários — login, cadastro, logout."""
import base64
import json
import os
import secrets
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from flask import Blueprint, request, redirect, url_for, render_template, session
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash

from .db import get_db, row_to_dict, registrar_evento

# Email do remetente (usuário M365 com permissão Mail.Send)
MAIL_SENDER = os.environ.get('MAIL_SENDER', 'engenharia19@ocupacional.com.br')

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


# ── Login Microsoft (registro SSO-Medicoes no Entra) ──────────────────
# Registro PRÓPRIO de login, separado do app do Graph (AZURE_*), que tem
# permissão de aplicação e não serve para identificar pessoa.
# Variáveis (Railway), todas opcionais — sem as três primeiras o botão não
# aparece e o login por senha segue exatamente como antes:
#   SSO_CLIENT_ID      client id do registro SSO-Medicoes
#   SSO_CLIENT_SECRET  segredo do registro
#   SSO_TENANT_ID      GUID do tenant (não o domínio: é comparado com a claim tid)
#   SSO_REDIRECT_URI   padrão abaixo; tem de ser idêntico ao cadastrado no registro
# A Microsoft só autentica. Quem ENTRA continua sendo quem tem cadastro ativo em
# `usuarios`: e-mail sem cadastro não vira conta nova (lição da Cobrança, 23/09,
# onde login Microsoft + autocadastro davam acesso a qualquer um do tenant).
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

    erro = None
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        senha = request.form.get('senha', '')
        try:
            with get_db() as conn:
                row = conn.execute(
                    'SELECT * FROM usuarios WHERE email=? AND ativo=1', (email,)
                ).fetchone()
            if row:
                d = row_to_dict(row)
                if check_password_hash(d['senha_hash'], senha):
                    user = User(d['id'], d['nome'], d['email'],
                                d.get('registro_mte', ''), d.get('role', 'tecnico'))
                    login_user(user, remember=True)
                    registrar_evento('login', f'{user.nome} ({email})',
                                     usuario=user.nome, ip=request.remote_addr)
                    return redirect(url_for('index'))
            erro = 'Email ou senha incorretos.'
        except Exception as e:
            erro = f'Erro: {e}'

    return render_template('login.html', erro=erro, sso=sso_ativo())


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
                           'Peça ao administrador para aprovar o seu cadastro.', 403)

    d = row_to_dict(row)
    user = User(d['id'], d['nome'], d['email'],
                d.get('registro_mte', ''), d.get('role', 'tecnico'))
    login_user(user, remember=True)
    registrar_evento('login', f'{user.nome} ({email}) via Microsoft',
                     usuario=user.nome, ip=request.remote_addr)
    return redirect(url_for('index'))


@auth_bp.route('/register', methods=['GET', 'POST'])
def register():
    if current_user.is_authenticated:
        return redirect(url_for('index'))

    erro = None
    if request.method == 'POST':
        nome         = request.form.get('nome', '').strip()
        email        = request.form.get('email', '').strip().lower()
        senha        = request.form.get('senha', '')
        registro_mte = request.form.get('registro_mte', '').strip()

        if not nome or not email or not senha:
            erro = 'Preencha todos os campos obrigatórios.'
        elif not email.endswith('@ocupacional.com.br'):
            erro = 'Somente e-mails @ocupacional.com.br podem criar conta.'
        else:
            try:
                senha_hash = generate_password_hash(senha)
                with get_db() as conn:
                    # ativo=0: conta nasce PENDENTE — admin aprova na tela de usuários.
                    # Sem isso qualquer um com e-mail @ocupacional entrava direto.
                    conn.execute(
                        'INSERT INTO usuarios (nome, email, senha_hash, registro_mte, ativo) VALUES (?,?,?,?,0)',
                        (nome, email, senha_hash, registro_mte)
                    )
                return redirect(url_for('auth.login') + '?pendente=1')
            except Exception as e:
                msg = str(e).lower()
                if 'unique' in msg or 'duplicate' in msg:
                    erro = 'Este email já está cadastrado.'
                else:
                    erro = f'Erro ao cadastrar: {e}'

    return render_template('register.html', erro=erro)


@auth_bp.route('/alterar-senha', methods=['GET', 'POST'])
@login_required
def alterar_senha():
    erro = None
    ok = None
    if request.method == 'POST':
        senha_atual  = request.form.get('senha_atual', '')
        nova_senha   = request.form.get('nova_senha', '')
        confirma     = request.form.get('confirma', '')

        if not senha_atual or not nova_senha or not confirma:
            erro = 'Preencha todos os campos.'
        elif nova_senha != confirma:
            erro = 'A nova senha e a confirmação não coincidem.'
        elif len(nova_senha) < 6:
            erro = 'A nova senha deve ter pelo menos 6 caracteres.'
        else:
            try:
                with get_db() as conn:
                    row = conn.execute(
                        'SELECT * FROM usuarios WHERE id=?', (current_user.id,)
                    ).fetchone()
                d = row_to_dict(row)
                if not check_password_hash(d['senha_hash'], senha_atual):
                    erro = 'Senha atual incorreta.'
                else:
                    novo_hash = generate_password_hash(nova_senha)
                    with get_db() as conn:
                        conn.execute(
                            'UPDATE usuarios SET senha_hash=? WHERE id=?',
                            (novo_hash, current_user.id)
                        )
                    ok = 'Senha alterada com sucesso!'
            except Exception as e:
                erro = f'Erro: {e}'

    return render_template('alterar_senha.html', erro=erro, ok=ok)


@auth_bp.route('/logout')
@login_required
def logout():
    registrar_evento('logout', current_user.nome, usuario=current_user.nome, ip=request.remote_addr)
    logout_user()
    return redirect(url_for('auth.login'))


@auth_bp.route('/esqueci-senha', methods=['GET', 'POST'])
def esqueci_senha():
    erro = None
    ok = None
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        try:
            with get_db() as conn:
                row = conn.execute(
                    'SELECT id, nome FROM usuarios WHERE email=? AND ativo=1', (email,)
                ).fetchone()
            if row:
                d = row_to_dict(row)
                token = secrets.token_urlsafe(32)
                expira = (datetime.utcnow() + timedelta(hours=2)).strftime('%Y-%m-%d %H:%M:%S')
                with get_db() as conn:
                    conn.execute(
                        'INSERT INTO password_reset_tokens (user_id, token, expira_em) VALUES (?,?,?)',
                        (d['id'], token, expira)
                    )
                # Monta link de reset
                base_url = request.host_url.rstrip('/')
                link = f"{base_url}/auth/reset-senha/{token}"
                # Envia email via Graph
                try:
                    from .graph import graph_post, graph_ok
                    if graph_ok():
                        graph_post(f'/users/{MAIL_SENDER}/sendMail', {
                            'message': {
                                'subject': 'Redefinição de senha — Ocupacional SST',
                                'body': {
                                    'contentType': 'HTML',
                                    'content': f'''
                                        <p>Olá, <strong>{d["nome"]}</strong>!</p>
                                        <p>Recebemos uma solicitação para redefinir sua senha no portal <strong>Ocupacional SST</strong>.</p>
                                        <p><a href="{link}" style="background:#2DD4BF;color:#07090E;padding:10px 20px;border-radius:6px;text-decoration:none;font-weight:700;">Redefinir senha</a></p>
                                        <p>O link expira em <strong>2 horas</strong>.</p>
                                        <p>Se você não solicitou isso, ignore este e-mail.</p>
                                    '''
                                },
                                'toRecipients': [{'emailAddress': {'address': email}}]
                            },
                            'saveToSentItems': False
                        })
                except Exception as e:
                    print(f'[auth] email reset erro: {e}')
            # Sempre mostra msg genérica (segurança)
            ok = 'Se o e-mail estiver cadastrado, você receberá as instruções em breve.'
        except Exception as e:
            erro = f'Erro: {e}'
    return render_template('esqueci_senha.html', erro=erro, ok=ok)


@auth_bp.route('/reset-senha/<token>', methods=['GET', 'POST'])
def reset_senha(token):
    erro = None
    # Valida token
    try:
        now = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
        with get_db() as conn:
            row = conn.execute(
                'SELECT * FROM password_reset_tokens WHERE token=? AND usado=0 AND expira_em > ?',
                (token, now)
            ).fetchone()
        if not row:
            return render_template('reset_senha.html', token=token, erro='Link inválido ou expirado.', expirado=True)
        t = row_to_dict(row)
    except Exception as e:
        return render_template('reset_senha.html', token=token, erro=f'Erro: {e}', expirado=True)

    if request.method == 'POST':
        nova_senha = request.form.get('nova_senha', '')
        confirma   = request.form.get('confirma', '')
        if len(nova_senha) < 6:
            erro = 'A senha deve ter pelo menos 6 caracteres.'
        elif nova_senha != confirma:
            erro = 'As senhas não coincidem.'
        else:
            try:
                novo_hash = generate_password_hash(nova_senha)
                with get_db() as conn:
                    conn.execute('UPDATE usuarios SET senha_hash=? WHERE id=?', (novo_hash, t['user_id']))
                    conn.execute('UPDATE password_reset_tokens SET usado=1 WHERE id=?', (t['id'],))
                return redirect(url_for('auth.login') + '?senha_ok=1')
            except Exception as e:
                erro = f'Erro: {e}'

    return render_template('reset_senha.html', token=token, erro=erro, expirado=False)
