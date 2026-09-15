"""Applications: the record, its status, its documents and its timeline.

Two things here are deliberately narrow.

A status is changed through one endpoint (``POST .../status``) which returns the
updated record *and* the recomputed stage counters *and* the linked job's new
application state, so the screen can put all three right without a reload and
without a second request that could disagree with the first.

A document is addressed by its row id and never by a path.  ``GET
.../documents/{link_id}/file`` is the only way a stored file is served, it reads
the path from the row rather than from the request, and it refuses anything that
does not resolve to a real file inside ``documents/applications/``.  Nothing in
this module puts a filesystem path into a response.
"""

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from .. import application_documents as appdocs
from .. import applications as applications_mod
from ..db import connect
from ..schema_v2 import APPLICATION_STATUSES, CLOSED_STATUSES

router = APIRouter(prefix='/api/applications', tags=['applications'])


class ApplicationPayload(BaseModel, extra='ignore'):
    company: str = ''
    position: str = ''
    level: str = ''
    location: str = ''
    work_model: str = ''
    source: str = ''
    job_url: str = ''
    applied_date: str = ''
    status: str = 'Preparation'
    last_response: str = ''
    summary: str = ''
    next_action: str = ''
    follow_up_date: str = ''
    contact_name: str = ''
    contact_details: str = ''
    salary_range: str = ''
    priority: str = 'B'
    cv_version: str = ''
    cover_letter: str = ''
    notes: str = ''
    match_summary: str = ''
    salary_estimate: str = ''


class EventPayload(BaseModel, extra='ignore'):
    event_date: str = ''
    event_type: str = 'Note'
    person: str = ''
    note: str = ''
    next_step: str = ''
    follow_up_date: str = ''


class StatusPayload(BaseModel, extra='ignore'):
    status: str = ''


class DocumentLink(BaseModel, extra='ignore'):
    document_id: int
    document_kind: str = ''
    label: str = ''
    #: Accepted and ignored: the old link payload carried a free-text role.
    role: str = ''


def _meta():
    """Everything the screen needs to render controls, in one place."""
    return {
        'statuses': APPLICATION_STATUSES,
        'closed_statuses': list(CLOSED_STATUSES),
        'event_types': list(applications_mod.EVENT_TYPES),
        'document_kinds': [{'kind': k, 'label': v} for k, v in appdocs.KINDS],
        'document_formats': [s.lstrip('.').upper() for s in appdocs.ALLOWED_SUFFIXES],
    }


def _job_state(application_id):
    """How the Jobs screen should now show the job this application belongs to.

    Read back from the job, not assumed from the status that was just written,
    so the Jobs card and the Applications card cannot drift apart.
    """
    with connect() as conn:
        row = conn.execute(
            'SELECT id FROM discovered_jobs WHERE application_id=? OR id=('
            '  SELECT job_id FROM applications WHERE id=?) LIMIT 1',
            (application_id, application_id)).fetchone()
        if row is None:
            return None
        from .. import jobs_service
        job = jobs_service.get_card(row['id'], conn)
    if job is None:
        return None
    return {'job_id': job['id'], 'has_application': job['has_application'],
            'application_status': job['application_status'],
            'application_active': job['application_active']}


@router.get('')
def list_applications(status: str = ''):
    payload = {
        'applications': applications_mod.list_applications(status=status),
        'board': applications_mod.board(),
    }
    payload.update(_meta())
    return payload


@router.post('')
def create_application(payload: ApplicationPayload):
    try:
        return applications_mod.create(payload.model_dump())
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@router.get('/{application_id}')
def get_application(application_id: int):
    application = applications_mod.get_application(application_id)
    if application is None:
        raise HTTPException(404, 'Application not found')
    return application


@router.put('/{application_id}')
def update_application(application_id: int, payload: dict):
    application = applications_mod.update(application_id, payload)
    if application is None:
        raise HTTPException(404, 'Application not found')
    return application


