"""AWS Key Management Service: one customer-managed key per client; the key material never leaves KMS.

Each client's key-encryption key is a symmetric KMS key reached through the alias `alias/<prefix><client>` and tagged
`caseforge:client=<client>`. The application asks KMS to encrypt and decrypt data keys (at most 4 KB, which a 32-byte
data key is far under). Every request carries the encryption context `{"caseforge:client": <client>}`, and decrypt
names the client's own key, so a token wrapped for one client never opens under another's key even if it is copied.

    wrap      Encrypt under the alias. The token reads `kms:v<N>:<base64 ciphertext blob>`, where N is the key version
              at the time (1 + the key's rotations). KMS keeps every older key material, so decrypting a token works
              whatever happened since; the version is there so an operator can see what a rotation re-wrapped.
    rotate    RotateKeyOnDemand: new material for new wraps, the old material still decrypts what it wrapped. Yearly
              automatic rotation is also switched on when the key is made. AWS allows a limited number of on-demand
              rotations per key (10 at the time of writing); the error says so when the limit is reached.
    destroy   Disable the key at once (nothing can be wrapped or unwrapped from that moment) and schedule its deletion
              after KMS_DELETION_DAYS (7 to 30, AWS's own bounds). While deletion is pending the key reports destroyed
              and is not replaced; once AWS deletes it, its alias goes too.

Settings: KMS_REGION (default AWS_REGION, then us-east-1), KMS_ENDPOINT_URL (only for an emulator such as LocalStack
or moto), KMS_ALIAS_PREFIX (default caseforge-client-), KMS_DELETION_DAYS (default 7). Credentials come the way AWS
intends: the instance or pod role, or the standard AWS environment variables; none is ever configured here.

The role needs, on keys tagged caseforge:client: kms:Encrypt, kms:Decrypt, kms:DescribeKey, kms:ListKeyRotations,
kms:RotateKeyOnDemand, kms:EnableKeyRotation, kms:DisableKey, kms:ScheduleKeyDeletion; and kms:CreateKey (with the tag
required in the request), kms:TagResource, kms:CreateAlias on alias/<prefix>*. See infra/modules/aws/machine-operator.
"""
from __future__ import annotations

import base64
import binascii
import re
import time
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from .base import KeyDestroyed, KeyInfo, KeyServiceError

_TENANT = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")
_TOKEN = re.compile(r"^kms:v(\d+):([A-Za-z0-9+/=]+)$")
CONTEXT_KEY = "caseforge:client"
TAG_KEY = "caseforge:client"
DESTROYED_STATES = frozenset({"PendingDeletion", "PendingReplicaDeletion", "Disabled"})
GONE = frozenset({"NotFoundException"})
UNUSABLE = frozenset({"KMSInvalidStateException", "DisabledException"})
FOREIGN = frozenset({"InvalidCiphertextException", "IncorrectKeyException"})


def _client(region: str, endpoint_url: str):
    try:
        import boto3
        from botocore.config import Config
    except ImportError:
        raise KeyServiceError("KEY_PROVIDER=aws-kms needs the 'boto3' package. Run: uv sync --group aws") from None
    return boto3.client("kms", region_name=region, endpoint_url=endpoint_url or None,
                        config=Config(retries={"max_attempts": 5, "mode": "standard"}, connect_timeout=5, read_timeout=10))


