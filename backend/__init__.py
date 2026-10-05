"""The RecruitAI web application: database, application workflow and API.

The extraction code in `llm/` and `app/resume_text.py` is used as a library.
Nothing in this package asks a model to decide anything; it stores what the
Reader extracted, tracks where each application is in the workflow, and keeps
the audit trail.
"""
