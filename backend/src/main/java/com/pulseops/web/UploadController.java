package com.pulseops.web;

import com.pulseops.ingestion.IngestionRejectedException;
import com.pulseops.ingestion.IngestionService;
import com.pulseops.security.PulseOpsUser;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.stereotype.Controller;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.multipart.MultipartFile;
import org.springframework.web.servlet.mvc.support.RedirectAttributes;

@Controller
public class UploadController {

    private final IngestionService ingestion;

    public UploadController(IngestionService ingestion) {
        this.ingestion = ingestion;
    }

    @GetMapping("/upload")
    public String form() {
        return "upload";
    }

    @PostMapping("/upload")
    public String upload(@AuthenticationPrincipal PulseOpsUser user,
                         @RequestParam("file") MultipartFile file,
                         RedirectAttributes redirect) {
        try {
            IngestionService.QueuedJob job = ingestion.submitUpload(user, file);
            redirect.addFlashAttribute("success",
                    "File accepted. Job #" + job.jobId() + " is queued for processing.");
            return "redirect:/jobs/" + job.jobId();
        } catch (IngestionRejectedException e) {
            redirect.addFlashAttribute("error", e.getMessage());
            return "redirect:/upload";
        }
    }
}
