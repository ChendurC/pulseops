package com.pulseops.security;

import com.pulseops.user.AppUser;
import com.pulseops.user.Role;
import org.springframework.security.core.CredentialsContainer;
import org.springframework.security.core.GrantedAuthority;
import org.springframework.security.core.authority.SimpleGrantedAuthority;
import org.springframework.security.core.userdetails.UserDetails;

import java.util.Collection;
import java.util.List;

/**
 * The signed-in user as Spring Security sees it.
 * Carries the tenant id so every service call can scope queries to the user's own tenant.
 */
public class PulseOpsUser implements UserDetails, CredentialsContainer {

    private final Long userId;
    private final Long tenantId;
    private final String tenantCode;
    private final String tenantName;
    private final String username;
    private final Role role;
    private final boolean enabled;
    private String passwordHash;

    public PulseOpsUser(AppUser user) {
        this.userId = user.getId();
        this.tenantId = user.getTenant().getId();
        this.tenantCode = user.getTenant().getCode();
        this.tenantName = user.getTenant().getName();
        this.username = user.getUsername();
        this.role = user.getRole();
        this.enabled = user.isEnabled() && user.getTenant().isActive();
        this.passwordHash = user.getPasswordHash();
    }

    @Override
    public Collection<? extends GrantedAuthority> getAuthorities() {
        return List.of(new SimpleGrantedAuthority("ROLE_" + role.name()));
    }

    @Override
    public String getPassword() { return passwordHash; }

    @Override
    public String getUsername() { return username; }

    @Override
    public boolean isEnabled() { return enabled; }

    /** Called by Spring Security after login so the hash is not kept in the session. */
    @Override
    public void eraseCredentials() { this.passwordHash = null; }

    public Long getUserId() { return userId; }
    public Long getTenantId() { return tenantId; }
    public String getTenantCode() { return tenantCode; }
    public String getTenantName() { return tenantName; }
    public Role getRole() { return role; }
}
