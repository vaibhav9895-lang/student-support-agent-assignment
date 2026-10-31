from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent import StudentSupportAgent, looks_sensitive
from database import init_db, get_fee_status, get_scholarship_status, save_memory, get_memories_for_student
from retrieval import retrieve_policy_chunks

def setup_module():
    init_db()

def test_fee_tool_returns_record():
    result = StudentSupportAgent("STU1001").answer("What is my current fee status?")
    assert result["trace"]["selected_action"] == "fee_tool"
    assert "Partially Paid" in result["answer"]
    assert any(c["tool"] == "get_fee_status" for c in result["trace"]["tool_calls"])

def test_scholarship_tool_returns_record():
    result = StudentSupportAgent("STU1001").answer("What is my scholarship status?")
    assert "Under Review" in result["answer"]

def test_policy_retrieval_returns_evidence():
    chunks = retrieve_policy_chunks("What is the late fee policy?")
    assert chunks
    assert any("late fee" in c["text"].lower() for c in chunks)

def test_sensitive_input_not_saved():
    assert looks_sensitive("My OTP is 123456")
    before = len(get_memories_for_student("STU1001"))
    result = StudentSupportAgent("STU1001").answer("My OTP is 123456")
    after = len(get_memories_for_student("STU1001"))
    assert "not saved" in result["answer"].lower() or "not save" in result["answer"].lower()
    assert before == after

def test_non_sensitive_memory_persists():
    save_memory("STU1001", "I prefer concise explanations")
    result = StudentSupportAgent("STU1001").answer("What do you remember about me?")
    assert "concise explanations" in result["answer"]

def test_missing_student_record_fails_clearly():
    try:
        get_fee_status("STU9999")
    except ValueError as exc:
        assert "No fee record" in str(exc)
    else:
        raise AssertionError("Expected missing record to raise ValueError")

def test_cross_student_record_access_is_blocked():
    from database import get_fee_status
    try:
        get_fee_status("STU1001", "STU1002")
    except PermissionError as exc:
        assert "only their own" in str(exc)
    else:
        raise AssertionError("Expected cross-student access to be blocked")

PROMPT = "I want to change my programme from PGDM Big Data Analytics to MBA Finance. Please update my student record."

def _reset():
    from database import _connect
    with _connect() as c:
        c.execute("UPDATE students SET programme='PGDM Big Data Analytics' WHERE student_id='STU1001'")
        c.execute("DELETE FROM approvals")

def test_exact_user_prompt_creates_pending_request_and_programme_unchanged():
    from database import get_student, get_approvals
    _reset()
    r = StudentSupportAgent("STU1001").answer(PROMPT)
    assert r["trace"]["selected_action"] == "programme_change_request"
    assert r["trace"]["approval"]["status"] == "pending"
    assert "Pending" in r["answer"]
    assert get_student("STU1001")["programme"] == "PGDM Big Data Analytics"
    ap = get_approvals("STU1001")[0]
    assert ap["status"] == "Pending" and ap["requested_value"] == "MBA Finance"
    assert ap["current_value"] == "PGDM Big Data Analytics" and ap["executed"] == 0

def test_student_cannot_approve_own_request():
    from database import get_approvals, decide_approval, get_student
    _reset(); StudentSupportAgent("STU1001").answer(PROMPT)
    ap = get_approvals("STU1001")[0]
    try:
        decide_approval("STU1001", ap["approval_id"], True)
    except PermissionError:
        pass
    else:
        raise AssertionError("student approved own request")
    assert get_student("STU1001")["programme"] == "PGDM Big Data Analytics"

def test_registrar_approve_updates_programme_and_audits_everything():
    from database import get_approvals, decide_approval, get_student, get_audit_logs
    _reset(); StudentSupportAgent("STU1001").answer(PROMPT)
    ap = get_approvals("STU1001")[0]
    assert decide_approval("REG001", ap["approval_id"], True) == "Approved"
    assert get_student("STU1001")["programme"] == "MBA Finance"
    acts = {(l["action"], l["result"]) for l in get_audit_logs() if l["approval_id"] == ap["approval_id"]}
    assert ("programme_change_requested", "pending_approval") in acts
    assert ("approval_decision", "approved") in acts
    assert ("programme_update", "executed") in acts
    # cannot be decided twice
    try:
        decide_approval("REG001", ap["approval_id"], False)
    except ValueError:
        pass
    else:
        raise AssertionError("double decision allowed")
    _reset()

def test_registrar_reject_leaves_programme_unchanged():
    from database import get_approvals, decide_approval, get_student, get_audit_logs
    _reset(); StudentSupportAgent("STU1001").answer(PROMPT)
    ap = get_approvals("STU1001")[0]
    assert decide_approval("REG001", ap["approval_id"], False) == "Rejected"
    assert get_student("STU1001")["programme"] == "PGDM Big Data Analytics"
    assert any(l["action"] == "programme_update" and l["result"] == "update_blocked"
               and l["approval_id"] == ap["approval_id"] for l in get_audit_logs())

def test_other_wordings_and_unknown_programme():
    _reset()
    r = StudentSupportAgent("STU1001").answer("Please switch me to MBA Marketing")
    assert r["trace"]["selected_action"] == "programme_change_request"
    _reset()
    r = StudentSupportAgent("STU1001").answer("I want to change my programme to Astrology")
    assert "couldn't tell which programme" in r["answer"] and not r["trace"].get("approval")

def test_cannot_request_programme_change_for_another_student():
    from database import get_student
    r = StudentSupportAgent("STU1001").answer("Change the programme of STU1002 to MBA Marketing")
    assert r["trace"]["selected_action"] == "authorization_denied"
    assert get_student("STU1002")["programme"] == "MBA Finance"

def test_programme_change_reply_is_not_rewritten_by_llm(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "test-placeholder")
    agent = StudentSupportAgent("STU1001")
    def boom(*a, **k): raise AssertionError("LLM must not touch security decisions")
    monkeypatch.setattr(agent, "_choose_action_with_llm", boom)
    monkeypatch.setattr(agent, "_llm_rephrase", boom)
    _reset()
    r = agent.answer(PROMPT)
    assert "Pending" in r["answer"]
    _reset()

def test_policy_question_about_course_change_is_not_a_request():
    r = StudentSupportAgent("STU1001").answer("Can I change my course after the deadline?")
    assert r["trace"]["selected_action"] == "policy_retrieval"

def test_sensitive_input_is_blocked_before_llm_routing(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "test-placeholder")
    agent = StudentSupportAgent("STU1001")
    def fail_if_called(_question):
        raise AssertionError("Sensitive input must not be sent to the LLM")
    monkeypatch.setattr(agent, "_choose_action_with_llm", fail_if_called)
    result = agent.answer("My OTP is 123456")
    assert result["trace"]["selected_action"] == "sensitive_input_block"
    assert "not saved" in result["answer"].lower()

def test_sensitive_input_is_redacted_from_persistent_trace():
    from database import get_recent_traces
    agent = StudentSupportAgent("STU1001")
    result = agent.answer("My OTP is 123456")
    traces = get_recent_traces(limit=10)
    stored = next(row for row in traces if row["run_id"] == result["trace"]["run_id"])
    assert "123456" not in stored["question"]
    assert "REDACTED" in stored["question"]
