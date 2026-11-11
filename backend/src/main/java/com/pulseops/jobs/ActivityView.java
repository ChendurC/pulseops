package com.pulseops.jobs;

import java.time.OffsetDateTime;

/** One row of a job's history. actor is null for SYSTEM actions (e.g. the worker). */
public record ActivityView(OffsetDateTime at, String action, String actorType, String actor,
                           String details) {
}
