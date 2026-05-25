# -*- coding: utf-8 -*-
"""Autenticação de usuários — login, cadastro, logout."""
from flask import Blueprint, request, redirect, url_for, render_template
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash

from .db import get_db, row_to_dict

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
        else:
            try:
                senha_hash = generate_password_hash(senha)
                with get_db() as conn:
                    conn.execute(
                        'INSERT INTO usuarios (nome, email, senha_hash, registro_mte) VALUES (?,?,?,?)',
                        (nome, email, senha_hash, registro_mte)
                    )
                return redirect(url_for('auth.login') + '?ok=1')
            except Exception as e:
                msg = str(e).lower()
                if 'unique' in msg or 'duplicate' in msg:
                    erro = 'Este email já está cadastrado.'
                else:
                    erro = f'Erro ao cadastrar: {e}'

    return render_template('register.html', erro=erro)


@auth_bp.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('auth.login'))
