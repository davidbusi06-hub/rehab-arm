"""Run with: python -m unittest test_prescription.py"""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import prescription


class PrescriptionTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name) / "prescriptions"
        patcher = patch.object(prescription, "PRESCRIPTION_DIR", self.root)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_none_until_saved_then_round_trips(self):
        self.assertIsNone(prescription.load("p01"))
        saved = prescription.save("p01", "plan.txt", b"  Elbow flexion, 3 sets of 10.\n")
        self.assertEqual(saved["text"], "Elbow flexion, 3 sets of 10.")
        loaded = prescription.load("p01")
        self.assertEqual((loaded["filename"], loaded["text"], loaded["size_bytes"]),
                         ("plan.txt", "Elbow flexion, 3 sets of 10.", 31))
        self.assertTrue((self.root / "p01" / "prescription.txt").exists())
        self.assertIsNone(prescription.load("p02"))

    def test_a_new_upload_replaces_the_old_one_completely(self):
        prescription.save("p01", "old.txt", b"old plan")
        prescription.save("p01", "new.md", b"new plan")
        self.assertEqual(prescription.load("p01")["text"], "new plan")
        self.assertEqual(sorted(p.name for p in (self.root / "p01").iterdir()),
                         ["meta.json", "prescription.md", "text.txt"])

    def test_delete(self):
        prescription.save("p01", "plan.txt", b"plan")
        self.assertTrue(prescription.delete("p01"))
        self.assertIsNone(prescription.load("p01"))
        self.assertFalse(prescription.delete("p01"))

    def test_bad_files_are_refused(self):
        for name, data in (("plan.exe", b"x"), ("plan", b"x"), ("plan.txt", b""),
                           ("plan.txt", b"x" * (prescription.MAX_BYTES + 1))):
            with self.subTest(name=name, size=len(data)), self.assertRaises(ValueError):
                prescription.save("p01", name, data)
        self.assertIsNone(prescription.load("p01"))

    def test_patient_id_and_file_name_cannot_escape_the_folder(self):
        for bad in ("../p01", "p01/../x", "", None):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                prescription.save(bad, "plan.txt", b"x")
        saved = prescription.save("p01", "../../evil.txt", b"x")
        self.assertEqual((saved["filename"], saved["stored_as"]), ("evil.txt", "prescription.txt"))
        self.assertEqual([p.name for p in self.root.iterdir()], ["p01"])

    def test_a_pdf_that_cannot_be_read_is_stored_without_text(self):
        saved = prescription.save("p01", "scan.pdf", b"%PDF-1.4 not really a pdf")
        self.assertEqual((saved["text"], saved["text_chars"]), ("", 0))
        self.assertTrue((self.root / "p01" / "prescription.pdf").exists())

    def test_text_that_is_not_utf8_still_loads(self):
        self.assertIn("caf", prescription.save("p01", "plan.txt", b"caf\xe9 visit")["text"])


if __name__ == "__main__":
    unittest.main()
