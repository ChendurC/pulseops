package com.pulseops.audit;

import com.pulseops.security.PulseOpsUser;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;

import java.util.UUID;

/** Writes the audit trail: who did what to which record, and when. */
@Service
public class AuditService {

    private final JdbcTemplate jdbc;

    public AuditService(JdbcTemplate jdbc) {
        this.jdbc = jdbc;
    }

    public void recordUserAction(PulseOpsUser user, String action, String entityType, long entityId,
                                 UUID correlationId, String details) {
        jdbc.update("""
                INSERT INTO audit_logs (tenant_id, actor_type, actor_user_id, action, entity_type,
                                        entity_id, correlation_id, details)
                VALUES (?, 'USER', ?, ?, ?, ?, ?, ?)""",
                user.getTenantId(), user.getUserId(), action, entityType, entityId, correlationId,
                details);
    }
}
