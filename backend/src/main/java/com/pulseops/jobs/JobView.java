package com.pulseops.jobs;

import java.time.OffsetDateTime;
import java.util.Set;
import java.util.UUID;

/** One processing job as shown on screen. */
public record JobView(
        long id,
        String fileName,
        String status,
        Integer totalRecords,
        Integer validRecords,
        Integer invalidRecords,
        String errorCategory,
        String errorMessage,
        int attemptCount,
        int maxAttempts,
        int manualRetryCount,
        UUID correlationId,
        OffsetDateTime submittedAt,
        OffsetDateTime startedAt,
        OffsetDateTime finishedAt) {

    /**
     * Failures caused by infrastructure (transient) can be retried.
     * Failures caused by the data itself (permanent) need a corrected file instead.
     * Keep in sync with the WHERE clause in JobCommandService.retry.
     */
    public static final Set<String> RETRYABLE_CATEGORIES =
            Set.of("PROCESSING_EXCEPTION", "LEASE_EXPIRED", "STORAGE_FILE_MISSING");

    public boolean isInProgress() {
        return "QUEUED".equals(status) || "PROCESSING".equals(status);
    }

    public boolean hasReport() {
        return "COMPLETED".equals(status) || "COMPLETED_WITH_ERRORS".equals(status);
    }

    public boolean isRetryable() {
        return "FAILED".equals(status) && RETRYABLE_CATEGORIES.contains(errorCategory);
    }
}
