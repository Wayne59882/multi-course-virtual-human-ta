#!/usr/bin/env python3
"""Multi-Course Virtual Human Teaching Assistant accounts and roles (R12).

* Everyone signs in before using the system (built-in course included).
* New accounts are "pending" until the administrator approves them from the
  review link that is e-mailed to VQ_AUTH_ADMIN_EMAIL (default: the feedback
  recipient, VQ_FEEDBACK_TO_EMAIL).  The administrator decides "student" or
  "teacher".
* Teachers upload PDFs and build courses.  A teacher only sees the courses
  they built.  Each course has a course code; students who enter the code see
  that course.  Students cannot upload or change courses.

Storage: auth/auth.sqlite3 next to this file (passwords are PBKDF2-SHA256
hashes; session and review tokens are stored only as SHA-256 hashes).
Compatible with Python 3.6+.
"""
import hashlib, hmac, html, json, os, re, secrets, shlex, smtplib, sqlite3, ssl, sys, threading, time
import urllib.error, urllib.request
from email.message import EmailMessage
from pathlib import Path
from urllib.parse import parse_qs, quote

ROOT = Path(__file__).resolve().parent


def _load_env_file(path):
    """Read `export KEY=value` lines (feedback/feedback.env) without overriding the environment."""
    try:
        lines = path.read_text(encoding='utf-8').splitlines()
    except Exception:
        return
    for line in lines:
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        if line.startswith('export '):
            line = line[7:].strip()
        if '=' not in line:
            continue
        key, value = line.split('=', 1)
        key = key.strip()
        if not re.match(r'^[A-Za-z_][A-Za-z0-9_]*$', key) or os.environ.get(key):
            continue
        try:
            parts = shlex.split(value)
        except ValueError:
            continue
        os.environ[key] = parts[0] if parts else ''


_load_env_file(ROOT / 'feedback' / 'feedback.env')

DB_PATH = Path(os.environ.get('VQ_AUTH_DB') or str(ROOT / 'auth' / 'auth.sqlite3'))
_SITE_URL_FILE = Path(__file__).resolve().parent / 'logs' / 'site_url.txt'


def public_url():
    """The site's public address: VQ_PUBLIC_URL, else the address recorded by the
    automatic update after the site moved, else the original address."""
    configured = os.environ.get('VQ_PUBLIC_URL')
    if configured:
        return configured.strip().rstrip('/')
    try:
        recorded = _SITE_URL_FILE.read_text(encoding='utf-8').strip().rstrip('/')
        if recorded.startswith('https://'):
            return recorded
    except OSError:
        pass
    return 'https://lungta.twchu.workers.dev'


PUBLIC_URL = public_url()
# Existing deployments keep their session-cookie protocol across the project rename.
COOKIE = 'lungta_sid'
SESSION_SEC = 30 * 86400
REVIEW_SEC = 30 * 86400
PBKDF2_ITER = 200000
CODE_ALPHABET = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'
ROLE_LABEL = {'student': '學生 Student', 'teacher': '老師 Teacher'}
STATUS_LABEL = {'pending': '審核中 Pending', 'approved': '已核准 Approved', 'rejected': '未核准 Rejected',
                'disabled': '已停用 Disabled'}

# Course functions only teachers may call (upload, build, delete, regenerate).
TEACHER_ONLY = {'course-upload-chunk', 'course-upload-status', 'course-inspect', 'course-stage-status',
                'course-build', 'course-job-status', 'course-build-cancel', 'course-delete', 'course-retry-preparation',
                'course-expand-questions'}
PUBLIC_API = {'/api/health', '/api/reference/health', '/api/kb/health'}
# Pages that open the application; everything else under web/ is a static asset.
GATED_PAGES = {'/', '/index.html', '/reference', '/reference/', '/reference/index.html',
               '/kb', '/kb/', '/kb/index.html'}


def admin_email():
    return (os.environ.get('VQ_AUTH_ADMIN_EMAIL') or os.environ.get('VQ_FEEDBACK_TO_EMAIL')
            or 'admin@example.com').strip().lower()


def _log(message):
    try:
        sys.stderr.write('[AUTH] %s %s\n' % (time.strftime('%Y-%m-%d %H:%M:%S'), message))
        sys.stderr.flush()
    except Exception:
        pass


# ---------------------------------------------------------------- database
_LOCK = threading.RLock()
_READY = False
SCHEMA = '''
CREATE TABLE IF NOT EXISTS users(
  id INTEGER PRIMARY KEY AUTOINCREMENT, email TEXT UNIQUE NOT NULL, name TEXT NOT NULL, pw TEXT NOT NULL,
  requested_role TEXT NOT NULL, role TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'pending',
  note TEXT NOT NULL DEFAULT '', created_at REAL, decided_at REAL, last_login REAL, review_sent_at REAL);
CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY, user_id INTEGER NOT NULL, created_at REAL, expires_at REAL);
CREATE TABLE IF NOT EXISTS reviews(token TEXT PRIMARY KEY, user_id INTEGER NOT NULL, created_at REAL, used_at REAL, decision TEXT);
CREATE TABLE IF NOT EXISTS courses(course_id TEXT PRIMARY KEY, owner_id INTEGER NOT NULL, code TEXT UNIQUE, created_at REAL);
CREATE TABLE IF NOT EXISTS stages(stage_id TEXT PRIMARY KEY, owner_id INTEGER NOT NULL, created_at REAL);
CREATE TABLE IF NOT EXISTS jobs(job_id TEXT PRIMARY KEY, owner_id INTEGER NOT NULL, course_id TEXT, created_at REAL);
CREATE TABLE IF NOT EXISTS enrollments(user_id INTEGER NOT NULL, course_id TEXT NOT NULL, created_at REAL, PRIMARY KEY(user_id, course_id));
CREATE INDEX IF NOT EXISTS sessions_user ON sessions(user_id);
CREATE INDEX IF NOT EXISTS courses_owner ON courses(owner_id);
'''


