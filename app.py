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

@app.teardown_appcontext
def close(_):
    c = g.pop('db', None)
    if c:
        c.close()

SCHEMA_SQL = r'''
CREATE TABLE IF NOT EXISTS users (id BIGSERIAL PRIMARY KEY,name TEXT NOT NULL,email TEXT NOT NULL UNIQUE,password_hash TEXT NOT NULL,role TEXT NOT NULL CHECK(role IN ('admin','responsavel')),active INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS students (id BIGSERIAL PRIMARY KEY,name TEXT NOT NULL,registration TEXT NOT NULL UNIQUE,grade_level TEXT NOT NULL,class_name TEXT NOT NULL,active INTEGER NOT NULL DEFAULT 1,photo_file TEXT,card_pdf_file TEXT,card_preview_file TEXT);
CREATE TABLE IF NOT EXISTS guardians (user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,student_id BIGINT NOT NULL REFERENCES students(id) ON DELETE CASCADE,PRIMARY KEY(user_id,student_id));
CREATE TABLE IF NOT EXISTS grades (id BIGSERIAL PRIMARY KEY,student_id BIGINT NOT NULL REFERENCES students(id) ON DELETE CASCADE,subject TEXT NOT NULL,term TEXT NOT NULL,score DOUBLE PRECISION NOT NULL CHECK(score>=0 AND score<=10),updated_at TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP::text),UNIQUE(student_id,subject,term));
CREATE TABLE IF NOT EXISTS payments (id BIGSERIAL PRIMARY KEY,student_id BIGINT NOT NULL REFERENCES students(id) ON DELETE CASCADE,title TEXT NOT NULL,amount_cents INTEGER NOT NULL CHECK(amount_cents>=0),due_date TEXT NOT NULL,status TEXT NOT NULL CHECK(status IN ('Pendente','Pago','Vencido')),reference_year INTEGER,reference_month INTEGER,boleto_file TEXT,paid_at TEXT);
CREATE UNIQUE INDEX IF NOT EXISTS ux_payments_student_month ON payments(student_id,reference_year,reference_month) WHERE reference_year IS NOT NULL AND reference_month IS NOT NULL;
CREATE TABLE IF NOT EXISTS notice_categories(id BIGSERIAL PRIMARY KEY,name TEXT NOT NULL UNIQUE,active INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS notices(id BIGSERIAL PRIMARY KEY,title TEXT NOT NULL,body TEXT NOT NULL,kind TEXT NOT NULL DEFAULT 'Comunicado',created_at TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP::text),image_file TEXT,attachment_file TEXT,pinned INTEGER NOT NULL DEFAULT 0,published INTEGER NOT NULL DEFAULT 1,archived INTEGER NOT NULL DEFAULT 0,notice_category_id BIGINT REFERENCES notice_categories(id));
CREATE TABLE IF NOT EXISTS event_categories(id BIGSERIAL PRIMARY KEY,name TEXT NOT NULL UNIQUE,color TEXT NOT NULL DEFAULT '#397f85',active INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS events(id BIGSERIAL PRIMARY KEY,event_date TEXT NOT NULL,title TEXT NOT NULL,details TEXT NOT NULL DEFAULT '',category_id BIGINT REFERENCES event_categories(id),end_date TEXT);
CREATE TABLE IF NOT EXISTS messages(id BIGSERIAL PRIMARY KEY,student_id BIGINT NOT NULL REFERENCES students(id) ON DELETE CASCADE,sender_id BIGINT NOT NULL REFERENCES users(id),body TEXT NOT NULL,created_at TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP::text),subject TEXT NOT NULL DEFAULT 'Mensagem da escola');
CREATE TABLE IF NOT EXISTS message_reads(message_id BIGINT NOT NULL REFERENCES messages(id) ON DELETE CASCADE,user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,read_at TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP::text),PRIMARY KEY(message_id,user_id));
CREATE TABLE IF NOT EXISTS audit(id BIGSERIAL PRIMARY KEY,actor_id BIGINT NOT NULL,action TEXT NOT NULL,entity TEXT NOT NULL,entity_id BIGINT,created_at TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP::text));
CREATE TABLE IF NOT EXISTS portal_settings(key TEXT PRIMARY KEY,filename TEXT,updated_at TEXT DEFAULT (CURRENT_TIMESTAMP::text));
CREATE TABLE IF NOT EXISTS report_subjects(id BIGSERIAL PRIMARY KEY,school_year INTEGER NOT NULL,segment TEXT NOT NULL CHECK(segment IN ('F2','EM')),class_name TEXT NOT NULL DEFAULT '*',name TEXT NOT NULL,position INTEGER NOT NULL DEFAULT 0,active INTEGER NOT NULL DEFAULT 1,UNIQUE(school_year,segment,class_name,name));
CREATE TABLE IF NOT EXISTS report_concepts(id BIGSERIAL PRIMARY KEY,student_id BIGINT NOT NULL REFERENCES students(id) ON DELETE CASCADE,school_year INTEGER NOT NULL,subject_id BIGINT NOT NULL REFERENCES report_subjects(id),term INTEGER NOT NULL CHECK(term BETWEEN 1 AND 3),co TEXT NOT NULL DEFAULT '',at TEXT NOT NULL DEFAULT '',ha TEXT NOT NULL DEFAULT '',re TEXT NOT NULL DEFAULT '',fa INTEGER NOT NULL DEFAULT 0 CHECK(fa>=0),UNIQUE(student_id,school_year,subject_id,term));
CREATE TABLE IF NOT EXISTS report_finals(student_id BIGINT NOT NULL REFERENCES students(id) ON DELETE CASCADE,school_year INTEGER NOT NULL,subject_id BIGINT NOT NULL REFERENCES report_subjects(id),tf INTEGER NOT NULL DEFAULT 0 CHECK(tf>=0),cf TEXT NOT NULL DEFAULT '',result TEXT NOT NULL DEFAULT '',PRIMARY KEY(student_id,school_year,subject_id));
CREATE TABLE IF NOT EXISTS report_descriptions(student_id BIGINT NOT NULL REFERENCES students(id) ON DELETE CASCADE,school_year INTEGER NOT NULL,term INTEGER NOT NULL CHECK(term BETWEEN 1 AND 3),body TEXT NOT NULL DEFAULT '',PRIMARY KEY(student_id,school_year,term));
CREATE TABLE IF NOT EXISTS report_publications(student_id BIGINT NOT NULL REFERENCES students(id) ON DELETE CASCADE,school_year INTEGER NOT NULL,term INTEGER NOT NULL CHECK(term BETWEEN 1 AND 3),published INTEGER NOT NULL DEFAULT 0,PRIMARY KEY(student_id,school_year,term));
'''
MIGRATIONS_SQL = r'''
ALTER TABLE messages ADD COLUMN IF NOT EXISTS subject TEXT NOT NULL DEFAULT 'Mensagem da escola';
ALTER TABLE events ADD COLUMN IF NOT EXISTS category_id BIGINT REFERENCES event_categories(id);
ALTER TABLE events ADD COLUMN IF NOT EXISTS end_date TEXT;
ALTER TABLE students ADD COLUMN IF NOT EXISTS photo_file TEXT;
ALTER TABLE students ADD COLUMN IF NOT EXISTS card_pdf_file TEXT;
ALTER TABLE students ADD COLUMN IF NOT EXISTS card_preview_file TEXT;
ALTER TABLE payments ADD COLUMN IF NOT EXISTS reference_year INTEGER;
ALTER TABLE payments ADD COLUMN IF NOT EXISTS reference_month INTEGER;
ALTER TABLE payments ADD COLUMN IF NOT EXISTS boleto_file TEXT;
ALTER TABLE payments ADD COLUMN IF NOT EXISTS paid_at TEXT;
ALTER TABLE notices ADD COLUMN IF NOT EXISTS image_file TEXT;
ALTER TABLE notices ADD COLUMN IF NOT EXISTS attachment_file TEXT;
ALTER TABLE notices ADD COLUMN IF NOT EXISTS pinned INTEGER NOT NULL DEFAULT 0;
ALTER TABLE notices ADD COLUMN IF NOT EXISTS published INTEGER NOT NULL DEFAULT 1;
ALTER TABLE notices ADD COLUMN IF NOT EXISTS archived INTEGER NOT NULL DEFAULT 0;
ALTER TABLE notices ADD COLUMN IF NOT EXISTS notice_category_id BIGINT REFERENCES notice_categories(id);
'''

