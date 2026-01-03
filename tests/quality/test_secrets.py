"""Tests for credential detection and redaction.

Two failure modes, and the false-positive one is worse. A missed secret is a security
problem the user can still notice; a wrongly redacted memory silently corrupts content
and nobody finds out. So the "must not redact" cases are as load-bearing as the rest.
"""

import pytest

from visp_memory.quality.secrets import redact, redact_memory_fields, scan


class TestDetection:
    @pytest.mark.parametrize(
        "kind,text",
        [
            ("aws-access-key-id", "export AWS_ACCESS_KEY_ID=AKIAIOSFODNN7EXAMPLE"),
            ("github-token", "token ghp_016C7cAbCdEfGhIjKlMnOpQrStUvWxYz1234 works"),
            ("anthropic-key", "ANTHROPIC_API_KEY=sk-ant-api03-abcdefghijklmnopqrstuvwxyz"),
            ("openai-key", "use sk-abcdefghijklmnopqrstuvwxyz012345"),
            # Google keys are AIza plus exactly 35 characters.
            ("google-api-key", "key AIzaSyD-1234567890abcdefghijklmnopqrstu"),
            ("slack-token", "xoxb-123456789012-abcdefghijklmnop"),
            ("stripe-key", "sk_live_abcdefghijklmnopqrstuvwx"),
            ("private-key", "-----BEGIN RSA PRIVATE KEY-----\nMIIE..."),
        ],
    )
    def test_detects_known_credential_formats(self, kind, text):
        assert kind in scan(text)

    def test_detects_jwt(self):
        jwt = (
            "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0."
            "dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U"
        )
        assert "jwt" in scan(f"Authorization header was {jwt}")

    def test_detects_password_in_connection_string(self):
        found = scan("postgres://admin:hunter2secret@db.internal:5432/app")
        assert "connection-string-password" in found

    def test_detects_bearer_token(self):
        assert "bearer-token" in scan("curl -H 'Authorization: Bearer abcdefghij0123456789xyz'")

    @pytest.mark.parametrize(
        "text",
        [
            "password = 'correct-horse-battery'",
            'api_key: "abcdefgh12345678"',
            "CLIENT_SECRET=zyxwvu9876543210abc",
        ],
    )
    def test_detects_credential_assignments(self, text):
        assert "credential-assignment" in scan(text)


class TestFalsePositives:
    """Wrongly redacting a real memory is the worse failure: it is silent."""

    @pytest.mark.parametrize(
        "text",
        [
            "The auth module reads api_key from the environment at startup.",
            "We store the password hash, never the password itself.",
            "Set OPENAI_API_KEY before running the suite.",
            "Rotate the token quarterly; see the runbook for the procedure.",
            "Migrations run offline against a maintenance replica.",
        ],
    )
    def test_prose_about_secrets_is_untouched(self, text):
        result = redact(text)
        assert not result.redacted, result.findings
        assert result.text == text

    @pytest.mark.parametrize(
        "text",
        [
            'api_key = os.getenv("OPENAI_API_KEY")',
            "password = process.env.DB_PASSWORD",
            "token: ${VAULT_TOKEN}",
            "secret = <your-secret-here>",
            "api_key = 'changeme'",
            "password = 'xxxxxxxxxxxx'",
            "token = 'REDACTED'",
        ],
    )
    def test_indirection_and_placeholders_are_untouched(self, text):
        result = redact(text)
        assert not result.redacted, f"{text!r} -> {result.findings}"

    def test_short_values_are_not_credentials(self):
        assert not redact("password = 'abc'").redacted