def _connect():
    c = sqlite3.connect(str(DB_PATH), timeout=20)
    c.row_factory = sqlite3.Row
    return c


def init():
    global _READY
    with _LOCK:
        if _READY:
            return
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(str(DB_PATH.parent), 0o700)
        except Exception:
            pass
        c = _connect()
        try:
            c.executescript(SCHEMA)
            # The administrator can use the site as a chosen teacher (sessions.acting_as).
            if 'acting_as' not in [row[1] for row in c.execute('PRAGMA table_info(sessions)')]:
                c.execute('ALTER TABLE sessions ADD COLUMN acting_as INTEGER')
            c.commit()
        finally:
            c.close()
        try:
            os.chmod(str(DB_PATH), 0o600)
        except Exception:
            pass
        _READY = True


def _run(fn):
    """Run fn(connection) in one transaction."""
    init()
    with _LOCK:
        c = _connect()
        try:
            result = fn(c)
            c.commit()
            return result
        except Exception:
            c.rollback()
            raise
        finally:
            c.close()


def _one(sql, args=()):
    return _run(lambda c: c.execute(sql, args).fetchone())


def _all(sql, args=()):
    return _run(lambda c: c.execute(sql, args).fetchall())


def _exec(sql, args=()):
    return _run(lambda c: c.execute(sql, args).rowcount)


def _th(token):
    return hashlib.sha256(str(token).encode('utf-8')).hexdigest()


# ---------------------------------------------------------------- passwords
def hash_password(password):
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, PBKDF2_ITER)
    return 'pbkdf2_sha256$%d$%s$%s' % (PBKDF2_ITER, salt.hex(), digest.hex())


def check_password(password, stored):
    try:
        _algo, iterations, salt, expected = str(stored).split('$')
        digest = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), bytes.fromhex(salt), int(iterations))
        return hmac.compare_digest(digest.hex(), expected)
    except Exception:
        return False


_DUMMY_HASH = None


def _dummy_check(password):
    global _DUMMY_HASH
    if _DUMMY_HASH is None:
        _DUMMY_HASH = hash_password(secrets.token_urlsafe(12))
    check_password(password, _DUMMY_HASH)


# ---------------------------------------------------------------- users
EMAIL_RE = re.compile(r'^[^@\s<>"\'(),;:]{1,64}@[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+$')


def _clean(value, limit):
    return re.sub(r'\s+', ' ', str(value or '')).strip()[:limit]


def public_user(row):
    if not row:
        return None
    email = row['email']
    return {'id': row['id'], 'email': email, 'name': row['name'], 'role': row['role'] or '',
            'status': row['status'], 'requested_role': row['requested_role'],
            'is_admin': email == admin_email() and row['status'] == 'approved'}


def user_by_email(email):
    return _one('SELECT * FROM users WHERE email=?', (str(email or '').strip().lower(),))


def user_by_id(uid):
    return _one('SELECT * FROM users WHERE id=?', (int(uid),))


def _session_token(handler):
    raw = handler.headers.get('Cookie') or ''
    m = re.search(r'(?:^|;\s*)' + COOKIE + r'=([A-Za-z0-9_\-]{20,200})', raw)
    return m.group(1) if m else ''


def user_from_request(handler, act=True):
    """The signed-in user.  While the administrator uses the site as a teacher, that teacher
    (with acting_admin set) unless act=False asks for the administrator."""
    token = _session_token(handler)
    if not token:
        return None
    row = _one('SELECT u.*, s.acting_as AS acting_as FROM sessions s JOIN users u ON u.id=s.user_id '
               'WHERE s.token=? AND s.expires_at>? AND u.status=?', (_th(token), time.time(), 'approved'))
    user = public_user(row)
    if act and user and user['is_admin'] and row['acting_as']:
        target = teacher_account(row['acting_as'])
        if target and target['id'] != user['id']:
            acted = public_user(target)
            acted['acting_admin'] = {'id': user['id'], 'name': user['name']}
            return acted
    return user


def create_session(user_id):
    token = secrets.token_urlsafe(32)
    now = time.time()

    def work(c):
        c.execute('DELETE FROM sessions WHERE expires_at<?', (now,))
        c.execute('INSERT INTO sessions(token,user_id,created_at,expires_at) VALUES(?,?,?,?)',
                  (_th(token), user_id, now, now + SESSION_SEC))
        c.execute('UPDATE users SET last_login=? WHERE id=?', (now, user_id))
    _run(work)
    return token


def _cookie(handler, token, max_age):
    secure = bool(handler.headers.get('x-vq-cloudflare-proxy')) or \
        (handler.headers.get('X-Forwarded-Proto') or '').lower() == 'https'
    return '%s=%s; Path=/; Max-Age=%d; HttpOnly; SameSite=Lax%s' % (COOKIE, token, max_age, '; Secure' if secure else '')


# ---------------------------------------------------------------- e-mail
def _resend_from():
    return os.environ.get('VQ_RESEND_FROM', '').strip() or 'MC-VHTA <onboarding@resend.dev>'


def _provider():
    requested = os.environ.get('VQ_EMAIL_PROVIDER', '').strip().lower()
    if requested == 'resend' and os.environ.get('VQ_RESEND_API_KEY', '').strip():
        return 'resend'
    if requested == 'smtp' and os.environ.get('VQ_SMTP_HOST', '').strip():
        return 'smtp'
    if os.environ.get('VQ_RESEND_API_KEY', '').strip():
        return 'resend'
    if os.environ.get('VQ_SMTP_HOST', '').strip():
        return 'smtp'
    return ''


def can_email(to_addr):
    """The Resend test sender (...@resend.dev) only delivers to the Resend account owner."""
    provider = _provider()
    if not provider:
        return False
    if provider == 'resend' and _resend_from().lower().rstrip('>').endswith('@resend.dev'):
        return to_addr.lower() == admin_email()
    return True


