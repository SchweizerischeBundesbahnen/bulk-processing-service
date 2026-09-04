#!/bin/sh
# Docker healthcheck. Follows the scheme the API server was configured with.
set -eu

# Strip surrounding whitespace. app/tls.py reads a blank value as unset, and the
# probe has to reach the same verdict, or it would ask for a scheme the server
# does not serve.
trim() {
    trimmed="$1"
    trimmed="${trimmed#"${trimmed%%[![:space:]]*}"}"
    trimmed="${trimmed%"${trimmed##*[![:space:]]}"}"
    printf '%s' "${trimmed}"
}

port="$(trim "${PORT:-9070}")"
cert_file="$(trim "${TLS_CERT_FILE:-}")"

url="http://localhost:${port}/health"
set --

if [ -n "${cert_file}" ]; then
    url="https://localhost:${port}/health"
    # The certificate is verified by the caller of the service, not here: this
    # probe talks to its own process over the loopback interface and asks one
    # question, whether that process still answers.
    set -- --insecure
fi

exec curl --fail --silent --show-error "$@" "${url}"
