# -*- coding: utf-8 -*-
"""Casos fixos dos geradores de documento + extração de texto.

Usado por `test_laudos_golden.py`. Payload FIXO de propósito: o valor do golden é
justamente não mudar. Se um caso precisar mudar, o gabarito muda junto e o diff
aparece na revisão — que é o ponto.

Por que existe: até 28/07/2026 os 4 geradores de documento não tinham NENHUM teste,
enquanto 15 bugs de documento já haviam sido achados à mão (auditorias de 12/06 e
21/07). Golden test pega REGRESSÃO — não prova que o número está certo. A conferência
do gabarito é do Matheus.
"""
import io
import re
import zipfile

# ── Empresa de teste (todos os casos usam a mesma) ───────────────────────────
EMPRESA = {
    'razaoSocial': 'GOLDEN INDUSTRIA LTDA',
    'nomeFantasia': 'GOLDEN',
    'cnpj': '12.345.678/0001-90',
    'endereco': 'Rua Teste, 100',
    'bairro': 'Centro',
    'cidade': 'Belo Horizonte',
    'uf': 'MG',
    'cep': '30000-000',
    'cnae': '25.11-0-00',
    'descricaoCnae': 'Fabricacao de estruturas metalicas',
    'grauRisco': '3',
    'telefone': '(31) 3333-3333',
    'email': 'contato@golden.com.br',
    'responsavel': 'Responsavel Teste',
}

# ── QUÍMICO ──────────────────────────────────────────────────────────────────
# Tolueno com vazão 0,100–0,102 L/min por 240 min → volume 24,0 L.
# Vi != Vf de propósito (item 6 do Bernardo: variação de vazão tem que ser real).
# LT NR-15 78 ppm, TWA ACGIH 20 ppm, concentração 18,5 → abaixo do LT NR-15 mas
# perto do TWA; é o caso que exercita conclusão por limite.
QUIMICO = {
    'empresa': EMPRESA,
    'config': {
        'bomba': 'SKC AirChek',
        'bombaSN': 'A63555',
        'calibrador': 'DEFENDER 510M S/N:126958',
    },
    'avaliacoes': [{
        'agente': 'Tolueno',
        'trabalhador': 'Ana Souza',
        'cargo': 'Pintor',
        'setor': 'Acabamento',
        'jornada': '8h48',
        'tempoExposicao': '480',
        'metodo': 'NIOSH 1501',
        'amostradorDesc': 'Tubo de carvao SKC 226-01',
        'acessorios': '',
        'filtroNumero': 'TCP1001',
        'bomba': 'SKC AirChek',
        'bombaSN': 'A63555',
        'vazao': '0,100',
        'vazaoInicial': '0,100',
        'vazaoFinal': '0,102',
        'tempoColeta': '240',
        'volume': '24,0',
        'concentracao': '18,5',
        'ltNR15': '78',
        'ltTWA': '20',
        'ltSTEL': '',
        'naNR15': '39',
        'naTWA': '10',
        'dataColeta': '10/06/2026',
        'dataAnalise': '20/06/2026',
        'pontoEstacionario': '',
        'fonte': 'Laboratorio X',
        'conclusao': '',
    }],
}

# ── RUÍDO ────────────────────────────────────────────────────────────────────
# 2 avaliações: uma ACIMA do LT (86,4 Q5) e uma ABAIXO (79,2 Q5).
# Q5 (NR-15) e Q3 (NHO-01) juntos — a conclusão dupla é decisão do Matheus
# (commit 4ab2c95). O quadro resumo tem que trazer as DUAS avaliações; a linha
# fantasma "Coordenador de base 80,5" do template não pode reaparecer.
RUIDO = {
    'empresa': EMPRESA,
    'tecnico': 'Matheus Costa',
    'dataLaudo': '10/06/2026',
    'avaliacoes': [
        {'setor': 'Producao', 'cargo': 'Operador de Prensa',
         'lavgQ5': '86,4', 'lavgQ3': '88,1', 'nenQ5': '87,0', 'nenQ3': '89,2'},
        {'setor': 'Expedicao', 'cargo': 'Conferente',
         'lavgQ5': '79,2', 'lavgQ3': '80,0', 'nenQ5': '79,8', 'nenQ3': '80,5'},
    ],
}

