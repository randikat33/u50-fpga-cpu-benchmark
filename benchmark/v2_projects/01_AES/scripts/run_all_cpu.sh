#!/usr/bin/env bash
# Runs every CPU implementation with the same arguments (the equivalent of --impl all).
# usage: scripts/run_all_cpu.sh [aes_cpu args...]   e.g. --no-io --size-mb 1024 --mode enc
# env:   AES_CPU=path/to/aes_cpu (default build/aes_cpu), IMPLS="vaes aesni ttable openssl"
set -u
here="$(cd "$(dirname "$0")/.." && pwd)"
bin="${AES_CPU:-$here/build/aes_cpu}"
rc=0
for impl in ${IMPLS:-vaes aesni ttable openssl}; do
    echo "=== impl=$impl ==="
    "$bin" --impl "$impl" "$@" || rc=1
done
exit $rc
