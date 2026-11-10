package com.pulseops.web;

import org.springframework.web.bind.annotation.ControllerAdvice;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.multipart.MaxUploadSizeExceededException;
import org.springframework.web.servlet.mvc.support.RedirectAttributes;

/** A file over the size limit is rejected by Spring before our controller runs. */
@ControllerAdvice
public class UploadErrorAdvice {

    @ExceptionHandler(MaxUploadSizeExceededException.class)
    public String tooLarge(RedirectAttributes redirect) {
        redirect.addFlashAttribute("error", "The file is larger than 5 MB.");
        return "redirect:/upload";
    }
}
