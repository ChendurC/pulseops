package com.pulseops.jobs;

import java.math.BigDecimal;
import java.time.LocalDate;
import java.util.List;

/** Aggregated report figures for one job, calculated by PostgreSQL from validated rows. */
public record ReportData(
        long transactionCount,
        long totalUnits,
        BigDecimal totalSales,
        int invalidRecords,
        List<Group> byProduct,
        List<Group> byCountry,
        List<Daily> byDay) {

    public record Group(String key, long transactions, long units, BigDecimal sales) { }

    public record Daily(LocalDate day, long transactions, long units, BigDecimal sales) { }
}
