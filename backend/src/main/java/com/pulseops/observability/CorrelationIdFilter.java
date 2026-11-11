package com.pulseops.observability;

import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.slf4j.MDC;
import org.springframework.core.Ordered;
import org.springframework.core.annotation.Order;
import org.springframework.stereotype.Component;
import org.springframework.web.filter.OncePerRequestFilter;

import java.io.IOException;
import java.util.UUID;
import java.util.regex.Pattern;

/**
 * Gives every HTTP request a correlation ID (reusing a valid incoming X-Correlation-Id),
 * puts it in the logging context (MDC) and returns it in the response header.
 * Runs before Spring Security so even rejected requests are traceable.
 */
@Component
@Order(Ordered.HIGHEST_PRECEDENCE)
public class CorrelationIdFilter extends OncePerRequestFilter {

    public static final String HEADER = "X-Correlation-Id";
    public static final String MDC_KEY = "correlation_id";
    private static final Pattern UUID_PATTERN = Pattern.compile(
            "^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$");
    private static final Logger log = LoggerFactory.getLogger(CorrelationIdFilter.class);

    @Override
    protected void doFilterInternal(HttpServletRequest request, HttpServletResponse response,
                                    FilterChain chain) throws ServletException, IOException {
        String incoming = request.getHeader(HEADER);
        // Only accept a well-formed UUID from outside; anything else could be log injection.
        String id = incoming != null && UUID_PATTERN.matcher(incoming).matches()
                ? incoming.toLowerCase()
                : UUID.randomUUID().toString();
        MDC.put(MDC_KEY, id);
        response.setHeader(HEADER, id);
        long start = System.nanoTime();
        try {
            chain.doFilter(request, response);
        } finally {
            if (!request.getRequestURI().startsWith("/css/")) {
                log.info("http_request method={} path={} status={} duration_ms={}",
                        request.getMethod(), request.getRequestURI(), response.getStatus(),
                        (System.nanoTime() - start) / 1_000_000);
            }
            MDC.clear(); // threads are reused: never leak one request's context into the next
        }
    }

    /** The current request's correlation ID as a UUID, or a new one outside a request. */
    public static UUID currentOrNew() {
        String id = MDC.get(MDC_KEY);
        return id != null && UUID_PATTERN.matcher(id).matches() ? UUID.fromString(id) : UUID.randomUUID();
    }
}
