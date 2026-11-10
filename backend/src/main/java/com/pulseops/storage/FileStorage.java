package com.pulseops.storage;

/**
 * Where uploaded files live. Local disk today; an S3 implementation can replace it later
 * without changing any caller.
 */
public interface FileStorage {

    /** Saves the bytes under a server-generated key and returns that key. */
    String save(long tenantId, byte[] content);

    byte[] read(String key);
}
