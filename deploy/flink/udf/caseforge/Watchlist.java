package caseforge;

import java.util.HashSet;
import java.util.Locale;
import java.util.Set;

/**
 * A client's watchlist as a stream job screens against it: the names in order, the pairs a person has ruled out,
 * and the client's threshold. The first listed name that is alike enough, and not ruled out, is the match. This is
 * the application's rule (sanctions_name in backend/app/detection/scenarios.py). It has no Flink in it.
 */
public final class Watchlist {
    /** A listed name and how alike the counterparty is to it. */
    public static final class Match {
        public final String name;
        public final double ratio;

        Match(String name, double ratio) {
            this.name = name;
            this.ratio = ratio;
        }
    }

    private final String[] names;
    private final NameSimilarity[] prepared;
    private final Set<String> exempt;
    private final double threshold;

    /** @param exempt each as "position of the name in names" + ":" + the counterparty name, folded */
    public Watchlist(String[] names, String[] exempt, double threshold) {
        this.names = names.clone();
        this.prepared = new NameSimilarity[names.length];
        for (int i = 0; i < names.length; i++) {
            prepared[i] = new NameSimilarity(names[i].toLowerCase(Locale.ROOT));
        }
        this.exempt = new HashSet<>();
        for (String x : exempt) {
            if (x != null && !x.isEmpty()) {
                this.exempt.add(x);
            }
        }
        this.threshold = threshold;
    }

    /** A name as exemptions are keyed: case and spacing ignored. */
    public static String folded(String name) {
        StringBuilder out = new StringBuilder();
        boolean gap = false;
        for (int c : name.toLowerCase(Locale.ROOT).codePoints().toArray()) {
            if (Character.isWhitespace(c) || Character.isSpaceChar(c)) {
                gap = out.length() > 0;
            } else {
                if (gap) {
                    out.append(' ');
                    gap = false;
                }
                out.appendCodePoint(c);
            }
        }
        return out.toString();
    }

    public Match match(String counterparty) {
        if (counterparty == null || counterparty.isEmpty()) {
            return null;
        }
        int[] seen = counterparty.toLowerCase(Locale.ROOT).codePoints().toArray();
        int[] sorted = seen.clone();
        java.util.Arrays.sort(sorted);
        String key = exempt.isEmpty() ? null : folded(counterparty);
        for (int i = 0; i < names.length; i++) {
            if (key != null && exempt.contains(i + ":" + key)) {
                continue;   // a person decided this name is not this entry
            }
            NameSimilarity listed = prepared[i];
            // Two cheap ceilings first. Neither is ever below the true ratio, so nothing that matches is skipped.
            if (listed.atMostByLength(seen) < threshold || listed.atMostByCharacters(sorted) < threshold) {
                continue;
            }
            double r = listed.ratio(seen);
            if (r >= threshold) {
                return new Match(names[i], r);
            }
        }
        return null;
    }
}
