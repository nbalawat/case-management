package caseforge;

import java.util.Arrays;
import org.apache.flink.table.annotation.DataTypeHint;
import org.apache.flink.table.data.ArrayData;
import org.apache.flink.table.functions.ScalarFunction;
import org.apache.flink.types.Row;

/**
 * WATCH_MATCH(counterparty, names, exempt, threshold) for Flink SQL: the first listed name the counterparty is
 * alike enough to, with how alike, or NULL. All the rule is in Watchlist. This class only hands it to Flink.
 *
 *   CREATE TEMPORARY FUNCTION WATCH_MATCH AS 'caseforge.WatchMatch'
 */
public class WatchMatch extends ScalarFunction {
    private static final long serialVersionUID = 1L;

    private transient ArrayData namesAsGiven;   // Flink hands the same list over for every payment: checked by identity first
    private transient ArrayData exemptAsGiven;
    private transient String[] names;
    private transient String[] exempt;
    private transient double threshold;
    private transient Watchlist list;
    private transient String lastSeen;          // the planner may ask about the same payment more than once
    private transient Row lastAnswer;

    @DataTypeHint("ROW<name STRING, ratio DOUBLE>")
    public Row eval(
            String counterparty,
            @DataTypeHint(value = "ARRAY<STRING>", bridgedTo = ArrayData.class) ArrayData names,
            @DataTypeHint(value = "ARRAY<STRING>", bridgedTo = ArrayData.class) ArrayData exempt,
            Double threshold) {
        if (counterparty == null || names == null || threshold == null) {
            return null;
        }
        if (list == null || names != namesAsGiven || exempt != exemptAsGiven || threshold != this.threshold) {
            String[] listed = strings(names);
            String[] ruledOut = strings(exempt);
            if (list == null || threshold != this.threshold || !Arrays.equals(listed, this.names) || !Arrays.equals(ruledOut, this.exempt)) {
                this.names = listed;
                this.exempt = ruledOut;
                this.threshold = threshold;
                list = new Watchlist(listed, ruledOut, threshold);
                lastSeen = null;
            }
            namesAsGiven = names;
            exemptAsGiven = exempt;
        }
        if (counterparty.equals(lastSeen)) {
            return lastAnswer;
        }
        Watchlist.Match m = list.match(counterparty);
        lastSeen = counterparty;
        lastAnswer = m == null ? null : Row.of(m.name, m.ratio);
        return lastAnswer;
    }

    private static String[] strings(ArrayData given) {
        if (given == null) {
            return new String[0];
        }
        String[] out = new String[given.size()];
        for (int i = 0; i < out.length; i++) {
            out[i] = given.isNullAt(i) ? "" : given.getString(i).toString();
        }
        return out;
    }
}
