from django.test import TestCase
from django.utils import timezone

from apps.elttool.models import Company, ProxyAgent, User
from apps.elttool.services.proxy_agent_bootstrap import validate_and_rotate_bootstrap_token
from apps.elttool.services.proxy_agent_service import generate_bootstrap_token
class ProxyAgentBootstrapTests(TestCase):
    def setUp(self):
        now = timezone.now()
        self.company = Company.objects.create(
            cmp_name="Bootstrap Co",
            start_date=now,
            end_date=now,
        )
        self.user = User.objects.create(
            email="bootstrap@test.com",
            cmp=self.company,
        )
        raw, encrypted = generate_bootstrap_token(self.company.cmp_id)
        self.raw_token = raw
        self.agent = ProxyAgent.objects.create(
            agent_name="Test",
            agent_uid=f"agent-{self.company.cmp_id}-aabbccddeeff0011",
            cmp=self.company,
            bootstrap_token=encrypted,
            created_by=self.user,
        )

    def test_validate_and_rotate_success(self):
        ok, new_token = validate_and_rotate_bootstrap_token(
            self.agent.agent_uid,
            self.raw_token,
        )
        self.assertTrue(ok)
        self.assertTrue(new_token)
        self.assertNotEqual(new_token, self.raw_token)

        ok2, _ = validate_and_rotate_bootstrap_token(
            self.agent.agent_uid,
            self.raw_token,
        )
        self.assertFalse(ok2)

    def test_validate_rejects_wrong_token(self):
        ok, new_token = validate_and_rotate_bootstrap_token(
            self.agent.agent_uid,
            "wrong-token",
        )
        self.assertFalse(ok)
        self.assertIsNone(new_token)
