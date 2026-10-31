import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
DB_PATH = Path(os.getenv("STUDENT_SUPPORT_DB", str(DATA_DIR / "student_support.db")))

def _connect():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def _now():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")

def init_db():
    with _connect() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS students (
            student_id TEXT PRIMARY KEY, name TEXT NOT NULL, programme TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS fees (
            student_id TEXT PRIMARY KEY, total_fee REAL NOT NULL, amount_paid REAL NOT NULL,
            status TEXT NOT NULL, FOREIGN KEY(student_id) REFERENCES students(student_id)
        );
        CREATE TABLE IF NOT EXISTS scholarships (
            student_id TEXT PRIMARY KEY, scholarship TEXT NOT NULL, status TEXT NOT NULL,
            updated_at TEXT NOT NULL, FOREIGN KEY(student_id) REFERENCES students(student_id)
        );
        CREATE TABLE IF NOT EXISTS memories (
            id INTEGER PRIMARY KEY AUTOINCREMENT, student_id TEXT NOT NULL,
            content TEXT NOT NULL, created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS support_requests (
            request_id TEXT PRIMARY KEY, student_id TEXT NOT NULL, issue TEXT NOT NULL,
            status TEXT NOT NULL, created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS traces (
            run_id TEXT PRIMARY KEY, student_id TEXT NOT NULL, question TEXT NOT NULL,
            action TEXT NOT NULL, answer TEXT NOT NULL, trace_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS approvals (
            approval_id TEXT PRIMARY KEY, student_id TEXT NOT NULL, action_type TEXT NOT NULL,
            requested_value TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL,
            decided_at TEXT, decision_note TEXT
        );
        CREATE TABLE IF NOT EXISTS staff (
            staff_id TEXT PRIMARY KEY, name TEXT NOT NULL, role TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS audit_logs (
            audit_id INTEGER PRIMARY KEY AUTOINCREMENT, student_id TEXT NOT NULL,
            action TEXT NOT NULL, target_student_id TEXT, decision TEXT NOT NULL,
            approval_id TEXT, result TEXT NOT NULL, details TEXT, created_at TEXT NOT NULL
        );
        """)
        _add_missing_columns(conn)
        if conn.execute("SELECT COUNT(*) FROM staff").fetchone()[0] == 0:
            conn.execute("INSERT INTO staff VALUES ('REG001', 'Demo Registrar', 'registrar')")
        existing = conn.execute("SELECT COUNT(*) FROM students").fetchone()[0]
        if existing == 0:
            students = [
                ("STU1001", "Aarav Shah", "PGDM Big Data Analytics"),
                ("STU1002", "Mira Patel", "MBA Finance"),
                ("STU1003", "Kabir Mehta", "B.Tech Computer Engineering"),
            ]
            conn.executemany("INSERT INTO students VALUES (?, ?, ?)", students)
            conn.executemany("INSERT INTO fees VALUES (?, ?, ?, ?)", [
                ("STU1001", 180000, 120000, "Partially Paid"),
                ("STU1002", 160000, 160000, "Paid"),
                ("STU1003", 140000, 90000, "Partially Paid"),
            ])
            conn.executemany("INSERT INTO scholarships VALUES (?, ?, ?, ?)", [
                ("STU1001", "Merit Scholarship", "Under Review", "2026-10-15"),
                ("STU1002", "Need-Based Scholarship", "Approved", "2026-10-10"),
                ("STU1003", "Merit Scholarship", "Documents Required", "2026-10-18"),
            ])


PROGRAMME_CATALOG = [
    "PGDM Big Data Analytics",
    "PGDM Artificial Intelligence & Data Science",
    "MBA Finance",
    "MBA Marketing",
    "B.Tech Computer Engineering",
]

def _add_missing_columns(conn):
    """Upgrade an older database in place (keeps existing data)."""
    def cols(table):
        return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
    for col, ddl in [("current_value", "TEXT"), ("requested_by", "TEXT"),
                     ("decided_by", "TEXT"), ("executed", "INTEGER DEFAULT 0")]:
        if col not in cols("approvals"):
            conn.execute(f"ALTER TABLE approvals ADD COLUMN {col} {ddl}")
    for col, ddl in [("actor_role", "TEXT"), ("approval_required", "INTEGER DEFAULT 0"),
                     ("approval_status", "TEXT"), ("executed", "INTEGER DEFAULT 0")]:
        if col not in cols("audit_logs"):
            conn.execute(f"ALTER TABLE audit_logs ADD COLUMN {col} {ddl}")

def get_staff(staff_id):
    with _connect() as conn:
        row = conn.execute("SELECT * FROM staff WHERE staff_id = ?", (staff_id,)).fetchone()
        return dict(row) if row else None

def _is_approver(actor_id):
    staff = get_staff(actor_id) if actor_id else None
    return bool(staff and staff["role"] == "registrar")

def list_demo_students():
    with _connect() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM students ORDER BY student_id")]

def get_student(student_id):
    with _connect() as conn:
        row = conn.execute("SELECT * FROM students WHERE student_id = ?", (student_id,)).fetchone()
        return dict(row) if row else None

def _authorize_owner(actor_student_id, target_student_id):
    if not actor_student_id or actor_student_id != target_student_id:
        log_audit(actor_student_id or "UNKNOWN", "record_access", target_student_id,
                  "denied", None, "blocked", "Cross-student access attempt")
        raise PermissionError("Access denied: students may access only their own private records.")

def log_audit(student_id, action, target_student_id=None, decision="allowed", approval_id=None,
              result="success", details="", actor_role="student", approval_required=0,
              approval_status=None, executed=0):
    """One row per important step. `student_id` is the ACTOR (who did it)."""
    with _connect() as conn:
        conn.execute("""INSERT INTO audit_logs
            (student_id, action, target_student_id, decision, approval_id, result, details, created_at,
             actor_role, approval_required, approval_status, executed)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (student_id, action, target_student_id, decision, approval_id, result, details[:1000], _now(),
             actor_role, int(approval_required), approval_status, int(executed)))

def get_audit_logs(student_id=None, limit=100):
    with _connect() as conn:
        if student_id:
            rows = conn.execute("SELECT * FROM audit_logs WHERE student_id = ? ORDER BY audit_id DESC LIMIT ?", (student_id, limit)).fetchall()
        else:
            rows = conn.execute("SELECT * FROM audit_logs ORDER BY audit_id DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]

def get_fee_status(student_id, target_student_id=None):
    target_student_id = target_student_id or student_id
    _authorize_owner(student_id, target_student_id)
    with _connect() as conn:
        row = conn.execute("SELECT * FROM fees WHERE student_id = ?", (student_id,)).fetchone()
        if not row:
            raise ValueError("No fee record is available for this demo student.")
        record = dict(row)
        record["balance"] = max(0, record["total_fee"] - record["amount_paid"])
        return record

def get_scholarship_status(student_id, target_student_id=None):
    target_student_id = target_student_id or student_id
    _authorize_owner(student_id, target_student_id)
    with _connect() as conn:
        row = conn.execute("SELECT * FROM scholarships WHERE student_id = ?", (student_id,)).fetchone()
        if not row:
            raise ValueError("No scholarship record is available for this demo student.")
        return dict(row)

def create_support_request(student_id, issue, target_student_id=None):
    target_student_id = target_student_id or student_id
    _authorize_owner(student_id, target_student_id)
    request_id = "REQ-" + uuid.uuid4().hex[:8].upper()
    with _connect() as conn:
        conn.execute("INSERT INTO support_requests VALUES (?, ?, ?, ?, ?)",
                     (request_id, student_id, issue[:500], "Open", _now()))
    return request_id

def save_memory(student_id, content):
    from agent import looks_sensitive
    if looks_sensitive(content):
        return False
    with _connect() as conn:
        # Avoid duplicate memories and keep memory small and useful.
        found = conn.execute("SELECT id FROM memories WHERE student_id = ? AND lower(content) = lower(?)",
                             (student_id, content)).fetchone()
        if found:
            return True
        conn.execute("INSERT INTO memories(student_id, content, created_at) VALUES (?, ?, ?)",
                     (student_id, content[:300], _now()))
    return True

def search_memories(student_id, query, limit=3):
    terms = [t.lower() for t in query.split() if len(t) > 3]
    with _connect() as conn:
        rows = conn.execute("SELECT content, created_at FROM memories WHERE student_id = ? ORDER BY id DESC LIMIT 30",
                            (student_id,)).fetchall()
    results = []
    for row in rows:
        content = row["content"]
        if any(term in content.lower() for term in terms) or any(k in query.lower() for k in ["remember", "programme", "program", "preference", "know about me"]):
            results.append({"content": content, "created_at": row["created_at"]})
        if len(results) >= limit:
            break
    return results

def get_memories_for_student(student_id):
    with _connect() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT content, created_at FROM memories WHERE student_id = ? ORDER BY id DESC", (student_id,)
        )]

def log_trace(run_id, student_id, question, action, answer, trace):
    with _connect() as conn:
        conn.execute("INSERT OR REPLACE INTO traces VALUES (?, ?, ?, ?, ?, ?, ?)",
                     (run_id, student_id, question[:1000], action, answer[:3000],
                      json.dumps(trace, ensure_ascii=False), _now()))

def get_recent_traces(limit=30):
    with _connect() as conn:
        rows = conn.execute("SELECT run_id, student_id, question, action, trace_json, created_at FROM traces ORDER BY rowid DESC LIMIT ?",
                            (limit,)).fetchall()
    result = []
    for r in rows:
        d = dict(r)
        try:
            d["trace"] = json.loads(d.pop("trace_json"))
        except Exception:
            d["trace"] = {}
        d["question"] = d["question"][:100]
        result.append(d)
    return result

def get_recent_support_requests(student_id=None):
    with _connect() as conn:
        if student_id:
            rows = conn.execute("SELECT request_id, issue, status, created_at FROM support_requests WHERE student_id = ? ORDER BY created_at DESC LIMIT 20",
                                (student_id,)).fetchall()
        else:
            rows = conn.execute("SELECT request_id, student_id, issue, status, created_at FROM support_requests ORDER BY created_at DESC LIMIT 50").fetchall()
    return [dict(r) for r in rows]


def request_programme_change(student_id, requested_programme):
    """Create a PENDING approval. The programme is NOT changed here."""
    with _connect() as conn:
        student = conn.execute("SELECT * FROM students WHERE student_id = ?", (student_id,)).fetchone()
        if not student:
            raise ValueError("Student record was not found.")
        if requested_programme == student["programme"]:
            raise ValueError("You are already enrolled in that programme.")
        dup = conn.execute("SELECT approval_id FROM approvals WHERE student_id = ? AND action_type = 'programme_change' "
                           "AND status = 'Pending'", (student_id,)).fetchone()
        if dup:
            raise ValueError(f"You already have a pending programme-change request ({dup['approval_id']}).")
        approval_id = "APR-" + uuid.uuid4().hex[:8].upper()
        conn.execute("""INSERT INTO approvals (approval_id, student_id, action_type, requested_value, status,
                        created_at, decided_at, decision_note, current_value, requested_by, decided_by, executed)
                        VALUES (?, ?, 'programme_change', ?, 'Pending', ?, NULL, NULL, ?, ?, NULL, 0)""",
                     (approval_id, student_id, requested_programme[:120], _now(), student["programme"], student_id))
    log_audit(student_id, "programme_change_requested", student_id, "allowed", approval_id, "pending_approval",
              f"From '{student['programme']}' to '{requested_programme[:120]}'. Programme NOT changed yet.",
              "student", 1, "Pending", 0)
    return approval_id

def decide_approval(actor_id, approval_id, approve, note=""):
    """Only an authorised approver (staff role 'registrar') may decide. Students are blocked, even for their own request."""
    with _connect() as conn:
        row = conn.execute("SELECT * FROM approvals WHERE approval_id = ?", (approval_id,)).fetchone()
    if not row:
        raise ValueError("Approval request was not found.")
    if not _is_approver(actor_id):
        log_audit(actor_id or "UNKNOWN", "approval_decision", row["student_id"], "denied", approval_id,
                  "blocked", "Actor is not an authorised approver", "student", 1, row["status"], 0)
        raise PermissionError("Access denied: only an authorised approver can approve or reject this request.")
    status = "Approved" if approve else "Rejected"
    with _connect() as conn:
        # `AND status = 'Pending'` makes the decision atomic: it can only be made once.
        changed = conn.execute("UPDATE approvals SET status = ?, decided_at = ?, decided_by = ?, decision_note = ?, executed = ? "
                               "WHERE approval_id = ? AND status = 'Pending'",
                               (status, _now(), actor_id, note[:200] or status, 1 if approve else 0, approval_id)).rowcount
        if not changed:
            raise ValueError(f"This request was already decided ({row['status']}).")
        if approve and row["action_type"] == "programme_change":
            conn.execute("UPDATE students SET programme = ? WHERE student_id = ?",
                         (row["requested_value"], row["student_id"]))
    log_audit(actor_id, "approval_decision", row["student_id"], "allowed", approval_id, status.lower(),
              f"{status} by {actor_id}. {row['action_type']}: {row['current_value']} -> {row['requested_value']}",
              "registrar", 1, status, 0)
    if approve:
        log_audit(actor_id, "programme_update", row["student_id"], "allowed", approval_id, "executed",
                  f"Programme updated to '{row['requested_value']}'", "registrar", 1, status, 1)
    else:
        log_audit(actor_id, "programme_update", row["student_id"], "allowed", approval_id, "update_blocked",
                  "Request rejected; programme left unchanged", "registrar", 1, status, 0)
    return status

def get_approvals(student_id=None, status=None):
    sql, args = "SELECT * FROM approvals WHERE 1=1", []
    if student_id:
        sql += " AND student_id = ?"; args.append(student_id)
    if status:
        sql += " AND status = ?"; args.append(status)
    with _connect() as conn:
        rows = conn.execute(sql + " ORDER BY rowid DESC LIMIT 50", args).fetchall()
    return [dict(r) for r in rows]

def get_pending_approvals_for_approver(actor_id):
    """Pending requests across all students, visible ONLY to an authorised approver."""
    if not _is_approver(actor_id):
        raise PermissionError("Access denied: only an authorised approver can view all pending requests.")
    return get_approvals(status="Pending")