def init_db():
    with app.app_context():
        conn = db().conn
        # Executa DDL em transação; se houver erro, nenhuma mudança parcial é confirmada.
        try:
            for statement in (SCHEMA_SQL + MIGRATIONS_SQL).split(';'):
                if statement.strip():
                    conn.execute(statement)
        except Exception:
            conn.rollback()
            raise
        for name, color in [('Feriados','#d65a5a'),('Reuniões','#e5a33b'),('Avaliações','#9465bf'),('Recessos','#339b75'),('Eventos escolares','#397fc5'),('Treinamentos','#cc79a8'),('Sábados letivos','#168b99')]:
            conn.execute('INSERT INTO event_categories(name,color) VALUES(%s,%s) ON CONFLICT(name) DO NOTHING',(name,color))
        for name in ('Avisos importantes','Eventos','Projetos','Informações gerais','Notícias do CEN'):
            conn.execute('INSERT INTO notice_categories(name) VALUES(%s) ON CONFLICT(name) DO NOTHING',(name,))
        conn.commit()

@app.cli.command('init-db')
def init_command():
    init_db()
    click.echo('Estrutura PostgreSQL conferida.')

@app.cli.command('create-admin')
@click.option('--name',prompt=True)
@click.option('--email',prompt=True)
@click.password_option()
def create_admin(name,email,password):
    init_db()
    with app.app_context():
        db().execute('INSERT INTO users(name,email,password_hash,role) VALUES(?,?,?,?)',(name.strip(),email.strip().lower(),generate_password_hash(password),'admin'))
        db().commit()
        click.echo('Administrador criado.')

# Arquivos privados no Supabase Storage. A chave nunca é enviada ao navegador.
STORAGE_BUCKET = 'cen-arquivos'

def storage_config():
    url = os.environ.get('SUPABASE_URL', '').strip().rstrip('/')
    key = os.environ.get('SUPABASE_SECRET_KEY', '').strip()
    if not url.startswith('https://') or not key:
        raise RuntimeError('Configure SUPABASE_URL e SUPABASE_SECRET_KEY no Render de testes.')
    return url, key

def storage_request(method, kind, filename, data=None):
    if kind not in ('pdf', 'image') or not filename or '/' in filename or '\\' in filename:
        abort(404)
    url, key = storage_config()
    object_path = urllib.parse.quote(f'{STORAGE_BUCKET}/{kind}/{filename}', safe='/')
    endpoint = f"{url}/storage/v1/object/{'authenticated/' if method == 'GET' else ''}{object_path}"
    headers = {'apikey': key, 'Authorization': 'Bearer ' + key}
    if data is not None:
        headers['Content-Type'] = 'application/pdf' if kind == 'pdf' else 'image/jpeg'
        headers['x-upsert'] = 'false'
    req = urllib.request.Request(endpoint, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=25) as response:
            return response.read()
    except urllib.error.HTTPError as exc:
        if method == 'GET' and exc.code == 404:
            abort(404)
        detalhe = exc.read().decode('utf-8', errors='replace')
        app.logger.error(
            'Falha Supabase Storage: operacao=%s status=%s resposta=%s',
            method, exc.code, detalhe[:500]
        )
        abort(502, 'Não foi possível acessar o armazenamento de arquivos.')
    except (urllib.error.URLError, TimeoutError, OSError):
        app.logger.exception('Falha de comunicação com Supabase Storage (%s)', method)
        abort(502, 'Armazenamento temporariamente indisponível.')

def storage_upload(raw, kind):
    ext = '.pdf' if kind == 'pdf' else '.jpg'
    filename = uuid.uuid4().hex + ext
    storage_request('POST', kind, filename, raw)
    return filename

def storage_delete(kind, filename):
    try:
        storage_request('DELETE', kind, filename)
    except Exception:
        app.logger.warning('Não foi possível remover arquivo temporário do Storage.')

