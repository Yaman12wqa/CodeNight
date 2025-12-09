@echo off
"%~dp0.venv\Scripts\python" -m uvicorn agent_service.main:app --host 0.0.0.0 --port 8001 --reload