def send_email(to_addr, subject, text, html_body=None, key=''):
    provider = _provider()
    if provider == 'resend':
        payload = {'from': _resend_from(), 'to': [to_addr], 'subject': subject, 'text': text}
        if html_body:
            payload['html'] = html_body
        headers = {'Authorization': 'Bearer ' + os.environ.get('VQ_RESEND_API_KEY', '').strip(),
                   'Content-Type': 'application/json', 'User-Agent': 'NTULUNG-TA-auth'}
        if key:
            headers['Idempotency-Key'] = key[:200]
        req = urllib.request.Request(os.environ.get('VQ_RESEND_API_URL', 'https://api.resend.com/emails').strip(),
                                     data=json.dumps(payload, ensure_ascii=False).encode('utf-8'),
                                     headers=headers, method='POST')
        try:
            with urllib.request.urlopen(req, timeout=float(os.environ.get('VQ_RESEND_TIMEOUT', '12'))) as r:
                data = json.loads(r.read().decode('utf-8', 'replace') or '{}')
                if not data.get('id'):
                    raise RuntimeError('Resend returned no email id')
                return data['id']
        except urllib.error.HTTPError as e:
            raise RuntimeError('Resend HTTP %s: %s' % (e.code, e.read().decode('utf-8', 'replace')[:300]))
    if provider == 'smtp':
        host = os.environ.get('VQ_SMTP_HOST', '').strip()
        port = int(os.environ.get('VQ_SMTP_PORT', '587') or 587)
        user = os.environ.get('VQ_SMTP_USER', '').strip()
        password = os.environ.get('VQ_SMTP_PASSWORD', '')
        msg = EmailMessage()
        msg['To'] = to_addr
        msg['From'] = os.environ.get('VQ_SMTP_FROM_EMAIL', '').strip() or user or admin_email()
        msg['Subject'] = subject
        msg.set_content(text)
        if html_body:
            msg.add_alternative(html_body, subtype='html')
        timeout = float(os.environ.get('VQ_SMTP_TIMEOUT', '10'))
        use_ssl = str(os.environ.get('VQ_SMTP_SSL', '0')).lower() in ('1', 'true', 'yes')
        s = smtplib.SMTP_SSL(host, port, timeout=timeout, context=ssl.create_default_context()) if use_ssl \
            else smtplib.SMTP(host, port, timeout=timeout)
        try:
            if not use_ssl and str(os.environ.get('VQ_SMTP_STARTTLS', '1')).lower() not in ('0', 'false', 'no'):
                s.starttls(context=ssl.create_default_context())
            if user:
                s.login(user, password)
            s.send_message(msg)
        finally:
            try:
                s.quit()
            except Exception:
                pass
        return 'smtp'
    raise RuntimeError('No e-mail service is configured (feedback/feedback.env)')


def _review_email(user, token):
    link = public_url() + '/api/auth/review?t=' + quote(token)
    role = ROLE_LABEL.get(user['requested_role'], user['requested_role'])
    when = time.strftime('%Y-%m-%d %H:%M', time.localtime(user['created_at'] or time.time()))
    subject = '[MC-VHTA] 帳號申請待審核：%s（申請身分：%s）' % (user['name'], role.split(' ')[0])
    text = ('有新的帳號申請需要審核 / A new account is waiting for approval.\n\n'
            '姓名 Name：%s\nEmail：%s\n申請身分 Requested role：%s\n說明 Note：%s\n申請時間 Submitted：%s\n\n'
            '開啟審核頁面，選擇「核准為學生」、「核准為老師」或「不核准」：\n%s\n\n'
            '此連結 30 天內有效，只能使用一次。\n') % (user['name'], user['email'], role, user['note'] or '—', when, link)
    esc = html.escape
    html_body = ('<div style="font-family:Arial,\'Noto Sans TC\',sans-serif;font-size:15px;color:#1d3550;line-height:1.6">'
                 '<p>有新的帳號申請需要審核 / A new account is waiting for approval.</p>'
                 '<table style="border-collapse:collapse">'
                 + ''.join('<tr><td style="padding:3px 14px 3px 0;color:#5b7087">%s</td><td><b>%s</b></td></tr>' % (esc(k), esc(v))
                           for k, v in (('姓名 Name', user['name']), ('Email', user['email']), ('申請身分 Role', role),
                                        ('說明 Note', user['note'] or '—'), ('申請時間', when))) +
                 '</table><p style="margin:22px 0"><a href="%s" style="background:#1f6fb2;color:#fff;padding:11px 20px;'
                 'border-radius:8px;text-decoration:none;font-weight:bold">開啟審核頁面 Review</a></p>'
                 '<p style="color:#6b7f93;font-size:13px">此連結 30 天內有效，只能使用一次。</p></div>') % esc(link)
    return subject, text, html_body


