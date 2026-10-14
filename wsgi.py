"""Inicialização do portal CEN de TESTES com PostgreSQL/Supabase."""
import os
from werkzeug.security import generate_password_hash
from app import app, init_db, db, PGIntegrityError

def bootstrap_admin():
    name = os.getenv('ADMIN_NAME', '').strip()
    email = os.getenv('ADMIN_EMAIL', '').strip().lower()
    password = os.getenv('ADMIN_PASSWORD', '')
    with app.app_context():
        if db().execute("SELECT id FROM users WHERE role = 'admin' LIMIT 1").fetchone():
            app.logger.info('Administrador existente preservado.')
            return
        if not name or not email or len(password) < 16:
            raise RuntimeError('Configure ADMIN_NAME, ADMIN_EMAIL e ADMIN_PASSWORD (mínimo 16 caracteres).')
        try:
            db().execute('INSERT INTO users(name,email,password_hash,role) VALUES(?,?,?,?)',
                         (name,email,generate_password_hash(password),'admin'))
            db().commit()
        except PGIntegrityError:
            db().rollback()
            raise
        app.logger.info('Administrador inicial criado.')

def initialize():
    if not os.getenv('DATABASE_URL'):
        raise RuntimeError('DATABASE_URL ausente: configure no serviço de TESTE.')
    init_db()
    bootstrap_admin()
    app.logger.info('Estrutura PostgreSQL pronta.')

initialize()
