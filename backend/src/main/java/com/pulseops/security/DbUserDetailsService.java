package com.pulseops.security;

import com.pulseops.user.AppUserRepository;
import org.springframework.security.core.userdetails.UserDetails;
import org.springframework.security.core.userdetails.UserDetailsService;
import org.springframework.security.core.userdetails.UsernameNotFoundException;
import org.springframework.stereotype.Service;

/** Tells Spring Security how to find a user in our PostgreSQL users table. */
@Service
public class DbUserDetailsService implements UserDetailsService {

    private final AppUserRepository users;

    public DbUserDetailsService(AppUserRepository users) {
        this.users = users;
    }

    @Override
    public UserDetails loadUserByUsername(String username) {
        return users.findByUsername(username)
                .map(PulseOpsUser::new)
                // Same message whether the user exists or not: don't reveal valid usernames.
                .orElseThrow(() -> new UsernameNotFoundException("Bad credentials"));
    }
}
