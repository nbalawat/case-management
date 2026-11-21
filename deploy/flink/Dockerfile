# Apache Flink with the two things the stream jobs need: the Kafka SQL connector, and the name-matching function
# the name screening job calls (udf/, about three hundred lines of Java with no dependencies beyond Flink itself).
FROM flink:2.1 AS flink

# The function is compiled against the Flink it will run in. The official image has no compiler, so a JDK image does it.
FROM eclipse-temurin:17-jdk AS udf
COPY --from=flink /opt/flink/lib/ /flink-lib/
COPY udf/ /src/
RUN mkdir /classes \
 && javac --release 17 -proc:none -Xlint:all -Werror -cp "/flink-lib/*" -d /classes /src/caseforge/*.java \
 && jar cf /caseforge-udf.jar -C /classes .

FROM flink
ADD --chown=flink:flink --chmod=644 --checksum=sha256:11cbbcd1163c4fae0d23f9e2c55b4ff57a719a39fe96fc780001cd21a4e5ba15 \
    https://repo1.maven.org/maven2/org/apache/flink/flink-sql-connector-kafka/5.0.0-2.1/flink-sql-connector-kafka-5.0.0-2.1.jar \
    /opt/flink/lib/
COPY --from=udf --chown=flink:flink /caseforge-udf.jar /opt/flink/lib/
