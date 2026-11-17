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

    def test_desk_chat_id_targets_the_group_separately(self):
        env = dict(BASE_ENV, PSX_DESK_CHAT_ID="-100555")
        channels = desk.resolve_channels(env)
        self.assertEqual(channels["desk"]["chat_id"], "-100555")
        self.assertEqual(channels["risk"]["chat_id"], "-100555")

    def test_comma_separated_chat_list_routes_to_the_first_entry(self):
        env = dict(BASE_ENV, TELEGRAM_CHAT_ID="1971314850,-100555")
        channels = desk.resolve_channels(env)
        self.assertEqual(channels["signals"]["chat_id"], "1971314850")

    def test_desk_chat_id_beats_a_broadcast_list(self):
        env = dict(BASE_ENV, TELEGRAM_CHAT_ID="1971314850,-100555", PSX_DESK_CHAT_ID="-100555")
        channels = desk.resolve_channels(env)
        self.assertEqual(channels["desk"]["chat_id"], "-100555")


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


UPDATES_FIXTURE = {
    "ok": True,
    "result": [
        {"update_id": 1, "message": {
            "message_id": 5, "message_thread_id": 3,
            "chat": {"id": -1002345678901, "type": "supergroup", "title": "PSX Desk", "is_forum": True},
            "text": "/start@my_psx_trade_signals_bot"}},
        {"update_id": 2, "message": {
            "message_id": 6, "message_thread_id": 4,
            "chat": {"id": -1002345678901, "type": "supergroup", "title": "PSX Desk", "is_forum": True},
            "text": "/start@my_psx_trade_signals_bot"}},
        {"update_id": 3, "message": {
            "message_id": 7,
            "chat": {"id": 888888, "type": "private", "first_name": "Syed"},
            "text": "/start"}},
    ],
}


class DeskIdExtractionTest(unittest.TestCase):
    def test_extracts_chat_and_topic_ids(self):
        chats = desk.extract_desk_ids(UPDATES_FIXTURE)
        self.assertEqual(set(chats), {"-1002345678901", "888888"})
        forum = chats["-1002345678901"]
        self.assertTrue(forum["is_forum"])
        self.assertEqual(forum["title"], "PSX Desk")
        self.assertEqual(sorted(forum["topics"]), [3, 4])
        self.assertEqual(chats["888888"]["topics"], {})

    def test_topic_creation_messages_supply_labels(self):
        payload = {"result": [{"message": {
            "chat": {"id": -100, "type": "supergroup", "title": "Desk", "is_forum": True},
            "message_thread_id": 9,
            "forum_topic_created": {"name": "Signals"},
        }}]}
        chats = desk.extract_desk_ids(payload)
        self.assertEqual(chats["-100"]["topics"][9], "Signals")

    def test_env_block_maps_recognisable_topic_names(self):
        chat = {"chat_id": "-100",
                "topics": {2: "Desk", 3: "Signals", 4: "Event desk", 5: "Risk", 6: "Chit chat"}}
        block = desk.suggest_env_block(chat)
        self.assertIn("TELEGRAM_CHAT_ID=-100", block)
        self.assertIn("PSX_TOPIC_DESK=2", block)
        self.assertIn("PSX_TOPIC_SIGNALS=3", block)
        self.assertIn("PSX_TOPIC_EVENTS=4", block)
        self.assertIn("PSX_TOPIC_RISK=5", block)
        self.assertIn("assign manually", block)
        self.assertIn("#   PSX_TOPIC_???=6", block)

    def test_report_and_empty_state(self):
        report = desk.format_desk_ids(desk.extract_desk_ids(UPDATES_FIXTURE))
        self.assertIn("PSX Desk", report)
        self.assertIn("topic 3", report)
        self.assertIn("privacy mode", desk.format_desk_ids({}))

    def test_commands_identify_lanes_when_topic_names_are_invisible(self):
        chat = {"chat_id": "-100", "topics": {
            5: "/start@my_psx_trade_signals_bot", 6: "/active", 7: "/events SNGP", 8: "/performance"}}
        block = desk.suggest_env_block(chat)
        self.assertIn("PSX_TOPIC_DESK=5", block)
        self.assertIn("PSX_TOPIC_SIGNALS=6", block)
        self.assertIn("PSX_TOPIC_EVENTS=7", block)
        self.assertIn("PSX_TOPIC_RISK=8", block)

    def test_membership_updates_supply_the_chat_before_any_message(self):
        payload = {"result": [{"update_id": 1, "my_chat_member": {
            "chat": {"id": -1003984606400, "type": "supergroup", "title": "PSX Desk", "is_forum": True},
            "new_chat_member": {"status": "administrator"},
        }}]}
        chats = desk.extract_desk_ids(payload)
        self.assertIn("-1003984606400", chats)
        self.assertEqual(chats["-1003984606400"]["title"], "PSX Desk")


if __name__ == "__main__":
    unittest.main(verbosity=2)
