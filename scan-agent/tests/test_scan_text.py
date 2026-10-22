"""Full-page OCR is retained even when no issued-document reference matches."""
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import cv2
import numpy as np

from scan_agent.capture.filedrop import _read
from scan_agent.pipeline import MAX_PAGE_TEXT, PageInfo, extract_document, page_transcription
from scan_agent.processing.ocr import ocr_page_text


class ScannedTextTests(unittest.TestCase):
    def test_unmatched_multipage_document_retains_text_in_page_order(self):
        pages = [PageInfo(sequence=n, image=np.zeros((40, 30), dtype=np.uint8),
                          skew=0, qr_payload=None, footer=None) for n in (8, 9)]
        lookup = Mock()
        with patch('scan_agent.pipeline.ocr_page_text',
                   side_effect=['FIRST PAGE\nMaria', 'SECOND PAGE']):
            result = extract_document(pages, 1, lookup)
        self.assertEqual([p['ocr_text'] for p in result.payload['pages']],
                         ['FIRST PAGE\nMaria', 'SECOND PAGE'])
        self.assertEqual(result.payload['fields'], {})
        lookup.assert_not_called()

    def test_display_ocr_failure_is_explicit_and_text_is_bounded(self):
        image = np.zeros((40, 30), dtype=np.uint8)
        with patch('scan_agent.pipeline.ocr_page_text', side_effect=RuntimeError('timeout')):
            result = page_transcription(image)
        self.assertIsNone(result['ocr_text'])
        self.assertIn('could not finish', result['ocr_text_error'])
        with patch('scan_agent.pipeline.ocr_page_text', return_value='a' * (MAX_PAGE_TEXT + 1)):
            result = page_transcription(image)
        self.assertEqual(len(result['ocr_text']), MAX_PAGE_TEXT)
        self.assertTrue(result['ocr_text_truncated'])

    def test_real_tor_and_coe_transcription_reads_student_name(self):
        fixtures = Path(__file__).parent / 'fixtures'
        for kind in ('TOR', 'COE'):
            with self.subTest(document_type=kind):
                image = next(_read(fixtures / f'{kind}-2026-000001.pdf'))
                text = ocr_page_text(image)
                self.assertIn('SANTOS', text.upper())
                self.assertIn('2025-00001', text)

    def test_transcription_uses_original_camera_dimensions(self):
        original = np.zeros((480, 640), dtype=np.uint8)
        normalized = np.zeros((842, 595), dtype=np.uint8)
        page = PageInfo(sequence=1, image=normalized, skew=0,
                        qr_payload=None, footer=None, source_image=original)
        with patch('scan_agent.pipeline.ocr_page_text', return_value='Camera text') as reader:
            extract_document([page], 1, Mock())
        self.assertIs(reader.call_args.args[0], original)

    def test_sparse_fallback_accepts_confident_text_but_rejects_noise(self):
        image = np.zeros((60, 80), dtype=np.uint8)
        data = {'text': ['Customer', 'Delivery', 'Method'], 'conf': [92, 94, 90],
                'block_num': [1, 2, 2], 'par_num': [1, 1, 1], 'line_num': [1, 1, 1]}
        with patch('scan_agent.processing.ocr.pytesseract.image_to_string', return_value=''), \
             patch('scan_agent.processing.ocr.pytesseract.image_to_data', return_value=data):
            self.assertEqual(ocr_page_text(image), 'Customer\nDelivery Method')
            data['conf'] = [35, 25, 40]
            self.assertEqual(ocr_page_text(image), '')

    def test_short_word_is_not_discarded_by_character_count(self):
        image = np.zeros((60, 80), dtype=np.uint8)
        data = {'text': ['LTE'], 'conf': [92], 'block_num': [1],
                'par_num': [1], 'line_num': [1]}
        with patch('scan_agent.processing.ocr.pytesseract.image_to_string', return_value=''), \
             patch('scan_agent.processing.ocr.pytesseract.image_to_data', return_value=data):
            self.assertEqual(ocr_page_text(image), 'LTE')

    def test_real_large_white_text_on_tilted_dark_background(self):
        image = np.full((1000, 1400), 240, dtype=np.uint8)
        cv2.rectangle(image, (180, 100), (1200, 900), 45, -1)
        for line, y in [('LTE', 300), ('POCKET', 520), ('WIFI', 740)]:
            cv2.putText(image, line, (280, y), cv2.FONT_HERSHEY_SIMPLEX,
                        4, 235, 9, cv2.LINE_AA)
        matrix = cv2.getRotationMatrix2D((700, 500), 13, 1)
        tilted = cv2.warpAffine(image, matrix, (1400, 1000), borderValue=240)
        text = ocr_page_text(tilted).upper()
        for word in ('LTE', 'POCKET', 'WIFI'):
            self.assertIn(word, text)


if __name__ == '__main__':
    unittest.main()
