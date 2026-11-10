package com.pulseops.jobs;

import java.math.BigDecimal;
import java.nio.charset.StandardCharsets;

/**
 * Turns a report into CSV bytes. Every text cell is protected against
 * CSV/formula injection: a value like =HYPERLINK(...) would otherwise run in Excel.
 */
public final class CsvReportWriter {

    private CsvReportWriter() { }

    public static byte[] write(long jobId, ReportData r) {
        StringBuilder sb = new StringBuilder();
        sb.append("section,key,transactions,units,sales_value\n");
        row(sb, "SUMMARY", "TOTAL", r.transactionCount(), r.totalUnits(), r.totalSales());
        sb.append("SUMMARY,INVALID_RECORDS,").append(r.invalidRecords()).append(",,\n");
        for (ReportData.Group g : r.byProduct()) {
            row(sb, "PRODUCT", g.key(), g.transactions(), g.units(), g.sales());
        }
        for (ReportData.Group g : r.byCountry()) {
            row(sb, "COUNTRY", g.key(), g.transactions(), g.units(), g.sales());
        }
        for (ReportData.Daily d : r.byDay()) {
            row(sb, "DAY", d.day().toString(), d.transactions(), d.units(), d.sales());
        }
        return sb.toString().getBytes(StandardCharsets.UTF_8);
    }

    private static void row(StringBuilder sb, String section, String key, long t, long u,
                            BigDecimal sales) {
        sb.append(section).append(',').append(cell(key)).append(',')
          .append(t).append(',').append(u).append(',').append(sales.toPlainString()).append('\n');
    }

    /** Neutralise formula prefixes, then quote if needed. */
    static String cell(String value) {
        if (value == null) {
            return "";
        }
        String v = value;
        if (!v.isEmpty() && "=+-@\t\r".indexOf(v.charAt(0)) >= 0) {
            v = "'" + v;
        }
        if (v.contains(",") || v.contains("\"") || v.contains("\n") || v.contains("\r")) {
            v = "\"" + v.replace("\"", "\"\"") + "\"";
        }
        return v;
    }
}
