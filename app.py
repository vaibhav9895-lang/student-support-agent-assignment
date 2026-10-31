import os
from dotenv import load_dotenv
import streamlit as st
from agent import StudentSupportAgent
from pathlib import Path
from database import (init_db, list_demo_students, get_recent_traces, get_recent_support_requests,
                      get_approvals, decide_approval, get_audit_logs, get_student,
                      get_pending_approvals_for_approver, get_staff)
from evaluation import run_evaluation

# override=True: the .env file wins over any old key left in the Terminal session.
load_dotenv(Path(__file__).resolve().parent / ".env", override=True)

st.set_page_config(page_title="Student Support AI", page_icon="🎓", layout="wide")

init_db()

if not os.getenv("GROQ_API_KEY"):
    st.error("LLM API mode is required for this assignment. Add your Groq API key to the project's .env file, then restart Streamlit.")
    st.markdown(
        "1. Stop the app in the terminal with **Control + C**.  \\n"
        "2. Copy **.env.example** to **.env** in the project folder.  \\n"
        "3. Add your Groq API key to `GROQ_API_KEY` and choose the model in `GROQ_MODEL`.  \\n"
        "4. Run `streamlit run app.py` again.  \\n\\n"
        "**Never paste your API key into chat or commit the `.env` file to GitHub.**"
    )
    st.stop()

st.markdown("""
<style>
.block-container {max-width: 1180px; padding-top: 1.5rem;}
.hero {padding: 1.35rem 1.5rem; border-radius: 18px; background: linear-gradient(120deg,#14213d,#264a73); color: white; margin-bottom: 1rem;}
.hero h1 {margin:0; color:white; font-size:2rem;}
.hero p {margin:.45rem 0 0 0; color:#e6eef8;}
div[data-testid="stChatMessage"] {border-radius: 14px;}
.small-muted {color: #6b7280; font-size: .88rem;}
</style>
<div class="hero">
  <h1>🎓 Student Support AI</h1>
  <p>A tool-using student support assistant with policy retrieval, persistent memory and execution traces.</p>
</div>
""", unsafe_allow_html=True)

with st.sidebar:
    st.header("Demo controls")
    role = st.radio("Sign in as (simulated)", ["Student", "Registrar (approver)"],
                    help="Demonstration only. Real systems would use proper login.")
    students = list_demo_students()
    labels = {f"{s['name']} · {s['student_id']}": s["student_id"] for s in students}
    if role == "Student":
        selected_label = st.selectbox("Demo student", list(labels.keys()))
        student_id = labels[selected_label]
        actor_id = student_id
    else:
        staff = get_staff("REG001")
        selected_label = f"{staff['name']} · {staff['staff_id']}"
        student_id = students[0]["student_id"]   # only used to keep the chat agent constructed
        actor_id = staff["staff_id"]
        st.info(f"Signed in as **{selected_label}**")
    st.caption("Demo identity selector (simulated sign-in, NOT production authentication). Permissions are enforced in database/tool code. Synthetic data only.")
    if st.button("Start a fresh chat", use_container_width=True):
        st.session_state.messages = []
        st.session_state.agent = StudentSupportAgent(student_id, require_api=True)
        st.rerun()
    st.divider()
    st.subheader("Example questions")
    examples = [
        "What is my current fee status?",
        "What is my scholarship status?",
        "The payment portal failed. Can I pay on the next working day?",
        "Create a support request: I cannot access the student portal.",
        "What programme am I enrolled in?",
        "What is the late fee policy?",
        "Can I change my course after the deadline?"
    ]
    for q in examples:
        if st.button(q, key="ex_"+q, use_container_width=True):
            st.session_state.pending_question = q
    st.divider()
    st.caption("Mode: " + "LLM API (required)")

if "messages" not in st.session_state:
    st.session_state.messages = []
if "agent" not in st.session_state or st.session_state.get("agent_student") != student_id:
    st.session_state.agent = StudentSupportAgent(student_id, require_api=True)
    st.session_state.agent_student = student_id

tabs = st.tabs(["💬 Chat", "🧭 Trace explorer", "📊 Evaluation", "🧠 Memory & requests", "✅ Approvals", "📜 Audit log"])

with tabs[0]:
    st.markdown(f"**Signed-in demo profile:** {selected_label}")
    if role != "Student":
        st.info("The registrar does not use the student chat. Open the **Approvals** tab to approve or reject requests.")
    for message in st.session_state.messages:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])
            if message.get("evidence"):
                with st.expander("Evidence used"):
                    for item in message["evidence"]:
                        st.write(item)
            if message.get("trace"):
                with st.expander("Execution trace"):
                    st.json(message["trace"])

    pending = st.session_state.pop("pending_question", None)
    question = st.chat_input("Ask about fees, scholarships, policies or support...", disabled=(role != "Student"))
    if pending and not question:
        question = pending

    if question:
        st.session_state.messages.append({"role": "user", "content": question})
        with st.chat_message("user"):
            st.markdown(question)
        with st.chat_message("assistant"):
            with st.spinner("Checking relevant records and policy..."):
                result = st.session_state.agent.answer(question)
            st.markdown(result["answer"])
            if result.get("evidence"):
                with st.expander("Evidence used"):
                    for item in result["evidence"]:
                        st.write(item)
            with st.expander("Execution trace"):
                st.json(result["trace"])
        st.session_state.messages.append({
            "role": "assistant", "content": result["answer"],
            "evidence": result.get("evidence", []), "trace": result["trace"]
        })

