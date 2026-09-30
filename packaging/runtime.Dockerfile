# Rendered only by package_runtime.py context after verifying local pinned inputs.
# No external frontend image, online apt/pip resolver, Rust build or implicit extras.
FROM @BASE_IMAGE@
COPY source /opt/health-buddy/source
COPY release /opt/health-buddy/release
RUN --network=none --mount=type=bind,source=inputs,target=/build-inputs,readonly \
    python -I -B /opt/health-buddy/source/packaging/install_runtime.py @ARCH@
LABEL org.opencontainers.image.revision="@COMMIT@" \
      org.opencontainers.image.version="@VERSION@" \
      io.health-buddy.source-archive-sha256="@SOURCE_ARCHIVE@" \
      io.health-buddy.input-lock-sha256="@INPUT_LOCK@"
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /opt/health-buddy/source
USER 65532:65532
ENTRYPOINT ["python", "-I", "-B", "/opt/health-buddy/source/scripts/runtime_entrypoint.py"]
CMD ["api"]
