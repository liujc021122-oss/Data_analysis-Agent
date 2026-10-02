#!/usr/bin/env bash
set -euo pipefail

force=false
if [[ "${1:-}" == "--force" ]]; then
    force=true
elif [[ $# -gt 0 ]]; then
    printf 'error: unknown argument: %s (use --force to replace existing certificates)\n' "$1" >&2
    exit 2
fi

if ! command -v openssl >/dev/null 2>&1; then
    printf 'error: openssl is required to generate local certificates\n' >&2
    exit 127
fi

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd -- "$script_dir/.." && pwd)"
cert_dir="$repo_root/deploy/certs"
cert_file="$cert_dir/local.crt"
key_file="$cert_dir/local.key"

if [[ "$force" != true && ( -e "$cert_file" || -e "$key_file" ) ]]; then
    printf 'error: %s or %s already exists; pass --force to replace it\n' "$cert_file" "$key_file" >&2
    exit 1
fi

mkdir -p "$cert_dir"
openssl req -x509 -nodes -newkey rsa:2048 -days 30 \
  -keyout "$key_file" \
  -out "$cert_file" \
  -subj "/CN=localhost" \
  -addext "subjectAltName=DNS:localhost,IP:127.0.0.1"

printf 'created %s and %s\n' "$cert_file" "$key_file"