with tabs[1]:
    st.subheader("Recent agent runs")
    traces = get_recent_traces(limit=30)
    if traces:
        st.dataframe(traces, use_container_width=True, hide_index=True)
        chosen = st.selectbox("Inspect trace JSON", options=[r["run_id"] for r in traces])
        selected = next(r for r in traces if r["run_id"] == chosen)
        st.json(selected["trace"])
    else:
        st.info("Ask a question in the Chat tab to generate a trace.")

with tabs[2]:
    st.subheader("Reproducible evaluation")
    st.write("Runs a sample test set through the configured LLM-backed agent. API usage and results may vary by model.")
    if st.button("Run evaluation", type="primary"):
        with st.spinner("Running evaluation cases..."):
            report = run_evaluation()
        st.session_state.eval_report = report
    if "eval_report" in st.session_state:
        report = st.session_state.eval_report
        c1, c2, c3 = st.columns(3)
        c1.metric("Task success", f"{report['success_rate']:.0%}")
        c2.metric("Average latency", f"{report['average_latency_ms']:.1f} ms")
        c3.metric("Tests passed", f"{report['passed']}/{report['total']}")
        st.markdown("**Comparison: Assignment 1 suite vs Assignment 2 suite**")
        st.dataframe([{"suite": k, **v} for k, v in report["by_group"].items()], use_container_width=True, hide_index=True)
        st.dataframe(report["results"], use_container_width=True, hide_index=True)
        st.caption("This evaluation uses the configured LLM API. Token counts are provider-reported where available.")

with tabs[3]:
    st.subheader("Persistent memory")
    st.write("The agent can remember useful non-sensitive preferences or programme facts between chats. Sensitive values are filtered.")
    from database import get_memories_for_student, get_recent_support_requests
    memories = get_memories_for_student(student_id)
    if memories:
        st.dataframe(memories, use_container_width=True, hide_index=True)
    else:
        st.info("No saved memories yet. Try: “Remember that I prefer concise explanations.”")
    st.subheader("Recent support requests")
    requests = get_recent_support_requests(student_id)
    if requests:
        st.dataframe(requests, use_container_width=True, hide_index=True)
    else:
        st.info("No support requests created for this demo student yet.")

with tabs[4]:
    if role == "Student":
        st.subheader("My approval requests")
        st.caption("A programme change is NOT executed until the registrar approves it. You cannot approve your own request.")
        mine = get_approvals(student_id)
        if mine:
            st.dataframe([{"approval_id": a["approval_id"], "from": a["current_value"], "to": a["requested_value"],
                           "status": a["status"], "requested": a["created_at"], "decided_by": a["decided_by"],
                           "executed": bool(a["executed"])} for a in mine], use_container_width=True, hide_index=True)
            st.write("**Current programme:** " + get_student(student_id)["programme"])
        else:
            st.info("No requests yet. Try in Chat: “I want to change my programme from PGDM Big Data Analytics to MBA Finance.”")
    else:
        st.subheader("Pending approvals (registrar)")
        st.caption("Demonstration approver. Permission is checked in Python (database.decide_approval), not just hidden in the screen.")
        pending = get_pending_approvals_for_approver(actor_id)
        if not pending:
            st.info("No pending requests.")
        for a in pending:
            who = get_student(a["student_id"])
            with st.container(border=True):
                st.markdown(f"**{a['approval_id']}** · Student **{who['name']} ({a['student_id']})**")
                st.write(f"Current programme: **{a['current_value']}**  →  Requested: **{a['requested_value']}**")
                st.caption(f"Requested on {a['created_at']} by {a['requested_by']}")
                left, right = st.columns(2)
                if left.button("Approve", key=f"approve_{a['approval_id']}", type="primary"):
                    decide_approval(actor_id, a["approval_id"], True)
                    st.rerun()
                if right.button("Reject", key=f"reject_{a['approval_id']}"):
                    decide_approval(actor_id, a["approval_id"], False)
                    st.rerun()
        st.subheader("Decided requests")
        done = [a for a in get_approvals() if a["status"] != "Pending"]
        if done:
            st.dataframe([{k: a[k] for k in ["approval_id", "student_id", "current_value", "requested_value",
                                              "status", "decided_by", "decided_at", "executed"]} for a in done],
                         use_container_width=True, hide_index=True)

with tabs[5]:
    st.subheader("Audit log")
    st.caption("Actor = who did it. Students see only their own entries; the registrar sees all.")
    audit = get_audit_logs(student_id if role == "Student" else None, limit=60)
    if role == "Student":
        # include registrar decisions about this student's requests
        audit = [r for r in get_audit_logs(None, limit=200) if r["student_id"] == student_id or r["target_student_id"] == student_id][:60]
    cols = ["audit_id", "created_at", "student_id", "actor_role", "action", "target_student_id", "decision",
            "approval_required", "approval_status", "executed", "result", "approval_id", "details"]
    if audit:
        st.dataframe([{c: r.get(c) for c in cols} for r in audit], use_container_width=True, hide_index=True)
    else:
        st.info("No audit events recorded yet.")

st.markdown('<p class="small-muted">Assignment 2 demo · Simulated sign-in · Synthetic data · Not production authentication</p>', unsafe_allow_html=True)
