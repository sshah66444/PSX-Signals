"""
test_desk.py
Desk lane routing: topic/identity resolution, update routing, envelope formatting,
injected delivery, and the thread-aware command replies.

No network: delivery is an injected callable.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))

import desk
import telegram_notifier

BASE_ENV = {"TELEGRAM_BOT_TOKEN": "main-token", "TELEGRAM_CHAT_ID": "-100123"}


class ChannelResolutionTest(unittest.TestCase):
    def test_defaults_to_the_main_bot_and_chat(self):
        channels = desk.resolve_channels(dict(BASE_ENV))
        for role in desk.ROLES:
            self.assertEqual(channels[role]["token"], "main-token")
            self.assertEqual(channels[role]["chat_id"], "-100123")
            self.assertIsNone(channels[role]["thread_id"])
            self.assertTrue(channels[role]["enabled"])

    def test_lane_topics_and_identities_override(self):
        env = dict(BASE_ENV, PSX_TOPIC_SIGNALS="12", PSX_TOPIC_RISK=" 34 ",
                   PSX_BOT_TOKEN_EVENTS="events-token")
        channels = desk.resolve_channels(env)
        self.assertEqual(channels["signals"]["thread_id"], 12)
        self.assertEqual(channels["risk"]["thread_id"], 34)
        self.assertEqual(channels["events"]["token"], "events-token")
        self.assertEqual(channels["signals"]["token"], "main-token")
        self.assertIsNone(channels["desk"]["thread_id"])

    def test_invalid_topic_is_treated_as_no_topic(self):
        env = dict(BASE_ENV, PSX_TOPIC_DESK="general", PSX_TOPIC_SIGNALS="")
        channels = desk.resolve_channels(env)
        self.assertIsNone(channels["desk"]["thread_id"])
        self.assertIsNone(channels["signals"]["thread_id"])

    def test_missing_credentials_disables_every_lane(self):
        channels = desk.resolve_channels({})
        self.assertFalse(any(channel["enabled"] for channel in channels.values()))

    def test_explicit_token_and_chat_win_over_env(self):
        channels = desk.resolve_channels(dict(BASE_ENV), chat_id="-999", token="explicit")
        self.assertEqual(channels["desk"]["chat_id"], "-999")
        self.assertEqual(channels["desk"]["token"], "explicit")


class RoutingTest(unittest.TestCase):
    def test_lifecycle_updates_route_to_the_risk_lane(self):
        for update_type in ("STOP_HIT", "TP1_HIT", "TP2_HIT", "TRAIL_STOP", "TIME_STOP",
                            "HOLDING_CAP", "INVALIDATED", "PARTIAL_CLOSED"):
            self.assertEqual(desk.role_for_update(update_type), "risk", update_type)

    def test_event_messages_route_to_the_events_lane(self):
        self.assertEqual(desk.role_for_update("EVENT_BLOCK"), "events")
        self.assertEqual(desk.role_for_update("EVENT_WATCH"), "events")

    def test_unknown_update_falls_back_to_desk(self):
        self.assertEqual(desk.role_for_update("SOMETHING_ELSE"), "desk")
        self.assertEqual(desk.role_for_update(None), "desk")

    def test_envelope_carries_the_lane_badge(self):
        text = desk.envelope("signals", "card body", symbol="mebl")
        self.assertIn("<b>ENGINE</b>", text)
        self.assertIn("$MEBL", text)
        self.assertTrue(text.endswith("card body"))
        veto = desk.envelope("events", "blocked", symbol="SNGP", headline="ex-dividend +3d")
        self.assertIn("<b>EVENT DESK</b>", veto)
        self.assertIn("ex-dividend +3d", veto)


class DeliveryTest(unittest.TestCase):
    def setUp(self):
        self.sent = []

    def sender(self, token, chat_id, text, thread_id=None):
        self.sent.append({"token": token, "chat_id": chat_id, "text": text, "thread_id": thread_id})
        return True

    def test_post_uses_the_lane_target_and_wraps_the_text(self):
        env = dict(BASE_ENV, PSX_TOPIC_RISK="77", PSX_BOT_TOKEN_RISK="risk-token")
        ok = desk.post("risk", "STOP HIT at 96", self.sender, environ=env)
        self.assertTrue(ok)
        self.assertEqual(len(self.sent), 1)
        message = self.sent[0]
        self.assertEqual(message["token"], "risk-token")
        self.assertEqual(message["chat_id"], "-100123")
        self.assertEqual(message["thread_id"], 77)
        self.assertIn("<b>RISK</b>", message["text"])
        self.assertIn("STOP HIT at 96", message["text"])

    def test_post_skips_dry_runs_and_unconfigured_lanes(self):
        self.assertFalse(desk.post("desk", "hello", self.sender, environ=dict(BASE_ENV), dry_run=True))
        self.assertFalse(desk.post("desk", "hello", self.sender, environ={}))
        self.assertEqual(self.sent, [])

    def test_post_survives_a_sender_without_thread_support(self):
        captured = {}

        def legacy_sender(token, chat_id, text):
            captured.update(token=token, chat_id=chat_id, text=text)
            return True

        ok = desk.post("events", "veto", legacy_sender, environ=dict(BASE_ENV, PSX_TOPIC_EVENTS="5"))
        self.assertTrue(ok)
        self.assertIn("EVENT DESK", captured["text"])

    def test_describe_reports_lanes(self):
        text = desk.describe(dict(BASE_ENV, PSX_TOPIC_SIGNALS="9"))
        self.assertIn("signals", text)
        self.assertIn("topic=9", text)
        self.assertIn("topic=general", text)


class CommandThreadTest(unittest.TestCase):
    def test_command_replies_inherit_the_topic_and_reset_afterwards(self):
        seen = {}
        original = telegram_notifier._handle_interactive_command

        def spy(token, chat_id, cmd_text):
            seen["thread"] = telegram_notifier._ACTIVE_THREAD_ID

        telegram_notifier._handle_interactive_command = spy
        try:
            telegram_notifier.handle_interactive_command("t", "-100", "/events SNGP", thread_id=42)
            self.assertEqual(seen["thread"], 42)
            self.assertIsNone(telegram_notifier._ACTIVE_THREAD_ID)
        finally:
            telegram_notifier._handle_interactive_command = original

    def test_send_payload_includes_the_thread_when_given(self):
        captured = {}

        def fake_post(url, payload):
            captured.update(payload)
            return True, None

        original = telegram_notifier._post_with_retry
        telegram_notifier._post_with_retry = fake_post
        try:
            telegram_notifier.send_telegram_message("t", "-100", "hello", thread_id=7)
        finally:
            telegram_notifier._post_with_retry = original
        self.assertEqual(captured.get("message_thread_id"), 7)
        self.assertEqual(captured.get("chat_id"), "-100")


if __name__ == "__main__":
    unittest.main(verbosity=2)

