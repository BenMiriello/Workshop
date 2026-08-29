<!-- author: self -->
Starlette/FastAPI `add_middleware` inserts at the FRONT of the stack, so the LAST thing registered is the OUTERMOST.

`BaseHTTPMiddleware` (including any `@app.middleware("http")` function) re-emits every response it handles as a CHUNKED stream (`more_body=True`).

Workshop's `SSEAwareGZipMiddleware` deliberately refuses to compress a chunked body: it has no per-chunk flush, and stalling SSE would break live chat streaming. So when compression was registered LAST in `backend/main.py`, it sat outside `strip_api_prefix` (a BaseHTTPMiddleware), saw only chunks, and compressed NOTHING, on every response, while appearing correctly wired. Measured 2026-08-14: a 4.83MB events response went out raw.

Fix: register it FIRST, making it innermost, next to the router where a JSONResponse still arrives as one complete body message. Synthetic proof at identical payload: 223,902 bytes before, 2,255 after; SSE unaffected in both orders.

Nothing about the failure is loud. Pinned by two tests in `backend/tests/test_compression_middleware.py`: one asserts compression is innermost in the real app, one demonstrates the mechanism.

Second trap in the same area: Starlette's `StaticFiles` uses a 64 KiB `chunk_size`, so any file larger than that streams as multiple chunks and hits the same refusal. The public edge had to raise `chunk_size` before the JS bundle would compress at all.