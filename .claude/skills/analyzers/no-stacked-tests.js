'use strict';
var child_process = require('child_process');

exports.analyze = function(context, config) {
  var findings = [];
  try {
    var out = child_process.execSync(
      "ps -eo pid,etime,command | grep '[p]ython3.*-m pytest' 2>/dev/null || true",
      { encoding: 'utf8', timeout: 3000 }
    ).trim();
    if (!out) return findings;

    var lines = out.split('\n').filter(function(l) { return l.trim(); });
    if (lines.length >= 2) {
      var pids = lines.map(function(l) { return l.trim().split(/\s+/)[0]; });
      findings.push({
        severity: 'warning',
        message: 'Multiple pytest processes detected (' + lines.length +
          ' running, PIDs: ' + pids.join(', ') +
          '). Before taking action, check your conversation history to determine ' +
          'if these are truly stacked (you launched multiple runs yourself) or ' +
          'legitimate parallel runs from different agents. If YOU launched both, ' +
          'do NOT stack test runs — wait for the existing run to finish. ' +
          'If they are from separate agents, this is fine and no action is needed.'
      });
    }
  } catch (e) { /* ps/grep failed */ }
  return findings;
};
