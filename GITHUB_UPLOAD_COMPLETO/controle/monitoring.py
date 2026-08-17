# -*- coding: utf-8 -*-
"""
monitoring.py — Observabilidade central do sistema.

Integra:
  - Sentry SDK (erros + exceptions + contexto operacional)
  - Logs estruturados JSON (stdout + BetterStack opcional)
  - PostHog (eventos server-side)
  - Health check do banco

Configuração via variáveis de ambiente:
  SENTRY_DSN          — DSN do projeto Sentry
  BETTERSTACK_TOKEN   — Token do BetterStack/Logtail
  POSTHOG_KEY         — API key do PostHog
  RAILWAY_ENVIRONMENT — 'production' | 'staging' | 'development'
"""

import os
import json
import logging
import traceback
from datetime import datetime, timezone

# ── Configuração de ambiente ──────────────────────────────────────────
ENV         = os.environ.get('RAILWAY_ENVIRONMENT', 'development')
SENTRY_DSN  = os.environ.get('SENTRY_DSN', '')
BS_TOKEN    = os.environ.get('BETTERSTACK_TOKEN', '')
PH_KEY      = os.environ.get('POSTHOG_KEY', '')
PH_HOST     = os.environ.get('POSTHOG_HOST', 'https://app.posthog.com')
RELEASE     = os.environ.get('RAILWAY_GIT_COMMIT_SHA', 'local')[:8]


# ══════════════════════════════════════════════════════════════════════
# 1. LOGGER ESTRUTURADO (JSON)
# ══════════════════════════════════════════════════════════════════════

class _JSONFormatter(logging.Formatter):
    """Formata logs como JSON para facilitar parsing no BetterStack/Logtail."""
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            'ts':        datetime.now(timezone.utc).isoformat(),
            'level':     record.levelname,
            'logger':    record.name,
            'msg':       record.getMessage(),
            'env':       ENV,
            'release':   RELEASE,
        }
        if record.exc_info:
            payload['exception'] = self.formatException(record.exc_info)
        # Campos extras passados via `extra={}`
        for k, v in record.__dict__.items():
            if k not in ('name','msg','args','levelname','levelno','pathname','filename',
                         'module','exc_info','exc_text','stack_info','lineno','funcName',
                         'created','msecs','relativeCreated','thread','threadName',
                         'processName','process','message'):
                if not k.startswith('_'):
                    payload[k] = v
        return json.dumps(payload, ensure_ascii=False, default=str)


class _BetterStackHandler(logging.Handler):
    """Envia logs para BetterStack via HTTP em background (não bloqueia)."""
    def __init__(self, token: str):
        super().__init__()
        self.token = token
        self._url  = 'https://in.logs.betterstack.com'

    def emit(self, record: logging.LogRecord):
        try:
            import urllib.request
            payload = json.dumps({
                'dt':      datetime.now(timezone.utc).isoformat(),
                'level':   record.levelname.lower(),
                'message': record.getMessage(),
                'logger':  record.name,
                'env':     ENV,
                **{k: v for k, v in record.__dict__.items()
                   if not k.startswith('_') and k not in (
                       'name','msg','args','levelname','levelno','pathname',
                       'filename','module','exc_info','exc_text','stack_info',
                       'lineno','funcName','created','msecs','relativeCreated',
                       'thread','threadName','processName','process','message')},
            }, ensure_ascii=False, default=str).encode()
            req = urllib.request.Request(
                self._url,
                data=payload,
                headers={
                    'Authorization': f'Bearer {self.token}',
                    'Content-Type':  'application/json',
                },
                method='POST',
            )
            # TLS verificado (contexto padrão): BetterStack é endpoint público
            # com certificado válido. NÃO desligar a verificação — contexto não
            # verificado expõe o token de ingestão + o conteúdo dos logs a um
            # atacante on-path (MITM) entre o Railway e o BetterStack. CWE-295.
            urllib.request.urlopen(req, timeout=3)
        except Exception:
            pass  # nunca quebra a aplicação


def setup_logging():
    """Configura logging estruturado. Chame uma vez no startup."""
    root = logging.getLogger()
    root.setLevel(logging.INFO)

    # Console com JSON
    ch = logging.StreamHandler()
    ch.setFormatter(_JSONFormatter())
    # Remove handlers duplicados
    root.handlers = [h for h in root.handlers if not isinstance(h, logging.StreamHandler)]
    root.addHandler(ch)

    # BetterStack opcional
    if BS_TOKEN:
        bsh = _BetterStackHandler(BS_TOKEN)
        bsh.setLevel(logging.INFO)
        root.addHandler(bsh)
        logging.getLogger('ocupacional').info(
            'BetterStack logging ativado', extra={'service': 'monitoring'})

    return root


