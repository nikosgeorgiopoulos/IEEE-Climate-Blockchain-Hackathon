import os
import stat

from proofofair import crypto


def test_key_created_with_mode_600_and_reloaded(isolated_env):
    sk1 = crypto.load_or_create_key("gateway")
    path = crypto.key_path("gateway")
    assert path.exists()
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    sk2 = crypto.load_or_create_key("gateway")
    assert sk1.encode() == sk2.encode()


def test_sign_and_verify():
    sk = crypto.load_or_create_key("sim-01")
    msg = b'{"a":"1.00"}'
    sig = crypto.sign(sk, msg)
    assert len(sig) == 128
    assert crypto.verify(crypto.pubkey_hex(sk), msg, sig)


def test_modified_message_fails():
    sk = crypto.load_or_create_key("sim-02")
    sig = crypto.sign(sk, b'{"pm25":"40.00"}')
    assert not crypto.verify(crypto.pubkey_hex(sk), b'{"pm25":"12.00"}', sig)


def test_wrong_key_fails():
    a = crypto.load_or_create_key("a")
    b = crypto.load_or_create_key("b")
    sig = crypto.sign(a, b"hello")
    assert not crypto.verify(crypto.pubkey_hex(b), b"hello", sig)


def test_verify_never_raises_on_garbage():
    assert crypto.verify("zz", b"m", "00") is False
    assert crypto.verify("", b"m", "") is False
    assert crypto.verify(None, b"m", None) is False  # type: ignore[arg-type]


def test_invalid_key_name_rejected():
    import pytest

    with pytest.raises(ValueError):
        crypto.key_path("../etc/passwd")
