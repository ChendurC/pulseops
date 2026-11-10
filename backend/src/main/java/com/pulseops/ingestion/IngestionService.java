package com.pulseops.ingestion;

import com.pulseops.security.PulseOpsUser;
import com.pulseops.storage.FileStorage;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.util.StringUtils;
import org.springframework.web.multipart.MultipartFile;

import java.io.IOException;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.HexFormat;
import java.util.Locale;
import java.util.UUID;

/** Accepts a CSV file, stores it, and queues a processing job for the Python worker. */
@Service
public class IngestionService {

    private static final Logger log = LoggerFactory.getLogger(IngestionService.class);
    static final long MAX_BYTES = 5L * 1024 * 1024;

    private final JdbcTemplate jdbc;
    private final FileStorage storage;

    public IngestionService(JdbcTemplate jdbc, FileStorage storage) {
        this.jdbc = jdbc;
        this.storage = storage;
    }

    public record QueuedJob(long jobId, UUID correlationId) { }

    @Transactional
    public QueuedJob submitUpload(PulseOpsUser user, MultipartFile file) {
        String originalName = safeFileName(file.getOriginalFilename());
        if (file.isEmpty()) {
            throw new IngestionRejectedException("The file is empty.");
        }
        if (file.getSize() > MAX_BYTES) {
            throw new IngestionRejectedException("The file is larger than 5 MB.");
        }
        if (!originalName.toLowerCase(Locale.ROOT).endsWith(".csv")) {
            throw new IngestionRejectedException("Only .csv files are accepted.");
        }

        byte[] content;
        try {
            content = file.getBytes();
        } catch (IOException e) {
            throw new IngestionRejectedException("The file could not be read.");
        }
        if (looksBinary(content)) {
            throw new IngestionRejectedException("The file does not look like a text CSV file.");
        }

        String key = storage.save(user.getTenantId(), content);
        UUID correlationId = UUID.randomUUID();

        // Tenant id always comes from the authenticated user, never from the request.
        Long ingestionId = jdbc.queryForObject("""
                INSERT INTO ingestions (tenant_id, source, original_filename, storage_key,
                                        file_sha256, size_bytes, created_by)
                VALUES (?, 'UPLOAD', ?, ?, ?, ?, ?)
                RETURNING id""",
                Long.class,
                user.getTenantId(), originalName, key, sha256(content), (long) content.length,
                user.getUserId());

        Long jobId = jdbc.queryForObject("""
                INSERT INTO processing_jobs (tenant_id, ingestion_id, correlation_id)
                VALUES (?, ?, ?)
                RETURNING id""",
                Long.class, user.getTenantId(), ingestionId, correlationId);

        log.info("ingestion accepted job_id={} tenant_id={} correlation_id={} bytes={}",
                jobId, user.getTenantId(), correlationId, content.length);
        return new QueuedJob(jobId, correlationId);
    }

    /** Keeps only the last path segment and strips control characters; metadata only. */
    static String safeFileName(String name) {
        String base = StringUtils.getFilename(name == null ? "" : name.replace('\\', '/'));
        if (base == null || base.isBlank()) {
            return "upload.csv";
        }
        String cleaned = base.replaceAll("\\p{Cntrl}", "").trim();
        return cleaned.length() > 255 ? cleaned.substring(0, 255) : cleaned;
    }

    private static boolean looksBinary(byte[] content) {
        int limit = Math.min(content.length, 1024);
        for (int i = 0; i < limit; i++) {
            if (content[i] == 0) {
                return true;
            }
        }
        return false;
    }

    private static String sha256(byte[] content) {
        try {
            return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(content));
        } catch (NoSuchAlgorithmException e) {
            throw new IllegalStateException(e);
        }
    }
}