def store_upload(file, kind):
    if not file or not file.filename: abort(400)
    raw = file.read()
    if kind == 'pdf':
        if not raw.startswith(b'%PDF-'): abort(400, 'Envie um PDF válido.')
    else:
        from PIL import Image, UnidentifiedImageError
        try:
            im = Image.open(io.BytesIO(raw)); im.verify()
            im = Image.open(io.BytesIO(raw)); im.thumbnail((1600, 1600))
            out = io.BytesIO(); im.convert('RGB').save(out, format='JPEG', quality=88)
            raw = out.getvalue()
        except (UnidentifiedImageError, OSError, ValueError):
            abort(400, 'Imagem inválida.')
    return storage_upload(raw, kind)

def private_file(kind, filename):
    if not filename or '/' in filename or '\\' in filename: abort(404)
    raw = storage_request('GET', kind, filename)
    response = send_file(io.BytesIO(raw), mimetype='application/pdf' if kind == 'pdf' else 'image/jpeg', as_attachment=False)
    response.headers['Cache-Control'] = 'private, no-store'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    return response

def current_user():
    if 'uid' not in session:return None
    return db().execute('SELECT * FROM users WHERE id=? AND active=1',(session['uid'],)).fetchone()
@app.context_processor
def context():
    me = current_user()
    return {'me': me, 'today': date.today().isoformat(), 'selected_student_id':session.get('selected_student_id'),
            'nav_students': allowed_students() if me and me['role'] == 'responsavel' else [], 'unread_count': unread_count() if me and me['role']=='responsavel' else 0}
def login_required(fn):
    @wraps(fn)
    def inner(*a,**kw):
        if not current_user():return redirect(url_for('login'))
        return fn(*a,**kw)
    return inner
def admin_required(fn):
    @wraps(fn)
    @login_required
    def inner(*a,**kw):
        if current_user()['role']!='admin':abort(403)
        return fn(*a,**kw)
    return inner

def allowed_students():
    if current_user()['role']=='admin':return db().execute('SELECT * FROM students ORDER BY name').fetchall()
    return db().execute('SELECT s.* FROM students s JOIN guardians g ON g.student_id=s.id WHERE g.user_id=? AND s.active=1 ORDER BY s.name',(session['uid'],)).fetchall()
def unread_count():
    return db().execute("SELECT COUNT(*) FROM messages m JOIN guardians g ON g.student_id=m.student_id AND g.user_id=? LEFT JOIN message_reads r ON r.message_id=m.id AND r.user_id=? WHERE r.message_id IS NULL AND m.sender_id IN (SELECT id FROM users WHERE role='admin')",(session['uid'],session['uid'])).fetchone()[0]

def check_student(sid):
    s=db().execute('SELECT * FROM students WHERE id=?',(sid,)).fetchone()
    if not s:abort(404)
    if current_user()['role']!='admin' and not db().execute('SELECT 1 FROM guardians WHERE user_id=? AND student_id=?',(session['uid'],sid)).fetchone():abort(403)
    return s
def audit(action,entity,eid):db().execute('INSERT INTO audit(actor_id,action,entity,entity_id) VALUES(?,?,?,?)',(session['uid'],action,entity,eid))
def value(key,limit=255):return request.form.get(key,'').strip()[:limit]
def number(key):
    try:return int(value(key))
    except ValueError:abort(400)

@app.get('/')
def home():
    if not current_user():return redirect(url_for('login'))
    students=allowed_students()
    requested=request.args.get('aluno',type=int)
    if requested is not None:
        if not any(st['id']==requested for st in students):abort(403)
        session['selected_student_id']=requested
    selected=next((st for st in students if st['id']==session.get('selected_student_id')),students[0] if students else None)
    if selected:session['selected_student_id']=selected['id']
    banner=db().execute("SELECT filename FROM portal_settings WHERE key='home_banner'").fetchone()
    return render_template('home.html',students=students,selected=selected,banner=banner['filename'] if banner else None)
@app.get('/aluno/<int:sid>/foto')
@login_required
def student_photo(sid):
    st=check_student(sid);return private_file('image',st['photo_file'])

@app.get('/aluno/<int:sid>/carteirinha')
@login_required
def student_card(sid):
    st=check_student(sid);return private_file('pdf',st['card_pdf_file'])

@app.get('/aluno/<int:sid>/carteirinha/miniatura')
@login_required
def student_card_preview(sid):
    st=check_student(sid);return private_file('image',st['card_preview_file'])

@app.get('/inicio/banner')
@login_required
def home_banner():
    row=db().execute("SELECT filename FROM portal_settings WHERE key='home_banner'").fetchone()
    return private_file('image',row['filename'] if row else None)

@app.post('/admin/aluno/<int:sid>/foto')
@admin_required
def upload_student_photo(sid):
    check_student(sid);name=store_upload(request.files.get('file'),'image')
    db().execute('UPDATE students SET photo_file=? WHERE id=?',(name,sid));audit('foto','aluno',sid);db().commit()
    flash('Foto atualizada.','success');return redirect(url_for('student',sid=sid))

@app.post('/admin/aluno/<int:sid>/carteirinha')
@admin_required
def upload_student_card(sid):
    check_student(sid);pdf=request.files.get('file')
    # Gera a miniatura em memória, antes de publicar os dois arquivos.
    try:
        import fitz
        raw_pdf = pdf.read()
        if not raw_pdf.startswith(b'%PDF-'): abort(400, 'Envie um PDF válido.')
        with fitz.open(stream=raw_pdf, filetype='pdf') as doc:
            if len(doc) < 1: raise ValueError('PDF sem páginas')
            pix = doc[0].get_pixmap(matrix=fitz.Matrix(1.1,1.1), alpha=False)
            from PIL import Image
            im = Image.open(io.BytesIO(pix.tobytes('png')))
            out = io.BytesIO(); im.convert('RGB').save(out, format='JPEG', quality=84)
        preview_bytes = out.getvalue()
    except Exception:
        flash('Não foi possível gerar a miniatura. Verifique o PDF.', 'error')
        return redirect(url_for('student', sid=sid))
    name = storage_upload(raw_pdf, 'pdf')
    try:
        preview = storage_upload(preview_bytes, 'image')
    except Exception:
        storage_delete('pdf', name)
        raise
    db().execute('UPDATE students SET card_pdf_file=?,card_preview_file=? WHERE id=?',(name,preview,sid))
    audit('carteirinha','aluno',sid);db().commit()
    flash('Carteirinha atualizada.','success');return redirect(url_for('student',sid=sid))