def _notify_applicant(user, decision):
    """Best effort.  Returns (sent, note)."""
    to_addr = user['email']
    if not can_email(to_addr):
        return False, 'not-supported'
    if decision == 'reject':
        subject = '[MC-VHTA] 帳號申請結果'
        text = '%s 您好：\n\n您的 MC-VHTA 帳號申請未獲核准。如有疑問，請聯絡課程老師或管理員。\n' % user['name']
    else:
        subject = '[MC-VHTA] 帳號已開通'
        text = ('%s 您好：\n\n您的 MC-VHTA 帳號已核准，身分：%s。\n請到以下網址登入：\n%s/login/\n'
                % (user['name'], ROLE_LABEL.get(decision, decision), public_url()))
    try:
        send_email(to_addr, subject, text, key='auth-decision/%s/%s/%d' % (user['id'], decision, int(time.time() // 60)))
        return True, ''
    except Exception as e:
        _log('applicant notification failed: %s' % e)
        return False, str(e)[:200]


# ---------------------------------------------------------------- rate limits
_RL_LOCK = threading.Lock()
_FAILS = {}
_REG_TIMES = []


def _too_many_failures(email):
    now = time.time()
    with _RL_LOCK:
        recent = [t for t in _FAILS.get(email, []) if now - t < 900]
        _FAILS[email] = recent
        return len(recent) >= 8


def _record_failure(email):
    with _RL_LOCK:
        _FAILS.setdefault(email, []).append(time.time())
        if len(_FAILS) > 5000:
            _FAILS.clear()


def _registration_allowed():
    now = time.time()
    with _RL_LOCK:
        _REG_TIMES[:] = [t for t in _REG_TIMES if now - t < 3600]
        if len(_REG_TIMES) >= 40:
            return False
        _REG_TIMES.append(now)
        return True


# ---------------------------------------------------------------- courses
def _new_code(c):
    while True:
        code = ''.join(secrets.choice(CODE_ALPHABET) for _ in range(6))
        if not c.execute('SELECT 1 FROM courses WHERE code=?', (code,)).fetchone():
            return code


def course_owner(course_id):
    row = _one('SELECT owner_id FROM courses WHERE course_id=?', (course_id,))
    return row['owner_id'] if row else None


def record_course(course_id, owner_id):
    """Assign an unowned course to owner_id; returns the owner."""
    def work(c):
        row = c.execute('SELECT owner_id FROM courses WHERE course_id=?', (course_id,)).fetchone()
        if row:
            return row['owner_id']
        c.execute('INSERT INTO courses(course_id,owner_id,code,created_at) VALUES(?,?,?,?)',
                  (course_id, owner_id, _new_code(c), time.time()))
        return owner_id
    return _run(work)


def course_code(course_id):
    row = _one('SELECT code FROM courses WHERE course_id=?', (course_id,))
    return row['code'] if row else ''


def is_enrolled(user_id, course_id):
    return bool(_one('SELECT 1 FROM enrollments WHERE user_id=? AND course_id=?', (user_id, course_id)))


def can_access(user, course_id):
    if user.get('is_admin'):
        return True  # the administrator manages every teacher's courses
    if course_owner(course_id) == user['id']:
        return True
    return user['role'] == 'student' and is_enrolled(user['id'], course_id)


def is_owner(user, course_id):
    return bool(user.get('is_admin')) or course_owner(course_id) == user['id']


def teacher_account(user_id):
    """An approved teacher (or the administrator) who may own courses; None otherwise."""
    try:
        row = user_by_id(int(user_id))
    except (TypeError, ValueError):
        return None
    if row and row['status'] == 'approved' and (row['role'] == 'teacher' or row['email'] == admin_email()):
        return row
    return None


def set_course_owner(course_id, owner_id):
    def work(c):
        if c.execute('SELECT 1 FROM courses WHERE course_id=?', (course_id,)).fetchone():
            c.execute('UPDATE courses SET owner_id=? WHERE course_id=?', (owner_id, course_id))
        else:
            c.execute('INSERT INTO courses(course_id,owner_id,code,created_at) VALUES(?,?,?,?)',
                      (course_id, owner_id, _new_code(c), time.time()))
    _run(work)


# ---------------------------------------------------------------- API gate
def _json_body(body, ctype):
    if not body or 'json' not in (ctype or '').lower():
        return None
    try:
        data = json.loads(body.decode('utf-8'))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _multipart_field(body, ctype, name):
    if not body or 'multipart/form-data' not in (ctype or '').lower():
        return ''
    m = re.search(rb'name="' + name.encode() + rb'"\r\n(?:[^\r\n]+\r\n)*\r\n([^\r\n]{1,200})\r\n', body)
    return m.group(1).decode('utf-8', 'replace').strip() if m else ''


def _deny(status, code, message):
    return {'status': status, 'payload': {'ok': False, 'error': message, 'code': code}}


def authorize(user, path, query, body, ctype):
    """Check an /api/ request of a signed-in user.  Returns (denial or None, context)."""
    seg = path.rstrip('/').rsplit('/', 1)[-1]
    qs = parse_qs(query or '')
    data = _json_body(body, ctype)
    ctx = {'seg': seg, 'json': data, 'course_id': '', 'stage_id': ''}
    diagnostic_ops={'course-upload-diagnostics','course-upload-source','course-admin-uploads'}
    if seg=='course-admin-uploads' and not user.get('is_admin'):
        return _deny(403,'admin_only','Only the administrator can inspect all uploads.'),ctx
    teacher_only = seg in diagnostic_ops or seg in TEACHER_ONLY or seg.startswith('course-ai-')
    admin = bool(user.get('is_admin'))
    if teacher_only and user['role'] != 'teacher' and not admin:
        return _deny(403, 'teacher_only', 'Only teachers can upload or manage course materials.'), ctx

    def first(name):
        v = (qs.get(name) or [''])[0]
        if not v and data is not None and isinstance(data.get(name), str):
            v = data.get(name)
        return str(v or '').strip()[:200]

    stage_id = first('stage_id') or _multipart_field(body, ctype, 'stage_id')
    if stage_id:
        ctx['stage_id'] = stage_id
        row = _one('SELECT owner_id FROM stages WHERE stage_id=?', (stage_id,))
        if seg in diagnostic_ops and not row and not admin:
            return _deny(403,'stage_forbidden','Upload ownership could not be verified.'),ctx
        if row and row['owner_id'] != user['id'] and not admin:
            return _deny(403, 'stage_forbidden', 'This upload belongs to another teacher.'), ctx
        if not row and teacher_only and seg not in diagnostic_ops:
            _exec('INSERT OR IGNORE INTO stages(stage_id,owner_id,created_at) VALUES(?,?,?)',
                  (stage_id, user['id'], time.time()))
    if seg in ('course-job-status', 'course-build-cancel'):
        job_id = first('id') or first('job_id')
        row = _one('SELECT owner_id FROM jobs WHERE job_id=?', (job_id,)) if job_id else None
        if row and row['owner_id'] != user['id'] and not admin:
            return _deny(403, 'job_forbidden', 'This course build belongs to another teacher.'), ctx
        if seg == 'course-build-cancel' and not row and not admin:
            return _deny(403, 'job_forbidden', 'Only the teacher who started this build can cancel it.'), ctx
    if seg == 'course-build' and admin and data is not None and data.get('owner_id') not in (None, ''):
        # The administrator can build a course on behalf of another teacher.
        owner = teacher_account(data.get('owner_id'))
        if not owner:
            return _deny(400, 'owner_invalid', 'Choose an approved teacher as the course owner.'), ctx
        ctx['owner_id'] = owner['id']
    course_id = first('course_id') or (first('id') if seg == 'course-status' else '')
    if course_id:
        ctx['course_id'] = course_id
        allowed = is_owner(user, course_id) if teacher_only else can_access(user, course_id)
        if not allowed:
            return _deny(403, 'course_forbidden', 'You do not have access to this course. Ask the course teacher for the course code.'), ctx
    return None, ctx


def after_response(user, ctx, status, data, ctype):
    """Record ownership from build responses and filter the course list.  Returns the body to send."""
    seg = ctx.get('seg')
    if status >= 300 or not seg or not seg.startswith('course-') or 'json' not in (ctype or '').lower():
        return data
    try:
        payload = json.loads(data.decode('utf-8'))
    except Exception:
        return data
    if not isinstance(payload, dict):
        return data
    if seg=='course-admin-uploads' and user.get('is_admin'):
        owners={row['stage_id']:{'id':row['owner_id'],'name':row['name'] or '',
                'email':row['email'] or '','role':row['role'] or ''} for row in _all(
            'SELECT s.stage_id,s.owner_id,u.name,u.email,u.role FROM stages s LEFT JOIN users u ON u.id=s.owner_id')}
        for report in payload.get('uploads') or []:
            report['owner']=owners.get(report.get('stage_id'))
        return json.dumps(payload,ensure_ascii=False).encode('utf-8')
    if seg == 'course-list':
        courses = payload.get('courses') if isinstance(payload.get('courses'), list) else []
        admin = bool(user.get('is_admin'))
        people = {row['id']: row for row in _all('SELECT id,name,email FROM users')} if admin else {}
        kept = []
        for course in courses:
            cid = str((course or {}).get('course_id') or '')
            if not cid:
                continue
            if user['role'] == 'teacher' or admin:
                owner = course_owner(cid)
                if owner is None and admin:
                    owner = record_course(cid, user['id'])  # courses built before accounts existed
                if owner == user['id'] or admin:
                    course = dict(course)
                    course['access_code'] = course_code(cid)
                    course['owned'] = owner == user['id']
                    if admin:
                        person = people.get(owner)
                        course['owner_id'] = owner
                        course['owner_name'] = (person['name'] or '') if person else ''
                        course['owner_email'] = (person['email'] or '') if person else ''
                    kept.append(course)
            elif is_enrolled(user['id'], cid):
                kept.append(course)
        payload['courses'] = kept
        payload['role'] = user['role']
        return json.dumps(payload, ensure_ascii=False).encode('utf-8')
    if (user['role'] == 'teacher' or user.get('is_admin')) and seg in ('course-build', 'course-job-status', 'course-stage-status'):
        job = payload.get('job') if isinstance(payload.get('job'), dict) else (payload if seg == 'course-job-status' else None)
        if job:
            cid = str(job.get('course_id') or ((job.get('result') or {}).get('course_id') if isinstance(job.get('result'), dict) else '') or '')
            jid = str(job.get('id') or '')
            owner = ctx.get('owner_id') or user['id']
            if jid:
                _exec('INSERT OR IGNORE INTO jobs(job_id,owner_id,course_id,created_at) VALUES(?,?,?,?)',
                      (jid, owner, cid, time.time()))
                row = _one('SELECT owner_id FROM jobs WHERE job_id=?', (jid,))
                owner = row['owner_id'] if row else owner  # the teacher the build was started for
            if cid:
                record_course(cid, owner)
    if seg == 'course-delete' and payload.get('ok') and ctx.get('course_id'):
        cid = ctx['course_id']
        _run(lambda c: (c.execute('DELETE FROM enrollments WHERE course_id=?', (cid,)),
                        c.execute('DELETE FROM courses WHERE course_id=?', (cid,))))
    return data


# ---------------------------------------------------------------- /api/auth/*
def _reply(handler, status, payload, cookie=None):
    body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
    handler.send_response(status)
    handler.send_header('Content-Type', 'application/json; charset=utf-8')
    handler.send_header('Content-Length', str(len(body)))
    if cookie:
        handler.send_header('Set-Cookie', cookie)
    handler.end_headers()
    try:
        handler.wfile.write(body)
    except (BrokenPipeError, ConnectionResetError):
        pass


def _page(handler, status, title, inner):
    doc = ('<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8">'
           '<meta name="viewport" content="width=device-width,initial-scale=1"><title>%s</title>'
           '<meta name="robots" content="noindex"><style>'
           'body{margin:0;background:#eef4fa;font-family:-apple-system,"Segoe UI","Noto Sans TC",Arial,sans-serif;color:#1d3550}'
           '.card{max-width:520px;margin:32px auto;background:#fff;border:1px solid #d3e1ee;border-radius:16px;padding:24px 22px;box-shadow:0 10px 30px rgba(30,70,110,.08)}'
           'h1{font-size:20px;margin:0 0 4px}.sub{color:#5b7087;font-size:13px;margin-bottom:16px}'
           'dl{display:grid;grid-template-columns:auto 1fr;gap:6px 14px;margin:0 0 18px}dt{color:#5b7087}dd{margin:0;font-weight:600;word-break:break-word}'
           '.btns{display:grid;gap:10px}button{font:inherit;font-weight:700;padding:12px;border-radius:10px;border:1px solid #8fb4d9;background:#fff;color:#174d7f;cursor:pointer}'
           'button.s{background:#1f6fb2;border-color:#1f6fb2;color:#fff}button.t{background:#0f766e;border-color:#0f766e;color:#fff}button.r{color:#a12d2d;border-color:#e3b4b4}'
           '.ok{background:#e8f6ee;color:#185c37;padding:12px;border-radius:10px}.warn{background:#fff6e5;color:#7a4b00;padding:12px;border-radius:10px;margin-top:10px}'
           '.err{background:#fff0f0;color:#8f2f2f;padding:12px;border-radius:10px}a{color:#1f6fb2}'
           '</style></head><body><main class="card">%s</main></body></html>') % (html.escape(title), inner)
    body = doc.encode('utf-8')
    handler.send_response(status)
    handler.send_header('Content-Type', 'text/html; charset=utf-8')
    handler.send_header('Content-Length', str(len(body)))
    handler.send_header('X-Robots-Tag', 'noindex')
    handler.send_header('Referrer-Policy', 'no-referrer')
    handler.end_headers()
    try:
        handler.wfile.write(body)
    except (BrokenPipeError, ConnectionResetError):
        pass


def _read(handler, limit=65536):
    n = int(handler.headers.get('Content-Length', '0') or 0)
    if n < 0 or n > limit:
        raise ValueError('Request too large')
    return handler.rfile.read(n) if n else b''


def _body_json(handler):
    try:
        data = json.loads((_read(handler) or b'{}').decode('utf-8'))
        return data if isinstance(data, dict) else {}
    except ValueError:
        return {}


def _decide(user_id, decision):
    """decision: student | teacher | reject | disable.  Returns the updated row."""
    now = time.time()

    def work(c):
        if decision in ('student', 'teacher'):
            c.execute("UPDATE users SET status='approved', role=?, decided_at=? WHERE id=?", (decision, now, user_id))
        elif decision == 'reject':
            c.execute("UPDATE users SET status='rejected', role='', decided_at=? WHERE id=?", (now, user_id))
        elif decision == 'disable':
            c.execute("UPDATE users SET status='disabled', decided_at=? WHERE id=?", (now, user_id))
        if decision in ('reject', 'disable'):
            c.execute('DELETE FROM sessions WHERE user_id=?', (user_id,))
        c.execute("UPDATE reviews SET used_at=?, decision=? WHERE user_id=? AND used_at IS NULL", (now, decision, user_id))
        return c.execute('SELECT * FROM users WHERE id=?', (user_id,)).fetchone()
    return _run(work)


def _review_page(handler, token, done=None, notice=''):
    row = _one('SELECT r.created_at AS r_created, r.used_at, r.decision, u.* FROM reviews r JOIN users u ON u.id=r.user_id '
               'WHERE r.token=?', (_th(token),)) if token else None
    if not row:
        return _page(handler, 404, '審核連結無效', '<h1>審核連結無效</h1><p class="err">這個審核連結不存在或已失效。'
                     '請到網站以管理員帳號登入，在「帳號審核」中處理。</p>')
    esc = html.escape
    details = ('<dl><dt>姓名</dt><dd>%s</dd><dt>Email</dt><dd>%s</dd><dt>申請身分</dt><dd>%s</dd>'
               '<dt>說明</dt><dd>%s</dd><dt>目前狀態</dt><dd>%s</dd></dl>') % (
        esc(row['name']), esc(row['email']), esc(ROLE_LABEL.get(row['requested_role'], row['requested_role'])),
        esc(row['note'] or '—'),
        esc(STATUS_LABEL.get(row['status'], row['status']) + ((' · ' + ROLE_LABEL.get(row['role'], '')) if row['status'] == 'approved' else '')))
    if done:
        msg = {'student': '已核准為學生。', 'teacher': '已核准為老師。', 'reject': '已設為不核准。'}.get(done, '已處理。')
        extra = '<p class="ok">%s</p>' % esc(msg)
        if notice:
            extra += '<p class="warn">%s</p>' % esc(notice)
        return _page(handler, 200, '審核完成', '<h1>審核完成</h1><div class="sub">MC-VHTA 帳號審核</div>' + details + extra)
    if row['used_at'] or time.time() - (row['r_created'] or 0) > REVIEW_SEC:
        used = '<p class="warn">此審核連結已使用或已過期（30 天）。如需變更，請以管理員帳號登入網站，在「帳號審核」中處理。</p>'
        return _page(handler, 200, '帳號審核', '<h1>帳號審核</h1><div class="sub">MC-VHTA</div>' + details + used)
    form = ('<form method="post" action="/api/auth/review" class="btns">'
            '<input type="hidden" name="t" value="%s">'
            '<button class="s" name="action" value="student">核准為學生 Approve as Student</button>'
            '<button class="t" name="action" value="teacher">核准為老師 Approve as Teacher</button>'
            '<button class="r" name="action" value="reject">不核准 Reject</button></form>') % esc(token)
    return _page(handler, 200, '帳號審核', '<h1>帳號審核</h1><div class="sub">MC-VHTA · 請確認申請人身分後選擇</div>' + details + form)


def handle(handler, method, path, query):
    """Serve /api/auth/*.  Always writes a response."""
    try:
        init()
        user = user_from_request(handler)
        if path == '/api/auth/me' and method == 'GET':
            if not user:
                return _reply(handler, 401, {'ok': False, 'code': 'login_required', 'error': 'Please sign in.'})
            return _reply(handler, 200, {'ok': True, 'user': user, 'admin_email': admin_email() if user['is_admin'] else ''})

        if path == '/api/auth/login' and method == 'POST':
            d = _body_json(handler)
            email = _clean(d.get('email'), 254).lower()
            password = str(d.get('password') or '')
            if _too_many_failures(email):
                return _reply(handler, 429, {'ok': False, 'code': 'too_many', 'error': '嘗試次數過多，請 15 分鐘後再試。'})
            row = user_by_email(email) if email else None
            if not row:
                _dummy_check(password)
            if not row or not check_password(password, row['pw']):
                _record_failure(email)
                return _reply(handler, 401, {'ok': False, 'code': 'bad_credentials', 'error': 'Email 或密碼不正確。'})
            if row['status'] != 'approved':
                msg = {'pending': '帳號審核中，管理員核准後即可登入。', 'rejected': '帳號申請未獲核准。',
                       'disabled': '此帳號已停用。'}.get(row['status'], '帳號尚未開通。')
                return _reply(handler, 403, {'ok': False, 'code': row['status'], 'error': msg})
            token = create_session(row['id'])
            return _reply(handler, 200, {'ok': True, 'user': public_user(row)}, _cookie(handler, token, SESSION_SEC))

        if path == '/api/auth/logout' and method == 'POST':
            token = _session_token(handler)
            if token:
                _exec('DELETE FROM sessions WHERE token=?', (_th(token),))
            return _reply(handler, 200, {'ok': True}, _cookie(handler, 'x' * 24, 0))

        if path == '/api/auth/register' and method == 'POST':
            d = _body_json(handler)
            email = _clean(d.get('email'), 254).lower()
            name = _clean(d.get('name'), 60)
            note = _clean(d.get('note'), 300)
            role = str(d.get('role') or '').strip().lower()
            password = str(d.get('password') or '')
            if not EMAIL_RE.match(email):
                return _reply(handler, 400, {'ok': False, 'code': 'email', 'error': '請輸入有效的 Email。'})
            if not name:
                return _reply(handler, 400, {'ok': False, 'code': 'name', 'error': '請輸入姓名。'})
            if role not in ('student', 'teacher'):
                return _reply(handler, 400, {'ok': False, 'code': 'role', 'error': '請選擇學生或老師。'})
            if len(password) < 8 or len(password) > 128:
                return _reply(handler, 400, {'ok': False, 'code': 'password', 'error': '密碼需為 8–128 個字元。'})
            existing = user_by_email(email)
            if existing and existing['status'] == 'pending':
                return _reply(handler, 200, {'ok': True, 'status': 'pending', 'already': True,
                                             'message': '這個 Email 的申請已送出，正在等待審核。'})
            if existing and existing['status'] in ('approved', 'disabled'):
                return _reply(handler, 409, {'ok': False, 'code': 'exists', 'error': '這個 Email 已有帳號，請直接登入。'})
            if not _registration_allowed():
                return _reply(handler, 429, {'ok': False, 'code': 'too_many', 'error': '申請太頻繁，請稍後再試。'})
            pw = hash_password(password)
            token = secrets.token_urlsafe(32)
            now = time.time()

            def work(c):
                if existing:
                    c.execute("UPDATE users SET name=?, pw=?, requested_role=?, role='', status='pending', note=?, "
                              "created_at=?, decided_at=NULL WHERE id=?", (name, pw, role, note, now, existing['id']))
                    uid = existing['id']
                else:
                    uid = c.execute('INSERT INTO users(email,name,pw,requested_role,status,note,created_at) '
                                    "VALUES(?,?,?,?,'pending',?,?)", (email, name, pw, role, note, now)).lastrowid
                c.execute('INSERT INTO reviews(token,user_id,created_at) VALUES(?,?,?)', (_th(token), uid, now))
                return c.execute('SELECT * FROM users WHERE id=?', (uid,)).fetchone()
            row = _run(work)
            notified = False
            try:
                subject, text, html_body = _review_email(row, token)
                send_email(admin_email(), subject, text, html_body, key='auth-review/' + _th(token)[:40])
                notified = True
                _exec('UPDATE users SET review_sent_at=? WHERE id=?', (time.time(), row['id']))
            except Exception as e:
                _log('review e-mail for %s failed: %s' % (email, e))
            _log('registration %s as %s (review e-mail %s)' % (email, role, 'sent' if notified else 'NOT sent'))
            return _reply(handler, 200, {'ok': True, 'status': 'pending', 'notified': notified,
                                         'message': '申請已送出，管理員核准後即可登入。'})

        if path == '/api/auth/review':
            if method == 'GET':
                token = (parse_qs(query or '').get('t') or [''])[0]
                return _review_page(handler, token)
            form = parse_qs(_read(handler).decode('utf-8', 'replace'))
            token = (form.get('t') or [''])[0]
            action = (form.get('action') or [''])[0]
            row = _one('SELECT r.user_id, r.used_at, r.created_at FROM reviews r WHERE r.token=?', (_th(token),)) if token else None
            if not row or action not in ('student', 'teacher', 'reject') or row['used_at'] or \
                    time.time() - (row['created_at'] or 0) > REVIEW_SEC:
                return _review_page(handler, token)
            updated = _decide(row['user_id'], action)
            sent, _why = _notify_applicant(updated, action)
            if sent:
                notice = '已寄信通知申請人。'
            elif action != 'reject':
                notice = '系統無法直接寄信給申請人（目前的寄件者只能寄到管理員信箱），請自行告知對方可以登入：%s/login/' % public_url()
            else:
                notice = ''
            _log('review decision %s for user %s' % (action, updated['email']))
            return _review_page(handler, token, done=action, notice=notice)

        if not user:
            return _reply(handler, 401, {'ok': False, 'code': 'login_required', 'error': 'Please sign in.'})

        if path == '/api/auth/act' and method == 'POST':
            # Administrator: use the site as a teacher (user_id), or return to the administrator (null).
            real = user_from_request(handler, act=False)
            if not real or not real['is_admin']:
                return _reply(handler, 403, {'ok': False, 'code': 'admin_only', 'error': '只有管理員可以使用。'})
            target_id = _body_json(handler).get('user_id')
            target = teacher_account(target_id) if target_id not in (None, '', 0) else None
            if target_id not in (None, '', 0) and (not target or target['id'] == real['id']):
                return _reply(handler, 400, {'ok': False, 'error': '請選擇已核准的老師。'})
            token = _th(_session_token(handler))
            _exec('UPDATE sessions SET acting_as=? WHERE token=?', (target['id'] if target else None, token))
            _log('administrator %s %s' % (real['email'], ('now acting as ' + target['email']) if target else 'returned to the administrator view'))
            return _reply(handler, 200, {'ok': True, 'user': user_from_request(handler)})

        if path == '/api/auth/password' and method == 'POST':
            if user.get('acting_admin'):
                return _reply(handler, 403, {'ok': False, 'code': 'acting', 'error': '請先回到管理員身分再修改密碼。'})
            d = _body_json(handler)
            row = user_by_id(user['id'])
            new = str(d.get('new_password') or '')
            if not check_password(str(d.get('old_password') or ''), row['pw']):
                return _reply(handler, 400, {'ok': False, 'code': 'bad_credentials', 'error': '目前密碼不正確。'})
            if len(new) < 8 or len(new) > 128:
                return _reply(handler, 400, {'ok': False, 'code': 'password', 'error': '新密碼需為 8–128 個字元。'})
            current = _th(_session_token(handler))
            _run(lambda c: (c.execute('UPDATE users SET pw=? WHERE id=?', (hash_password(new), user['id'])),
                            c.execute('DELETE FROM sessions WHERE user_id=? AND token<>?', (user['id'], current))))
            return _reply(handler, 200, {'ok': True})

        if path == '/api/auth/join' and method == 'POST':
            if user['role'] != 'student':
                return _reply(handler, 403, {'ok': False, 'code': 'student_only', 'error': '只有學生帳號需要輸入課程代碼。'})
            code = re.sub(r'[^A-Za-z0-9]', '', str(_body_json(handler).get('code') or '')).upper()[:12]
            row = _one('SELECT course_id FROM courses WHERE code=?', (code,)) if code else None
            if not row:
                return _reply(handler, 404, {'ok': False, 'code': 'bad_code', 'error': '找不到這個課程代碼，請向老師確認。'})
            _exec('INSERT OR IGNORE INTO enrollments(user_id,course_id,created_at) VALUES(?,?,?)',
                  (user['id'], row['course_id'], time.time()))
            return _reply(handler, 200, {'ok': True, 'course_id': row['course_id']})

        if path == '/api/auth/leave' and method == 'POST':
            cid = str(_body_json(handler).get('course_id') or '')
            _exec('DELETE FROM enrollments WHERE user_id=? AND course_id=?', (user['id'], cid))
            return _reply(handler, 200, {'ok': True})

        if path.startswith('/api/auth/admin/'):
            if not user['is_admin']:
                return _reply(handler, 403, {'ok': False, 'code': 'admin_only', 'error': '只有管理員可以使用。'})
            if path == '/api/auth/admin/users' and method == 'GET':
                rows = _all('SELECT u.*, (SELECT COUNT(*) FROM courses c WHERE c.owner_id=u.id) AS courses, '
                            '(SELECT COUNT(*) FROM enrollments e WHERE e.user_id=u.id) AS joined FROM users u '
                            "ORDER BY CASE status WHEN 'pending' THEN 0 ELSE 1 END, created_at DESC")
                users = [{'id': r['id'], 'email': r['email'], 'name': r['name'], 'requested_role': r['requested_role'],
                          'role': r['role'], 'status': r['status'], 'note': r['note'], 'created_at': r['created_at'],
                          'last_login': r['last_login'], 'courses': r['courses'], 'joined': r['joined'],
                          'is_admin': r['email'] == admin_email()} for r in rows]
                return _reply(handler, 200, {'ok': True, 'users': users})
            if path == '/api/auth/admin/decide' and method == 'POST':
                d = _body_json(handler)
                action = str(d.get('action') or '')
                try:
                    target = user_by_id(int(d.get('user_id')))
                except Exception:
                    target = None
                if not target or action not in ('student', 'teacher', 'reject', 'disable'):
                    return _reply(handler, 400, {'ok': False, 'error': '無效的操作。'})
                if target['id'] == user['id'] and action in ('reject', 'disable', 'student'):
                    return _reply(handler, 400, {'ok': False, 'error': '不能停用或降級自己的管理員帳號。'})
                updated = _decide(target['id'], action)
                sent, _why = _notify_applicant(updated, action) if target['status'] == 'pending' else (False, '')
                return _reply(handler, 200, {'ok': True, 'user': public_user(updated), 'notified': sent})
            if path == '/api/auth/admin/course-owner' and method == 'POST':
                d = _body_json(handler)
                cid = str(d.get('course_id') or '').strip()[:200]
                target = teacher_account(d.get('user_id'))
                if not cid or not re.match(r'^[A-Za-z0-9_.-]+$', cid):
                    return _reply(handler, 400, {'ok': False, 'error': '無效的課程。'})
                if not target:
                    return _reply(handler, 400, {'ok': False, 'error': '請選擇已核准的老師。'})
                set_course_owner(cid, target['id'])
                _log('course %s assigned to %s' % (cid, target['email']))
                return _reply(handler, 200, {'ok': True, 'course_id': cid, 'owner_id': target['id'], 'owner_name': target['name']})
            if path == '/api/auth/admin/reset-password' and method == 'POST':
                try:
                    target = user_by_id(int(_body_json(handler).get('user_id')))
                except Exception:
                    target = None
                if not target:
                    return _reply(handler, 400, {'ok': False, 'error': '找不到使用者。'})
                temp = ''.join(secrets.choice('abcdefghjkmnpqrstuvwxyz23456789') for _ in range(10))
                _run(lambda c: (c.execute('UPDATE users SET pw=? WHERE id=?', (hash_password(temp), target['id'])),
                                c.execute('DELETE FROM sessions WHERE user_id=?', (target['id'],))))
                return _reply(handler, 200, {'ok': True, 'temporary_password': temp})
        return _reply(handler, 404, {'ok': False, 'error': 'Unknown account endpoint.'})
    except ValueError as e:
        return _reply(handler, 400, {'ok': False, 'error': str(e)})
    except Exception as e:
        _log('error on %s: %r' % (path, e))
        return _reply(handler, 500, {'ok': False, 'error': 'Account service error.', 'detail': str(e)[:200]})
