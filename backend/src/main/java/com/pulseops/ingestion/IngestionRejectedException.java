package com.pulseops.ingestion;

/** The upload was refused before a job was created (wrong type, empty, too big...). */
public class IngestionRejectedException extends RuntimeException {

    public IngestionRejectedException(String message) {
        super(message);
    }
}