@app.post('/admin/inicio/banner')
@admin_required
def upload_home_banner():
    name=store_upload(request.files.get('file'),'image')
    db().execute("INSERT INTO portal_settings(key,filename) VALUES('home_banner',?) ON CONFLICT(key) DO UPDATE SET filename=excluded.filename,updated_at=CURRENT_TIMESTAMP",(name,))
    audit('banner','portal',None);db().commit()
    flash('Banner atualizado.','success');return redirect(url_for('admin')+'#banner')

@app.route('/login',methods=['GET','POST'])
@limiter.limit('8 per minute',methods=['POST'])
def login():
    if request.method == 'POST':
        u = db().execute(
            'SELECT * FROM users WHERE email=? AND active=1',
            (value('email').lower(),)
        ).fetchone()

        if u and check_password_hash(
            u['password_hash'],
            request.form.get('password', '')
        ):
            session.clear()
            session['uid'] = u['id']

            if u['role'] == 'admin':
                return redirect(url_for('admin'))

            return redirect(url_for('home'))

        flash('E-mail ou senha inválidos.', 'error')

    return render_template('login.html')
@app.post('/logout')
@login_required
def logout():session.clear();return redirect(url_for('login'))
@app.get('/aluno/<int:sid>')
@login_required
def student(sid):
    s=check_student(sid)
    return render_template('student.html',s=s,grades=db().execute('SELECT * FROM grades WHERE student_id=? ORDER BY subject,term',(sid,)).fetchall(),payments=db().execute('SELECT * FROM payments WHERE student_id=? ORDER BY due_date DESC',(sid,)).fetchall(),messages=db().execute('SELECT m.*,u.name sender FROM messages m JOIN users u ON u.id=m.sender_id WHERE student_id=? ORDER BY m.id',(sid,)).fetchall())
CONCEPTS=('','MB','B','R','S','I')
DEFAULT_SUBJECTS={
 'F2':['Língua Portuguesa e Produções Textuais','Redação','Artes e Modernidades','Música e Habilidades Socioemocionais','Inglês','Educação Física','Geografia','História','Ciências','Matemática'],
 'EM':['Português','Redação','Literatura','Artes','Inglês','Espanhol','Educação Física','Geografia','História','Biologia','Física','Química','Filosofia','Sociologia','Matemática','Itinerários Formativos']}

def report_segment(level):
    import unicodedata
    x=''.join(c for c in unicodedata.normalize('NFKD',level.lower()) if not unicodedata.combining(c))
    if 'medio' in x or x.strip() in ('em','ensino medio'):return 'EM'
    if 'fundamental ii' in x or 'fundamental 2' in x or 'fundamental 6' in x or x.strip() in ('f2','fund ii') or any(f'{i}º' in x or f'{i}o ano' in x for i in range(6,10)):return 'F2'
    return 'F1'

def report_year():
    y=request.args.get('ano',str(date.today().year))
    try:y=int(y)
    except ValueError:abort(400)
    if y<2000 or y>2100:abort(400)
    return y

def ensure_subjects(year,segment):
    if segment=='F1':return
    if not db().execute('SELECT 1 FROM report_subjects WHERE school_year=? AND segment=?',(year,segment)).fetchone():
        for i,name in enumerate(DEFAULT_SUBJECTS[segment]):
            db().execute('INSERT OR IGNORE INTO report_subjects(school_year,segment,class_name,name,position) VALUES(?,?,?,?,?)',(year,segment,'*',name,i))
        db().commit()

def report_subject_list(year,segment,student):
    ensure_subjects(year,segment)
    return db().execute("SELECT * FROM report_subjects WHERE school_year=? AND segment=? AND (class_name=? OR class_name='*') ORDER BY CASE WHEN class_name=? THEN 0 ELSE 1 END,position,id",(year,segment,student['class_name'],student['class_name'])).fetchall()

@app.get('/aluno/<int:sid>/boletim')
@login_required
def boletim(sid):
    st=check_student(sid);year=report_year();segment=report_segment(st['grade_level']);admin_mode=current_user()['role']=='admin'
    subjects=report_subject_list(year,segment,st) if segment!='F1' else []
    visible={r['term'] for r in db().execute('SELECT term FROM report_publications WHERE student_id=? AND school_year=? AND published=1',(sid,year))}
    published=set(visible)
    if admin_mode:visible={1,2,3}
    concept_rows=db().execute('SELECT * FROM report_concepts WHERE student_id=? AND school_year=?',(sid,year)).fetchall()
    concepts={(r['subject_id'],r['term']):r for r in concept_rows if r['term'] in visible}
    finals={r['subject_id']:r for r in db().execute('SELECT * FROM report_finals WHERE student_id=? AND school_year=?',(sid,year))} if 3 in visible else {}
    descriptions={r['term']:r['body'] for r in db().execute('SELECT * FROM report_descriptions WHERE student_id=? AND school_year=?',(sid,year)) if r['term'] in visible}
    return render_template('boletim.html',s=st,year=year,segment=segment,subjects=subjects,concepts=concepts,finals=finals,descriptions=descriptions,visible=visible,published=published,admin_mode=admin_mode,choices=CONCEPTS)

