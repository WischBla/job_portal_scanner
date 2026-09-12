"""Applications: pipeline, detail, timeline and the documents used."""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import applications as applications_mod
from ..schema_v2 import APPLICATION_STATUSES

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


class DocumentLink(BaseModel):
    document_id: int
    role: str = ''


@router.get('')
def list_applications(status: str = ''):
    return {
        'applications': applications_mod.list_applications(status=status),
        'board': applications_mod.board(),
        'statuses': APPLICATION_STATUSES,
        'event_types': list(applications_mod.EVENT_TYPES),
    }


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


@router.post('/{application_id}/documents')
def attach_document(application_id: int, payload: DocumentLink):
    return {'documents': applications_mod.attach_document(
        application_id, payload.document_id, payload.role)}


@router.delete('/{application_id}/documents/{document_id}')
def detach_document(application_id: int, document_id: int):
    applications_mod.detach_document(application_id, document_id)
    return {'deleted': True}
