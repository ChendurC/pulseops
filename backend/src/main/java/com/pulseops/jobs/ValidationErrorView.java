package com.pulseops.jobs;

public record ValidationErrorView(
        Integer lineNumber, String columnName, String errorCode, String message, String rawValue) {
}
