"""
Tests for gmail_actions.py — the only module allowed to mutate Gmail.
Uses a small fake of the googleapiclient users().labels()/messages() chain
so we can assert on label creation, coloring, and archive/undo behavior
without touching a real inbox.
Run: python -m unittest test_gmail_actions -v
"""
import unittest

import gmail_actions
from gmail_actions import (
    CATEGORY_LABELS,
    LABEL_COLORS,
    apply_label_and_route,
    ids_back_in_inbox,
    undo_route,
)


class _Execute:
    def __init__(self, result):
        self._result = result

    def execute(self, num_retries=0):
        return self._result


class FakeLabels:
    def __init__(self, store):
        self._store = store  # name -> {"id": str, "color": dict|None}

    def list(self, userId):
        labels = [{"id": v["id"], "name": name, "color": v.get("color")} for name, v in self._store.items()]
        return _Execute({"labels": labels})

    def create(self, userId, body):
        label_id = f"Label_{len(self._store) + 1}"
        self._store[body["name"]] = {"id": label_id, "color": body.get("color")}
        return _Execute({"id": label_id, "name": body["name"]})

    def patch(self, userId, id, body):
        for v in self._store.values():
            if v["id"] == id:
                v["color"] = body.get("color")
        return _Execute({"id": id})


class FakeMessages:
    def __init__(self, store):
        self._store = store  # message_id -> set of label ids

    def modify(self, userId, id, body):
        labels = self._store.setdefault(id, set())
        for l in body.get("addLabelIds", []):
            labels.add(l)
        for l in body.get("removeLabelIds", []):
            labels.discard(l)
        return _Execute({"id": id, "labelIds": sorted(labels)})

    def list(self, userId, labelIds, maxResults=500):
        matched = [mid for mid, labels in self._store.items() if all(l in labels for l in labelIds)]
        return _Execute({"messages": [{"id": m} for m in matched]})


class FakeUsers:
    def __init__(self, labels, messages):
        self._labels = labels
        self._messages = messages

    def labels(self):
        return self._labels

    def messages(self):
        return self._messages


class FakeService:
    def __init__(self):
        self.label_store = {}
        self.message_store = {}
        self._users = FakeUsers(FakeLabels(self.label_store), FakeMessages(self.message_store))

    def users(self):
        return self._users


class GmailActionsTestCase(unittest.TestCase):
    def setUp(self):
        gmail_actions._label_id_cache.clear()
        self.service = FakeService()


class TestGetOrCreateLabel(GmailActionsTestCase):
    def test_creates_label_with_category_color(self):
        apply_label_and_route(self.service, "m1", "interview", "high")
        label = self.service.label_store[CATEGORY_LABELS["interview"]]
        self.assertEqual(label["color"], LABEL_COLORS["interview"])

    def test_reuses_existing_label_id_across_calls(self):
        apply_label_and_route(self.service, "m1", "interview", "high")
        first_id = self.service.label_store[CATEGORY_LABELS["interview"]]["id"]
        apply_label_and_route(self.service, "m2", "interview", "high")
        self.assertEqual(len(self.service.label_store), 1)
        self.assertEqual(self.service.label_store[CATEGORY_LABELS["interview"]]["id"], first_id)

    def test_patches_color_when_existing_label_has_wrong_color(self):
        self.service.label_store[CATEGORY_LABELS["promo"]] = {"id": "Label_1", "color": {"backgroundColor": "#000000"}}
        apply_label_and_route(self.service, "m1", "promo", "low")
        self.assertEqual(self.service.label_store[CATEGORY_LABELS["promo"]]["color"], LABEL_COLORS["promo"])


class TestApplyLabelAndRoute(GmailActionsTestCase):
    def test_low_priority_is_archived(self):
        apply_label_and_route(self.service, "m1", "job_alert", "low")
        labels = self.service.message_store["m1"]
        self.assertNotIn("INBOX", labels)

    def test_high_priority_stays_in_inbox(self):
        self.service.message_store["m1"] = {"INBOX"}
        apply_label_and_route(self.service, "m1", "interview", "high")
        self.assertIn("INBOX", self.service.message_store["m1"])

    def test_unknown_category_falls_back_to_other_label(self):
        apply_label_and_route(self.service, "m1", "not_a_real_category", "medium")
        self.assertIn(CATEGORY_LABELS["other"], self.service.label_store)

    def test_skip_inbox_for_low_can_be_disabled(self):
        self.service.message_store["m1"] = {"INBOX"}
        apply_label_and_route(self.service, "m1", "job_alert", "low", skip_inbox_for_low=False)
        self.assertIn("INBOX", self.service.message_store["m1"])


class TestUndoRoute(GmailActionsTestCase):
    def test_undo_restores_inbox_and_removes_category_label(self):
        apply_label_and_route(self.service, "m1", "job_alert", "low")
        label_id = self.service.label_store[CATEGORY_LABELS["job_alert"]]["id"]
        self.assertIn(label_id, self.service.message_store["m1"])
        self.assertNotIn("INBOX", self.service.message_store["m1"])

        undo_route(self.service, "m1", "job_alert")

        self.assertIn("INBOX", self.service.message_store["m1"])
        self.assertNotIn(label_id, self.service.message_store["m1"])

    def test_undo_when_label_never_existed_does_not_raise(self):
        self.service.message_store["m1"] = set()
        undo_route(self.service, "m1", "job_alert")
        self.assertIn("INBOX", self.service.message_store["m1"])


class TestIdsBackInInbox(GmailActionsTestCase):
    def test_returns_message_ids_with_label_and_inbox(self):
        apply_label_and_route(self.service, "m1", "job_alert", "low")
        undo_route(self.service, "m1", "job_alert")
        apply_label_and_route(self.service, "m2", "job_alert", "low")

        # m1 was rescued (moved back to inbox and lost its label by undo_route,
        # so re-apply the category label to simulate "still labeled, back in inbox")
        label_id = gmail_actions._find_label_id(self.service, CATEGORY_LABELS["job_alert"])
        self.service.message_store["m1"].add(label_id)

        self.assertEqual(ids_back_in_inbox(self.service, "job_alert"), {"m1"})

    def test_returns_empty_set_when_label_does_not_exist(self):
        self.assertEqual(ids_back_in_inbox(self.service, "job_alert"), set())


if __name__ == "__main__":
    unittest.main()
