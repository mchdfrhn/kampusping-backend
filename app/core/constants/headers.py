REQUEST_ID_HEADER = "X-Request-ID"
PROCESS_TIME_HEADER = "X-Process-Time"
# Custom header required on cookie-authenticated endpoints (refresh/logout).
# Browsers cannot send it cross-origin without a CORS preflight, which blocks CSRF.
CSRF_HEADER = "X-Requested-With"
