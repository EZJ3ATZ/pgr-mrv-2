# -*- coding: utf-8 -*-
"""Modulo Controle de Medicoes e Amostradores.
Isolado do gerador de PGR via Flask Blueprint.
"""
from .routes import controle_bp
from .auth import auth_bp, login_manager
from .db import init_db

__all__ = ['controle_bp', 'auth_bp', 'login_manager', 'init_db']
