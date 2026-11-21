# Apache Flink with the Kafka SQL connector the stream jobs need. Nothing else is added.
FROM flink:2.1
ADD --chown=flink:flink --checksum=sha256:11cbbcd1163c4fae0d23f9e2c55b4ff57a719a39fe96fc780001cd21a4e5ba15 \
    https://repo1.maven.org/maven2/org/apache/flink/flink-sql-connector-kafka/5.0.0-2.1/flink-sql-connector-kafka-5.0.0-2.1.jar \
    /opt/flink/lib/
