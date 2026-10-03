#!/bin/bash
set -e
exec timeout "${RPA_TIMEOUT:-600}" xvfb-run -a --server-args="-screen 0 1366x768x24" "$@"
