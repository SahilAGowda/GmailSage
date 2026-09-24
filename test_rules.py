"""
Rule regression tests built from real subjects in triage.db.
Run: python -m unittest test_rules -v
"""
import unittest

from rules_engine import apply_guard, classify_by_rules, domain_in, extract_address, is_keep_address


def rules(sender, subject, unsub=True, **kw):
    return classify_by_rules(sender, subject, unsub, **kw)


class TestDomains(unittest.TestCase):
    def test_subdomain_matches_parent(self):
        self.assertTrue(domain_in("match.indeed.com", {"indeed.com"}))
        self.assertTrue(domain_in("noreply44.jobs2web.com", {"jobs2web.com"}))
        self.assertFalse(domain_in("notindeed.com", {"indeed.com"}))
        self.assertFalse(domain_in("com", {"indeed.com"}))

    def test_extract_address(self):
        self.assertEqual(extract_address("LinkedIn <Jobs-Noreply@LinkedIn.com>"), "jobs-noreply@linkedin.com")


class TestNeverArchive(unittest.TestCase):
    def test_google_account_notice_not_archived(self):
        r = rules("Google <no-reply@accounts.google.com>", "You shared some Google Account data with Claude")
        self.assertNotEqual(r, ("newsletter", "low"))

    def test_guard_bumps_llm_low(self):
        self.assertEqual(apply_guard("promo", "low", "Claude subscription suspended due to payment issue"),
                         ("updates", "medium"))
        self.assertEqual(apply_guard("other", "low", "Your single-use code"), ("updates", "medium"))

    def test_guard_leaves_normal_promo(self):
        self.assertEqual(apply_guard("promo", "low", "Apple Watch Series 12 and Ultra 4 are here."), ("promo", "low"))

    def test_guard_known_contact(self):
        self.assertEqual(apply_guard("newsletter", "low", "hello", known_contact=True), ("newsletter", "medium"))

    def test_offer_letter_not_promo(self):
        self.assertEqual(rules("HR <hr@acme.com>", "Your offer letter from Acme"), ("application", "high"))

    def test_linkedin_split_by_sender(self):
        self.assertEqual(rules("LinkedIn <jobalerts-noreply@linkedin.com>", "Visa is hiring a Software Engineer"),
                         ("job_alert", "low"))
        self.assertEqual(rules("LinkedIn <invitations@linkedin.com>", "I want to connect"), ("social", "low"))
        # InMail / security mail: no rule archives it; keep list + guard keep it in the inbox
        self.assertTrue(is_keep_address("messages-noreply@linkedin.com"))
        self.assertEqual(apply_guard("newsletter", "low", "Sahil A, I'm still waiting for your response",
                                     known_contact=True), ("newsletter", "medium"))
        self.assertEqual(apply_guard("newsletter", "low", "Sahil A, here's your PIN 226575"), ("updates", "medium"))


class TestJobHunt(unittest.TestCase):
    def test_application_status_beats_ats_domain(self):
        self.assertEqual(rules("Deloitte <noreply44@noreply44.jobs2web.com>", "Your application for Analyst"),
                         ("application", "high"))

    def test_job_alert_via_subdomain(self):
        self.assertEqual(rules("Deloitte <x@noreply44.jobs2web.com>", "New jobs posted from southasiacareers.deloitte.com"),
                         ("job_alert", "low"))
        self.assertEqual(rules("Indeed <x@match.indeed.com>", "Java Full stack (Angular) @ Capgemini"),
                         ("job_alert", "low"))

    def test_recruiter_is_hiring_goes_to_llm(self):
        # 1:1 mail, no bulk headers: don't archive on subject alone
        self.assertIsNone(rules("Asha <asha@startup.io>", "Startup is hiring - quick chat?", unsub=False))

    def test_bulk_is_hiring_archived(self):
        self.assertEqual(rules("Unstop <x@mailer.unstop.com>", "IndiGo is hiring for Airport Operations"),
                         ("job_alert", "low"))

    def test_known_contact_personal(self):
        self.assertEqual(rules("Asha <asha@startup.io>", "Re: catch up", unsub=False, known_contact=True),
                         ("personal", "high"))


class TestCategories(unittest.TestCase):
    def test_learning_and_finance_reachable(self):
        self.assertEqual(rules("Udemy <x@students.udemy.com>", "Last chance to save on a year of learning."),
                         ("learning", "low"))
        self.assertEqual(rules("Groww <x@digest.groww.in>", "US Fed hikes rates - Groww Digest"), ("finance", "low"))

    def test_gmail_category_signal(self):
        self.assertEqual(rules("Shop <x@shop.example>", "Big news", unsub=False, gmail_labels=["CATEGORY_PROMOTIONS"]),
                         ("promo", "low"))

    def test_unknown_non_bulk_goes_to_llm(self):
        self.assertIsNone(rules("Vercel <x@vercel.com>", "1 new project available to import", unsub=False))


if __name__ == "__main__":
    unittest.main()
