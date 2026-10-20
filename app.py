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
