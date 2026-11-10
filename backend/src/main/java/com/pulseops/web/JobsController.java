package com.pulseops.web;

import com.pulseops.jobs.CsvReportWriter;
import com.pulseops.jobs.JobQueryService;
import com.pulseops.jobs.JobView;
import com.pulseops.jobs.ReportData;
import com.pulseops.security.PulseOpsUser;
import org.springframework.http.ContentDisposition;
import org.springframework.http.HttpHeaders;
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.stereotype.Controller;
import org.springframework.ui.Model;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.server.ResponseStatusException;

@Controller
public class JobsController {

    private static final int PAGE_SIZE = 20;
    private final JobQueryService jobs;

    public JobsController(JobQueryService jobs) {
        this.jobs = jobs;
    }

    @GetMapping("/jobs")
    public String list(@AuthenticationPrincipal PulseOpsUser user,
                       @RequestParam(defaultValue = "0") int page, Model model) {
        int safePage = Math.max(page, 0);
        long total = jobs.countJobs(user.getTenantId());
        int totalPages = (int) Math.max(1, (total + PAGE_SIZE - 1) / PAGE_SIZE);
        model.addAttribute("jobs", jobs.listJobs(user.getTenantId(), safePage, PAGE_SIZE));
        model.addAttribute("page", safePage);
        model.addAttribute("totalPages", totalPages);
        model.addAttribute("total", total);
        return "jobs";
    }

    @GetMapping("/jobs/{id}")
    public String detail(@AuthenticationPrincipal PulseOpsUser user,
                         @PathVariable long id, Model model) {
        JobView job = findOr404(user, id);
        model.addAttribute("job", job);
        model.addAttribute("errors", jobs.findErrors(user.getTenantId(), id, 200));
        if (job.hasReport()) {
            model.addAttribute("report", jobs.buildReport(user.getTenantId(), job));
        }
        return "job-detail";
    }

    @GetMapping("/jobs/{id}/report.csv")
    public ResponseEntity<byte[]> reportCsv(@AuthenticationPrincipal PulseOpsUser user,
                                            @PathVariable long id) {
        JobView job = findOr404(user, id);
        if (!job.hasReport()) {
            throw new ResponseStatusException(HttpStatus.CONFLICT, "Report not available yet");
        }
        ReportData report = jobs.buildReport(user.getTenantId(), job);
        return ResponseEntity.ok()
                .contentType(new MediaType("text", "csv"))
                .header(HttpHeaders.CONTENT_DISPOSITION, ContentDisposition.attachment()
                        .filename("pulseops-job-" + id + "-report.csv").build().toString())
                .body(CsvReportWriter.write(id, report));
    }

    /** Another tenant's job gives 404, not 403: we don't even reveal that it exists. */
    private JobView findOr404(PulseOpsUser user, long id) {
        return jobs.findJob(user.getTenantId(), id)
                .orElseThrow(() -> new ResponseStatusException(HttpStatus.NOT_FOUND));
    }
}
