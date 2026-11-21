package caseforge;

import java.util.Arrays;
import java.util.HashMap;
import java.util.Map;

/**
 * How alike two names are, as a number from 0 to 1.
 *
 * This is the same measure the application uses (Python's difflib.SequenceMatcher(None, a, b).ratio()), ported
 * step for step so that a name scores the same in a stream job as it does in the application. The test
 * backend/tests/test_name_similarity_java.py holds the two to each other. It has no Flink in it.
 *
 * One side of the comparison (the listed name) is prepared once and compared against many payments.
 */
public final class NameSimilarity {
    private final int[] b;
    private final Map<Integer, int[]> b2j = new HashMap<>();   // where each character of the listed name occurs
    private final int[] sorted;                                // the listed name's characters in order

    public NameSimilarity(String listed) {
        b = listed.codePoints().toArray();
        sorted = b.clone();
        Arrays.sort(sorted);
        Map<Integer, Integer> counts = new HashMap<>();
        for (int c : b) {
            counts.merge(c, 1, Integer::sum);
        }
        Map<Integer, Integer> filled = new HashMap<>();
        for (int j = 0; j < b.length; j++) {
            int[] at = b2j.computeIfAbsent(b[j], c -> new int[counts.get(c)]);
            at[filled.merge(b[j], 1, Integer::sum) - 1] = j;
        }
        // difflib sets aside characters that make up more than 1% of a long text. Names are rarely this long.
        if (b.length >= 200) {
            int most = b.length / 100 + 1;
            b2j.values().removeIf(at -> at.length > most);
        }
    }

    public int length() {
        return b.length;
    }

    /** The most the ratio could be, from the lengths alone. Never below the true ratio. */
    public double atMostByLength(int[] a) {
        int total = a.length + b.length;
        return total == 0 ? 1.0 : 2.0 * Math.min(a.length, b.length) / total;
    }

    /**
     * The most the ratio could be, from the characters the two share. Never below the true ratio.
     *
     * @param sortedA the other name's characters in order, so one pass counts what the two have in common
     */
    public double atMostByCharacters(int[] sortedA) {
        int total = sortedA.length + b.length;
        if (total == 0) {
            return 1.0;
        }
        int shared = 0;
        int i = 0;
        int j = 0;
        while (i < sortedA.length && j < sorted.length) {
            if (sortedA[i] == sorted[j]) {
                shared++;
                i++;
                j++;
            } else if (sortedA[i] < sorted[j]) {
                i++;
            } else {
                j++;
            }
        }
        return 2.0 * shared / total;
    }

    public double ratio(int[] a) {
        int total = a.length + b.length;
        if (total == 0) {
            return 1.0;
        }
        return 2.0 * matches(a, 0, a.length, 0, b.length) / total;
    }

    public static double ratio(String a, String listed) {
        return new NameSimilarity(listed).ratio(a.codePoints().toArray());
    }

    /** Characters in the longest shared run, plus the same on each side of it. */
    private int matches(int[] a, int alo, int ahi, int blo, int bhi) {
        int besti = alo;
        int bestj = blo;
        int best = 0;
        int[] runs = new int[b.length + 1];      // runs[j + 1]: length of the shared run ending at a[i - 1], b[j]
        int[] next = new int[b.length + 1];
        for (int i = alo; i < ahi; i++) {
            Arrays.fill(next, 0);
            int[] at = b2j.get(a[i]);
            if (at != null) {
                for (int j : at) {
                    if (j < blo) {
                        continue;
                    }
                    if (j >= bhi) {
                        break;
                    }
                    int k = runs[j] + 1;
                    next[j + 1] = k;
                    if (k > best) {
                        besti = i - k + 1;
                        bestj = j - k + 1;
                        best = k;
                    }
                }
            }
            int[] swap = runs;
            runs = next;
            next = swap;
        }
        // Characters set aside above still count when they sit beside a shared run.
        while (besti > alo && bestj > blo && a[besti - 1] == b[bestj - 1]) {
            besti--;
            bestj--;
            best++;
        }
        while (besti + best < ahi && bestj + best < bhi && a[besti + best] == b[bestj + best]) {
            best++;
        }
        if (best == 0) {
            return 0;
        }
        int sum = best;
        if (alo < besti && blo < bestj) {
            sum += matches(a, alo, besti, blo, bestj);
        }
        if (besti + best < ahi && bestj + best < bhi) {
            sum += matches(a, besti + best, ahi, bestj + best, bhi);
        }
        return sum;
    }
}
