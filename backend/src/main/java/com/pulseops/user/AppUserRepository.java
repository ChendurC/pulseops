package com.pulseops.user;

import org.springframework.data.jpa.repository.EntityGraph;
import org.springframework.data.jpa.repository.JpaRepository;

import java.util.Optional;

public interface AppUserRepository extends JpaRepository<AppUser, Long> {

    /** Loads the user together with their tenant in one query (needed at login time). */
    @EntityGraph(attributePaths = "tenant")
    Optional<AppUser> findByUsername(String username);

    boolean existsByUsername(String username);
}
