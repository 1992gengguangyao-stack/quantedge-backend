import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from billing import activate_user_plan, expire_user_plan_if_needed, get_plan_limits, get_plan_usd_price


class BillingTests(unittest.TestCase):
    def test_annual_price_matches_twenty_percent_discount(self):
        self.assertEqual(get_plan_usd_price("starter", "annual"), 278.40)
        self.assertEqual(get_plan_usd_price("pro", "annual"), 758.40)
        self.assertEqual(get_plan_usd_price("expert", "annual"), 1910.40)

    def test_monthly_activation_sets_thirty_day_expiry(self):
        now = datetime(2026, 7, 16, tzinfo=timezone.utc)
        user = SimpleNamespace(plan="free", plan_expires_at=None)
        expiry = activate_user_plan(user, "starter", "monthly", now)
        self.assertEqual(user.plan, "starter")
        self.assertEqual(expiry, now + timedelta(days=30))

    def test_annual_renewal_extends_existing_same_plan(self):
        now = datetime(2026, 7, 16, tzinfo=timezone.utc)
        current_expiry = now + timedelta(days=10)
        user = SimpleNamespace(plan="pro", plan_expires_at=current_expiry)
        expiry = activate_user_plan(user, "pro", "annual", now)
        self.assertEqual(expiry, current_expiry + timedelta(days=365))

    def test_expired_plan_downgrades_to_free(self):
        now = datetime(2026, 7, 16, tzinfo=timezone.utc)
        user = SimpleNamespace(plan="expert", plan_expires_at=now - timedelta(seconds=1))
        self.assertTrue(expire_user_plan_if_needed(user, now))
        self.assertEqual(user.plan, "free")
        self.assertIsNone(user.plan_expires_at)

    def test_each_paid_plan_increases_enforced_workspace_limits(self):
        free = get_plan_limits("free")
        starter = get_plan_limits("starter")
        pro = get_plan_limits("pro")
        expert = get_plan_limits("expert")
        for key in free:
            self.assertLess(free[key], starter[key])
            self.assertLess(starter[key], pro[key])
            self.assertLess(pro[key], expert[key])

    def test_unknown_plan_falls_back_to_free_copy(self):
        limits = get_plan_limits("unknown")
        limits["saved_strategies"] = 999
        self.assertEqual(get_plan_limits("free")["saved_strategies"], 3)


if __name__ == "__main__":
    unittest.main()