@app.post('/admin/boletim/<int:sid>/conceito')
@admin_required
def save_concept(sid):
    st=check_student(sid);year=number('year');term=number('term');subject_id=number('subject_id')
    if term not in (1,2,3) or not 2000<=year<=2100:abort(400)
    segment=report_segment(st['grade_level'])
    subject=db().execute("SELECT 1 FROM report_subjects WHERE id=? AND school_year=? AND segment=? AND active=1 AND (class_name=? OR class_name='*')",(subject_id,year,segment,st['class_name'])).fetchone()
    if not subject:abort(400)
    vals=[value(k,2).upper() for k in ('co','at','ha','re')]
    if any(v not in CONCEPTS for v in vals):abort(400)
    fa=number('fa')
    if fa<0:abort(400)
    db().execute('INSERT INTO report_concepts(student_id,school_year,subject_id,term,co,at,ha,re,fa) VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(student_id,school_year,subject_id,term) DO UPDATE SET co=excluded.co,at=excluded.at,ha=excluded.ha,re=excluded.re,fa=excluded.fa',(sid,year,subject_id,term,*vals,fa))
    audit('conceitos','boletim',sid);db().commit()
    return redirect(url_for('boletim',sid=sid,ano=year))

@app.post('/admin/boletim/<int:sid>/final')
@admin_required
def save_final(sid):
    st=check_student(sid);year=number('year');subject_id=number('subject_id');tf=number('tf');cf=value('cf',2).upper();result=value('result',100)
    if not 2000<=year<=2100 or tf<0 or cf not in CONCEPTS:abort(400)
    if not db().execute("SELECT 1 FROM report_subjects WHERE id=? AND school_year=? AND segment=? AND (class_name=? OR class_name='*')",(subject_id,year,report_segment(st['grade_level']),st['class_name'])).fetchone():abort(400)
    db().execute('INSERT INTO report_finals(student_id,school_year,subject_id,tf,cf,result) VALUES(?,?,?,?,?,?) ON CONFLICT(student_id,school_year,subject_id) DO UPDATE SET tf=excluded.tf,cf=excluded.cf,result=excluded.result',(sid,year,subject_id,tf,cf,result))
    audit('resultado','boletim',sid);db().commit();return redirect(url_for('boletim',sid=sid,ano=year))

@app.post('/admin/boletim/<int:sid>/relatorio')
@admin_required
def save_description(sid):
    st=check_student(sid)
    if report_segment(st['grade_level'])!='F1':abort(400)
    year=number('year');term=number('term')
    if not 2000<=year<=2100 or term not in (1,2,3):abort(400)
    db().execute('INSERT INTO report_descriptions(student_id,school_year,term,body) VALUES(?,?,?,?) ON CONFLICT(student_id,school_year,term) DO UPDATE SET body=excluded.body',(sid,year,term,value('body',20000)))
    audit('relatorio','boletim',sid);db().commit();return redirect(url_for('boletim',sid=sid,ano=year))

@app.post('/admin/boletim/<int:sid>/publicar')
@admin_required
def publish_report(sid):
    check_student(sid);year=number('year');term=number('term');published=number('published')
    if not 2000<=year<=2100 or term not in (1,2,3) or published not in (0,1):abort(400)
    db().execute('INSERT INTO report_publications(student_id,school_year,term,published) VALUES(?,?,?,?) ON CONFLICT(student_id,school_year,term) DO UPDATE SET published=excluded.published',(sid,year,term,published))
    audit('publicar' if published else 'ocultar','boletim',sid);db().commit();return redirect(url_for('boletim',sid=sid,ano=year))

@app.post('/admin/disciplinas')
@admin_required
def add_report_subject():
    year=number('year');segment=value('segment');name=value('name',150);class_name=value('class_name',100) or '*';position=number('position')
    if not 2000<=year<=2100 or segment not in ('F2','EM') or not name or position<0:abort(400)
    ensure_subjects(year,segment)
    db().execute('INSERT INTO report_subjects(school_year,segment,class_name,name,position) VALUES(?,?,?,?,?)',(year,segment,class_name,name,position))
    audit('criar','disciplina',None);db().commit();return redirect(url_for('admin')+'#disciplinas')

@app.post('/admin/disciplinas/<int:subject_id>')
@admin_required
def edit_report_subject(subject_id):
    name=value('name',150);position=number('position');active=number('active')
    if not name or position<0 or active not in (0,1):abort(400)
    row=db().execute('SELECT id FROM report_subjects WHERE id=?',(subject_id,)).fetchone()
    if not row:abort(404)
    db().execute('UPDATE report_subjects SET name=?,position=?,active=? WHERE id=?',(name,position,active,subject_id))
    audit('editar','disciplina',subject_id);db().commit();return redirect(url_for('admin')+'#disciplinas')

@app.get('/aluno/<int:sid>/financeiro')
@login_required
def financeiro(sid):
    student=check_student(sid)
    year=request.args.get('ano',default=date.today().year,type=int)
    if not 2000<=year<=2100:abort(400)
    rows=db().execute('SELECT * FROM payments WHERE student_id=? AND reference_year=? ORDER BY reference_month',(sid,year)).fetchall()
    by_month={r['reference_month']:r for r in rows}
    months=[]
    for m in range(1,13):
        payment=by_month.get(m)
        status='Sem boleto'
        if payment:
            if payment['status']=='Pago':status='Pago'
            elif payment['due_date']<date.today().isoformat():status='Vencido'
            elif payment['status']=='Vencido':status='Em atraso'
            else:status='Em aberto'
        months.append({'month':m,'name':('Janeiro','Fevereiro','Março','Abril','Maio','Junho','Julho','Agosto','Setembro','Outubro','Novembro','Dezembro')[m-1],'payment':payment,'status':status})
    legacy=db().execute('SELECT * FROM payments WHERE student_id=? AND reference_year IS NULL AND substr(due_date,1,4)=? ORDER BY due_date',(sid,str(year))).fetchall()
    return render_template('financeiro.html',s=student,months=months,legacy=legacy,year=year)

@app.get('/aluno/<int:sid>/boleto/<int:pid>')
@login_required
def view_boleto(sid,pid):
    check_student(sid)
    payment=db().execute('SELECT boleto_file FROM payments WHERE id=? AND student_id=?',(pid,sid)).fetchone()
    if not payment or not payment['boleto_file']:abort(404)
    response=private_file('pdf',payment['boleto_file'])
    response.headers['Cache-Control']='private, no-store'
    response.headers['X-Content-Type-Options']='nosniff'
    response.headers['Content-Disposition']=(('attachment' if request.args.get('baixar')=='1' else 'inline')+'; filename="boleto.pdf"')
    return response

