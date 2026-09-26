// Altair's PJSR entry point (SPEC §6.5, docs/pixinsight-cli.md):
//   PixInsight.exe -n=5 --automation-mode --no-startup-scripts --force-exit -r=altair_runner.js,<job.json>
// Reads job.json from jsArguments[0], dispatches on job.kind (and job.phase
// for MERGE) and always writes result.json, from the catch block too.
#feature-id    Altair > Runner
#include "lib/json_io.js"
#include "calib_master.js"
#include "wbpp_driver.js"
#include "merge.js"

function altairMain() {
   if (typeof jsArguments == "undefined" || jsArguments.length < 1)
      throw new Error("usage: -r=altair_runner.js,<job.json>");
   var job = altairReadJson(jsArguments[0]);
   var result = new AltairResult(job);
   try {
      altairEnsureDir(job.output_dir);
      altairLog("job " + job.job_id + " " + job.kind + (job.phase ? " (" + job.phase + ")" : ""));
      if (job.kind == "CALIB_MASTER")
         altairCalibMaster(job, result);
      else if (job.kind == "PROJECT_REFERENCE")
         altairReference(job, result);
      else if (job.kind == "NIGHT_STACK")
         (job.pixinsight && job.pixinsight.engine == "wbpp" ? altairWbppNightStack : altairNightStack)(job, result);
      else if (job.kind == "MERGE" && job.phase == "measure")
         altairMergeMeasure(job, result);
      else if (job.kind == "MERGE")
         altairMergeIntegrate(job, result);
      else
         throw new Error("unknown job kind " + job.kind);
   } catch (e) {
      altairLog("failed: " + e);
      result.fail(e);
   }
   result.write();
}

altairMain();
