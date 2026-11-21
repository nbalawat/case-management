package caseforge;

import java.io.BufferedReader;
import java.io.InputStreamReader;
import java.io.PrintStream;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.List;

/**
 * For the tests only: answers on standard output what the classes above make of lines on standard input, so the
 * application's own test suite can hold this code to the Python it was ported from. Fields are tab-separated.
 *
 *   ratio   a  b                    prints the ratio
 *   fold    name                    prints the folded name
 *   list    threshold  name...      starts a new list
 *   exempt  position:folded...      rules pairs out of the list (starts the list again with them)
 *   match   counterparty            prints the matched name, a tab and the ratio, or a single dash
 */
public final class Check {
    public static void main(String[] args) throws Exception {
        BufferedReader in = new BufferedReader(new InputStreamReader(System.in, StandardCharsets.UTF_8));
        PrintStream out = new PrintStream(System.out, false, StandardCharsets.UTF_8);
        String[] names = new String[0];
        double threshold = 1.0;
        Watchlist list = null;
        for (String line = in.readLine(); line != null; line = in.readLine()) {
            String[] f = line.split("\t", -1);
            switch (f[0]) {
                case "ratio":
                    out.println(Double.toString(NameSimilarity.ratio(f[1], f[2])));
                    break;
                case "fold":
                    out.println(Watchlist.folded(f[1]));
                    break;
                case "list": {
                    threshold = Double.parseDouble(f[1]);
                    List<String> all = new ArrayList<>();
                    for (int i = 2; i < f.length; i++) {
                        all.add(f[i]);
                    }
                    names = all.toArray(new String[0]);
                    list = new Watchlist(names, new String[0], threshold);
                    break;
                }
                case "exempt": {
                    String[] pairs = new String[f.length - 1];
                    System.arraycopy(f, 1, pairs, 0, pairs.length);
                    list = new Watchlist(names, pairs, threshold);
                    break;
                }
                case "match": {
                    Watchlist.Match m = list.match(f[1]);
                    out.println(m == null ? "-" : m.name + "\t" + m.ratio);
                    break;
                }
                default:
                    throw new IllegalArgumentException("Unknown line: " + f[0]);
            }
        }
        out.flush();
    }
}
