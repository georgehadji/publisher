# Publisher -- LLM egress allowlist proxy (E3.1/E6.2). See tinyproxy.conf's
# own header for why this exists: the worker container has no other route
# to the internet, and this is the one thing standing between the E6.2
# structure-infer stage and reopening the SSRF surface E3.1 closed.
#
# E3.5-style digest pin (matches Dockerfile.worker/Dockerfile.api's own
# convention) -- recorded from `docker inspect --format='{{index
# .RepoDigests 0}}' alpine:3.20` at pin time.
FROM alpine:3.20@sha256:d9e853e87e55526f6b2917df91a2115c36dd7c696a35be12163d44e6e2a4b6bc
RUN apk add --no-cache tinyproxy
COPY tinyproxy.conf /etc/tinyproxy/tinyproxy.conf
COPY allowed-hosts.txt /etc/tinyproxy/allowed-hosts.txt
EXPOSE 8888
# Drop privilege at the IMAGE level (matches Dockerfile.worker's own
# convention), not by asking tinyproxy to setuid/setgid itself at runtime --
# that needs CAP_SETUID/CAP_SETGID, which docker-compose.yml's `cap_drop:
# [ALL]` on this service deliberately removes. `apk add tinyproxy` already
# creates a `tinyproxy` system user/group for exactly this purpose -- reuse
# it rather than creating a second, colliding one. Port 8888 is
# unprivileged, so no capability is needed to bind it as this uid either way.
USER tinyproxy:tinyproxy
CMD ["tinyproxy", "-d", "-c", "/etc/tinyproxy/tinyproxy.conf"]