@router.post('/{application_id}/status')
def set_status(application_id: int, payload: StatusPayload):
    """Move the application to another status.

    Local tracking only.  Nothing is sent to an employer, no form is submitted
    and no external service is contacted - this writes a row and returns what
    the screen has to repaint.
    """
    if not str(payload.status or '').strip():
        raise HTTPException(400, 'A status is required.')
    application = applications_mod.set_status(application_id, payload.status)
    if application is None:
        raise HTTPException(404, 'Application not found')
    return {'application': application, 'board': applications_mod.board(),
            'job': _job_state(application_id)}


@router.delete('/{application_id}')
def delete_application(application_id: int):
    if not applications_mod.delete(application_id):
        raise HTTPException(404, 'Application not found')
    return {'deleted': True}


@router.get('/{application_id}/events')
def list_events(application_id: int):
    return {'events': applications_mod.list_events(application_id)}


@router.post('/{application_id}/events')
def add_event(application_id: int, payload: EventPayload):
    if applications_mod.get_application(application_id) is None:
        raise HTTPException(404, 'Application not found')
    return applications_mod.add_event(application_id, payload.model_dump())


@router.delete('/{application_id}/events/{event_id}')
def delete_event(application_id: int, event_id: int):
    if not applications_mod.delete_event(event_id):
        raise HTTPException(404, 'Event not found')
    return {'deleted': True}


# -- the documents this application was sent with --------------------------
@router.get('/{application_id}/documents')
def list_documents(application_id: int):
    documents = applications_mod.list_documents(application_id)
    return {'documents': documents, 'summary': appdocs.summary(documents),
            'document_kinds': _meta()['document_kinds']}


@router.post('/{application_id}/documents')
def attach_document(application_id: int, payload: DocumentLink):
    """Take the chosen document-store file and keep a copy of it here.

    A copy, not a reference: after this the application holds its own bytes, so
    a new primary CV tomorrow changes nothing about what this record says was
    sent today.
    """
    try:
        document = applications_mod.attach_document(
            application_id, payload.document_id, kind=payload.document_kind,
            label=payload.label)
    except appdocs.ApplicationDocumentError as exc:
        raise HTTPException(400, str(exc))
    return {'document': document,
            'documents': applications_mod.list_documents(application_id)}


@router.post('/{application_id}/documents/upload')
async def upload_document(application_id: int, file: UploadFile = File(...),
                          document_kind: str = Form('other'), label: str = Form('')):
    data = await file.read()
    try:
        document = applications_mod.upload_document(
            application_id, document_kind, file.filename, data, label=label)
    except appdocs.ApplicationDocumentError as exc:
        raise HTTPException(400, str(exc))
    return {'document': document,
            'documents': applications_mod.list_documents(application_id)}


@router.delete('/{application_id}/documents/{link_id}')
def detach_document(application_id: int, link_id: int):
    if not applications_mod.detach_document(application_id, link_id):
        raise HTTPException(404, 'Document not found on this application')
    return {'deleted': True,
            'documents': applications_mod.list_documents(application_id)}


@router.get('/{application_id}/documents/{link_id}/file')
def download_document(application_id: int, link_id: int):
    """Serve one stored application document by id.

    The request carries two integers and no path at all.  The path comes from
    the row, and :func:`application_documents.resolve` only returns it when it
    resolves to a real file under ``documents/applications/`` - so a row with a
    traversing path, however it got into the database, reads as "not found"
    rather than as a file outside the workspace.
    """
    row = appdocs.get(application_id, link_id)
    if row is None:
        raise HTTPException(404, 'Document not found on this application')
    path = appdocs.resolve(row)
    if path is None:
        raise HTTPException(404, 'The stored file is no longer available')
    return FileResponse(str(path), filename=row['original_filename'] or path.name,
                        media_type=row['mime_type'] or 'application/octet-stream')
