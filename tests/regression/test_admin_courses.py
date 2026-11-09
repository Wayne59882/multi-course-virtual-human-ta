"""The administrator manages every teacher's courses; teachers still see only their own.
Exercises the account gate directly with a throw-away account database (no server)."""
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

    # The page sends the chosen owner and shows it in the course list.
    controller = (ROOT / 'app/web/shared/js/course/course-controller.js').read_text(encoding='utf-8')
    roles = (ROOT / 'app/web/shared/js/auth/role-access.js').read_text(encoding='utf-8')
    assert "body.owner_id=Number(owner.value)" in controller and 'c.owner_name&&!c.owned' in controller
    assert '/api/auth/admin/course-owner' in roles and 'vqBuildOwner' in roles
print('[OK] administrator: sees, deletes, retries and reassigns every course, builds for a teacher; teachers and students stay isolated')
