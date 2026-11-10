package com.pulseops.dev;

import com.pulseops.tenant.Tenant;
import com.pulseops.tenant.TenantRepository;
import com.pulseops.user.AppUser;
import com.pulseops.user.AppUserRepository;
import com.pulseops.user.Role;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.CommandLineRunner;
import org.springframework.context.annotation.Profile;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.stereotype.Component;

/**
 * Creates two fictional tenants and their demo users, ONLY when the "dev" profile is active.
 * Safe to run on every start: it skips anything that already exists.
 * All data is synthetic.
 */
@Component
@Profile("dev")
public class DevDataSeeder implements CommandLineRunner {

    private static final Logger log = LoggerFactory.getLogger(DevDataSeeder.class);

    private final TenantRepository tenants;
    private final AppUserRepository users;
    private final PasswordEncoder passwordEncoder;
    private final String demoPassword;

    public DevDataSeeder(TenantRepository tenants,
                         AppUserRepository users,
                         PasswordEncoder passwordEncoder,
                         @Value("${pulseops.demo-password:}") String demoPassword) {
        this.tenants = tenants;
        this.users = users;
        this.passwordEncoder = passwordEncoder;
        this.demoPassword = demoPassword;
    }

    @Override
    public void run(String... args) {
        if (demoPassword == null || demoPassword.isBlank()) {
            throw new IllegalStateException(
                    "PULSEOPS_DEMO_PASSWORD must be set when the dev profile is active");
        }

        Tenant meridian = ensureTenant("MERIDIAN", "Meridian Health Group", "HEALTHCARE");
        Tenant harbour = ensureTenant("HARBOURLINE", "Harbourline Retail", "RETAIL");

        int created = 0;
        created += ensureUser(meridian, "meridian.admin", Role.TENANT_ADMIN);
        created += ensureUser(meridian, "meridian.analyst", Role.ANALYST);
        created += ensureUser(meridian, "meridian.support", Role.SUPPORT_ENGINEER);
        created += ensureUser(harbour, "harbour.admin", Role.TENANT_ADMIN);
        created += ensureUser(harbour, "harbour.analyst", Role.ANALYST);

        // Never log the password itself.
        log.info("Dev seed complete: 2 tenants ensured, {} new demo users created", created);
    }

    private Tenant ensureTenant(String code, String name, String industry) {
        return tenants.findByCode(code)
                .orElseGet(() -> tenants.save(new Tenant(code, name, industry)));
    }

    private int ensureUser(Tenant tenant, String username, Role role) {
        if (users.existsByUsername(username)) {
            return 0;
        }
        users.save(new AppUser(tenant, username, passwordEncoder.encode(demoPassword), role));
        return 1;
    }
}
