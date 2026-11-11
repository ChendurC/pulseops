package com.pulseops;

import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.security.crypto.password.PasswordEncoder;

import java.net.URI;
import java.net.URLEncoder;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.UUID;
import java.util.regex.Matcher;
import java.util.regex.Pattern;
import java.util.stream.Collectors;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * Black-box security tests: start the real app on a random port and talk to it over HTTP,
 * exactly like a browser (or an attacker) would. Uses the local PostgreSQL from Docker.
 *
 * Each test creates two throw-away tenants (A and B) and removes them afterwards.
 * Stop the Python worker while running these: it would otherwise pick up re-queued jobs.
 */
@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT)
class SecurityAndTenantIsolationTest {

    private static final String PASSWORD = "Test-Only-Password-1";
    private static final Pattern CSRF = Pattern.compile(
            "name=\"_csrf\"[^>]*value=\"([^\"]+)\"|value=\"([^\"]+)\"[^>]*name=\"_csrf\"");
    private static final Pattern SESSION_COOKIE = Pattern.compile("JSESSIONID=([^;]+)");

    @Value("${local.server.port}")
    int port;
    @Autowired
    JdbcTemplate jdbc;
    @Autowired
    PasswordEncoder encoder;

    private final String suffix = UUID.randomUUID().toString().substring(0, 8);
    private long tenantA;
    private long tenantB;
    private long jobA;
    private long jobB;

    // ------------------------------------------------------------------ test data

    @BeforeEach
    void createTwoTenants() {
        tenantA = jdbc.queryForObject("INSERT INTO tenants (code, name, industry) "
                + "VALUES (?, 'Test Tenant A', 'TEST') RETURNING id", Long.class, "TA-" + suffix);
        tenantB = jdbc.queryForObject("INSERT INTO tenants (code, name, industry) "
                + "VALUES (?, 'Test Tenant B', 'TEST') RETURNING id", Long.class, "TB-" + suffix);
        String hash = encoder.encode(PASSWORD);
        addUser(tenantA, "a.admin." + suffix, hash, "TENANT_ADMIN");
        addUser(tenantA, "a.analyst." + suffix, hash, "ANALYST");
        addUser(tenantB, "b.admin." + suffix, hash, "TENANT_ADMIN");
        jobA = addFailedJob(tenantA);
        jobB = addFailedJob(tenantB);
    }

    @AfterEach
    void removeTestData() {
        for (long t : new long[] {tenantA, tenantB}) {
            jdbc.update("DELETE FROM audit_logs WHERE tenant_id = ?", t);
            jdbc.update("DELETE FROM validation_errors WHERE tenant_id = ?", t);
            jdbc.update("DELETE FROM processed_transactions WHERE tenant_id = ?", t);
            jdbc.update("DELETE FROM processing_jobs WHERE tenant_id = ?", t);
            jdbc.update("DELETE FROM ingestions WHERE tenant_id = ?", t);
            jdbc.update("DELETE FROM users WHERE tenant_id = ?", t);
            jdbc.update("DELETE FROM tenants WHERE id = ?", t);
        }
    }

    private void addUser(long tenantId, String username, String hash, String role) {
        jdbc.update("INSERT INTO users (tenant_id, username, password_hash, role) VALUES (?, ?, ?, ?)",
                tenantId, username, hash, role);
    }

    /** A job that failed for an infrastructure reason, so it is eligible for retry. */
    private long addFailedJob(long tenantId) {
        Long ingestion = jdbc.queryForObject("""
                INSERT INTO ingestions (tenant_id, source, original_filename, storage_key,
                                        file_sha256, size_bytes)
                VALUES (?, 'UPLOAD', 'test.csv', 'tenant-test/none.csv', ?, 1) RETURNING id""",
                Long.class, tenantId, "0".repeat(64));
        return jdbc.queryForObject("""
                INSERT INTO processing_jobs (tenant_id, ingestion_id, correlation_id, status,
                                             error_category, error_message, attempt_count, finished_at)
                VALUES (?, ?, ?, 'FAILED', 'PROCESSING_EXCEPTION', 'test failure', 3, now())
                RETURNING id""",
                Long.class, tenantId, ingestion, UUID.randomUUID());
    }

    private String status(long jobId) {
        return jdbc.queryForObject("SELECT status FROM processing_jobs WHERE id = ?", String.class, jobId);
    }

    // ------------------------------------------------------------------ the tests

    @Test
    void anonymousVisitorIsRedirectedToLogin() throws Exception {
        HttpResponse<String> response = new Browser().get("/jobs");
        assertEquals(302, response.statusCode());
        assertTrue(response.headers().firstValue("Location").orElse("").endsWith("/login"));
    }

    @Test
    void wrongPasswordIsRejected() throws Exception {
        Browser browser = new Browser();
        String token = browser.csrfFrom("/login");
        HttpResponse<String> response = browser.post("/login", Map.of(
                "username", "a.admin." + suffix, "password", "wrong", "_csrf", token));
        assertTrue(response.headers().firstValue("Location").orElse("").contains("error"));
    }

    @Test
    void userSeesOwnJobButGets404ForAnotherTenantsJobAndReport() throws Exception {
        Browser a = loggedIn("a.admin." + suffix);
        assertEquals(200, a.get("/jobs/" + jobA).statusCode());
        assertEquals(404, a.get("/jobs/" + jobB).statusCode());
        assertEquals(404, a.get("/jobs/" + jobB + "/report.csv").statusCode());
    }