@app.get('/aluno/<int:sid>/mensagens')
@login_required
def mensagens(sid):
    student=check_student(sid)
    items=db().execute("SELECT m.* FROM messages m JOIN users u ON u.id=m.sender_id WHERE m.student_id=? AND u.role='admin' ORDER BY m.created_at DESC,m.id DESC",(sid,)).fetchall()
    return render_template('mensagens.html',s=student,messages=items)

@app.post('/aluno/<int:sid>/mensagens/<int:mid>/ler')
@login_required
def read_message(sid,mid):
    check_student(sid)
    if not db().execute("SELECT 1 FROM messages m JOIN users u ON u.id=m.sender_id WHERE m.id=? AND m.student_id=? AND u.role='admin'",(mid,sid)).fetchone():abort(404)
    db().execute('INSERT OR IGNORE INTO message_reads(message_id,user_id) VALUES(?,?)',(mid,session['uid']))
    db().commit()
    return redirect(url_for('mensagens',sid=sid)+'#msg-'+str(mid))

@app.post('/aluno/<int:sid>/mensagem')
@login_required
@limiter.limit('20 per hour')
def send_message(sid):
    check_student(sid)
    if current_user()['role']!='admin':abort(403)
    body=value('body',2000);subject=value('subject',200)
    if body and subject:
        cur=db().execute('INSERT INTO messages(student_id,sender_id,subject,body) VALUES(?,?,?,?)',(sid,session['uid'],subject,body));audit('enviar','mensagem',cur.lastrowid);db().commit()
    return redirect(url_for('mensagens',sid=sid))
@app.get('/mural')
@login_required
def mural():return render_template('mural.html',notices=db().execute('SELECT * FROM notices ORDER BY id DESC').fetchall(),events=db().execute('SELECT * FROM events ORDER BY event_date DESC').fetchall())
@app.get('/calendario')
@login_required
def calendario():
    try:year=int(request.args.get('ano',date.today().year));month=int(request.args.get('mes',date.today().month))
    except ValueError:abort(400)
    if year<2000 or year>2100 or month not in range(1,13):abort(400)
    import calendar as cal
    entries=db().execute('SELECT e.*,c.name category,c.color FROM events e LEFT JOIN event_categories c ON c.id=e.category_id WHERE e.event_date<=? AND COALESCE(e.end_date,e.event_date)>=? ORDER BY e.event_date,e.id',(f'{year:04d}-{month:02d}-{cal.monthrange(year,month)[1]:02d}',f'{year:04d}-{month:02d}-01')).fetchall()
    categories=db().execute('SELECT * FROM event_categories WHERE active=1 ORDER BY name').fetchall()
    return render_template('calendario.html',year=year,month=month,month_name=['','Janeiro','Fevereiro','Março','Abril','Maio','Junho','Julho','Agosto','Setembro','Outubro','Novembro','Dezembro'][month],weeks=cal.Calendar(firstweekday=6).monthdayscalendar(year,month),events=entries,categories=categories)

@app.get('/comunicados')
@login_required
def comunicados():
    return render_template('comunicados.html',notices=db().execute('SELECT n.*,c.name category_name FROM notices n LEFT JOIN notice_categories c ON c.id=n.notice_category_id WHERE n.published=1 AND n.archived=0 ORDER BY n.pinned DESC,n.created_at DESC,n.id DESC').fetchall())

@app.get('/admin')
@admin_required
def admin():
    students = allowed_students()

    guardians = db().execute(
        "SELECT * FROM users WHERE role='responsavel' ORDER BY name"
    ).fetchall()

    guardian_links = db().execute(
        """SELECT g.student_id, g.user_id, u.name AS guardian_name
           FROM guardians g
           JOIN users u ON u.id = g.user_id
           WHERE u.role = 'responsavel'
           ORDER BY u.name"""
    ).fetchall()

    student_guardians = {}
    guardian_students = {}
    students_by_id = {s['id']: s for s in students}

    for link in guardian_links:
        sid = link['student_id']
        uid = link['user_id']
        student_guardians.setdefault(sid, []).append(link['guardian_name'])
        if sid in students_by_id:
            guardian_students.setdefault(uid, []).append(students_by_id[sid])

    return render_template(
        'admin.html',
        students=students,
        guardians=guardians,
        student_guardians=student_guardians,
        guardian_students=guardian_students,
        notices=db().execute('SELECT * FROM notices ORDER BY id DESC').fetchall(),
        events=db().execute('SELECT * FROM events ORDER BY event_date DESC').fetchall(),
        categories=db().execute('SELECT * FROM event_categories ORDER BY name').fetchall(),
        report_subjects=db().execute(
            'SELECT * FROM report_subjects ORDER BY school_year DESC,segment,class_name,position'
        ).fetchall(),
        notice_categories=db().execute(
            'SELECT * FROM notice_categories ORDER BY name'
        ).fetchall()
    )

@app.post('/admin/aluno')
@admin_required
def add_student():
    cur=db().execute('INSERT INTO students(name,registration,grade_level,class_name) VALUES(?,?,?,?)',(value('name'),value('registration'),value('grade_level'),value('class_name')));audit('criar','aluno',cur.lastrowid);db().commit();return redirect(url_for('admin'))
@app.post('/admin/aluno/<int:sid>')
@admin_required
def update_student(sid):
    check_student(sid);db().execute('UPDATE students SET name=?,registration=?,grade_level=?,class_name=?,active=? WHERE id=?',(value('name'),value('registration'),value('grade_level'),value('class_name'),1 if value('active')=='1' else 0,sid));audit('editar','aluno',sid);db().commit();return redirect(url_for('student',sid=sid))
@app.post('/admin/responsavel')
@admin_required
def add_guardian():
    password=request.form.get('password','')
    if len(password)<6:flash('A senha inicial precisa ter pelo menos 6 caracteres.','error');return redirect(url_for('admin'))
    cur=db().execute('INSERT INTO users(name,email,password_hash,role) VALUES(?,?,?,?)',(value('name'),value('email').lower(),generate_password_hash(password),'responsavel'));audit('criar','responsavel',cur.lastrowid);db().commit();return redirect(url_for('admin'))


