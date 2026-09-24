"""
Tests for store.py — the SQLite layer triage.py/digest.py rely on for
dedup, the undo log, and the keep-senders learning list.
Run: python -m unittest test_store -v
"""
import os
import tempfile
import unittest

import store


class StoreTestCase(unittest.TestCase):
    def setUp(self):
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self._tmp_path = path
        self._orig_db_path = store.DB_PATH
        store.DB_PATH = path
        store.init_db()

    def tearDown(self):
        store.DB_PATH = self._orig_db_path
        os.remove(self._tmp_path)


class TestProcessedDedup(StoreTestCase):
    def test_unprocessed_message_is_not_marked(self):
        self.assertFalse(store.already_processed("m1"))

    def test_mark_processed_then_already_processed(self):
        store.mark_processed("m1", "job_alert", "low", "rules", subject="Hi", sender_domain="x.com")
        self.assertTrue(store.already_processed("m1"))

    def test_mark_processed_is_idempotent(self):
        store.mark_processed("m1", "job_alert", "low", "rules")
        store.mark_processed("m1", "newsletter", "medium", "llm")
        rows = store.get_recent_processed(hours=24)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][1], "newsletter")


class TestDigestQueries(StoreTestCase):
    def test_get_digest_items_includes_high_and_actionable_medium(self):
        store.mark_processed("m1", "interview", "high", "rules")
        store.mark_processed("m2", "updates", "medium", "llm")
        store.mark_processed("m3", "other", "medium", "llm")  # excluded: generic 'other'
        store.mark_processed("m4", "job_alert", "low", "rules")  # excluded: low priority

        ids = {row[0] for row in store.get_digest_items(hours=24)}
        self.assertEqual(ids, {"m1", "m2"})

    def test_get_digest_items_excludes_undone(self):
        store.mark_processed("m1", "interview", "high", "rules")
        store.mark_undone("m1", "interview")
        self.assertEqual(store.get_digest_items(hours=24), [])

    def test_get_top_archived_senders(self):
        store.mark_processed("m1", "job_alert", "low", "rules", sender_domain="jobs.example.com")
        store.mark_processed("m2", "job_alert", "low", "rules", sender_domain="jobs.example.com")
        store.mark_processed("m3", "newsletter", "low", "rules", sender_domain="news.example.com")

        top = store.get_top_archived_senders(days=7)
        self.assertEqual(top[0], ("jobs.example.com", 2))


class TestUndoCandidates(StoreTestCase):
    def test_only_low_priority_is_undo_candidate(self):
        store.mark_processed("m1", "job_alert", "low", "rules", sender_domain="a.com", sender_address="x@a.com")
        store.mark_processed("m2", "interview", "high", "rules", sender_domain="a.com", sender_address="y@a.com")

        candidates = {mid for mid, _cat in store.get_undo_candidates(hours=24)}
        self.assertEqual(candidates, {"m1"})

    def test_undo_candidates_filtered_by_sender(self):
        store.mark_processed("m1", "job_alert", "low", "rules", sender_domain="a.com", sender_address="x@a.com")
        store.mark_processed("m2", "job_alert", "low", "rules", sender_domain="b.com", sender_address="z@b.com")

        candidates = {mid for mid, _cat in store.get_undo_candidates(hours=24, sender="a.com")}
        self.assertEqual(candidates, {"m1"})

    def test_undone_message_is_not_a_candidate_again(self):
        store.mark_processed("m1", "job_alert", "low", "rules")
        store.mark_undone("m1", "job_alert")
        self.assertEqual(store.get_undo_candidates(hours=24), [])


class TestLLMCache(StoreTestCase):
    def test_cache_roundtrip(self):
        self.assertIsNone(store.cache_get("k1"))
        store.cache_put("k1", {"category": "promo", "priority": "low"})
        self.assertEqual(store.cache_get("k1"), {"category": "promo", "priority": "low"})


class TestContacts(StoreTestCase):
    def test_unknown_contact_returns_none(self):
        self.assertIsNone(store.contact_get("a@b.com"))

    def test_known_contact_roundtrip(self):
        store.contact_put("a@b.com", True)
        self.assertTrue(store.contact_get("a@b.com"))
        store.contact_put("a@b.com", False)
        self.assertFalse(store.contact_get("a@b.com"))

    def test_stale_contact_treated_as_unknown(self):
        store.contact_put("a@b.com", True)
        self.assertIsNone(store.contact_get("a@b.com", max_age_days=-1))


class TestKeepSenders(StoreTestCase):
    def test_add_and_check_keep_sender(self):
        self.assertFalse(store.is_keep_sender("a@b.com"))
        store.add_keep_sender("a@b.com", "rescued from archive")
        self.assertTrue(store.is_keep_sender("a@b.com"))

    def test_add_keep_sender_is_idempotent(self):
        store.add_keep_sender("a@b.com", "reason 1")
        store.add_keep_sender("a@b.com", "reason 2")
        self.assertTrue(store.is_keep_sender("a@b.com"))


if __name__ == "__main__":
    unittest.main()