    @Test
    void jobListShowsOnlyOwnTenantsJobs() throws Exception {
        String page = loggedIn("a.admin." + suffix).get("/jobs").body();
        assertTrue(page.contains("href=\"/jobs/" + jobA + "\""));
        assertFalse(page.contains("href=\"/jobs/" + jobB + "\""));
    }

    @Test
    void analystIsForbiddenFromRetrying() throws Exception {
        Browser analyst = loggedIn("a.analyst." + suffix);
        String token = analyst.csrfFrom("/jobs/" + jobA);
        assertFalse(analyst.get("/jobs/" + jobA).body().contains("Retry job"), "button must be hidden");
        assertEquals(403, analyst.post("/jobs/" + jobA + "/retry", Map.of("_csrf", token)).statusCode());
        assertEquals("FAILED", status(jobA));
    }

    @Test
    void postWithoutCsrfTokenIsRejected() throws Exception {
        Browser admin = loggedIn("a.admin." + suffix);
        assertEquals(403, admin.post("/jobs/" + jobA + "/retry", Map.of()).statusCode());
        assertEquals("FAILED", status(jobA));
    }

    @Test
    void adminCannotRetryAnotherTenantsJob() throws Exception {
        Browser admin = loggedIn("a.admin." + suffix);
        String token = admin.csrfFrom("/jobs/" + jobA);
        assertEquals(404, admin.post("/jobs/" + jobB + "/retry", Map.of("_csrf", token)).statusCode());
        assertEquals("FAILED", status(jobB));
    }

    @Test
    void adminRetryIsSafeToRepeatAndIsAudited() throws Exception {
        Browser admin = loggedIn("a.admin." + suffix);

        String token = admin.csrfFrom("/jobs/" + jobA);
        HttpResponse<String> first = admin.post("/jobs/" + jobA + "/retry", Map.of("_csrf", token));
        assertEquals(302, first.statusCode());
        assertEquals("QUEUED", status(jobA));

        // Double-click: the second retry must change nothing.
        token = admin.csrfFrom("/jobs/" + jobA);
        admin.post("/jobs/" + jobA + "/retry", Map.of("_csrf", token));
        Integer retries = jdbc.queryForObject(
                "SELECT manual_retry_count FROM processing_jobs WHERE id = ?", Integer.class, jobA);
        assertEquals(1, retries);

        // Exactly one audit entry, attributed to the admin via a JOIN on users.
        String actor = jdbc.queryForObject("""
                SELECT u.username FROM audit_logs a JOIN users u ON u.id = a.actor_user_id
                WHERE a.entity_id = ? AND a.action = 'JOB_RETRY_REQUESTED'""", String.class, jobA);
        assertEquals("a.admin." + suffix, actor);
    }

    @Test
    void everyResponseCarriesACorrelationId() throws Exception {
        HttpResponse<String> response = new Browser().get("/login");
        String id = response.headers().firstValue("X-Correlation-Id").orElse("");
        assertTrue(id.matches("[0-9a-f-]{36}"), "missing correlation id: " + id);
    }

    // ------------------------------------------------------------------ a tiny "browser"

    private Browser loggedIn(String username) throws Exception {
        Browser browser = new Browser();
        String token = browser.csrfFrom("/login");
        HttpResponse<String> response = browser.post("/login",
                Map.of("username", username, "password", PASSWORD, "_csrf", token));
        assertEquals(302, response.statusCode());
        assertFalse(response.headers().firstValue("Location").orElse("").contains("error"),
                "login failed for " + username);
        return browser;
    }

    /** Remembers the session cookie between requests and never follows redirects. */
    private final class Browser {
        private final HttpClient client = HttpClient.newBuilder()
                .followRedirects(HttpClient.Redirect.NEVER).build();
        private String sessionId;

        HttpResponse<String> get(String path) throws Exception {
            return send(request(path).GET().build());
        }

        HttpResponse<String> post(String path, Map<String, String> form) throws Exception {
            String body = new LinkedHashMap<>(form).entrySet().stream()
                    .map(e -> enc(e.getKey()) + "=" + enc(e.getValue()))
                    .collect(Collectors.joining("&"));
            return send(request(path)
                    .header("Content-Type", "application/x-www-form-urlencoded")
                    .POST(HttpRequest.BodyPublishers.ofString(body)).build());
        }

        /** Reads the hidden CSRF token that Spring Security puts into every form. */
        String csrfFrom(String path) throws Exception {
            Matcher m = CSRF.matcher(get(path).body());
            assertTrue(m.find(), "no CSRF token found on " + path);
            return m.group(1) != null ? m.group(1) : m.group(2);
        }

        private HttpRequest.Builder request(String path) {
            HttpRequest.Builder b = HttpRequest.newBuilder(URI.create("http://localhost:" + port + path));
            if (sessionId != null) {
                b.header("Cookie", "JSESSIONID=" + sessionId);
            }
            return b;
        }

        private HttpResponse<String> send(HttpRequest request) throws Exception {
            HttpResponse<String> response = client.send(request, HttpResponse.BodyHandlers.ofString());
            for (String cookie : response.headers().allValues("Set-Cookie")) {
                Matcher m = SESSION_COOKIE.matcher(cookie);
                if (m.find()) {
                    sessionId = m.group(1); // the session ID changes at login (session fixation defence)
                }
            }
            return response;
        }

        private String enc(String s) {
            return URLEncoder.encode(s, StandardCharsets.UTF_8);
        }
    }
}
