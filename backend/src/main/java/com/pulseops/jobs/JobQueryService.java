package com.pulseops.jobs;

import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.core.RowMapper;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.math.BigDecimal;
import java.time.LocalDate;
import java.time.OffsetDateTime;
import java.util.List;
import java.util.Optional;
import java.util.UUID;

/**
 * Read-only queries for jobs and reports.
 * EVERY query filters on tenant_id, so one customer can never read another's data,
 * even by guessing a job id in the URL.
 */
@Service
@Transactional(readOnly = true)
public class JobQueryService {

    private static final String JOB_COLUMNS = """
            j.id, i.original_filename, j.status, j.total_records, j.valid_records,
            j.invalid_records, j.error_category, j.error_message, j.attempt_count,
            j.max_attempts, j.manual_retry_count, j.correlation_id, j.submitted_at, j.started_at, j.finished_at
            """;

    private static final RowMapper<JobView> JOB_MAPPER = (rs, n) -> new JobView(
            rs.getLong("id"),
            rs.getString("original_filename"),
            rs.getString("status"),
            rs.getObject("total_records", Integer.class),
            rs.getObject("valid_records", Integer.class),
            rs.getObject("invalid_records", Integer.class),
            rs.getString("error_category"),
            rs.getString("error_message"),
            rs.getInt("attempt_count"),
            rs.getInt("max_attempts"),
            rs.getInt("manual_retry_count"),
            rs.getObject("correlation_id", UUID.class),
            rs.getObject("submitted_at", OffsetDateTime.class),
            rs.getObject("started_at", OffsetDateTime.class),
            rs.getObject("finished_at", OffsetDateTime.class));

    private final JdbcTemplate jdbc;

    public JobQueryService(JdbcTemplate jdbc) {
        this.jdbc = jdbc;
    }

    public List<JobView> listJobs(long tenantId, int page, int size) {
        return jdbc.query("SELECT " + JOB_COLUMNS + """
                FROM processing_jobs j JOIN ingestions i ON i.id = j.ingestion_id
                WHERE j.tenant_id = ?
                ORDER BY j.submitted_at DESC, j.id DESC
                LIMIT ? OFFSET ?""",
                JOB_MAPPER, tenantId, size, (long) page * size);
    }

    public long countJobs(long tenantId) {
        Long count = jdbc.queryForObject(
                "SELECT count(*) FROM processing_jobs WHERE tenant_id = ?", Long.class, tenantId);
        return count == null ? 0 : count;
    }

    public Optional<JobView> findJob(long tenantId, long jobId) {
        return jdbc.query("SELECT " + JOB_COLUMNS + """
                FROM processing_jobs j JOIN ingestions i ON i.id = j.ingestion_id
                WHERE j.tenant_id = ? AND j.id = ?""",
                JOB_MAPPER, tenantId, jobId).stream().findFirst();
    }

    public List<ValidationErrorView> findErrors(long tenantId, long jobId, int limit) {
        return jdbc.query("""
                SELECT line_number, column_name, error_code, message, raw_value
                FROM validation_errors
                WHERE tenant_id = ? AND job_id = ?
                ORDER BY line_number NULLS FIRST, id
                LIMIT ?""",
                (rs, n) -> new ValidationErrorView(
                        rs.getObject("line_number", Integer.class),
                        rs.getString("column_name"),
                        rs.getString("error_code"),
                        rs.getString("message"),
                        rs.getString("raw_value")),
                tenantId, jobId, limit);
    }

    /**
     * The job's audit history. LEFT JOIN (not INNER JOIN) because SYSTEM actions,
     * such as the worker recovering a job, have no user: an INNER JOIN would hide them.
     */
    public List<ActivityView> findActivity(long tenantId, long jobId) {
        return jdbc.query("""
                SELECT a.created_at, a.action, a.actor_type, u.username, a.details
                FROM audit_logs a
                LEFT JOIN users u ON u.id = a.actor_user_id
                WHERE a.tenant_id = ? AND a.entity_type = 'JOB' AND a.entity_id = ?
                ORDER BY a.created_at, a.id""",
                (rs, n) -> new ActivityView(
                        rs.getObject("created_at", OffsetDateTime.class),
                        rs.getString("action"),
                        rs.getString("actor_type"),
                        rs.getString("username"),
                        rs.getString("details")),
                tenantId, jobId);
    }

    public ReportData buildReport(long tenantId, JobView job) {
        long jobId = job.id();
        ReportData.Group totals = jdbc.queryForObject("""
                SELECT 'ALL' AS k, count(*) AS t, coalesce(sum(quantity), 0) AS u,
                       coalesce(sum(line_total), 0) AS s
                FROM processed_transactions WHERE tenant_id = ? AND job_id = ?""",
                (rs, n) -> group(rs.getString("k"), rs.getLong("t"), rs.getLong("u"),
                        rs.getBigDecimal("s")),
                tenantId, jobId);

        List<ReportData.Group> byProduct = groupBy("product_code", tenantId, jobId);
        List<ReportData.Group> byCountry = groupBy("country", tenantId, jobId);
        List<ReportData.Daily> byDay = jdbc.query("""
                SELECT transaction_date AS d, count(*) AS t, sum(quantity) AS u,
                       sum(line_total) AS s
                FROM processed_transactions WHERE tenant_id = ? AND job_id = ?
                GROUP BY transaction_date ORDER BY transaction_date""",
                (rs, n) -> new ReportData.Daily(rs.getObject("d", LocalDate.class),
                        rs.getLong("t"), rs.getLong("u"), rs.getBigDecimal("s")),
                tenantId, jobId);

        int invalid = job.invalidRecords() == null ? 0 : job.invalidRecords();
        return new ReportData(totals.transactions(), totals.units(), totals.sales(), invalid,
                byProduct, byCountry, byDay);
    }

    /** column is one of two fixed literals chosen in this class, never user input. */
    private List<ReportData.Group> groupBy(String column, long tenantId, long jobId) {
        if (!column.equals("product_code") && !column.equals("country")) {
            throw new IllegalArgumentException("Unsupported grouping column");
        }
        return jdbc.query("SELECT " + column + " AS k, count(*) AS t, sum(quantity) AS u, "
                        + "sum(line_total) AS s FROM processed_transactions "
                        + "WHERE tenant_id = ? AND job_id = ? "
                        + "GROUP BY " + column + " ORDER BY s DESC, k",
                (rs, n) -> group(rs.getString("k"), rs.getLong("t"), rs.getLong("u"),
                        rs.getBigDecimal("s")),
                tenantId, jobId);
    }

    private static ReportData.Group group(String key, long t, long u, BigDecimal s) {
        return new ReportData.Group(key, t, u, s);
    }
}
