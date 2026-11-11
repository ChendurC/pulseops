package com.pulseops.jobs;

import com.pulseops.audit.AuditService;
import com.pulseops.security.PulseOpsUser;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.security.access.AccessDeniedException;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.util.List;
import java.util.UUID;

/** Actions that change jobs. Every change is tenant-scoped and audited. */
@Service
public class JobCommandService {

    public enum RetryOutcome { QUEUED, NOT_FOUND, NOT_RETRYABLE }

    private static final Logger log = LoggerFactory.getLogger(JobCommandService.class);
    private final JdbcTemplate jdbc;
    private final AuditService audit;

    public JobCommandService(JdbcTemplate jdbc, AuditService audit) {
        this.jdbc = jdbc;
        this.audit = audit;
    }

    /**
     * Puts a FAILED job with a recoverable error back in the queue.
     * Safe to call twice (double-click): the WHERE status = 'FAILED' condition means
     * the second call changes nothing and reports NOT_RETRYABLE.
     */
    @Transactional
    public RetryOutcome retry(PulseOpsUser user, long jobId) {
        if (!user.isRetryAllowed()) {
            throw new AccessDeniedException("Role " + user.getRole() + " cannot retry jobs");
        }
        List<UUID> updated = jdbc.query("""
                UPDATE processing_jobs
                   SET status = 'QUEUED', attempt_count = 0, available_at = now(),
                       started_at = NULL, finished_at = NULL,
                       lease_expires_at = NULL, worker_id = NULL,
                       manual_retry_count = manual_retry_count + 1
                 WHERE tenant_id = ? AND id = ? AND status = 'FAILED'
                   AND error_category IN ('PROCESSING_EXCEPTION', 'LEASE_EXPIRED',
                                          'STORAGE_FILE_MISSING')
                RETURNING correlation_id""",
                (rs, n) -> rs.getObject(1, UUID.class), user.getTenantId(), jobId);

        if (updated.isEmpty()) {
            Long exists = jdbc.queryForObject(
                    "SELECT count(*) FROM processing_jobs WHERE tenant_id = ? AND id = ?",
                    Long.class, user.getTenantId(), jobId);
            return exists == null || exists == 0 ? RetryOutcome.NOT_FOUND : RetryOutcome.NOT_RETRYABLE;
        }

        audit.recordUserAction(user, "JOB_RETRY_REQUESTED", "JOB", jobId, updated.get(0),
                "Manual retry requested");
        log.info("job retry requested job_id={} by user_id={}", jobId, user.getUserId());
        return RetryOutcome.QUEUED;
    }
}