def get_logger(name: str) -> logging.Logger:
    """Retorna logger estruturado com prefixo 'ocupacional.<name>'."""
    return logging.getLogger(f'ocupacional.{name}')


# ══════════════════════════════════════════════════════════════════════
# 2. SENTRY
# ══════════════════════════════════════════════════════════════════════

_sentry_ok = False

def init_sentry(app=None):
    """Inicializa Sentry com Flask integration. Silencioso se sem DSN."""
    global _sentry_ok
    if not SENTRY_DSN:
        logging.getLogger('ocupacional').warning(
            'Sentry não configurado — defina SENTRY_DSN no Railway',
            extra={'service': 'monitoring'})
        return

    try:
        import sentry_sdk
        from sentry_sdk.integrations.flask     import FlaskIntegration
        from sentry_sdk.integrations.logging   import LoggingIntegration
        from sentry_sdk.integrations.threading import ThreadingIntegration

        sentry_logging = LoggingIntegration(
            level=logging.WARNING,
            event_level=logging.ERROR,
        )

        integrations = [
            FlaskIntegration(transaction_style='url'),
            sentry_logging,
            ThreadingIntegration(propagate_hub=True),
        ]

        sentry_sdk.init(
            dsn=SENTRY_DSN,
            integrations=integrations,
            traces_sample_rate=0.05,   # 5% das requests para performance
            profiles_sample_rate=0.01,
            environment=ENV,
            release=RELEASE,
            attach_stacktrace=True,
            send_default_pii=False,    # não envia dados pessoais
            before_send=_sentry_before_send,
        )
        _sentry_ok = True
        logging.getLogger('ocupacional').info(
            'Sentry inicializado', extra={'env': ENV, 'release': RELEASE})
    except ImportError:
        logging.getLogger('ocupacional').warning(
            'sentry-sdk não instalado — pip install sentry-sdk[flask]',
            extra={'service': 'monitoring'})
    except Exception as e:
        logging.getLogger('ocupacional').error(
            f'Erro ao iniciar Sentry: {e}', extra={'service': 'monitoring'})


def _sentry_before_send(event, hint):
    """Filtra eventos antes de enviar ao Sentry."""
    # Ignora erros de DB locked (são esperados durante sync)
    if 'database is locked' in str(event.get('exception', '')):
        return None
    return event


def capturar_erro(exc: Exception, **contexto):
    """Envia exceção ao Sentry com contexto adicional."""
    if not _sentry_ok:
        return
    try:
        import sentry_sdk
        with sentry_sdk.push_scope() as scope:
            for k, v in contexto.items():
                scope.set_tag(k, str(v))
            scope.set_context('detalhes', contexto)
            sentry_sdk.capture_exception(exc)
    except Exception:
        pass


def track_evento(evento: str, usuario: str = 'sistema', **props):
    """
    Rastreia evento operacional no PostHog.

    Uso:
        track_evento('planner_sync_concluido', criadas=100, erros=0)
        track_evento('demanda_criada', empresa='Acme', os='6482868')
    """
    if not PH_KEY:
        return
    try:
        import posthog
        posthog.api_key  = PH_KEY
        posthog.host     = PH_HOST
        posthog.capture(
            distinct_id=usuario,
            event=evento,
            properties={
                'env':     ENV,
                'release': RELEASE,
                **props,
            }
        )
    except ImportError:
        pass
    except Exception:
        pass


# ══════════════════════════════════════════════════════════════════════
# 4. HEALTH CHECK DO BANCO
# ══════════════════════════════════════════════════════════════════════

