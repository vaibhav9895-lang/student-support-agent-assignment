import os
import re
import time
import uuid
from typing import Any

from database import (
    get_fee_status, get_scholarship_status, create_support_request,
    save_memory, search_memories, log_trace, log_audit,
    request_programme_change, get_student, PROGRAMME_CATALOG
)
from retrieval import retrieve_policy_chunks

SENSITIVE_PATTERNS = [
    re.compile(r"\b(?:password|passwd|otp|one[- ]time password|api key|access token)\b", re.I),
    re.compile(r"\b\d{4,8}\b"),
]

def looks_sensitive(text: str) -> bool:
    lower = text.lower()
    if any(word in lower for word in ["password", "otp", "one-time password", "api key", "access token"]):
        return True
    # Conservative detection for common credential-like values.
    if re.search(r"\b(?:my otp is|password is|passcode is)\s*[:=]?\s*\S+", lower):
        return True
    return False

def extract_memory_candidate(question: str):
    patterns = [
        r"remember that (.+)",
        r"please remember (.+)",
        r"save this preference: (.+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, question, re.I)
        if match:
            value = match.group(1).strip().rstrip(".")
            if value and not looks_sensitive(value):
                return value
            return None
    return None

# Actions whose reply is written by Python, NOT rewritten by the LLM (the LLM must not
# soften, reword or "refuse" a security decision).
DETERMINISTIC_ACTIONS = {"authorization_denied", "programme_change_request", "sensitive_input_block"}

def is_programme_change_intent(text: str) -> bool:
    q = text.lower()
    wants_change = any(v in q for v in ["change", "switch", "update", "move", "transfer", "shift"])
    about_programme = any(w in q for w in ["programme", "program", "course"]) or any(p.lower() in q for p in PROGRAMME_CATALOG)
    # "course change policy / deadline" questions are policy questions, not requests.
    policy_question = any(w in q for w in ["policy", "deadline", "can i", "how do", "what is", "?"]) and "please" not in q and "i want" not in q
    return wants_change and about_programme and not policy_question

def extract_requested_programme(text: str, current: str):
    """Find the NEW programme in the sentence. Python decides this, not the LLM."""
    q = text.lower()
    hits = [(q.rfind(p.lower()), p) for p in PROGRAMME_CATALOG if p.lower() in q and p != current]
    if hits:
        return max(hits)[1]          # the one mentioned last is the target
    return None

class StudentSupportAgent:
    """Small, observable agent loop with intent routing, tools, retrieval and optional LLM wording."""

    def __init__(self, student_id: str, require_api: bool = False):
        self.student_id = student_id
        self.require_api = require_api
        self.api_enabled = bool(os.getenv("GROQ_API_KEY"))
        if self.require_api and not self.api_enabled:
            raise RuntimeError(
                "Groq API mode requires GROQ_API_KEY. Copy .env.example to .env and add your Groq API key."
            )

    def _choose_action(self, question: str) -> str:
        q = question.lower()
        if any(x in q for x in ["remember that", "please remember", "save this preference"]):
            return "memory_write"
        if any(x in q for x in ["change my programme to", "change my program to", "switch my programme to", "switch my program to"]):
            return "programme_change_request"
        if any(x in q for x in ["fee status", "fees status", "fee balance", "outstanding fee", "payment status", "paid my fee"]):
            return "fee_tool"
        if any(x in q for x in ["scholarship status", "my scholarship", "scholarship application"]):
            return "scholarship_tool"
        if any(x in q for x in ["create a support request", "raise a ticket", "submit a request", "support request"]):
            return "support_tool"
        if any(x in q for x in ["remember", "what do you know about me", "my preference", "my programme", "my program"]):
            return "memory_read"
        policy_terms = ["policy", "handbook", "deadline", "late fee", "payment portal", "next working day",
                        "course change", "withdraw", "scholarship eligibility", "refund"]
        if any(x in q for x in policy_terms):
            return "policy_retrieval"
        return "direct_answer"

    def answer(self, question: str) -> dict[str, Any]:
        started = time.perf_counter()
        run_id = str(uuid.uuid4())
        # In API mode, the LLM chooses the next action. Local routing remains available
        # only for unit tests and explicit offline development.
        # Enforce cross-student privacy before consulting the LLM. The identity comes
        # from the signed-in application session, never from a model-supplied target ID.
        from database import list_demo_students
        q_lower = question.lower()
        other_student = next((st for st in list_demo_students()
                              if st["student_id"] != self.student_id and
                              (st["student_id"].lower() in q_lower or st["name"].lower() in q_lower)), None)
        if looks_sensitive(question) and any(k in q_lower for k in ["password", "otp", "passcode", "token", "api key"]):
            action, action_usage = "sensitive_input_block", {}
        elif other_student and any(term in q_lower for term in ["fee", "scholarship", "record", "balance", "paid",
                                                                  "programme", "program", "change", "update", "details", "profile"]):
            action, action_usage = "authorization_denied", {}
        elif is_programme_change_intent(question):
            # Sensitive action: routed by application code so the LLM cannot skip the approval step.
            action, action_usage = "programme_change_request", {}
        elif self.api_enabled:
            action, action_usage = self._choose_action_with_llm(question)
        else:
            action, action_usage = self._choose_action(question), {}
        trace = {
            "run_id": run_id,
            "student_id": self.student_id,
            "selected_action": action,
            "tool_calls": [],
            "retrieved_chunks": [],
            "memory_items_used": [],
            "errors": [],
            "mode": "llm_api" if self.api_enabled else "mock",
            "input_tokens": action_usage.get("input_tokens"),
            "output_tokens": action_usage.get("output_tokens"),
        }
        evidence = []
        tool_result = None
        memory_context = search_memories(self.student_id, question, limit=3)

        # Do not echo or store credential-like content. It is not sent to memory.
        if action == "sensitive_input_block":
            answer = ("For your security, please do not share passwords, OTPs, API keys or access tokens in chat. "
                      "I have not saved that information to long-term memory. If a credential was exposed, change or revoke it.")
            trace["selected_action"] = "sensitive_input_block"
            trace["memory_items_used"] = []
            trace["input_tokens"] = None
            trace["output_tokens"] = None
        elif action == "authorization_denied":
            answer = "Access denied. You can view only your own private student records."
            trace["authorization"] = {"decision": "denied", "actor": self.student_id,
                                      "requested_target": other_student["student_id"]}
            log_audit(self.student_id, "private_record_access", other_student["student_id"],
                      "denied", None, "blocked", "Cross-student private data request",
                      "student", 0, None, 0)
        elif action == "memory_write":
            candidate = extract_memory_candidate(question)
            if candidate:
                save_memory(self.student_id, candidate)
                answer = f"I’ll remember this preference for future chats: **{candidate}**"
                evidence.append("Persistent memory: one non-sensitive preference saved.")
            else:
                answer = "I couldn't save that because it was empty or appeared to contain sensitive information."
        elif action == "fee_tool":
            tool_result = get_fee_status(self.student_id)
            trace["tool_calls"].append({"tool": "get_fee_status", "status": "success"})
            evidence.append(f"Student record tool: fee status for {self.student_id}")
            answer = self._format_fee(tool_result)
        elif action == "scholarship_tool":
            tool_result = get_scholarship_status(self.student_id)
            trace["tool_calls"].append({"tool": "get_scholarship_status", "status": "success"})
            evidence.append(f"Student record tool: scholarship status for {self.student_id}")
            answer = self._format_scholarship(tool_result)
        elif action == "programme_change_request":
            me = get_student(self.student_id)
            requested = extract_requested_programme(question, me["programme"])
            if not requested:
                options = ", ".join(p for p in PROGRAMME_CATALOG if p != me["programme"])
                answer = ("I couldn't tell which programme you want. Please name one of: " + options +
                          ". No change has been made.")
                trace["errors"].append("programme_not_recognised")
            else:
                try:
                    approval_id = request_programme_change(self.student_id, requested)
                except ValueError as exc:
                    answer = f"{exc} No change has been made."
                    trace["errors"].append(str(exc))
                else:
                    trace["tool_calls"].append({"tool": "request_programme_change", "status": "pending_approval"})
                    trace["authorization"] = {"decision": "allowed", "actor": self.student_id,
                                              "requested_target": self.student_id}
                    trace["approval"] = {"required": True, "status": "pending", "approval_id": approval_id,
                                         "action": "programme_change", "from": me["programme"], "to": requested,
                                         "executed": False}
                    evidence.append(f"Approval request {approval_id} created (Pending). Programme unchanged.")
                    answer = (f"Your request to change from **{me['programme']}** to **{requested}** needs approval from "
                              f"the registrar. **Approval ID:** `{approval_id}` · Status: **Pending**.\n\n"
                              f"Your current programme is still **{me['programme']}**. You can track the status in the "
                              "**Approvals** tab.")
        elif action == "support_tool":
            issue = re.sub(r"(?i)^(please\s+)?(create a support request|raise a ticket|submit a request|support request)\s*[:\-]?\s*", "", question).strip()
            if not issue:
                issue = question
            request_id = create_support_request(self.student_id, issue)
            tool_result = {"request_id": request_id, "status": "Open", "issue": issue}
            trace["tool_calls"].append({"tool": "create_support_request", "status": "success"})
            evidence.append(f"Support request record: {request_id}")
            answer = f"Your support request has been created. **Request ID:** `{request_id}`. Status: **Open**."
        elif action == "memory_read":
            if memory_context:
                answer = "Here is the relevant information I remember:\n\n" + "\n".join(f"- {m['content']}" for m in memory_context)
                evidence.extend([f"Memory item: {m['content']}" for m in memory_context])
                trace["memory_items_used"] = [m["content"] for m in memory_context]
            else:
                answer = "I don't have a saved memory relevant to that question yet. You can say, for example, “Remember that I prefer concise explanations.”"
        elif action == "policy_retrieval":
            chunks = retrieve_policy_chunks(question, top_k=3)
            trace["retrieved_chunks"] = [{"source": c["source"], "heading": c["heading"], "score": c["score"]} for c in chunks]
            if chunks:
                evidence.extend([f"{c['source']} — {c['heading']}: {c['text']}" for c in chunks])
                answer = self._answer_from_policy(question, chunks)
            else:
                answer = ("I couldn't find enough information in the available sample handbook to answer confidently. "
                          "Please contact the student support office to confirm the official rule.")
        else:
            if memory_context:
                trace["memory_items_used"] = [m["content"] for m in memory_context]
            answer = self._direct_answer(question, memory_context)

        # In API mode, use the LLM to compose the final response from the tool result,
        # retrieved evidence and relevant memory. Never silently downgrade an API run to mock.
        if self.api_enabled and action not in DETERMINISTIC_ACTIONS:
            answer, usage = self._llm_rephrase(question, answer, evidence, memory_context)
            trace["input_tokens"] = (trace["input_tokens"] or 0) + (usage.get("input_tokens") or 0)
            trace["output_tokens"] = (trace["output_tokens"] or 0) + (usage.get("output_tokens") or 0)
        elapsed = (time.perf_counter() - started) * 1000
        trace["latency_ms"] = round(elapsed, 2)
        trace["outcome"] = "answered"
        trace["evidence_count"] = len(evidence)
        safe_question = "[REDACTED: sensitive credential-like input]" if action == "sensitive_input_block" else question
        log_trace(run_id, self.student_id, safe_question, action, answer, trace)
        return {"answer": answer, "trace": trace, "evidence": evidence}

    def _choose_action_with_llm(self, question):
        """Ask the configured LLM to select one allowed agent action."""
        from openai import OpenAI
        client = OpenAI(
            api_key=os.getenv("GROQ_API_KEY"),
            base_url="https://api.groq.com/openai/v1",
        )
        model = os.getenv("GROQ_MODEL", "openai/gpt-oss-20b")
        allowed = [
            "fee_tool", "scholarship_tool", "support_tool", "policy_retrieval",
            "memory_write", "memory_read", "programme_change_request", "direct_answer"
        ]
        response = client.chat.completions.create(
            model=model,
            temperature=0,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You route requests for a student support agent. Select exactly one action "
                        "using the select_action function. Use fee_tool for fee/payment status lookups; "
                        "scholarship_tool for scholarship status; programme_change_request when a student asks to change/switch/update their own programme or course enrolment (in ANY wording, e.g. 'change from X to Y'); support_tool only when the user asks "
                        "to create/raise/submit a support request; policy_retrieval for handbook, policy, "
                        "deadline, late-fee, course-change or refund questions; memory_write when the user "
                        "explicitly asks to remember/save a non-sensitive preference; memory_read when asking "
                        "what is remembered; otherwise direct_answer. Never classify passwords, OTPs, API keys "
                        "or access tokens as memory_write."
                    ),
                },
                {"role": "user", "content": question},
            ],
            tools=[{
                "type": "function",
                "function": {
                    "name": "select_action",
                    "description": "Select the next action for the student support agent.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "action": {"type": "string", "enum": allowed}
                        },
                        "required": ["action"],
                        "additionalProperties": False,
                    },
                },
            }],
            tool_choice={"type": "function", "function": {"name": "select_action"}},
        )
        message = response.choices[0].message
        import json
        selected = json.loads(message.tool_calls[0].function.arguments)["action"]
        usage = {}
        if getattr(response, "usage", None):
            usage = {
                "input_tokens": getattr(response.usage, "prompt_tokens", None),
                "output_tokens": getattr(response.usage, "completion_tokens", None),
            }
        return selected, usage

    def _format_fee(self, record):
        return (f"Your current fee status is **{record['status']}**.\n\n"
                f"- Total fee: ₹{record['total_fee']:,.0f}\n"
                f"- Amount paid: ₹{record['amount_paid']:,.0f}\n"
                f"- Outstanding balance: ₹{record['balance']:,.0f}\n\n"
                "This is synthetic demonstration data, not an official fee statement.")

    def _format_scholarship(self, record):
        return (f"Your scholarship application status is **{record['status']}**.\n\n"
                f"- Scholarship: {record['scholarship']}\n"
                f"- Last updated: {record['updated_at']}\n\n"
                "This is synthetic demonstration data, not an official scholarship decision.")

    def _answer_from_policy(self, question, chunks):
        q = question.lower()
        combined = " ".join(c["text"].lower() for c in chunks)
        if "next working day" in q or "payment portal" in q:
            if "next working day" in combined:
                return ("The sample handbook says that if the payment portal is unavailable near a deadline, "
                        "you should save evidence of the failed transaction and contact the Accounts Office. "
                        "It does not guarantee an automatic extension to the next working day, so confirm with Accounts.")
        if "course change" in q:
            if "course change" in combined:
                return ("According to the sample handbook, course-change requests must be submitted through the academic office "
                        "and are subject to eligibility, seat availability and the published deadline. Contact the academic office "
                        "to confirm your specific case.")
        if "late fee" in q:
            if "late fee" in combined:
                return ("The sample handbook says late fees may apply after the published payment deadline. "
                        "The exact amount and any exception should be confirmed with the Accounts Office.")
        if "scholarship" in q and "eligibility" in q:
            if "scholarship" in combined:
                return ("The sample handbook describes scholarship eligibility as dependent on the applicable scheme criteria "
                        "and supporting documents. Check the specific scheme notice or contact the scholarship office.")
        # Only summarize relevant retrieved passages; no unsupported definitive claims.
        best = chunks[0]
        return (f"I found relevant information in the sample handbook under **{best['heading']}**:\n\n"
                f"> {best['text']}\n\n"
                "Please confirm time-sensitive or case-specific decisions with the relevant college office.")

    def _direct_answer(self, question, memories):
        q = question.lower()
        if any(g in q for g in ["hello", "hi", "hey"]):
            return "Hi! I can help with fee status, scholarships, student policies and support requests. What would you like to check?"
        if "what can you do" in q or "help me" in q:
            return ("I can check your demo fee and scholarship records, retrieve relevant handbook guidance, "
                    "remember non-sensitive preferences and create support requests.")
        if memories:
            return ("I can help with student support questions. I also found a relevant saved preference: "
                    + memories[0]["content"] + ". Ask me about fees, scholarships, policies or support requests.")
        return ("I can help with student fees, scholarship status, college policies and support requests. "
                "I don't have enough information to answer that specific question yet. Try asking about one of those topics "
                "or consult the relevant college office.")

    def _llm_rephrase(self, question, draft, evidence, memories):
        from openai import OpenAI
        client = OpenAI(api_key=os.getenv("GROQ_API_KEY"), base_url="https://api.groq.com/openai/v1")
        model = os.getenv("GROQ_MODEL", "openai/gpt-oss-20b")
        context = "\n".join(evidence[:4]) or "No external evidence available."
        memory = "\n".join(m["content"] for m in memories[:3]) or "No relevant saved memory."
        response = client.chat.completions.create(
            model=model,
            temperature=0.2,
            messages=[
                {"role": "system", "content": "You are a student support assistant. Use only the supplied draft and evidence. Do not invent college rules, fee facts or scholarship decisions. If evidence is insufficient, say so. Never ask for passwords or OTPs. Keep the answer concise."},
                {"role": "user", "content": f"Question: {question}\nDraft: {draft}\nRelevant evidence: {context}\nRelevant memory: {memory}\nImprove clarity without adding unsupported facts."}
            ]
        )
        usage = {}
        if getattr(response, "usage", None):
            usage = {
                "input_tokens": getattr(response.usage, "prompt_tokens", None),
                "output_tokens": getattr(response.usage, "completion_tokens", None)
            }
        return response.choices[0].message.content or draft, usage
