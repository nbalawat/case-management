"""The bus on Kafka, or on anything that speaks its protocol (Redpanda, Amazon MSK, Azure Event Hubs, Confluent).

Needs the optional `kafka` dependency group. Connection security is plain client properties in KAFKA_CONFIG, so the
same code runs against a laptop broker and a managed service.
"""
from __future__ import annotations

import json
from collections.abc import Iterable

from ..config import settings
from .bus import BusError, Message

try:
    import confluent_kafka as ck
    from confluent_kafka.admin import AdminClient, NewTopic
except ImportError:  # pragma: no cover - exercised only without the optional dependency
    ck = None


def _extra() -> dict:
    if not settings.kafka_config:
        return {}
    try:
        cfg = json.loads(settings.kafka_config)
    except ValueError as e:
        raise BusError(f"KAFKA_CONFIG is not valid JSON: {e}") from None
    if not isinstance(cfg, dict):
        raise BusError("KAFKA_CONFIG must be a JSON object of client properties")
    return cfg


class KafkaBus:
    def __init__(self, url: str, partitions: int = 3, replication: int | None = None) -> None:
        if ck is None:
            raise BusError("The Kafka client is not installed. Install the 'kafka' dependency group (uv sync --group kafka).")
        self.url = url
        self._servers = url.removeprefix("kafka://").strip("/")
        if not self._servers:
            raise BusError("kafka:// needs at least one host:port")
        self._base = {"bootstrap.servers": self._servers} | _extra()
        self._partitions, self._replication = partitions, replication
        # Idempotent, all replicas acknowledge: a retry inside the client can neither duplicate nor reorder a key.
        self._producer = ck.Producer(self._base | {"enable.idempotence": True, "acks": "all", "linger.ms": 5, "compression.type": "lz4"})
        self._subs: list[KafkaSubscription] = []

    def publish(self, messages: Iterable[Message], timeout: float = 30.0) -> int:
        failed: list[str] = []
        n = 0

        def done(err, _msg) -> None:
            if err is not None:
                failed.append(str(err))

        for m in messages:
            while True:
                try:
                    self._producer.produce(m.topic, key=m.key.encode(), value=m.value, headers=[(k, v.encode()) for k, v in m.headers.items()], on_delivery=done)
                    break
                except BufferError:  # the client's send buffer is full: let it drain, then carry on
                    self._producer.poll(0.5)
            n += 1
        left = self._producer.flush(timeout)
        if left or failed:
            raise BusError(f"Kafka did not acknowledge {left + len(failed)} of {n} messages" + (f": {failed[0]}" if failed else ""))
        return n

    def subscribe(self, group: str, pattern: str) -> "KafkaSubscription":
        sub = KafkaSubscription(self._base, group, pattern)
        self._subs.append(sub)
        return sub

    def _admin(self):
        return AdminClient(self._base)

    def ensure_topics(self, names: Iterable[str]) -> None:
        admin = self._admin()
        have = set(admin.list_topics(timeout=10).topics)
        want = [n for n in names if n not in have]
        if not want:
            return
        brokers = len(admin.list_topics(timeout=10).brokers)
        rf = self._replication or min(3, brokers)
        for name, fut in admin.create_topics([NewTopic(n, num_partitions=self._partitions, replication_factor=rf) for n in want]).items():
            try:
                fut.result(15)
            except ck.KafkaException as e:
                if e.args[0].code() != ck.KafkaError.TOPIC_ALREADY_EXISTS:
                    raise BusError(f"Could not create topic {name}: {e.args[0].str()}") from None

    def topics(self) -> list[str]:
        try:
            return sorted(t for t in self._admin().list_topics(timeout=10).topics if not t.startswith("__"))
        except ck.KafkaException as e:
            raise BusError(f"Kafka at {self._servers} cannot be reached: {e.args[0].str()}") from None

    def delete_topics(self, names: Iterable[str]) -> int:
        names = list(names)
        if not names:
            return 0
        try:
            futures = self._admin().delete_topics(names, operation_timeout=30)
        except ck.KafkaException as e:
            raise BusError(f"Kafka at {self._servers} cannot be reached: {e.args[0].str()}") from None
        gone = 0
        for name, fut in futures.items():
            try:
                fut.result(30)
                gone += 1
            except ck.KafkaException as e:
                if e.args[0].code() != ck.KafkaError.UNKNOWN_TOPIC_OR_PART:
                    raise BusError(f"Could not delete topic {name}: {e.args[0].str()}") from None
        return gone

    def group_members(self, group: str) -> int | None:
        """How many consumers are in a group now, or None if the broker will not say."""
        try:
            admin = self._admin()  # held until the answer comes: a collected client never completes its futures
            desc = admin.describe_consumer_groups([group])[group].result(10)
            return len(desc.members)
        except Exception:  # noqa: BLE001  an operational nicety, never worth failing over
            return None

    def partitions(self, topic: str) -> int | None:
        try:
            meta = self._admin().list_topics(topic, timeout=10).topics.get(topic)
            return len(meta.partitions) if meta is not None and meta.error is None else None
        except ck.KafkaException:
            return None

    def close(self) -> None:
        for s in self._subs:
            s.close()
        self._producer.flush(10)


class KafkaSubscription:
    def __init__(self, base: dict, group: str, pattern: str) -> None:
        self._consumer = ck.Consumer(base | {
            "group.id": group, "enable.auto.commit": False, "auto.offset.reset": "earliest",
            "topic.metadata.refresh.interval.ms": 10_000,  # a new client's topics join within seconds
            "partition.assignment.strategy": "cooperative-sticky"})
        self._consumer.subscribe([pattern if pattern.startswith("^") else "^" + pattern])
        self._closed = False

    def poll(self, max_messages: int = 500, timeout: float = 1.0) -> list[Message]:
        out: list[Message] = []
        for m in self._consumer.consume(max_messages, timeout):
            if m.error():
                if m.error().code() in (ck.KafkaError._PARTITION_EOF, ck.KafkaError.UNKNOWN_TOPIC_OR_PART):
                    continue
                raise BusError(f"Kafka consumer error: {m.error().str()}")
            out.append(Message(m.topic(), (m.key() or b"").decode(errors="replace"), m.value() or b"",
                               {k: (v or b"").decode(errors="replace") for k, v in (m.headers() or [])}, m.partition(), m.offset()))
        return out

    def commit(self) -> None:
        try:
            self._consumer.commit(asynchronous=False)
        except ck.KafkaException as e:
            if e.args[0].code() != ck.KafkaError._NO_OFFSET:  # nothing consumed yet is not a failure
                raise BusError(f"Kafka did not accept the commit: {e.args[0].str()}") from None

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            self._consumer.close()