class AwsKmsKeyProvider:
    name = "aws-kms"

    def __init__(self, region: str = "us-east-1", *, endpoint_url: str = "", alias_prefix: str = "caseforge-client-",
                 deletion_days: int = 7, client: Any = None, sleep: Callable[[float], None] = time.sleep):
        if not 7 <= int(deletion_days) <= 30:
            raise KeyServiceError("KMS_DELETION_DAYS must be between 7 and 30 (AWS's own bounds)")
        if not re.match(r"^[A-Za-z0-9/_-]{1,60}$", alias_prefix or "") or alias_prefix.startswith("aws/"):
            raise KeyServiceError(f"KMS_ALIAS_PREFIX '{alias_prefix}' is not a usable alias prefix")
        self.region, self.prefix, self.deletion_days = region, alias_prefix, int(deletion_days)
        self.kms = client or _client(region, endpoint_url)
        self._sleep = sleep

    # --- plumbing ------------------------------------------------------------------------------------------------------
    def _alias(self, tenant: str) -> str:
        if not _TENANT.match(tenant or ""):
            raise KeyServiceError(f"'{tenant}' is not a client id")
        return f"alias/{self.prefix}{tenant}"

    def _call(self, op: str, **kwargs) -> dict:
        from botocore.exceptions import BotoCoreError, ClientError

        try:
            return getattr(self.kms, op)(**kwargs)
        except ClientError as e:
            code = e.response.get("Error", {}).get("Code", "")
            raise _KmsError(code, e.response.get("Error", {}).get("Message", "") or code) from None
        except BotoCoreError as e:
            raise KeyServiceError(f"AWS KMS in {self.region} did not answer: {type(e).__name__}") from None

    def _describe(self, tenant: str) -> dict | None:
        """The client's key metadata, or None when there is no such key."""
        try:
            return self._call("describe_key", KeyId=self._alias(tenant))["KeyMetadata"]
        except _KmsError as e:
            if e.code in GONE:
                return None
            raise e.plain(f"describe {tenant}'s key") from None

    def _version(self, key_id: str) -> tuple[int, datetime | None]:
        """1 + the number of rotations, and when the latest happened."""
        count, latest, marker = 0, None, None
        while True:
            page = self._call("list_key_rotations", KeyId=key_id, **({"Marker": marker} if marker else {}))
            for r in page.get("Rotations", []):
                count += 1
                when = r.get("RotationDate")
                latest = when if latest is None or (when and when > latest) else latest
            if not page.get("Truncated"):
                return 1 + count, latest
            marker = page.get("NextMarker")

    def _usable(self, tenant: str) -> dict:
        meta = self._describe(tenant)
        if meta is None:
            raise self._missing(tenant)
        if meta.get("KeyState") in DESTROYED_STATES or not meta.get("Enabled", True):
            raise KeyDestroyed(f"{tenant}'s key is {meta.get('KeyState', 'disabled')} in AWS KMS: what it sealed cannot be opened")
        return meta

    # --- the contract --------------------------------------------------------------------------------------------------
    def create(self, tenant: str) -> KeyInfo:
        alias = self._alias(tenant)
        meta = self._describe(tenant)
        if meta is not None:
            if meta.get("KeyState") in DESTROYED_STATES:
                raise KeyDestroyed(f"{tenant}'s key was destroyed; the client is not given a new key while its deletion is pending")
            return self.info(tenant)
        try:
            made = self._call("create_key", Description=f"CaseForge key-encryption key for client {tenant}",
                              KeyUsage="ENCRYPT_DECRYPT", KeySpec="SYMMETRIC_DEFAULT",
                              Tags=[{"TagKey": TAG_KEY, "TagValue": tenant}, {"TagKey": "caseforge:purpose", "TagValue": "client-kek"}])
        except _KmsError as e:
            raise e.plain(f"create {tenant}'s key") from None
        key_id = made["KeyMetadata"]["KeyId"]
        try:
            self._call("create_alias", AliasName=alias, TargetKeyId=key_id)
        except _KmsError as e:
            self._retire(key_id)  # never leave a key nobody can reach (and that still costs money)
            if e.code == "AlreadyExistsException":  # another process made it first: theirs is the client's key
                return self.info(tenant)
            raise e.plain(f"name {tenant}'s key") from None
        try:
            self._call("enable_key_rotation", KeyId=key_id)
        except _KmsError:
            pass  # yearly rotation is good practice, not a condition of working
        return self.info(tenant)

    def wrap(self, tenant: str, data_key: bytes) -> str:
        meta = self._usable(tenant)  # a missing key is never made here: a destroyed client must not get a new one
        version, _ = self._version(meta["KeyId"])
        try:
            out = self._call("encrypt", KeyId=meta["Arn"], Plaintext=data_key, EncryptionContext={CONTEXT_KEY: tenant})
        except _KmsError as e:
            raise self._refused(tenant, e, "wrap a data key") from None
        return f"kms:v{version}:{base64.b64encode(out['CiphertextBlob']).decode()}"

    def unwrap(self, tenant: str, token: str) -> bytes:
        m = _TOKEN.match(token or "")
        if not m:
            raise KeyServiceError("Not an AWS KMS token (kms:vN:...)")
        try:
            blob = base64.b64decode(m.group(2), validate=True)
        except (binascii.Error, ValueError):
            raise KeyServiceError("The AWS KMS token is damaged") from None
        meta = self._usable(tenant)  # KMS refuses a disabled key itself; checking first holds even where it would not
        try:
            out = self._call("decrypt", KeyId=meta["Arn"], CiphertextBlob=blob, EncryptionContext={CONTEXT_KEY: tenant})
        except _KmsError as e:
            raise self._refused(tenant, e, "unwrap a data key") from None
        return out["Plaintext"]

    def rotate(self, tenant: str) -> KeyInfo:
        meta = self._usable(tenant)
        before, _ = self._version(meta["KeyId"])
        try:
            self._call("rotate_key_on_demand", KeyId=meta["KeyId"])
        except _KmsError as e:
            raise e.plain(f"rotate {tenant}'s key") from None
        for wait in (0.0, 0.5, 1.0, 2.0, 3.0, 5.0):  # KMS rotates asynchronously; it lists the rotation within seconds
            if wait:
                self._sleep(wait)
            if self._version(meta["KeyId"])[0] > before:
                break
        info = self.info(tenant)
        return info if info.version > before else KeyInfo(info.tenant, info.provider, before + 1, info.created, info.rotated, False, info.reference)

    def destroy(self, tenant: str) -> KeyInfo:
        meta = self._describe(tenant)
        if meta is not None and meta.get("KeyState") not in ("PendingDeletion", "PendingReplicaDeletion"):
            self._retire(meta["KeyId"], strict=True)
        return KeyInfo(tenant, self.name, 0, destroyed=True, reference=meta["Arn"] if meta else self._alias(tenant))

    def info(self, tenant: str) -> KeyInfo:
        meta = self._describe(tenant)
        if meta is None:
            return KeyInfo(tenant, self.name, 0, reference=self._alias(tenant))
        created = _naive(meta.get("CreationDate"))
        if meta.get("KeyState") in DESTROYED_STATES:
            return KeyInfo(tenant, self.name, 0, created, None, True, meta["Arn"])
        version, rotated = self._version(meta["KeyId"])
        return KeyInfo(tenant, self.name, version, created, _naive(rotated), False, meta["Arn"])

    # --- helpers -------------------------------------------------------------------------------------------------------
    def _retire(self, key_id: str, strict: bool = False) -> None:
        """Disable at once, then schedule deletion: the key stops working now and is gone after the waiting period."""
        try:
            self._call("disable_key", KeyId=key_id)
            self._call("schedule_key_deletion", KeyId=key_id, PendingWindowInDays=self.deletion_days)
        except _KmsError as e:
            if strict:
                raise e.plain("destroy the key") from None

    def _missing(self, tenant: str) -> KeyServiceError:
        return KeyDestroyed(f"{tenant}'s key is not in AWS KMS: it was destroyed, or never made")

    def _refused(self, tenant: str, e: "_KmsError", what: str) -> KeyServiceError:
        if e.code in GONE:
            return self._missing(tenant)
        if e.code in UNUSABLE:
            return KeyDestroyed(f"{tenant}'s key can no longer be used in AWS KMS ({e.message}): what it sealed cannot be opened")
        if e.code in FOREIGN:
            return KeyServiceError(f"That data key was not wrapped by {tenant}'s key")
        return e.plain(what)


class _KmsError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message[:200]

    def plain(self, what: str) -> KeyServiceError:
        hint = " (the machine's role is missing a KMS permission)" if self.code == "AccessDeniedException" else ""
        return KeyServiceError(f"AWS KMS refused to {what}: {self.code}: {self.message}{hint}")


def _naive(when: Any) -> datetime | None:
    if not isinstance(when, datetime):
        return None
    return when.astimezone(timezone.utc).replace(tzinfo=None) if when.tzinfo else when
