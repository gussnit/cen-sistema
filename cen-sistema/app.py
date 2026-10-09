import os, sqlite3, secrets, click
from datetime import date
from functools import wraps
from pathlib import Path
from flask import Flask, render_template, request, redirect, url_for, session, flash, abort, g
from werkzeug.security import generate_password_hash, check_password_hash
from flask_wtf.csrf import CSRFProtect
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

ROOT=Path(__file__).resolve().parent
app=Flask(__name__)
app.config.update(SECRET_KEY=os.environ.get('SECRET_KEY') or secrets.token_hex(32),SESSION_COOKIE_HTTPONLY=True,SESSION_COOKIE_SAMESITE='Lax',SESSION_COOKIE_SECURE=os.getenv('COOKIE_SECURE','0')=='1',MAX_CONTENT_LENGTH=2*1024*1024)
csrf=CSRFProtect(app)
limiter=Limiter(get_remote_address,app=app,default_limits=[])
DB=os.getenv('DATABASE_PATH',str(ROOT/'cen.sqlite3'))

def db():
    if 'db' not in g:
        g.db=sqlite3.connect(DB);g.db.row_factory=sqlite3.Row;g.db.execute('PRAGMA foreign_keys=ON')
    return g.db
@app.teardown_appcontext
def close(_):
    c=g.pop('db',None)
    if c:c.close()

def init_db():
    with app.app_context():
        db().executescript('''
        CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, name TEXT NOT NULL,email TEXT NOT NULL UNIQUE,password_hash TEXT NOT NULL,role TEXT NOT NULL CHECK(role IN ('admin','responsavel')),active INTEGER NOT NULL DEFAULT 1);
        CREATE TABLE IF NOT EXISTS students(id INTEGER PRIMARY KEY,name TEXT NOT NULL,registration TEXT NOT NULL UNIQUE,grade_level TEXT NOT NULL,class_name TEXT NOT NULL,active INTEGER NOT NULL DEFAULT 1);
        CREATE TABLE IF NOT EXISTS guardians(user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,student_id INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,PRIMARY KEY(user_id,student_id));
        CREATE TABLE IF NOT EXISTS grades(id INTEGER PRIMARY KEY,student_id INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,subject TEXT NOT NULL,term TEXT NOT NULL,score REAL NOT NULL CHECK(score>=0 AND score<=10),updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,UNIQUE(student_id,subject,term));
        CREATE TABLE IF NOT EXISTS payments(id INTEGER PRIMARY KEY,student_id INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,title TEXT NOT NULL,amount_cents INTEGER NOT NULL CHECK(amount_cents>=0),due_date TEXT NOT NULL,status TEXT NOT NULL CHECK(status IN ('Pendente','Pago','Vencido')));
        CREATE TABLE IF NOT EXISTS notices(id INTEGER PRIMARY KEY,title TEXT NOT NULL,body TEXT NOT NULL,kind TEXT NOT NULL DEFAULT 'Comunicado',created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY,event_date TEXT NOT NULL,title TEXT NOT NULL,details TEXT NOT NULL DEFAULT '');
        CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY,student_id INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,sender_id INTEGER NOT NULL REFERENCES users(id),body TEXT NOT NULL,created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY,actor_id INTEGER NOT NULL,action TEXT NOT NULL,entity TEXT NOT NULL,entity_id INTEGER,created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
        ''');db().commit()

@app.cli.command('init-db')
def init_command():
    init_db();click.echo('Banco de dados criado.')
@app.cli.command('create-admin')
@click.option('--name',prompt=True)
@click.option('--email',prompt=True)
@click.password_option()
def create_admin(name,email,password):
    init_db()
    with app.app_context():
        db().execute('INSERT INTO users(name,email,password_hash,role) VALUES(?,?,?,?)',(name.strip(),email.strip().lower(),generate_password_hash(password),'admin'));db().commit();click.echo('Administrador criado.')

def current_user():
    if 'uid' not in session:return None
    return db().execute('SELECT * FROM users WHERE id=? AND active=1',(session['uid'],)).fetchone()
@app.context_processor
def context():return {'me':current_user(),'today':date.today().isoformat()}
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
    return render_template('home.html',students=allowed_students(),notices=db().execute('SELECT * FROM notices ORDER BY id DESC LIMIT 4').fetchall())
