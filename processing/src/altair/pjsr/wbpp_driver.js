// NIGHT_STACK through the WBPP engine (SPEC §6.5). Driving WBPP headlessly
// (loading its engine sources, applying a profile, adding pre-built masters,
// a manual registration reference with autocrop off) depends on WBPP
// internals that differ between versions and must be verified in the phase 0
// spike on the installed PixInsight. Until then this driver runs the native
// pipeline, which honours the same job.json/result.json contract, and says so
// in the result.
#include "native_pipeline.js"

function altairWbppNightStack(job, result) {
   altairLog("WBPP engine driving is not verified on this PixInsight version; using the native pipeline");
   altairNightStack(job, result);
   result.value.metrics.engine = "native (wbpp requested)";
}