@app.post('/admin/responsavel/<int:uid>/editar')
@admin_required
def edit_guardian(uid):
    guardian = db().execute(
        "SELECT id FROM users WHERE id=? AND role='responsavel'",
        (uid,)
    ).fetchone()

    if not guardian:
        abort(404)

    name = value('name')
    email = value('email').lower()
    password = request.form.get('password', '')

    if not name or not email:
        abort(400)

    if password and len(password) < 6:
        flash('A nova senha precisa ter pelo menos 6 caracteres.', 'error')
        return redirect(url_for('admin') + '#responsaveis')

    if password:
        db().execute(
            """
            UPDATE users
            SET name=?, email=?, password_hash=?
            WHERE id=? AND role='responsavel'
            """,
            (name, email, generate_password_hash(password), uid)
        )
    else:
        db().execute(
            """
            UPDATE users
            SET name=?, email=?
            WHERE id=? AND role='responsavel'
            """,
            (name, email, uid)
        )

    audit('editar', 'responsavel', uid)
    db().commit()

    flash('Dados do responsável atualizados com sucesso!', 'success')
    return redirect(url_for('admin') + '#responsaveis')

@app.post('/admin/vinculo')
@admin_required
def link_guardian():
    uid=number('user_id');sid=number('student_id');db().execute('INSERT OR IGNORE INTO guardians(user_id,student_id) VALUES(?,?)',(uid,sid));audit('vincular','aluno',sid);db().commit();return redirect(url_for('admin'))
@app.post('/admin/aluno/<int:sid>/nota')
@admin_required
def add_grade(sid):
    check_student(sid)
    try:score=float(value('score').replace(',','.'))
    except ValueError:abort(400)
    if not 0<=score<=10:abort(400)
    db().execute('INSERT INTO grades(student_id,subject,term,score) VALUES(?,?,?,?) ON CONFLICT(student_id,subject,term) DO UPDATE SET score=excluded.score,updated_at=CURRENT_TIMESTAMP',(sid,value('subject'),value('term'),score));audit('atualizar','nota',sid);db().commit();return redirect(url_for('student',sid=sid)+'#notas')
@app.post('/admin/aluno/<int:sid>/financeiro')
@admin_required
def add_payment(sid):
    check_student(sid)
    year=number('year');month=number('month')
    if not 2000<=year<=2100 or not 1<=month<=12:abort(400)
    try:
        import decimal
        cents=int((decimal.Decimal(value('amount').replace('.','').replace(',','.'))*100).quantize(decimal.Decimal('1')))
    except (decimal.InvalidOperation,ValueError):abort(400,'Valor inválido')
    if cents<0:abort(400)
    due=value('due_date')
    try:date.fromisoformat(due)
    except ValueError:abort(400,'Data inválida')
    status=value('status')
    if status not in ('Pendente','Pago','Vencido'):abort(400)
    pdf=request.files.get('file')
    if not pdf or not pdf.filename:abort(400,'Envie o boleto em PDF.')
    filename=store_upload(pdf,'pdf')
    existing=db().execute('SELECT id FROM payments WHERE student_id=? AND reference_year=? AND reference_month=?',(sid,year,month)).fetchone()
    if existing:
        db().execute('UPDATE payments SET title=?,amount_cents=?,due_date=?,status=?,boleto_file=?,paid_at=CASE WHEN ?="Pago" THEN COALESCE(paid_at,CURRENT_TIMESTAMP) ELSE NULL END WHERE id=?',('Mensalidade',cents,due,status,filename,status,existing['id']))
        pid=existing['id']
    else:
        cur=db().execute('INSERT INTO payments(student_id,title,amount_cents,due_date,status,reference_year,reference_month,boleto_file,paid_at) VALUES(?,?,?,?,?,?,?,?,CASE WHEN ?="Pago" THEN CURRENT_TIMESTAMP ELSE NULL END)',(sid,'Mensalidade',cents,due,status,year,month,filename,status))
        pid=cur.lastrowid
    audit('salvar','boleto',pid);db().commit()
    return redirect(url_for('financeiro',sid=sid,ano=year))

@app.post('/admin/cobranca/<int:pid>/status')
@admin_required
def payment_status(pid):
    status=value('status')
    if status not in ('Pendente','Pago','Vencido'):abort(400)
    db().execute('UPDATE payments SET status=?,paid_at=CASE WHEN ?="Pago" THEN COALESCE(paid_at,CURRENT_TIMESTAMP) ELSE NULL END WHERE id=?',(status,status,pid));audit('atualizar','cobranca',pid);db().commit();return redirect(request.referrer if request.referrer and request.referrer.startswith(request.host_url) else url_for('admin'))
@app.get('/comunicado/<int:nid>/imagem')
@login_required
def notice_image(nid):
    n=db().execute('SELECT image_file,published,archived FROM notices WHERE id=?',(nid,)).fetchone()
    if not n or (current_user()['role']!='admin' and (not n['published'] or n['archived'])):abort(404)
    return private_file('image',n['image_file'])

@app.get('/comunicado/<int:nid>/anexo')
@login_required
def notice_attachment(nid):
    n=db().execute('SELECT attachment_file,published,archived FROM notices WHERE id=?',(nid,)).fetchone()
    if not n or (current_user()['role']!='admin' and (not n['published'] or n['archived'])):abort(404)
    response=private_file('pdf',n['attachment_file'])
    response.headers['Cache-Control']='private, no-store'
    response.headers['Content-Disposition']=(('attachment' if request.args.get('baixar')=='1' else 'inline')+'; filename="comunicado.pdf"')
    return response

@app.post('/admin/aviso')
@admin_required
def add_notice():
    title=value('title',200);body=value('body',10000)
    if not title or not body:abort(400)
    cid=number('notice_category_id') if value('notice_category_id') else None
    if cid and not db().execute('SELECT 1 FROM notice_categories WHERE id=? AND active=1',(cid,)).fetchone():abort(400)
    image=request.files.get('image');attachment=request.files.get('attachment')
    image_file=store_upload(image,'image') if image and image.filename else None
    attachment_file=store_upload(attachment,'pdf') if attachment and attachment.filename else None
    cur=db().execute('INSERT INTO notices(title,body,kind,notice_category_id,image_file,attachment_file,pinned,published,archived) VALUES(?,?,?,?,?,?,?,?,0)',(title,body,value('kind',100) or 'Comunicado',cid,image_file,attachment_file,1 if value('pinned')=='1' else 0,1 if value('published')=='1' else 0))
    audit('criar','aviso',cur.lastrowid);db().commit();return redirect(url_for('admin')+'#comunicados')

