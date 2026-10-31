import os, tempfile
# Use a throw-away database for tests so the real demo data is never touched.
os.environ["STUDENT_SUPPORT_DB"] = os.path.join(tempfile.mkdtemp(), "test.db")
os.environ.pop("GROQ_API_KEY", None)
