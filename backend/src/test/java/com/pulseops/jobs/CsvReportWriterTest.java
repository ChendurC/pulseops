package com.pulseops.jobs;

import org.junit.jupiter.api.Test;

import java.math.BigDecimal;
import java.nio.charset.StandardCharsets;
import java.time.LocalDate;
import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

class CsvReportWriterTest {

    @Test
    void neutralisesFormulaInjection() {
        // =HYPERLINK("x") -> prefixed with ' and, because it contains quotes, CSV-quoted
        assertEquals("\"'=HYPERLINK(\"\"x\"\")\"", CsvReportWriter.cell("=HYPERLINK(\"x\")"));
        assertEquals("'+cmd", CsvReportWriter.cell("+cmd"));
        assertEquals("'-1+2", CsvReportWriter.cell("-1+2"));
        assertEquals("'@SUM(A1)", CsvReportWriter.cell("@SUM(A1)"));
        assertEquals("P-100", CsvReportWriter.cell("P-100"));
    }

    @Test
    void quotesCellsContainingCommasOrQuotes() {
        assertEquals("\"A,B\"", CsvReportWriter.cell("A,B"));
        assertEquals("\"say \"\"hi\"\"\"", CsvReportWriter.cell("say \"hi\""));
    }

    @Test
    void writesSummaryGroupsAndDays() {
        ReportData r = new ReportData(2, 5, new BigDecimal("60.07"), 1,
                List.of(new ReportData.Group("P-100", 1, 3, new BigDecimal("59.97"))),
                List.of(new ReportData.Group("GB", 2, 5, new BigDecimal("60.07"))),
                List.of(new ReportData.Daily(LocalDate.of(2026, 10, 1), 2, 5,
                        new BigDecimal("60.07"))));
        String csv = new String(CsvReportWriter.write(7, r), StandardCharsets.UTF_8);
        assertTrue(csv.startsWith("section,key,transactions,units,sales_value\n"));
        assertTrue(csv.contains("SUMMARY,TOTAL,2,5,60.07\n"));
        assertTrue(csv.contains("SUMMARY,INVALID_RECORDS,1,,\n"));
        assertTrue(csv.contains("PRODUCT,P-100,1,3,59.97\n"));
        assertTrue(csv.contains("COUNTRY,GB,2,5,60.07\n"));
        assertTrue(csv.contains("DAY,2026-10-01,2,5,60.07\n"));
    }
}