@app.post('/admin/aviso/<int:nid>')
@admin_required
def edit_notice(nid):
    old=db().execute('SELECT * FROM notices WHERE id=?',(nid,)).fetchone()
    if not old:abort(404)
    title=value('title',200);body=value('body',10000)
    if not title or not body:abort(400)
    cid=number('notice_category_id') if value('notice_category_id') else None
    if cid and not db().execute('SELECT 1 FROM notice_categories WHERE id=?',(cid,)).fetchone():abort(400)
    image=request.files.get('image');attachment=request.files.get('attachment')
    image_file=store_upload(image,'image') if image and image.filename else old['image_file']
    attachment_file=store_upload(attachment,'pdf') if attachment and attachment.filename else old['attachment_file']
    db().execute('UPDATE notices SET title=?,body=?,notice_category_id=?,image_file=?,attachment_file=?,pinned=?,published=?,archived=? WHERE id=?',(title,body,cid,image_file,attachment_file,1 if value('pinned')=='1' else 0,1 if value('published')=='1' else 0,1 if value('archived')=='1' else 0,nid))
    audit('editar','aviso',nid);db().commit();return redirect(url_for('admin')+'#comunicados')

@app.post('/admin/categoria-comunicado')
@admin_required
def add_notice_category():
    name=value('name',80)
    if not name:abort(400)
    cur=db().execute('INSERT INTO notice_categories(name) VALUES(?)',(name,));audit('criar','categoria_comunicado',cur.lastrowid);db().commit()
    return redirect(url_for('admin')+'#comunicados')

@app.post('/admin/categoria-comunicado/<int:cid>')
@admin_required
def edit_notice_category(cid):
    name=value('name',80)
    if not name:abort(400)
    db().execute('UPDATE notice_categories SET name=?,active=? WHERE id=?',(name,1 if value('active')=='1' else 0,cid));audit('editar','categoria_comunicado',cid);db().commit()
    return redirect(url_for('admin')+'#comunicados')

@app.post('/admin/evento')
@admin_required
def add_event():
    start=value('event_date');end=value('end_date') or start
    try:
        if date.fromisoformat(end)<date.fromisoformat(start):abort(400)
    except ValueError:abort(400)
    cur=db().execute('INSERT INTO events(event_date,end_date,title,details,category_id) VALUES(?,?,?,?,?)',(start,end,value('title'),value('details',1000),number('category_id') if value('category_id') else None));audit('criar','evento',cur.lastrowid);db().commit();return redirect(url_for('calendario'))
@app.post('/admin/evento/<int:eid>')
@admin_required
def edit_event(eid):
    if not db().execute('SELECT 1 FROM events WHERE id=?',(eid,)).fetchone():abort(404)
    start=value('event_date');end=value('end_date') or start
    try:
        if date.fromisoformat(end)<date.fromisoformat(start):abort(400)
    except ValueError:abort(400)
    cid=number('category_id') if value('category_id') else None
    db().execute('UPDATE events SET event_date=?,end_date=?,title=?,details=?,category_id=? WHERE id=?',(start,end,value('title'),value('details',1000),cid,eid))
    audit('editar','evento',eid);db().commit();return redirect(url_for('admin')+'#eventos')

@app.post('/admin/evento/<int:eid>/excluir')
@admin_required
def delete_event(eid):
    db().execute('DELETE FROM events WHERE id=?',(eid,));audit('excluir','evento',eid);db().commit()
    return redirect(url_for('admin')+'#eventos')

@app.post('/admin/mensagens/enviar')
@admin_required
def send_admin_message():
    subject=value('subject',200);body=value('body',2000);scope=value('scope')
    if not subject or not body or scope not in ('student','class','all'):abort(400)
    if scope=='student':
        sid=number('student_id');check_student(sid);student_ids=[sid]
    elif scope=='class':
        class_name=value('class_name',100)
        if not class_name:abort(400)
        student_ids=[r['id'] for r in db().execute('SELECT id FROM students WHERE class_name=? AND active=1',(class_name,))]
    else:
        student_ids=[r['id'] for r in db().execute('SELECT id FROM students WHERE active=1')]
    for sid in student_ids:
        cur=db().execute('INSERT INTO messages(student_id,sender_id,subject,body) VALUES(?,?,?,?)',(sid,session['uid'],subject,body))
        audit('enviar','mensagem',cur.lastrowid)
    db().commit();flash(f'Mensagem enviada para {len(student_ids)} aluno(s).','success')
    return redirect(url_for('admin')+'#mensagens')

@app.post('/admin/legenda')
@admin_required
def add_category():
    name=value('name',80);color=value('color',7)
    import re
    if not name or not re.fullmatch(r'#[0-9a-fA-F]{6}',color):abort(400)
    cur=db().execute('INSERT INTO event_categories(name,color) VALUES(?,?)',(name,color));audit('criar','legenda',cur.lastrowid);db().commit()
    return redirect(url_for('admin')+'#legendas')
@app.post('/admin/legenda/<int:cid>')
@admin_required
def update_category(cid):
    name=value('name',80);color=value('color',7)
    import re
    if not name or not re.fullmatch(r'#[0-9a-fA-F]{6}',color):abort(400)
    db().execute('UPDATE event_categories SET name=?,color=?,active=? WHERE id=?',(name,color,1 if value('active')=='1' else 0,cid));audit('editar','legenda',cid);db().commit()
    return redirect(url_for('admin')+'#legendas')
@app.errorhandler(PGIntegrityError)
def integrity(_):db().rollback();flash('Registro duplicado ou dados inválidos.','error');return redirect(url_for('admin'))
@app.errorhandler(403)
def forbidden(_):return 'Acesso não autorizado.',403
if __name__=='__main__':
    init_db();app.run(debug=os.getenv('FLASK_DEBUG')=='1')