def diagnostico_banco() -> dict:
    """
    Retorna diagnóstico completo do banco de dados.
    Útil para endpoint /admin/saude e debugging.
    """
    try:
        from .db import get_db
    except ImportError:
        return {'erro': 'db não disponível'}

    resultado = {
        'timestamp': datetime.now(timezone.utc).isoformat(),
        'env': ENV,
        'release': RELEASE,
    }

    def _scalar(row):
        """Extrai o primeiro valor de fetchone() — funciona com SQLite (tuple) e PostgreSQL (dict)."""
        if row is None:
            return 0
        if isinstance(row, dict):
            return next(iter(row.values()), 0)
        return row[0]

    def _cnt(sql, params=None):
        try:
            return _scalar(conn.execute(sql, params).fetchone() if params else conn.execute(sql).fetchone())
        except Exception:
            return 0

    try:
        with get_db() as conn:
            # ── Empresas ──────────────────────────────────────────────
            resultado['empresas'] = {
                'total': _cnt('SELECT COUNT(*) FROM empresas'),
                'pendentes': _cnt("SELECT COUNT(*) FROM empresas WHERE pendente=1"),
                'sem_cnpj': _cnt("SELECT COUNT(*) FROM empresas WHERE cnpj IS NULL OR cnpj=''"),
                'duplicatas': _cnt("""
                    SELECT COUNT(*) FROM (
                        SELECT LOWER(TRIM(nome)) FROM empresas
                        GROUP BY LOWER(TRIM(nome)) HAVING COUNT(*)>1
                    ) AS sub
                """),
                'top_duplicatas': [dict(r) for r in conn.execute("""
                    SELECT LOWER(TRIM(nome)) AS nome_key, COUNT(*) AS qtd
                    FROM empresas GROUP BY LOWER(TRIM(nome))
                    HAVING COUNT(*)>1 ORDER BY qtd DESC LIMIT 10
                """).fetchall()],
            }

            # ── Demandas ──────────────────────────────────────────────
            resultado['demandas'] = {
                'total': _cnt('SELECT COUNT(*) FROM demandas'),
                'sem_empresa': _cnt(
                    'SELECT COUNT(*) FROM demandas WHERE empresa_id=0 OR empresa_id IS NULL'
                ),
                'por_tipo': {r['tipo_demanda'] or 'null': r['qtd'] for r in conn.execute("""
                    SELECT tipo_demanda, COUNT(*) AS qtd
                    FROM demandas GROUP BY tipo_demanda ORDER BY qtd DESC
                """).fetchall()},
                'por_status': {r['status'] or 'null': r['qtd'] for r in conn.execute("""
                    SELECT status, COUNT(*) AS qtd
                    FROM demandas GROUP BY status
                """).fetchall()},
                'por_origem': {r['origem'] or 'null': r['qtd'] for r in conn.execute("""
                    SELECT origem, COUNT(*) AS qtd
                    FROM demandas GROUP BY origem
                """).fetchall()},
            }

            # ── OS ────────────────────────────────────────────────────
            total_dem = resultado['demandas']['total'] or 1
            com_os = _cnt("SELECT COUNT(*) FROM demandas WHERE numero_os IS NOT NULL AND numero_os != ''")
            resultado['os'] = {
                'com_os': com_os,
                'sem_os': total_dem - com_os,
                'pct_com_os': round(com_os / total_dem * 100, 1),
                'sample_sem_os': [dict(r) for r in conn.execute("""
                    SELECT id, titulo, tipo_demanda, planner_bucket
                    FROM demandas
                    WHERE (numero_os IS NULL OR numero_os='')
                      AND origem='planner'
                      AND tipo_demanda='operacional'
                    LIMIT 5
                """).fetchall()],
            }

            # ── Planner ───────────────────────────────────────────────
            planner_total = _cnt("SELECT COUNT(*) FROM demandas WHERE origem='planner'")
            sem_match = _cnt("SELECT COUNT(*) FROM demandas WHERE origem='planner' AND (empresa_id=0 OR empresa_id IS NULL)")
            resultado['planner'] = {
                'total_tarefas': planner_total,
                'vinculadas': planner_total - sem_match,
                'sem_vinculo': sem_match,
                'pct_vinculadas': round((planner_total - sem_match) / max(planner_total, 1) * 100, 1),
                'por_tipo': {r['tipo_demanda'] or 'null': r['qtd'] for r in conn.execute("""
                    SELECT tipo_demanda, COUNT(*) AS qtd
                    FROM demandas WHERE origem='planner'
                    GROUP BY tipo_demanda ORDER BY qtd DESC
                """).fetchall()},
                'ultimos_erros': [dict(r) for r in conn.execute("""
                    SELECT descricao, criado_em
                    FROM eventos
                    WHERE tipo LIKE '%erro%'
                    ORDER BY criado_em DESC LIMIT 10
                """).fetchall()] if _tabela_existe(conn, 'eventos') else [],
            }

            # ── Sync history ──────────────────────────────────────────
            sync_rows = conn.execute("""
                SELECT chave, valor, atualizado_em
                FROM ms_sync_state
                ORDER BY atualizado_em DESC
            """).fetchall() if _tabela_existe(conn, 'ms_sync_state') else []
            sync_data = {r['chave']: r['valor'] for r in sync_rows}
            resultado['sync'] = {
                'last_sync': sync_data.get('last_sync'),
                'last_error': sync_data.get('last_sync_error'),
                'stats': _parse_json_safe(sync_data.get('last_sync_stats')),
            }

            # ── Amostradores ──────────────────────────────────────────
            try:
                from .db import USE_PG
            except ImportError:
                USE_PG = False
            # Funções de data diferem entre SQLite e PostgreSQL
            if USE_PG:
                # COALESCE(...)<>'' em vez de IS NOT NULL: a coluna guarda ''
                # como "sem data" (295 casos em data_medicao), e ''::date estoura
                # com invalid input syntax no Postgres.
                _parados_sql = """
                    SELECT COUNT(*) FROM amostradores
                    WHERE status='laboratorio'
                      AND COALESCE(data_envio_lab,'') ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}'
                      AND data_envio_lab::date < CURRENT_DATE - 30
                """
                _vencendo_sql = """
                    SELECT COUNT(*) FROM amostradores
                    WHERE cert_validade IS NOT NULL AND cert_validade != ''
                      AND cert_validade::date BETWEEN CURRENT_DATE AND CURRENT_DATE + 7
                """
                _vencidos_sql = """
                    SELECT COUNT(*) FROM amostradores
                    WHERE cert_validade IS NOT NULL AND cert_validade != ''
                      AND cert_validade::date < CURRENT_DATE
                """
            else:
                _parados_sql = """
                    SELECT COUNT(*) FROM amostradores
                    WHERE status='laboratorio'
                      AND COALESCE(data_envio_lab,'') GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]*'
                      AND julianday('now') - julianday(data_envio_lab) > 30
                """
                _vencendo_sql = """
                    SELECT COUNT(*) FROM amostradores
                    WHERE cert_validade IS NOT NULL AND cert_validade != ''
                      AND julianday(cert_validade) - julianday('now') BETWEEN 0 AND 7
                """
                _vencidos_sql = """
                    SELECT COUNT(*) FROM amostradores
                    WHERE cert_validade IS NOT NULL AND cert_validade != ''
                      AND julianday(cert_validade) < julianday('now')
                """
            resultado['amostradores'] = {
                'total':          _cnt('SELECT COUNT(*) FROM amostradores'),
                'no_lab':         _cnt("SELECT COUNT(*) FROM amostradores WHERE status='laboratorio'"),
                'disponiveis':    _cnt("SELECT COUNT(*) FROM amostradores WHERE status='disponivel'"),
                'parados_lab_30d': _cnt(_parados_sql),
                'vencendo_7d':    _cnt(_vencendo_sql),
                'vencidos':       _cnt(_vencidos_sql),
            }

            # ── KPIs operacionais (Item 15) ────────────────────────────
            if USE_PG:
                _op7d_sql = """
                    SELECT COUNT(*) FROM demandas
                    WHERE status != 'concluida' AND prazo IS NOT NULL AND prazo != ''
                      AND prazo::date BETWEEN CURRENT_DATE AND CURRENT_DATE + 7
                """
                _opat_sql = """
                    SELECT COUNT(*) FROM demandas
                    WHERE status != 'concluida' AND prazo IS NOT NULL AND prazo != ''
                      AND prazo::date < CURRENT_DATE
                """
            else:
                _op7d_sql = """
                    SELECT COUNT(*) FROM demandas
                    WHERE status != 'concluida' AND prazo IS NOT NULL AND prazo != ''
                      AND julianday(prazo) - julianday('now') BETWEEN 0 AND 7
                """
                _opat_sql = """
                    SELECT COUNT(*) FROM demandas
                    WHERE status != 'concluida' AND prazo IS NOT NULL AND prazo != ''
                      AND julianday(prazo) < julianday('now')
                """
            resultado['operacional'] = {
                'os_prazo_7d':    _cnt(_op7d_sql),
                'os_atrasadas':   _cnt(_opat_sql),
                'os_sem_prazo':   _cnt("""
                    SELECT COUNT(*) FROM demandas
                    WHERE status != 'concluida'
                      AND (prazo IS NULL OR prazo = '')
                      AND origem = 'planner'
                """),
                'coletas_ruido':  _cnt('SELECT COUNT(*) FROM coletas_ruido')  if _tabela_existe(conn, 'coletas_ruido')  else 0,
                'coletas_quimico':_cnt('SELECT COUNT(*) FROM coletas_quimico') if _tabela_existe(conn, 'coletas_quimico') else 0,
                'planejamentos':  _cnt('SELECT COUNT(*) FROM planejamentos')   if _tabela_existe(conn, 'planejamentos')   else 0,
                'db_tipo': 'postgresql' if USE_PG else 'sqlite',
            }

    except Exception as e:
        resultado['erro_diagnostico'] = f'{type(e).__name__}: {e}'
        resultado['traceback'] = traceback.format_exc()

    return resultado


def _tabela_existe(conn, nome: str) -> bool:
    try:
        from .db import USE_PG
    except ImportError:
        USE_PG = False
    try:
        if USE_PG:
            r = conn.execute(
                "SELECT table_name FROM information_schema.tables WHERE table_schema='public' AND table_name=?",
                (nome,)
            ).fetchone()
        else:
            r = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (nome,)
            ).fetchone()
        return r is not None
    except Exception:
        return False


def _parse_json_safe(s):
    if not s:
        return {}
    try:
        return json.loads(s)
    except Exception:
        return {'raw': str(s)[:200]}
