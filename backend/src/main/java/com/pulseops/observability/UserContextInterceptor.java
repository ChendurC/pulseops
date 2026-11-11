package com.pulseops.observability;

import com.pulseops.security.PulseOpsUser;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import org.slf4j.MDC;
import org.springframework.security.core.Authentication;
import org.springframework.security.core.context.SecurityContextHolder;
import org.springframework.web.servlet.HandlerInterceptor;

/** After login is known, adds tenant_id and user_id to every log line of the request. */
public class UserContextInterceptor implements HandlerInterceptor {

    @Override
    public boolean preHandle(HttpServletRequest request, HttpServletResponse response, Object handler) {
        Authentication auth = SecurityContextHolder.getContext().getAuthentication();
        if (auth != null && auth.getPrincipal() instanceof PulseOpsUser user) {
            MDC.put("tenant_id", String.valueOf(user.getTenantId()));
            MDC.put("user_id", String.valueOf(user.getUserId()));
        }
        return true;
    }
}
