package com.pulseops.web;

import com.pulseops.security.PulseOpsUser;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.stereotype.Controller;
import org.springframework.ui.Model;
import org.springframework.web.bind.annotation.GetMapping;

@Controller
public class DashboardController {

    private final JdbcTemplate jdbc;

    public DashboardController(JdbcTemplate jdbc) {
        this.jdbc = jdbc;
    }

    @GetMapping("/")
    public String dashboard(@AuthenticationPrincipal PulseOpsUser user, Model model) {
        // Real, tenant-scoped query: the tenant id comes from the login, never from the request.
        Long jobCount = jdbc.queryForObject(
                "SELECT count(*) FROM processing_jobs WHERE tenant_id = ?",
                Long.class, user.getTenantId());
        Long userCount = jdbc.queryForObject(
                "SELECT count(*) FROM users WHERE tenant_id = ?",
                Long.class, user.getTenantId());

        model.addAttribute("user", user);
        model.addAttribute("jobCount", jobCount);
        model.addAttribute("userCount", userCount);
        return "dashboard";
    }
}
