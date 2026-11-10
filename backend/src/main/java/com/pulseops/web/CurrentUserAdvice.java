package com.pulseops.web;

import com.pulseops.security.PulseOpsUser;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.web.bind.annotation.ControllerAdvice;
import org.springframework.web.bind.annotation.ModelAttribute;

/** Makes the signed-in user available as "user" on every page (used by the top bar). */
@ControllerAdvice
public class CurrentUserAdvice {

    @ModelAttribute("user")
    public PulseOpsUser currentUser(@AuthenticationPrincipal PulseOpsUser user) {
        return user;
    }
}
