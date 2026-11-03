"""Folder drop: filename convention, routing, archiving."""

import json
import os
import tempfile
import unittest

from smartpantry.inbox import archive, parse_filename, scan_inbox


class InboxTest(unittest.TestCase):
    def test_filename_convention(self):
        self.assertEqual(parse_filename("2026-11-01_Marianos_eggs.jpg"),
                         ("2026-11-01", "Marianos"))
        self.assertEqual(parse_filename("receipt.txt"), (None, None))

    def test_scan_mixed_folder(self):
        with tempfile.TemporaryDirectory() as inbox:
            with open(os.path.join(inbox, "2026-11-01_Marianos_a.txt"),
                      "w", encoding="utf-8") as out:
                out.write("1 x Milk @ $4.29")
            with open(os.path.join(inbox, "plain.json"),
                      "w", encoding="utf-8") as out:
                json.dump([{"description": "Milk", "quantity": 1,
                            "price": 4.29, "confidence": 1.0}], out)
            with open(os.path.join(inbox, "scan.jpg"), "wb") as out:
                out.write(b"fake-image")
            with open(os.path.join(inbox, "notes.md"),
                      "w", encoding="utf-8") as out:
                out.write("not a receipt")
            os.makedirs(os.path.join(inbox, "processed"))
            result = scan_inbox(inbox, "2026-11-03", "Fallback")
        by_path = {os.path.basename(e["path"]): e
                   for e in result["entries"]}
        self.assertEqual(set(by_path), {"2026-11-01_Marianos_a.txt",
                                        "plain.json", "scan.jpg"})
        # .txt is inlined; .json/.jpg pass through as paths
        self.assertIn("Milk", by_path["2026-11-01_Marianos_a.txt"]["source"])
        self.assertTrue(
            by_path["scan.jpg"]["source"].endswith("scan.jpg"))
        # filename date/store win; flags fill the gaps
        self.assertEqual(
            (by_path["2026-11-01_Marianos_a.txt"]["date"],
             by_path["2026-11-01_Marianos_a.txt"]["store"]),
            ("2026-11-01", "Marianos"))
        self.assertEqual(by_path["plain.json"]["store"], "Fallback")
        self.assertEqual(result["skipped"], ["notes.md"])

    def test_archive_moves_files(self):
        with tempfile.TemporaryDirectory() as inbox:
            path = os.path.join(inbox, "2026-11-01_Marianos_a.txt")
            with open(path, "w", encoding="utf-8") as out:
                out.write("1 x Milk @ $4.29")
            entries = scan_inbox(inbox, "2026-11-03", "Fallback")["entries"]
            moved = archive(entries, inbox)
        self.assertEqual(len(moved), 1)
        self.assertFalse(os.path.exists(path))
        self.assertTrue(moved[0].endswith(
            os.path.join("processed", "2026-11-01_Marianos_a.txt")))

    def test_missing_inbox_raises(self):
        with self.assertRaises(NotADirectoryError):
            scan_inbox("/nonexistent-inbox", "2026-11-03", "Fallback")


if __name__ == "__main__":
    unittest.main()
