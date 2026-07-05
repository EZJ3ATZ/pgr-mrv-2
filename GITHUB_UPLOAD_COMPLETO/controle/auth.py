# -*- coding: utf-8 -*-
"""Autenticação de usuários — login, cadastro, logout."""
import os
import secrets
from datetime import datetime, timedelta
from flask import Blueprint, request, redirect, url_for, render_template
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

    return render_template('login.html', erro=erro)


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