class TestRedaction:
    def test_replaces_secret_and_keeps_the_sentence(self):
        result = redact("deploy fails unless AWS_ACCESS_KEY_ID=AKIAIOSFODNN7EXAMPLE is set")

        assert "AKIAIOSFODNN7EXAMPLE" not in result.text
        assert "[REDACTED:aws-access-key-id]" in result.text
        # The surrounding engineering fact survives -- that is the point of redacting
        # rather than rejecting.
        assert result.text.startswith("deploy fails unless")
        assert result.text.endswith("is set")

    def test_keeps_context_around_a_captured_group(self):
        result = redact("postgres://admin:hunter2secret@db.internal:5432/app")

        assert "hunter2secret" not in result.text
        assert result.text.startswith("postgres://admin:")
        assert result.text.endswith("@db.internal:5432/app")

    def test_redacts_every_occurrence(self):
        result = redact("first AKIAIOSFODNN7EXAMPLE then AKIA1234567890ABCDEF")
        assert "AKIA" not in result.text.replace("[REDACTED:aws-access-key-id]", "")
        assert len(result.findings) == 2

    def test_clean_text_is_returned_unchanged(self):
        text = "Billing totals round half-even at two decimal places."
        result = redact(text)
        assert result.text == text
        assert not result.redacted

    def test_handles_empty_and_non_string_input(self):
        assert redact("").text == ""
        assert redact(None).text is None


class TestPayloadRedaction:
    def test_redacts_content_field(self):
        payload = {"content": "key AKIAIOSFODNN7EXAMPLE", "category": "note"}
        updated, findings = redact_memory_fields(payload)

        assert "AKIAIOSFODNN7EXAMPLE" not in updated["content"]
        assert findings == ["aws-access-key-id"]
        assert updated["category"] == "note"

    def test_leaves_clean_payload_identical(self):
        payload = {"content": "nothing sensitive", "category": "note"}
        updated, findings = redact_memory_fields(payload)

        assert updated is payload  # no copy made when nothing changed
        assert findings == []

    def test_does_not_mutate_the_caller_dict(self):
        payload = {"content": "key AKIAIOSFODNN7EXAMPLE"}
        updated, _ = redact_memory_fields(payload)

        assert payload["content"] == "key AKIAIOSFODNN7EXAMPLE"
        assert updated is not payload


class TestStorageEnforcement:
    """Redaction must be enforced at the storage boundary, not per caller.

    Ten call sites write memories (layers, capture, compression, reflection, import, the
    REST API). Enforcing at each one means the next one added silently opts out.
    """

    @pytest.fixture
    def memory(self, tmp_path):
        from visp_memory import Memory, MemoryConfig

        config = MemoryConfig()
        config.storage.data_dir = tmp_path / "data"
        config.embedding.provider = "noop"
        return Memory(config=config)

    def test_secret_never_reaches_storage(self, memory):
        memory_id = memory.record(
            "deploy broke because AWS_ACCESS_KEY_ID=AKIAIOSFODNN7EXAMPLE expired"
        )
        stored = memory._storage.get_memory(memory_id)

        assert "AKIAIOSFODNN7EXAMPLE" not in stored["content"]
        assert "[REDACTED:aws-access-key-id]" in stored["content"]
        # The engineering fact survives.
        assert "deploy broke" in stored["content"]

    def test_redaction_is_recorded_on_the_memory(self, memory):
        memory_id = memory.record("token ghp_016C7cAbCdEfGhIjKlMnOpQrStUvWxYz1234 leaked")
        flags = memory._storage.get_memory(memory_id).get("quality_flags") or []

        assert "secret_redacted" in flags
        assert "secret_redacted:github-token" in flags

    def test_clean_memories_are_not_flagged(self, memory):
        memory_id = memory.learn("Billing rounds half-even at two decimal places")
        stored = memory._storage.get_memory(memory_id)

        assert stored["content"] == "Billing rounds half-even at two decimal places"
        assert not (stored.get("quality_flags") or [])

    def test_enforced_for_every_write_path(self, memory):
        """record/learn/warn/decision all funnel through store_memory."""
        secret = "sk-ant-api03-abcdefghijklmnopqrstuvwxyz"
        ids = [
            memory.record(f"saw {secret} in logs"),
            memory.learn(f"the key is {secret}"),
            memory.warn("src/auth.py", f"hardcoded {secret} here"),
            memory.decision(f"rotate {secret}", "it leaked"),
        ]
        for memory_id in ids:
            assert secret not in memory._storage.get_memory(memory_id)["content"]
