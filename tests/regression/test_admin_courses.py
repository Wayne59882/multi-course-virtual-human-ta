"""The administrator manages every teacher's courses; teachers still see only their own.
Exercises the account gate directly with a throw-away account database (no server)."""
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'app'))
os.environ['VQ_AUTH_ADMIN_EMAIL'] = 'admin@example.test'
import access_control as auth  # noqa: E402

JSON = 'application/json'


def call(user, seg, body=None, query=''):
    data = json.dumps(body).encode() if body is not None else b''
    denial, ctx = auth.authorize(user, '/api/reference/' + seg, query, data, JSON if body is not None else '')
    return denial, ctx


def respond(user, ctx, payload):
    return json.loads(auth.after_response(user, ctx, 200, json.dumps(payload).encode(), JSON))


with tempfile.TemporaryDirectory() as temporary:
    auth.DB_PATH = Path(temporary) / 'accounts.sqlite3'
    auth.init()
    for uid, email, name, role in [(1, 'admin@example.test', 'Admin', 'teacher'), (2, 'advisor@example.test', 'Advisor', 'teacher'),
                                   (3, 'other@example.test', 'Other Teacher', 'teacher'), (4, 'student@example.test', 'Student', 'student'),
                                   (5, 'waiting@example.test', 'Waiting', '')]:
        auth._exec('INSERT INTO users(id,email,name,pw,requested_role,role,status,created_at) VALUES(?,?,?,?,?,?,?,?)',
                   (uid, email, name, 'hash', role or 'teacher', role, 'approved' if role else 'pending', time.time()))
    admin, advisor, other, student = (auth.public_user(auth.user_by_id(i)) for i in (1, 2, 3, 4))
    assert admin['is_admin'] and not advisor['is_admin']
    auth.record_course('genomic-1', 2)
    auth.record_course('other-1', 3)
    auth._exec('INSERT INTO stages(stage_id,owner_id,created_at) VALUES(?,?,?)', ('advisor-stage', 2, time.time()))
    auth._exec('INSERT INTO jobs(job_id,owner_id,course_id,created_at) VALUES(?,?,?,?)', ('advisor-job', 2, 'genomic-1', time.time()))

    # The administrator can open, delete, retry and watch the advisor's course, upload and build.
    for seg, body, query in [('course-delete', {'course_id': 'genomic-1'}, ''), ('course-retry-preparation', {'course_id': 'genomic-1'}, ''),
                             ('course-status', None, 'id=genomic-1'), ('course-stage-status', None, 'stage_id=advisor-stage'),
                             ('course-job-status', None, 'id=advisor-job'), ('course-build-cancel', {'id': 'advisor-job'}, '')]:
        denial, _ = call(admin, seg, body, query)
        assert denial is None, (seg, denial)
        # Another teacher and a student are still refused.
        for outsider in (other, student):
            denial, _ = call(outsider, seg, body, query)
            assert denial and denial['status'] == 403, (seg, outsider['name'], denial)
    assert call(advisor, 'course-delete', {'course_id': 'genomic-1'})[0] is None

    # Course list: the administrator sees every course with its owner; a teacher only their own.
    listing = {'ok': True, 'courses': [{'course_id': 'genomic-1', 'course_name': 'Genomic study'},
                                       {'course_id': 'other-1', 'course_name': 'Other'},
                                       {'course_id': 'legacy-1', 'course_name': 'Before accounts'}]}
    shown = respond(admin, {'seg': 'course-list'}, listing)['courses']
    assert [c['course_id'] for c in shown] == ['genomic-1', 'other-1', 'legacy-1'], shown
    genomic = shown[0]
    assert genomic['owner_name'] == 'Advisor' and genomic['owner_email'] == 'advisor@example.test' and not genomic['owned']
    assert genomic['access_code'] and shown[2]['owned'] and auth.course_owner('legacy-1') == 1
    assert 'hash' not in json.dumps(shown)
    mine = respond(advisor, {'seg': 'course-list'}, listing)['courses']
    assert [c['course_id'] for c in mine] == ['genomic-1'] and 'owner_email' not in mine[0]

    # The administrator builds for the advisor: the job and the course belong to the advisor.
    denial, ctx = call(admin, 'course-build', {'stage_id': 'admin-stage', 'owner_id': 2})
    assert denial is None and ctx['owner_id'] == 2
    respond(admin, ctx, {'ok': True, 'job': {'id': 'build-for-advisor', 'status': 'running'}})
    later = call(admin, 'course-job-status', None, 'id=build-for-advisor')[1]
    respond(admin, later, {'id': 'build-for-advisor', 'status': 'done', 'result': {'course_id': 'genomic-2'}})
    assert auth.course_owner('genomic-2') == 2
    assert [c['course_id'] for c in respond(advisor, {'seg': 'course-list'}, {'ok': True, 'courses': [{'course_id': 'genomic-2'}]})['courses']] == ['genomic-2']
    # Only approved teachers can be chosen, and only the administrator can choose.
    for bad in (4, 5, 99, 'x'):
        denial, _ = call(admin, 'course-build', {'stage_id': 'admin-stage-%s' % bad, 'owner_id': bad})
        assert denial and denial['status'] == 400, (bad, denial)
    denial, ctx = call(other, 'course-build', {'stage_id': 'other-stage', 'owner_id': 2})
    assert denial is None and 'owner_id' not in ctx
    respond(other, ctx, {'ok': True, 'job': {'id': 'other-job', 'course_id': 'other-2'}})
    assert auth.course_owner('other-2') == 3

    # Moving a saved course to another teacher.
    auth.set_course_owner('other-1', 2)
    assert auth.course_owner('other-1') == 2 and auth.is_owner(advisor, 'other-1') and not auth.is_owner(other, 'other-1')
    assert auth.teacher_account(2) and auth.teacher_account(1) and not auth.teacher_account(4) and not auth.teacher_account(5)

    # Deleting removes the course record, whoever deletes it.
    respond(admin, {'seg': 'course-delete', 'course_id': 'genomic-1'}, {'ok': True})
    assert auth.course_owner('genomic-1') is None

    # Switching teacher: the administrator's session uses the site exactly as that teacher.
    class Request:
        def __init__(self, token, body=None):
            data = json.dumps(body).encode() if body is not None else b''
            self.headers = {'Cookie': '%s=%s' % (auth.COOKIE, token), 'Content-Length': str(len(data))}
            self.rfile, self.wfile, self.status = io.BytesIO(data), io.BytesIO(), None

        def send_response(self, status):
            self.status = status

        def send_header(self, *header):
            pass

        def end_headers(self):
            pass

        def reply(self):
            return json.loads(self.wfile.getvalue().decode('utf-8'))

    def api(token, path, body=None, method='POST'):
        request = Request(token, body)
        auth.handle(request, method, path, '')
        return request.status, request.reply()

    admin_token, advisor_token = auth.create_session(1), auth.create_session(2)
    assert auth.user_from_request(Request(admin_token))['is_admin']
    status, reply = api(admin_token, '/api/auth/act', {'user_id': 2})
    assert status == 200 and reply['user']['id'] == 2 and reply['user']['acting_admin']['id'] == 1, reply
    acting = auth.user_from_request(Request(admin_token))
    assert acting['id'] == 2 and acting['role'] == 'teacher' and not acting['is_admin']
    assert auth.user_from_request(Request(admin_token), act=False)['id'] == 1
    # Exactly the teacher's view: only the teacher's courses, and no administrator powers.
    shown = respond(acting, {'seg': 'course-list'}, listing)['courses']
    assert all(auth.course_owner(c['course_id']) == 2 for c in shown) and 'owner_email' not in json.dumps(shown)
    assert call(acting, 'course-delete', {'course_id': 'other-2'})[0]['status'] == 403
    assert api(admin_token, '/api/auth/admin/users', method='GET')[0] == 403
    # Uploads and builds made in the teacher's view belong to the teacher.
    denial, ctx = call(acting, 'course-build', {'stage_id': 'acting-stage'})
    assert denial is None
    respond(acting, ctx, {'ok': True, 'job': {'id': 'acting-job', 'course_id': 'genomic-3'}})
    assert auth.course_owner('genomic-3') == 2
    # The teacher's password cannot be changed from the administrator's session.
    assert api(admin_token, '/api/auth/password', {'old_password': 'x', 'new_password': 'y' * 10})[0] == 403
    # The teacher's own session is unaffected; only the administrator can switch.
    assert auth.user_from_request(Request(advisor_token)).get('acting_admin') is None
    assert api(advisor_token, '/api/auth/act', {'user_id': 3})[0] == 403
    # Students, accounts not yet approved and the administrator are not choices.
    for bad in (4, 5, 1):
        assert api(admin_token, '/api/auth/act', {'user_id': bad})[0] == 400, bad
    # While viewing as a teacher, the administrator's switcher still lists everyone to switch to.
    status, reply = api(admin_token, '/api/auth/act', method='GET')
    assert status == 200 and reply['acting_id'] == 2 and reply['admin']['id'] == 1
    assert [t['id'] for t in reply['teachers']] == [2, 3], reply
    assert api(advisor_token, '/api/auth/act', method='GET')[0] == 403
    # Builds in progress: the teacher (and the administrator viewing as them) sees their own;
    # the administrator sees everyone's with the owner; others see none.
    auth._exec('INSERT OR IGNORE INTO jobs(job_id,owner_id,course_id,created_at) VALUES(?,?,?,?)', ('other-build', 3, 'x', time.time()))
    running = {'ok': True, 'builds': [{'id': 'acting-job', 'course_name': 'Genomic study', 'status': 'running'},
                                      {'id': 'other-build', 'course_name': 'Other', 'status': 'queued'},
                                      {'id': 'unknown-build', 'course_name': 'Legacy', 'status': 'running'}]}
    assert [b['id'] for b in respond(acting, {'seg': 'course-builds'}, running)['builds']] == ['acting-job']
    # Work the administrator did in the teacher's view stays out of the teacher's own view.
    auth._exec('INSERT OR IGNORE INTO jobs(job_id,owner_id,course_id,created_at,actor_id) VALUES(?,?,?,?,?)', ('advisor-own', 2, 'y', time.time(), 2))
    running['builds'].append({'id': 'advisor-own', 'course_name': 'His own upload', 'status': 'running'})
    assert [b['id'] for b in respond(advisor, {'seg': 'course-builds'}, running)['builds']] == ['advisor-own']
    assert [b['id'] for b in respond(acting, {'seg': 'course-builds'}, running)['builds']] == ['acting-job', 'advisor-own']
    assert call(advisor, 'course-job-status', None, 'id=acting-job')[0]['status'] == 403
    assert call(advisor, 'course-build-cancel', {'id': 'acting-job'})[0]['status'] == 403
    assert call(acting, 'course-job-status', None, 'id=acting-job')[0] is None
    assert call(advisor, 'course-job-status', None, 'id=advisor-own')[0] is None
    assert [b['id'] for b in respond(other, {'seg': 'course-builds'}, running)['builds']] == ['other-build']
    seen = respond(admin, {'seg': 'course-builds'}, running)['builds']
    assert [b['id'] for b in seen] == ['acting-job', 'other-build', 'unknown-build', 'advisor-own'] and seen[0]['owner_name'] == 'Advisor'
    assert call(student, 'course-builds')[0]['status'] == 403
    status, reply = api(admin_token, '/api/auth/act', {'user_id': None})
    assert status == 200 and reply['user']['id'] == 1 and reply['user']['is_admin']
    assert auth.user_from_request(Request(admin_token))['is_admin']

    # The page: owners in the course list, owner transfer, and the teacher switcher.
    controller = (ROOT / 'app/web/shared/js/course/course-controller.js').read_text(encoding='utf-8')
    roles = (ROOT / 'app/web/shared/js/auth/role-access.js').read_text(encoding='utf-8')
    assert 'c.owner_name&&!c.owned' in controller
    assert '/api/auth/admin/course-owner' in roles and "'/api/auth/act'" in roles and 'fillAccounts' in roles
    # Opening another teacher's course switches to that teacher's view with the course open,
    # and the course's preparation has its own card (not the upload meters).
    assert 'window.VQ_beforeCourseSwitch=cid=>' in roles and 'VQ_beforeCourseSwitch(cid))return;' in controller
    assert 'function renderPrep(' in controller and 'v241MeterLectures' not in controller and 'id="v241Prep"' in controller
print('[OK] administrator: sees, deletes, retries and reassigns every course, builds for a teacher, switches to a teacher\'s exact view and back; teachers and students stay isolated')
