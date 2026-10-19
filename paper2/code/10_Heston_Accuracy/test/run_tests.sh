#!/bin/bash
# run_tests.sh - all paper-2 phase-2 tests. Exit 0 = pass.
set -u
cd "$(dirname "$0")/.."
rc=0
echo "== unit tests"; ./build/test_units || rc=1
echo "== program tests"; python3 test/test_program.py || rc=1
echo "== CPU-native program (statistical check against the reference price)"
for sc in ft qe; do
  ./build/hq_native --case P1 --scheme $sc --steps-per-year 16 --pairs 1000000 --threads 4 | python3 -c "
import json,sys
r=[json.loads(l[12:]) for l in sys.stdin if l.startswith('RESULT_JSON ')][-1]
bad=[k for k in range(3) if abs(float(r['bias%d'%k]))>5*float(r['se%d'%k])+0.01]
print('  native $sc: ok=%s, |bias| within 5 SE + 0.01 at all strikes: %s, %.2f ns/path-step/core'%(r['ok'],not bad,r['ns_per_path_step_core']))
sys.exit(0 if r['ok']==1 and not bad else 1)" || rc=1
done
[ $rc -eq 0 ] && echo "ALL TESTS PASSED" || echo "TESTS FAILED"
exit $rc
