package com.pulseops.web;

import com.pulseops.jobs.JobQueryService;
import com.pulseops.security.PulseOpsUser;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.stereotype.Controller;
import org.springframework.ui.Model;
import org.springframework.web.bind.annotation.GetMapping;

import java.util.LinkedHashMap;
import java.util.Map;

@Controller
public class DashboardController {

    private final JdbcTemplate jdbc;
    private final JobQueryService jobs;

    public DashboardController(JdbcTemplate jdbc, JobQueryService jobs) {
        this.jdbc = jdbc;
        this.jobs = jobs;
    }

    @GetMapping("/")
    public String dashboard(@AuthenticationPrincipal PulseOpsUser user, Model model) {
        long tenantId = user.getTenantId();

        // Real counts per status, scoped to the signed-in user's tenant.
        Map<String, Long> byStatus = new LinkedHashMap<>();
        for (String s : new String[] {"QUEUED", "PROCESSING", "COMPLETED",
                "COMPLETED_WITH_ERRORS", "FAILED"}) {
            byStatus.put(s, 0L);
        }
        jdbc.query("SELECT status, count(*) AS c FROM processing_jobs WHERE tenant_id = ? "
                        + "GROUP BY status",
                rs -> { byStatus.put(rs.getString("status"), rs.getLong("c")); }, tenantId);

        Long userCount = jdbc.queryForObject(
                "SELECT count(*) FROM users WHERE tenant_id = ?", Long.class, tenantId);

        model.addAttribute("jobCount", jobs.countJobs(tenantId));
        model.addAttribute("byStatus", byStatus);
        model.addAttribute("userCount", userCount);
        model.addAttribute("recentJobs", jobs.listJobs(tenantId, 0, 5));
        return "dashboard";
    }
}
