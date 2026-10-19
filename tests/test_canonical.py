import json

import pytest
from nacl.signing import SigningKey

from proofofair.canonical import canonicalize, fmt_value, normalize
from proofofair.crypto import sign, verify

SPEC_EXAMPLE = (
    b'{"device_id":"sc-18302","humidity":null,"pm10":"4.47","pm25":"2.78",'
    b'"temperature":null,"ts":"2026-10-19T11:01:48Z","v":1}'
)

# Shared with firmware/esp32_node/README.md — the ESP32 must produce these exact bytes.
ESP32_SEED_HEX = "9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60"
ESP32_VECTOR = {
    "args": ("esp32-01", "2026-10-19T11:05:00Z", 21.4, 12.35, 23.5, 61.0),
    "canonical": (
        b'{"device_id":"esp32-01","humidity":"61.00","pm10":"21.40","pm25":"12.35",'
        b'"temperature":"23.50","ts":"2026-10-19T11:05:00Z","v":1}'
    ),
}


def test_spec_example_bytes():
    assert canonicalize("sc-18302", "2026-10-19T11:01:48Z", 4.47, 2.78, None, None) == SPEC_EXAMPLE


def test_sorted_keys_no_spaces_and_version():
    out = canonicalize("sim-01", "2026-10-19T00:00:00Z", 1, 2, 3, 4)
    assert b" " not in out
    obj = json.loads(out)
    assert list(obj) == sorted(obj)
    assert obj["v"] == 1


def test_two_decimals_and_null():
    assert fmt_value(1) == "1.00"
    assert fmt_value("4.4") == "4.40"
    assert fmt_value(4.005) == f"{4.005:.2f}"
    assert fmt_value(None) is None
    assert fmt_value(-0.001) == "0.00"
    assert normalize(12.3456) == 12.35


def test_non_finite_rejected():
    with pytest.raises(ValueError):
        fmt_value(float("nan"))


def test_strings_and_floats_give_same_bytes():
    a = canonicalize("d", "2026-10-19T11:05:00Z", "21.40", "12.35", None, "61")
    b = canonicalize("d", "2026-10-19T11:05:00Z", 21.4, 12.35, None, 61.0)
    assert a == b


def test_esp32_test_vector():
    assert canonicalize(*ESP32_VECTOR["args"]) == ESP32_VECTOR["canonical"]
    sk = SigningKey(bytes.fromhex(ESP32_SEED_HEX))
    sig = sign(sk, ESP32_VECTOR["canonical"])
    assert verify(sk.verify_key.encode().hex(), ESP32_VECTOR["canonical"], sig)
    # Ed25519 is deterministic: this is the signature the firmware README lists.
    assert sig == sign(sk, ESP32_VECTOR["canonical"])
