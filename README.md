# Intelligent Student Support Agent — Assignment 1

A lightweight Agentic AI engineering project built with Python, Streamlit and SQLite. It demonstrates tool use, policy retrieval, persistent non-sensitive memory, context selection, traces and reproducible evaluation.

> **Demo only:** All student data and handbook policies are synthetic. This is not an official student information system. Assignment 2 adds identity, privacy, approval and audit controls (see below).

## Features

- Chat interface with fee and scholarship lookups.
- Support-request creation.
- Small policy retrieval system that returns relevant handbook sections.
- Persistent SQLite memory for non-sensitive preferences.
- Sensitive-input warning for credentials such as passwords and OTPs.
- Per-run trace with selected action, tools, retrieved evidence, latency and available token usage.
- Local evaluation report and automated tests.
- Required LLM API: the model selects the agent action and generates the final response using the selected tool result/evidence. The app will not start in mock mode.

## Requirements

Python 3.10 or newer is recommended.

## Setup

### macOS / Linux

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
streamlit run app.py
```

### Windows PowerShell

```powershell
py -m venv .venv
.venv\\Scripts\\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
streamlit run app.py
```

**LLM API is required.** Copy `.env.example` to `.env`, replace the placeholder with your own provider API key, and set `GROQ_MODEL` to a model available to your Groq account. The default is `openai/gpt-oss-20b`. The model is used for action selection and final response generation. Never share your key in chat or commit `.env` to GitHub. Groq free-tier limits and model availability can change; check your Groq console.

## Demo profiles

Use the profile selector in the sidebar:
- `STU1001` — Aarav Shah, PGDM Big Data Analytics
- `STU1002` — Mira Patel, MBA Finance
- `STU1003` — Kabir Mehta, B.Tech Computer Engineering

The selector is only a demo identity switcher, not production authentication. Do not use real student data.

## Example questions

- What is my current fee status?
- What is my scholarship status?
- The payment portal failed. Can I pay on the next working day?
- Create a support request: I cannot access the student portal.
- Remember that I prefer concise explanations.
- What do you remember about me?
- What is the late fee policy?

## Run tests and evaluation

```bash
pytest -q
python evaluation.py
```

The Evaluation tab in the UI also runs the test set and displays task success, latency and per-case results. Token usage is reported only when supplied by the configured model API. Mock mode does not invent provider token counts.

## Architecture

1. `app.py` — Streamlit interface, tabs and session state.
2. `agent.py` — action selection, agent loop, tool calls, memory handling, response grounding and trace creation.
3. `database.py` — SQLite schema, synthetic records, tools' data access, memory and trace persistence.
4. `retrieval.py` — handbook section loading, simple lexical relevance scoring and top-k retrieval.
5. `evaluation.py` — deterministic evaluation cases and reporting.
6. `tests/` — tests for tools, retrieval, memory and missing data.
7. `data/student_handbook.md` — clearly labelled sample policy content.

## Agent flow

Question → choose action → fetch only relevant tool/policy/memory context → construct answer → save trace and measurements.

In LLM API mode, the model selects one allowed action through a constrained function call. The application executes the selected local tool or retrieves relevant handbook/memory context, then asks the model to compose a grounded answer. The model does not receive the whole database or handbook. Local deterministic routing remains available only to automated unit tests when no API key is set; the Streamlit app blocks startup without a key.

## Limitations

- The profile selector is not authentication or authorization. Do not use real student records.
- The included handbook is fictional and must not be represented as a real college policy.
- The lightweight retrieval system uses lexical matching, not a vector database.
- API usage measurements depend on provider response metadata and model support. API calls may incur charges; keep keys private.
- Assignment 2 must add application-level authentication, authorization, approval controls and audit protections.


## LLM API behavior

The app requires `GROQ_API_KEY`. In the API-backed UI, Groq-hosted LLM chooses the action via a constrained function call; Python performs the actual database/retrieval operation; then the LLM writes the user-facing response from the returned evidence. Tool execution and authorization are performed by application code, not by the model alone. If the API request fails, the error is surfaced rather than silently switching to mock mode.


## Assignment 2 safety controls

This extension keeps the Assignment 1 chat application and adds: 

- **Application-layer ownership checks:** fee, scholarship and support-request tools derive the target from the signed-in demo student. Database functions reject a mismatched actor/target pair; the LLM cannot grant access.
- **Cross-student request blocking:** explicit requests naming another demo student are denied before the LLM call, and the denial is audited.
- **Human approval for sensitive changes:** a programme-change request creates a pending approval only. The programme field is updated only after the student approves it in **Memory & requests**. Rejecting leaves the programme unchanged.
- **Persistent audit log:** authorization decisions, approval requests and decisions are stored in SQLite and displayed in the app.
- **Sensitive-input handling:** password/OTP/API-key-like messages are blocked before the LLM call and are not saved to memory.

### Assignment 2 demonstration scenarios

1. Ask for your own fee/scholarship status: allowed.
2. Ask for another demo student's fee/scholarship record: blocked and audited.
3. Ask `Change my programme to MBA Finance`: creates a pending approval; the programme remains unchanged.
4. Reject the approval: no update occurs. Create another request and approve it: the programme updates after approval.
5. Enter a test OTP such as `My OTP is 123456`: blocked before model processing and not stored as memory.

**Authentication limitation:** the sidebar identity selector is a simulated sign-in mechanism for classroom demonstration, not production authentication. In a real deployment, replace it with institutional SSO/session authentication and derive the student ID exclusively from the verified server-side session. Do not use real student records in this demo.


## Assignment 2 — privacy, approval and audit rules

- **Identity:** sidebar sign-in (simulated). Students are `STU1001-3`; the approver is `REG001` (Demo Registrar). Not production authentication.
- **Privacy:** fee/scholarship/support tools take the *signed-in* student ID and refuse any other target (`database._authorize_owner`). A request naming another student is blocked in `agent.py` before any tool or LLM runs.
- **Sensitive action (programme change):** creates an `approvals` row with status **Pending**; the programme is NOT changed. Only a staff member with role `registrar` can approve/reject (`database.decide_approval`) — a student cannot approve their own request, even by calling the function directly.
- **Approve** → programme updated. **Reject** → programme unchanged. A request can be decided only once.
- **Audit log:** one row per step (request, authorization decision, approval required, approval status, executed yes/no, result, actor and role).
- **LLM limits:** programme-change routing, privacy denials and credential blocking are decided by Python; their replies are NOT rewritten by the LLM.
- **Secrets:** text with passwords/OTP/API keys is blocked before the LLM, never saved to memory, and stored in traces as `[REDACTED]`.
- Run `python check_setup.py` to verify `.env`/Groq without printing the key. Run `python -m pytest -q tests` and `python evaluation.py` for checks.

- Run `python generate_report.py` to run every required scenario on a temporary database and write `SUBMISSION_REPORT.md` (traces, audit rows, evaluation table). With a valid Groq key it records real latency and token usage.
