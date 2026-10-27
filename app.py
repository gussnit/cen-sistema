
from io import BytesIO
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import (
    SimpleDocTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle
)
import os, secrets, click, uuid, io
import urllib.request, urllib.error, urllib.parse
from werkzeug.utils import secure_filename
from flask import send_file
from datetime import date, datetime
from functools import wraps
from pathlib import Path
from flask import Flask, render_template, request, redirect, url_for, session, flash, abort, g
from werkzeug.security import generate_password_hash, check_password_hash
from flask_wtf.csrf import CSRFProtect
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

ROOT=Path(__file__).resolve().parent
app=Flask(__name__)
app.config.update(SECRET_KEY=os.environ.get('SECRET_KEY') or secrets.token_hex(32),SESSION_COOKIE_HTTPONLY=True,SESSION_COOKIE_SAMESITE='Lax',SESSION_COOKIE_SECURE=os.getenv('COOKIE_SECURE','0')=='1',MAX_CONTENT_LENGTH=12*1024*1024)
csrf=CSRFProtect(app)
limiter=Limiter(get_remote_address,app=app,default_limits=[])
# DATABASE_URL é usada pela conexão PostgreSQL; DATABASE_PATH não é utilizada.
UPLOAD_ROOT=Path(os.getenv('UPLOAD_ROOT',str(ROOT/'private_uploads'))).resolve()

# PostgreSQL via Supabase. Nunca imprime DATABASE_URL nos logs.
import psycopg
from psycopg.rows import dict_row
from psycopg import IntegrityError as PGIntegrityError

class RowCompat(dict):
    def __getitem__(self, key):
        if isinstance(key, int):
            return list(self.values())[key]
        return super().__getitem__(key)

class PortalCursor:
    def __init__(self, cursor):
        self._cursor = cursor
        self.lastrowid = None
    def fetchone(self):
        row = self._cursor.fetchone()
        return RowCompat(row) if row is not None else None
    def fetchall(self):
        return [RowCompat(row) for row in self._cursor.fetchall()]
    def __iter__(self):
        return (RowCompat(row) for row in self._cursor)

class PortalDB:
    def __init__(self, conn):
        self.conn = conn
    def execute(self, query, params=()):
        # Consultas existentes usam placeholders SQLite e INSERT OR IGNORE.
        query = query.replace('?', '%s')
        ignore = 'INSERT OR IGNORE INTO ' in query
        query = query.replace('INSERT OR IGNORE INTO ', 'INSERT INTO ')
        if ignore and 'ON CONFLICT' not in query.upper():
            query += ' ON CONFLICT DO NOTHING'
        # Compatibilidade com literais de string SQLite usados em CASE.
        query = query.replace('="Pago"', "='Pago'")
        # SELECT COUNT(*) etc. usam dict_row; acesso [0] resolvido abaixo.
        insert = query.lstrip().upper().startswith('INSERT INTO ')
        returns_id = insert and 'RETURNING ' not in query.upper() and 'ON CONFLICT' not in query.upper()
        if returns_id:
            query += ' RETURNING id'
        cur = self.conn.execute(query, params)
        out = PortalCursor(cur)
        if returns_id:
            record = cur.fetchone()
            out.lastrowid = record['id'] if record else None
        return out
    def commit(self):
        self.conn.commit()
    def rollback(self):
        self.conn.rollback()
    def close(self):
        self.conn.close()

def db():
    if 'db' not in g:
        url = os.getenv('DATABASE_URL', '').strip()
        if not url:
            raise RuntimeError('DATABASE_URL não configurada no Render (ambiente de teste).')
        if not url.startswith(('postgresql://', 'postgres://')):
            raise RuntimeError('DATABASE_URL deve ser uma URL PostgreSQL.')
        g.db = PortalDB(psycopg.connect(url, row_factory=dict_row, connect_timeout=10, sslmode='require'))
    return g.db
