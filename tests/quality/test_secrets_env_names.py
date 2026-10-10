"""Credential names written the way environments write them, and pathological input.

``_`` is a word character, so the old leading ``\\b`` never fired inside
``AWS_SECRET_ACCESS_KEY`` or ``DB_PASSWORD`` and those values were stored
verbatim. The placeholder exemption ``(?:your|my|the)\\w*`` also waved through
real passwords that merely start with "my" or "the". The connection-string
pattern's ``\\b`` matched inside long runs of scheme characters, which made it
quadratic.
"""

import time

import pytest

from visp_memory.quality.secrets import redact


@pytest.mark.parametrize(
    "text,secret",
    [
        (
            "deploy fails unless AWS_SECRET_ACCESS_KEY=wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
            "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
        ),
        ("DB_PASSWORD=Tr0ub4dor-and-3", "Tr0ub4dor-and-3"),
        ("export GITHUB_TOKEN=a1b2c3d4e5f6g7h8i9j0", "a1b2c3d4e5f6g7h8i9j0"),
        ("MYAPP_API_KEY=q8w7e6r5t4y3u2i1z", "q8w7e6r5t4y3u2i1z"),
        ("password=myS3cretPassw0rd99", "myS3cretPassw0rd99"),
        ("password=thePr0dP4ssw0rd!", "thePr0dP4ssw0rd!"),
    ],
)
def test_environment_style_assignments_are_redacted(text, secret):
    result = redact(text)

    assert "credential-assignment" in result.findings
    assert secret not in result.text


@pytest.mark.parametrize(
    "text",
    [
        "password = 'your_password_here'",
        "DB_PASSWORD=my_secret_key",
        "token = 'the_token'",
        "MYAPP_PASSWORD_FILE=/run/secrets/db",
    ],
)
def test_placeholders_and_non_credential_names_are_untouched(text):
    result = redact(text)

    assert not result.redacted, result.findings
    assert result.text == text


@pytest.mark.parametrize("unit", ["a.", "a-", "a+"])
def test_connection_string_scan_is_linear_on_long_scheme_runs(unit):
    text = unit * 100_000  # 200k characters

    started = time.perf_counter()
    result = redact(text)
    elapsed = time.perf_counter() - started

    assert result.text == text
    assert elapsed < 1.0, f"{elapsed:.2f}s"


def test_connection_string_password_is_still_redacted_after_a_scheme_run():
    result = redact("a.a.a. then postgres://admin:hunter2secret@db.internal:5432/app")

    assert "connection-string-password" in result.findings
    assert "hunter2secret" not in result.text