@app.route('/login',methods=['GET','POST'])
@limiter.limit('8 per minute',methods=['POST'])
def login():
    if request.method=='POST':
        u=db().execute('SELECT * FROM users WHERE email=? AND active=1',(value('email').lower(),)).fetchone()
        if u and check_password_hash(u['password_hash'],request.form.get('password','')):
            session.clear();session['uid']=u['id'];return redirect(url_for('home'))
        flash('E-mail ou senha inválidos.','error')
    return render_template('login.html')
@app.post('/logout')
@login_required
def logout():session.clear();return redirect(url_for('login'))
@app.get('/aluno/<int:sid>')
@login_required
def student(sid):
    s=check_student(sid)
    return render_template('student.html',s=s,grades=db().execute('SELECT * FROM grades WHERE student_id=? ORDER BY subject,term',(sid,)).fetchall(),payments=db().execute('SELECT * FROM payments WHERE student_id=? ORDER BY due_date DESC',(sid,)).fetchall(),messages=db().execute('SELECT m.*,u.name sender FROM messages m JOIN users u ON u.id=m.sender_id WHERE student_id=? ORDER BY m.id',(sid,)).fetchall())
@app.post('/aluno/<int:sid>/mensagem')
@login_required
@limiter.limit('20 per hour')
def send_message(sid):
    check_student(sid);body=value('body',2000)
    if body:
        db().execute('INSERT INTO messages(student_id,sender_id,body) VALUES(?,?,?)',(sid,session['uid'],body));db().commit()
    return redirect(url_for('student',sid=sid)+'#mensagens')
@app.get('/mural')
@login_required
def mural():return render_template('mural.html',notices=db().execute('SELECT * FROM notices ORDER BY id DESC').fetchall(),events=db().execute('SELECT * FROM events ORDER BY event_date DESC').fetchall())
@app.get('/admin')
@admin_required
def admin():
    return render_template('admin.html',students=allowed_students(),guardians=db().execute("SELECT * FROM users WHERE role='responsavel' ORDER BY name").fetchall(),notices=db().execute('SELECT * FROM notices ORDER BY id DESC').fetchall(),events=db().execute('SELECT * FROM events ORDER BY event_date DESC').fetchall())
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
    if len(password)<12:flash('A senha inicial precisa ter pelo menos 12 caracteres.','error');return redirect(url_for('admin'))
    cur=db().execute('INSERT INTO users(name,email,password_hash,role) VALUES(?,?,?,?)',(value('name'),value('email').lower(),generate_password_hash(password),'responsavel'));audit('criar','responsavel',cur.lastrowid);db().commit();return redirect(url_for('admin'))
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
    try:cents=round(float(value('amount').replace(',','.'))*100)
    except ValueError:abort(400)
    if cents<0:abort(400)
    cur=db().execute('INSERT INTO payments(student_id,title,amount_cents,due_date,status) VALUES(?,?,?,?,?)',(sid,value('title'),cents,value('due_date'),value('status')));audit('criar','cobranca',cur.lastrowid);db().commit();return redirect(url_for('student',sid=sid)+'#financeiro')
@app.post('/admin/cobranca/<int:pid>/status')
@admin_required
def payment_status(pid):
    status=value('status')
    if status not in ('Pendente','Pago','Vencido'):abort(400)
    db().execute('UPDATE payments SET status=? WHERE id=?',(status,pid));audit('atualizar','cobranca',pid);db().commit();return redirect(request.referrer if request.referrer and request.referrer.startswith(request.host_url) else url_for('admin'))
@app.post('/admin/aviso')
@admin_required
def add_notice():
    cur=db().execute('INSERT INTO notices(title,body,kind) VALUES(?,?,?)',(value('title'),value('body',3000),value('kind')));audit('criar','aviso',cur.lastrowid);db().commit();return redirect(url_for('mural'))
@app.post('/admin/evento')
@admin_required
def add_event():
    cur=db().execute('INSERT INTO events(event_date,title,details) VALUES(?,?,?)',(value('event_date'),value('title'),value('details',1000)));audit('criar','evento',cur.lastrowid);db().commit();return redirect(url_for('mural'))
@app.errorhandler(sqlite3.IntegrityError)
def integrity(_):db().rollback();flash('Registro duplicado ou dados inválidos.','error');return redirect(url_for('admin'))
@app.errorhandler(403)
def forbidden(_):return 'Acesso não autorizado.',403
if __name__=='__main__':
    init_db();app.run(debug=os.getenv('FLASK_DEBUG')=='1')
