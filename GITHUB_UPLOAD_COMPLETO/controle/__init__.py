# -*- coding: utf-8 -*-
"""Modulo Controle de Medicoes e Amostradores.
Isolado do gerador de PGR via Flask Blueprint.
"""
from .routes import controle_bp
from .db import init_db

__all__ = ['controle_bp', 'init_db']
