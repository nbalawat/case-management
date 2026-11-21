"""The Temporal payload codec on its own: no server needed."""
from __future__ import annotations

import asyncio

import pytest
from temporalio.api.common.v1 import Payload
from temporalio.converter import DataConverter

from app.config import settings
from app.durable import encryption as enc

MASTER = enc.parse_master(enc.new_master())


def payload(text: str) -> Payload:
    return Payload(metadata={"encoding": b"json/plain"}, data=text.encode())


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def configured():
    keys = ("temporal_encryption", "temporal_encryption_key", "temporal_key_version", "temporal_namespace_mode")
    old = {k: getattr(settings, k) for k in keys}
    yield lambda **kv: [object.__setattr__(settings, k, v) for k, v in kv.items()]
    for k, v in old.items():
        object.__setattr__(settings, k, v)


def test_a_payload_round_trips_and_is_unreadable_on_the_wire():
    codec = enc.EncryptionCodec("caseforge-acme", enc.DerivedKeys(MASTER))
    sealed = run(codec.encode([payload('{"case":"C-1","note":"spoke to the customer"}')]))
    assert sealed[0].metadata["encoding"] == enc.ENCODING and sealed[0].metadata[enc.KEY_ID] == b"caseforge-acme/v1"
    assert b"spoke to" not in sealed[0].data and b"C-1" not in sealed[0].data
    again = run(codec.encode([payload("same")]))[0].data
    assert again != run(codec.encode([payload("same")]))[0].data  # a fresh nonce every time
    assert run(codec.decode(sealed))[0].data == b'{"case":"C-1","note":"spoke to the customer"}'


def test_one_clients_codec_refuses_another_clients_payloads_even_with_the_same_master():
    keys = enc.DerivedKeys(MASTER)
    acme, kestrel = enc.EncryptionCodec("caseforge-acme", keys), enc.EncryptionCodec("caseforge-kestrel", keys)
    assert keys.key("caseforge-acme/v1") != keys.key("caseforge-kestrel/v1")
    with pytest.raises(enc.EncryptionError, match="sealed for 'caseforge-acme'"):
        run(kestrel.decode(run(acme.encode([payload("x")]))))


def test_a_relabelled_or_altered_payload_does_not_open():
    keys = enc.DerivedKeys(MASTER)
    acme, kestrel = enc.EncryptionCodec("caseforge-acme", keys), enc.EncryptionCodec("caseforge-kestrel", keys)
    sealed = run(acme.encode([payload("x")]))[0]
    relabelled = Payload(metadata={"encoding": enc.ENCODING, enc.KEY_ID: b"caseforge-kestrel/v1"}, data=sealed.data)
    with pytest.raises(enc.EncryptionError, match="could not be opened"):
        run(kestrel.decode([relabelled]))
    flipped = Payload(metadata=dict(sealed.metadata), data=sealed.data[:-1] + bytes([sealed.data[-1] ^ 1]))
    with pytest.raises(enc.EncryptionError, match="could not be opened"):
        run(acme.decode([flipped]))
    with pytest.raises(enc.EncryptionError, match="could not be opened"):
        run(enc.EncryptionCodec("caseforge-acme", enc.DerivedKeys(enc.parse_master(enc.new_master()))).decode([sealed]))


def test_rotation_seals_with_the_new_version_and_still_opens_the_old():
    old = run(enc.EncryptionCodec("ns", enc.DerivedKeys(MASTER, 1)).encode([payload("before")]))
    v2 = enc.EncryptionCodec("ns", enc.DerivedKeys(MASTER, 2))
    new = run(v2.encode([payload("after")]))
    assert new[0].metadata[enc.KEY_ID] == b"ns/v2"
    assert [p.data for p in run(v2.decode(old + new))] == [b"before", b"after"]


def test_history_written_before_encryption_reads_as_it_was():
    codec = enc.EncryptionCodec("ns", enc.DerivedKeys(MASTER))
    assert run(codec.decode([payload("plain")]))[0].data == b"plain"


def test_settings_decide_whether_payloads_are_sealed(configured):
    configured(temporal_encryption="auto", temporal_encryption_key="")
    assert enc.provider() is None and enc.converter_for("ns") is DataConverter.default and enc.describe() == {"encrypted": False}
    configured(temporal_encryption="required")
    with pytest.raises(enc.EncryptionError, match="required"):
        enc.converter_for("ns")
    assert "required" in enc.describe()["problem"]
    configured(temporal_encryption_key="too-short")
    with pytest.raises(enc.EncryptionError, match="at least 32 bytes"):
        enc.provider()
    configured(temporal_encryption="auto", temporal_encryption_key=enc.new_master(), temporal_key_version=3, temporal_namespace_mode="per_client")
    conv = enc.converter_for("caseforge-acme")
    assert isinstance(conv.payload_codec, enc.EncryptionCodec) and conv.payload_codec.scope == "caseforge-acme"
    assert enc.describe() == {"encrypted": True, "scope": "per_client", "key_version": 3, "provider": "DerivedKeys"}
    configured(temporal_encryption="off")
    assert enc.provider() is None
    configured(temporal_encryption="sometimes")
    with pytest.raises(enc.EncryptionError, match="auto, required or off"):
        enc.provider()


def test_failure_messages_are_sealed_too(configured):
    configured(temporal_encryption="auto", temporal_encryption_key=enc.new_master())
    from temporalio.api.failure.v1 import Failure
    from temporalio.exceptions import ApplicationError

    conv = enc.converter_for("ns")
    failure = Failure()
    run(conv.encode_failure(ApplicationError("No transition from triage to closed for C-9"), failure))
    assert "C-9" not in str(failure.message) and "C-9" not in str(failure)
    back = run(conv.decode_failure(failure))
    assert "No transition from triage to closed for C-9" in str(back)


def test_a_key_id_must_name_a_scope_and_version():
    for bad in ("", "nslash", "ns/vx", "/v1"):
        with pytest.raises(enc.EncryptionError):
            enc.scope_of(bad)
    assert enc.scope_of("caseforge-a/b/v12") == "caseforge-a/b"
