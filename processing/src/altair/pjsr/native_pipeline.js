// NIGHT_STACK, native engine (SPEC §6.5 fallback): ImageCalibration →
// (Debayer) → SubframeSelector → StarAlignment to the project reference →
// LocalNormalization → ImageIntegration. Registration is to the reference
// file with no autocrop, so the master is in project geometry (§9.1, §9.3).
#include "lib/json_io.js"
#include "lib/measure.js"
#include "lib/integrate.js"
#include "reference.js"

function altairRegister(job, items) {
   var S = new StarAlignment;
   S.referenceImage = job.reference.path;
   S.referenceIsFile = true;
   S.targets = items.map(function (i) { return [true, true, i.path]; });
   S.outputDirectory = job.output_dir;
   S.outputPostfix = "_r";
   S.overwriteExistingFiles = true;
   S.generateDrizzleData = job.drizzle_scale > 1;
   if (!S.executeGlobal())
      throw new Error("StarAlignment failed");
   var out = [];
   for (var i = 0; i < items.length; ++i) {
      var path = S.outputData[i] ? S.outputData[i][0] : "";
      out.push({ sha256: items[i].sha256, path: path, registered: path && path.length > 0,
                 drizzle: S.outputData[i] ? S.outputData[i][1] : "" });
   }
   return out;
}

function altairNormalize(job, items, referencePath) {
   var L = new LocalNormalization;
   L.referencePathOrViewId = referencePath;
   L.referenceIsView = false;
   L.targetItems = items.map(function (i) { return [true, i.path]; });
   L.outputDirectory = job.output_dir;
   L.generateNormalizedImages = LocalNormalization.prototype.GenerateNormalizedImages_Never;
   L.overwriteExistingFiles = true;
   if (!L.executeGlobal())
      throw new Error("LocalNormalization failed");
   return items.map(function (i) { return File.changeExtension(job.output_dir + "/" + File.extractName(i.path), ".xnml"); });
}

function altairNightStack(job, result) {
   var calibrated = [];
   for (var g = 0; g < job.groups.length; ++g)
      calibrated = calibrated.concat(altairCalibrate(job, job.groups[g], "_c"));
   for (var c = 0; c < calibrated.length; ++c)
      if (job.stack_kind == "final" && job.keep_calibrated_frames)
         result.output("calibrated_frame", calibrated[c].path, calibrated[c].sha256);
   var items = altairDebayerIfOsc(job, calibrated);
   var measured = altairMeasure(items);
   var registered = altairRegister(job, items);
   var used = [], frames = [];
   for (var i = 0; i < items.length; ++i) {
      var m = measured[i] || {};
      var ok = registered[i].registered && m.measured;
      frames.push({ sha256: items[i].sha256, used: !!ok, fwhm: m.fwhm, eccentricity: m.eccentricity, stars: m.stars,
                    psf_signal_weight: m.psf_signal_weight, weight: m.psf_signal_weight,
                    reason: ok ? null : (registered[i].registered ? "not measurable" : "registration failed") });
      if (ok)
         used.push({ path: registered[i].path, drizzle: registered[i].drizzle, weight: m.psf_signal_weight });
   }
   if (used.length < 1)
      throw new Error("no frame could be registered to the project reference");
   var best = used.reduce(function (a, b) { return b.weight > a.weight ? b : a; });
   var ln = altairNormalize(job, used, best.path);
   var w = altairIntegrate({
      images: used.map(function (u) { return u.path; }),
      lnFiles: ln,
      drizzleFiles: used.map(function (u) { return u.drizzle; }),
      weightMode: ImageIntegration.prototype.PSFSignalWeight,
      normalization: ImageIntegration.prototype.LocalNormalization,
      rejection: used.length >= 8 ? ImageIntegration.prototype.WinsorizedSigmaClip : ImageIntegration.prototype.PercentileClip,
      rejectionNormalization: ImageIntegration.prototype.LocalRejectionNormalization,
      rangeClipLow: true,
      rejectionMaps: true,
      updateDrizzle: job.drizzle_scale > 1
   });
   var master = w.integration;
   if (job.drizzle_scale > 1) {
      // The drizzled image is the master; the plain integration only fed the rejection data.
      master = altairDrizzle(used.map(function (u) { return u.drizzle; }), ln, job.drizzle_scale);
      w.integration.forceClose();
      w.integration = master;
   }
   altairSetKeyword(master, "ALTREFV", job.reference_version, "registered to project reference version");
   altairSetKeyword(master, "FILTER", "'" + job.filter + "'", "");
   var out = altairSave(master, job.output_dir + "/night_master.xisf");
   var overlap = altairCoverage(master);
   result.output("master", out);
   if (w.low) result.output("rejection_low", altairSave(w.low, job.output_dir + "/rejection_low.xisf"));
   if (w.high) result.output("rejection_high", altairSave(w.high, job.output_dir + "/rejection_high.xisf"));
   altairCloseAll(w);
   result.value.frames = frames;
   result.value.metrics = {
      frames: used.length, rejected: items.length - used.length, overlap_fraction: overlap,
      fwhm: altairMedian(frames.filter(function (f) { return f.used; }).map(function (f) { return f.fwhm; })),
      eccentricity: altairMedian(frames.filter(function (f) { return f.used; }).map(function (f) { return f.eccentricity; })),
      engine: "native", drizzle_scale: job.drizzle_scale
   };
}
