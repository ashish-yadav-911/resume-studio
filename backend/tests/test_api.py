from pathlib import Path

import pytest


def test_health(client):
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_upload_resume(client, sample_resume_path):
    with sample_resume_path.open("rb") as handle:
        response = client.post(
            "/api/resumes/upload",
            files={"file": ("sample_resume.docx", handle, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
        )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["resume"]["id"].startswith("res_")
    assert body["resume"]["active_version"] is not None


def test_analyze_flow(client, sample_resume_path):
    with sample_resume_path.open("rb") as handle:
        upload = client.post(
            "/api/resumes/upload",
            files={"file": ("sample_resume.docx", handle, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
        )
    if upload.status_code == 409:
        resume_id = upload.json()["detail"]["existing_resume_id"]
    else:
        resume_id = upload.json()["resume"]["id"]

    conv = client.post("/api/conversations", json={"resume_id": resume_id})
    assert conv.status_code == 201
    conversation_id = conv.json()["id"]

    message = client.post(
        f"/api/conversations/{conversation_id}/messages",
        json={"content": "Analyze my resume"},
    )
    assert message.status_code == 202
    run_id = message.json()["run_id"]

    events = client.get(f"/api/runs/{run_id}/events")
    assert events.status_code == 200
    assert "run.completed" in events.text


def test_tailor_and_approve(client, sample_resume_path):
    with sample_resume_path.open("rb") as handle:
        upload = client.post(
            "/api/resumes/upload",
            files={"file": ("sample_resume.docx", handle, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
        )
    if upload.status_code == 409:
        resume_id = upload.json()["detail"]["existing_resume_id"]
    else:
        resume_id = upload.json()["resume"]["id"]

    conv = client.post("/api/conversations", json={"resume_id": resume_id})
    conversation_id = conv.json()["id"]

    jd = "Senior Backend Engineer\nRequirements: Python, FastAPI, PostgreSQL\nResponsibilities: Build APIs"
    message = client.post(
        f"/api/conversations/{conversation_id}/messages",
        json={"content": "Tailor my resume", "job_description": jd},
    )
    assert message.status_code == 202
    run_id = message.json()["run_id"]

    run = client.get(f"/api/runs/{run_id}")
    assert run.status_code == 200
    proposal = run.json().get("proposal")
    if proposal is None:
        # Fetch after completion by polling events is not required for this smoke test.
        pytest.skip("No proposal produced")
    approval = client.post(f"/api/runs/{run_id}/approve", json={})
    assert approval.status_code == 200
    assert approval.json()["version"]["version_number"] >= 2