"""Profile: who Sebastian is, plus the documents on disk."""

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse

from .. import documents as documents_mod
from .. import person as person_mod
from ..db import connect, now_iso, row_to_dict

router = APIRouter(prefix='/api/profile', tags=['profile'])


def load_person(conn):
    row = conn.execute('SELECT * FROM person_profile WHERE id=1').fetchone()
    return person_mod.from_row(row_to_dict(row)) if row else dict(person_mod.DEFAULT_PERSON)


@router.get('')
def get_profile():
    with connect() as conn:
        return {
            'person': load_person(conn),
            'documents': documents_mod.list_documents(conn),
            # Every supported category, including the ones with no file yet -
            # those are reported as "Not configured" rather than invented.
            'document_categories': documents_mod.categories(conn),
            'document_kinds': [{'kind': k, 'label': v[1], 'language': v[2]}
                               for k, v in documents_mod.KINDS.items()],
        }


@router.put('')
def update_profile(payload: dict):
    with connect() as conn:
        merged = person_mod.merge(load_person(conn), payload or {})
        row = person_mod.to_row(merged)
        assignments = ','.join('{0}=?'.format(key) for key in row)
        conn.execute('UPDATE person_profile SET {0}, updated_at=? WHERE id=1'.format(assignments),
                     list(row.values()) + [now_iso()])
        conn.commit()
        return {'person': load_person(conn)}


@router.get('/documents')
def list_documents():
    return {'documents': documents_mod.list_documents()}


@router.post('/documents')
async def upload_document(kind: str = Form(...), file: UploadFile = File(...),
                          label: str = Form(''), notes: str = Form('')):
    data = await file.read()
    try:
        document = documents_mod.store(kind, file.filename, data, label=label, notes=notes)
    except documents_mod.DocumentError as exc:
        raise HTTPException(400, str(exc))
    return {'document': document}


@router.post('/documents/{document_id}/primary')
def set_primary(document_id: int):
    try:
        return {'document': documents_mod.set_primary(document_id)}
    except documents_mod.DocumentError as exc:
        raise HTTPException(404, str(exc))


@router.get('/documents/{document_id}/file')
def download_document(document_id: int):
    document = documents_mod.get(document_id)
    if document is None or not document['exists']:
        raise HTTPException(404, 'File not found on disk')
    return FileResponse(document['absolute_path'], filename=document['filename'],
                        media_type=document['mime_type'])


@router.delete('/documents/{document_id}')
def delete_document(document_id: int):
    if not documents_mod.delete(document_id):
        raise HTTPException(404, 'Document not found')
    return {'deleted': True}
