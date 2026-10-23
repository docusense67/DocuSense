"""Core localhost workflow with real PDF generation; no mechanism is actuated."""
import copy
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
from PIL import Image
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.config import get_settings
from app.db import Base, get_db
from app.main import app
from app.models import AuditLog, GeneratedDocument, User, UserSession
from app.security import hash_token
from app.seed import seed
from app.timeutil import utcnow
from tests.station_test_base import inline_thread


def weasyprint_available() -> bool:
    try:
        import weasyprint  # noqa: F401
    except OSError:
        return False
    return True


# Real PDFs: skipped on the Windows dev machine (no GTK), always rendered in CI.
@unittest.skipUnless(weasyprint_available(), 'WeasyPrint system libraries missing')
class CoreLocalWorkflowTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        if os.getenv('ADGVS_INLINE_TEST_THREADS'):
            workaround = patch('anyio.to_thread.run_sync', inline_thread)
            workaround.start()
            self.addCleanup(workaround.stop)
        self.engine = create_engine('sqlite://', connect_args={'check_same_thread': False},
                                    poolclass=StaticPool)
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine, autoflush=False, expire_on_commit=False)
        seed(self.db)
        for user in self.db.scalars(select(User)):
            user.must_change_password = False
            handle = user.email.split('@')[0]
            self.db.add(UserSession(user_id=user.id, token_hash=hash_token(f'core-{handle}'),
                                   created_at=utcnow(), last_seen_at=utcnow()))
        self.db.commit()
        def override_db():
            yield self.db
        app.dependency_overrides[get_db] = override_db
        self.folder = tempfile.TemporaryDirectory()
        self.storage = patch.object(get_settings(), 'storage_dir', self.folder.name)
        self.storage.start()
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                        base_url='https://testserver')

    async def asyncTearDown(self):
        await self.client.aclose()
        app.dependency_overrides.clear()
        self.db.close()
        self.engine.dispose()
        self.storage.stop()
        self.folder.cleanup()

    def login(self, handle):
        self.client.cookies.clear()
        self.client.cookies.set(get_settings().session_cookie_name, f'core-{handle}')

    async def generate(self, handle, document_type):
        self.login(handle)
        response = await self.client.post('/api/requests', json={
            'document_type': document_type, 'purpose': 'Core workflow validation'})
        self.assertEqual(response.status_code, 201, response.text)
        row = response.json()
        self.assertEqual(row['status'], 'requested', row)
        # Approve-first (S-11): approval generates the document; verification releases it.
        self.login('registrar')
        approved = await self.client.post(f"/api/requests/{row['id']}/approve")
        self.assertEqual(approved.status_code, 200, approved.text)
        self.assertEqual(approved.json()['status'], 'for_verification', approved.text)
        document = self.db.get(GeneratedDocument, approved.json()['document']['id'])
        self.login(handle)
        denied = await self.client.post(f'/api/documents/{document.id}/download-link')
        self.assertEqual(denied.status_code, 404)
        self.login('registrar')
        verified = await self.client.post(f"/api/requests/{row['id']}/verify")
        self.assertEqual(verified.status_code, 200, verified.text)
        self.login(handle)
        link = await self.client.post(f'/api/documents/{document.id}/download-link')
        self.assertEqual(link.status_code, 200, link.text)
        pdf = await self.client.get(link.json()['url'])
        self.assertEqual(pdf.status_code, 200)
        self.assertTrue(pdf.content.startswith(b'%PDF-'))
        path = Path(self.folder.name) / f'{document.id}.pdf'
        path.write_bytes(pdf.content)
        pdf_python = os.getenv('ADGVS_PDFIUM_PYTHON') or (
            '/opt/adgvs/venv/bin/python' if Path('/opt/adgvs/venv/bin/python').is_file()
            else sys.executable)
        result = subprocess.run([pdf_python, '-c',
            'import pypdfium2,sys; d=pypdfium2.PdfDocument(sys.argv[1]); '
            'print("\\n".join(p.get_textpage().get_text_range() for p in d))', str(path)],
            check=True, capture_output=True, text=True, timeout=20)
        for value in (document.document_number, document.data_snapshot['student_number'],
                      document.data_snapshot['student_name']):
            self.assertIn(value, result.stdout)
        self.login('juan.delacruz' if handle == 'maria.santos' else 'maria.santos')
        self.assertEqual((await self.client.get(link.json()['url'])).status_code, 404)
        return document, pdf.content

    async def test_request_generation_download_verification_reports_and_audit(self):
        tor, tor_pdf = await self.generate('maria.santos', 'TOR')
        coe, coe_pdf = await self.generate('juan.delacruz', 'COE')
        self.assertNotEqual(tor_pdf, coe_pdf)
        self.assertNotEqual(tor.qr_token, coe.qr_token)
        self.login('admin')
        station = await self.client.post('/api/stations', json={'name': 'Core station'})
        self.assertEqual(station.status_code, 201, station.text)
        station = station.json()
        self.login('registrar')
        session = await self.client.post('/api/scan-sessions',
            json={'mode': 'bulk', 'station_id': station['id']})
        self.assertEqual(session.status_code, 201, session.text)
        session_id = session.json()['session_id']
        snap = tor.data_snapshot
        payload = {'qr_token': tor.qr_token, 'document_number_read': tor.document_number,
            'pages': [{'sequence': 1, 'qr_present': True, 'footer': {
                'document_number': tor.document_number, 'page': 1,
                'pages': tor.page_count, 'ref': tor.data_hash[:12]}}],
            'fields': {key: {'value': snap[key], 'confidence': 98} for key in (
                'document_number', 'student_name', 'student_number',
                'program', 'date_issued', 'gwa')},
            'table': {'rows': [{'term': f"{term['semester']}, AY {term['academic_year']}",
                                **subject,
                'confidence': {'subject_code': 98, 'units': 98, 'grade': 98}}
                for term in snap['terms'] for subject in term['subjects']],
                      'term_gwas': [], 'unparsed': []}}
        image = io.BytesIO()
        Image.new('L', (40, 56), 255).save(image, format='PNG')
        for position, expected in enumerate(('VALID', 'INVALID', 'NEEDS_REVIEW'), 1):
            extracted = copy.deepcopy(payload)
            extracted['position'] = position
            if expected == 'VALID':
                extracted['pages'][0]['ocr_text'] = 'Printed paper text\nMARIA R. SANTOS'
            elif expected == 'NEEDS_REVIEW':
                extracted['pages'][0]['ocr_text'] = ''
            if expected == 'INVALID':
                extracted['fields']['student_number']['value'] = '9999-99999'
            if expected == 'NEEDS_REVIEW':
                extracted['fields']['student_number']['confidence'] = 30
            result = await self.client.post(f'/api/station/sessions/{session_id}/documents',
                headers={'X-Station-Key': station['api_key']},
                data={'payload': json.dumps(extracted)},
                files=[('pages', ('page.png', image.getvalue(), 'image/png'))])
            self.assertEqual(result.status_code, 201, result.text)
            self.assertEqual(result.json()['result'], expected, result.text)
            scan_id = result.json()['scanned_document_id']
            detail = await self.client.get(f'/api/validations/{scan_id}')
            self.assertEqual(detail.status_code, 200, detail.text)
            text = detail.json()['ocr_pages'][0]['text']
            self.assertEqual(text, extracted['pages'][0].get('ocr_text'))
            self.assertEqual(detail.json()['ocr_pages'][0]['page'], 1)

        for format in ('csv', 'pdf'):
            report = await self.client.get(
                f'/api/validations/export?format={format}&session_id={session_id}')
            self.assertEqual(report.status_code, 200, report.text[:200])
            self.assertTrue(report.content.startswith(b'%PDF-') if format == 'pdf'
                            else b'field_discrepancies' in report.content)
        actions = set(self.db.scalars(select(AuditLog.action)))
        self.assertTrue({'request_submitted', 'request_approved', 'document_generated',
                         'document_verified', 'document_released',
                         'document_downloaded'}.issubset(actions), actions)
        self.login('teacher')
        self.assertEqual((await self.client.get('/api/validations')).status_code, 403)
        self.assertEqual((await self.client.get('/api/students')).status_code, 403)

    async def test_local_demo_uses_real_renderer_and_repairs_only_known_fixture(self):
        from app.documents import pdf, service

        with patch.dict(os.environ):
            from scripts import dev_server
        original = service.render_pdf
        try:
            with patch.dict(os.environ, {'ADGVS_DEMO_FIXTURE_PDFS': 'false'}):
                dev_server.configure_renderer()
                self.assertIs(service.render_pdf, pdf.render_pdf)
            with patch.object(dev_server, 'engine', self.engine), patch.object(
                    dev_server, 'SessionLocal', return_value=self.db):
                dev_server.prepare()
                dev_server.prepare()
            fixture = json.loads((dev_server.FIXTURES / 'TOR-2026-000001.json').read_text())
            document = self.db.scalar(select(GeneratedDocument).where(
                GeneratedDocument.qr_token == fixture['qr_token']))
            self.assertTrue((Path(self.folder.name) / document.pdf_path).is_file())
            repairs = list(self.db.scalars(select(AuditLog).where(
                AuditLog.action == 'demo_document_file_restored')))
            self.assertEqual(len(repairs), 1)
        finally:
            service.render_pdf = original


if __name__ == '__main__':
    unittest.main()
