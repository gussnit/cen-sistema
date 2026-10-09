"""Inicialização do Sistema CEN para Gunicorn/Render.

Variáveis obrigatórias para o primeiro administrador:
ADMIN_NAME, ADMIN_EMAIL, ADMIN_PASSWORD.
"""
import os
import sqlite3
from pathlib import Path

from werkzeug.security import generate_password_hash
from app import app, init_db, db


def bootstrap_admin():
    name = os.getenv('ADMIN_NAME', '').strip()
    email = os.getenv('ADMIN_EMAIL', '').strip().lower()
    password = os.getenv('ADMIN_PASSWORD', '')

    with app.app_context():
        existing_admin = db().execute(
            "SELECT id FROM users WHERE role = 'admin' LIMIT 1"
        ).fetchone()
        if existing_admin:
            app.logger.info('Administrador existente preservado.')
            return

        if not name or not email or len(password) < 16:
            raise RuntimeError(
                'Primeiro administrador não criado: configure ADMIN_NAME, '
                'ADMIN_EMAIL e ADMIN_PASSWORD (mínimo 16 caracteres) no Render.'
            )

        try:
            db().execute(
                'INSERT INTO users (name, email, password_hash, role) VALUES (?, ?, ?, ?)',
                (name, email, generate_password_hash(password), 'admin'),
            )
            db().commit()
        except sqlite3.Error:
            db().rollback()
            raise
        app.logger.info('Primeiro administrador criado com sucesso.')


def initialize():
    db_path = Path(os.getenv('DATABASE_PATH', str(Path(__file__).resolve().parent / 'cen.sqlite3')))
    if not db_path.parent.is_dir():
        raise RuntimeError('Diretório do banco de dados não existe. Confira DATABASE_PATH.')
    init_db()
    bootstrap_admin()
    app.logger.info('Banco de dados inicializado com sucesso.')


initialize()