# ── CALOR ────────────────────────────────────────────────────────────────────
# Setor 1: M=350 W, tbs preenchido → céu aberto (0,7tbn + 0,1tbs + 0,2tg).
# Setor 2: tbs vazio → ambiente interno (0,7tbn + 0,3tg).
# M=350 W cai numa faixa que o `_NR15_QUADRO1` truncado em 346 W errava
# (regressão do bug normativo do commit a4e23b5 — limite saía permissivo demais).
# A medição mora em setores[].pontos[] — o IBUTG médio é ponderado pelo tempo de
# cada ponto, e o limite vem do M médio ponderado (não do M do pior ponto).
CALOR = {
    'empresa': EMPRESA,
    'avaliacao': {'data': '10/06/2026', 'tecnico': 'Matheus Costa'},
    'setores': [
        {'nome': 'FORNO', 'horario': '08:00 as 17:00',
         'vestimenta': 'Uniforme de Trabalho (0)',
         # Números, não strings: o form usa <input type="number"> e o envio faz
         # `tbn: +p.tbn` (index.html:7298). É o que a UI real manda.
         'pontos': [
             # céu aberto: tbs preenchido → 0,7*25 + 0,1*32 + 0,2*38 = 28,3
             {'local': 'Boca do forno', 'atividade': 'Operacao de forno', 'M': 350,
              'tbn': 25.0, 'tbs': 32.0, 'tg': 38.0, 'tempo': 30},
             # interno: tbs vazio → 0,7*24 + 0,3*30 = 25,8
             {'local': 'Painel de controle', 'atividade': 'Supervisao', 'M': 250,
              'tbn': 24.0, 'tbs': '', 'tg': 30.0, 'tempo': 30},
         ]},
        {'nome': 'ALMOXARIFADO', 'horario': '08:00 as 17:00',
         'vestimenta': 'Uniforme de Trabalho (0)',
         'pontos': [
             # interno: 0,7*22 + 0,3*26 = 23,2 — abaixo do limite, caso conforme
             {'local': 'Estoque', 'atividade': 'Movimentacao leve', 'M': 150,
              'tbn': 22.0, 'tbs': '', 'tg': 26.0, 'tempo': 60},
         ]},
    ],
}

# IBUTG esperado por ponto (conferido à mão contra a fórmula da NHO 06 / Anexo 3).
# Serve de asserção independente do golden: se a fórmula mudar, isto acusa.
CALOR_IBUTG_ESPERADO = {
    'Boca do forno': 28.3,       # 0,7*25,0 + 0,1*32,0 + 0,2*38,0  (céu aberto)
    'Painel de controle': 25.8,  # 0,7*24,0 + 0,3*30,0             (interno)
    'Estoque': 23.2,             # 0,7*22,0 + 0,3*26,0             (interno)
}

# ── PGR ──────────────────────────────────────────────────────────────────────
# 3 cargos: a tabela Setor/Cargo/Nº tem que clonar uma linha por cargo
# (regressão do bug do `replace` único — antes só o 1º cargo aparecia).
PGR = {
    'nome': 'GOLDEN INDUSTRIA LTDA',
    'cnpj': '12.345.678/0001-90',
    'rua': 'Rua Teste',
    'numero': '100',
    'complemento': '',
    'cep': '30000-000',
    'bairro': 'Centro',
    'cidade': 'Belo Horizonte',
    'uf': 'MG',
    'cargos': ['SOLDADOR', 'PINTOR', 'AUXILIAR DE PRODUCAO'],
}


# ── Extração e normalização ──────────────────────────────────────────────────
def docx_texto(blob):
    """Texto corrido do word/document.xml. Um `\\n` por parágrafo."""
    zin = zipfile.ZipFile(io.BytesIO(blob))
    xml = zin.read('word/document.xml').decode('utf-8')
    xml = xml.replace('</w:p>', '</w:p>\n')
    txt = re.sub(r'<[^>]+>', '', xml)
    linhas = [re.sub(r'[ \t ]+', ' ', ln).strip() for ln in txt.split('\n')]
    return '\n'.join(ln for ln in linhas if ln)


def pdf_texto(blob):
    from pypdf import PdfReader
    r = PdfReader(io.BytesIO(blob))
    txt = '\n'.join((p.extract_text() or '') for p in r.pages)
    linhas = [re.sub(r'[ \t ]+', ' ', ln).strip() for ln in txt.split('\n')]
    return '\n'.join(ln for ln in linhas if ln)


def normalizar(txt):
    """Tira o que muda a cada execução, senão o golden quebra sozinho amanhã.

    O documento carimba a data de emissão (`mes_ano()`), então mês/ano corrente e
    a data de hoje viram marcador. NÃO normalizo as datas do payload (10/06/2026,
    20/06/2026) — essas são dado de teste e mudar é justamente o que quero pegar.
    """
    import datetime
    hoje = datetime.date.today()
    meses = ['Janeiro', 'Fevereiro', 'Março', 'Abril', 'Maio', 'Junho', 'Julho',
             'Agosto', 'Setembro', 'Outubro', 'Novembro', 'Dezembro']
    mes_ano_corrente = f'{meses[hoje.month - 1]} / {hoje.year}'
    for alvo in (mes_ano_corrente,
                 mes_ano_corrente.replace(' / ', '/'),
                 hoje.strftime('%d/%m/%Y'),
                 hoje.isoformat()):
        txt = txt.replace(alvo, '<DATA-DE-EMISSAO>')
    # ids sequenciais de imagem/relacionamento que o gerador incrementa
    txt = re.sub(r'\brId\d+\b', 'rIdN', txt)
    return txt
