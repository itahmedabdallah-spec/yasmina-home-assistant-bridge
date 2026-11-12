import time,unittest
from unittest import mock
from bundle_catalog import MEMBERS,GROUP_ID,BULB_IDS,TV_ID,CORE_APPS,BundleError,targets
from bundle_wake_policy import wants_wake,request_deadline,ensure_awake_route
from bundle_scope_guard import authorize_request
from native_auth import OWNER

class CatalogTests(unittest.TestCase):
    def test_inventory_is_bounded_and_unique(self):
        self.assertTrue(1 <= len(MEMBERS) <= 6)
        self.assertEqual(len(MEMBERS),len(set(MEMBERS)))
        self.assertEqual(len(BULB_IDS),len(MEMBERS))
    def test_targets_preserve_catalogue_order(self):
        self.assertEqual(targets(list(reversed(MEMBERS))),MEMBERS)
    def test_known_apps(self):
        self.assertEqual(set(CORE_APPS),{'youtube','shahid','watchit','yango'})

class WakePolicyTests(unittest.TestCase):
    def test_only_explicit_on_or_app_wakes(self):
        self.assertTrue(wants_wake('on',None))
        self.assertTrue(wants_wake('open_app','youtube'))
        self.assertFalse(wants_wake('volume',20))
    def test_expired_deadline_rejected(self):
        with self.assertRaises(BundleError): request_deadline({'expires_at':time.time()-1})
    def test_non_wake_action_does_not_call_sender(self):
        sender=mock.Mock(return_value=1)
        result=ensure_awake_route('volume',10,time.time()+2,sender=sender)
        self.assertFalse(result['wake_attempted']);sender.assert_not_called()

class ScopeTests(unittest.TestCase):
    def claims(self,scope='home:lights'):
        return {'owner':OWNER,'scope':scope,'scopes':tuple(scope.split())}
    def test_lights_token_can_access_group(self):
        self.assertEqual(authorize_request(self.claims(),[GROUP_ID]),(GROUP_ID,))
    def test_lights_token_cannot_access_tv(self):
        with self.assertRaises(Exception): authorize_request(self.claims(),[TV_ID],tv_release_ready=True)
    def test_full_scope_needs_tv_release_gate(self):
        with self.assertRaises(Exception): authorize_request(self.claims('home:lights home:tv'),[TV_ID],tv_release_ready=False)
        self.assertEqual(authorize_request(self.claims('home:lights home:tv'),[TV_ID],tv_release_ready=True),(TV_ID,))

if __name__=='__main__': unittest.main()
