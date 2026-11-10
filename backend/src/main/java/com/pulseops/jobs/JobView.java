package com.pulseops.jobs;

import java.time.OffsetDateTime;
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
        UUID correlationId,
        OffsetDateTime submittedAt,
        OffsetDateTime startedAt,
        OffsetDateTime finishedAt) {

    public boolean isInProgress() {
        return "QUEUED".equals(status) || "PROCESSING".equals(status);
    }

    public boolean hasReport() {
        return "COMPLETED".equals(status) || "COMPLETED_WITH_ERRORS".equals(status);
    }
}
