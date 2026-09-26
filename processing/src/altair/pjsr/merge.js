// MERGE (SPEC §9.5, §9.6).
//
// master_merge. Phase "measure": all night masters in one
// SubframeSelector pass, so weights share one scale. Phase "integrate":
// write ALTNWGHT into temporary copies, LocalNormalization to the heaviest
// master, ImageIntegration with keyword weights and rangeLow = 0 (uncovered
// pixels excluded), coverage map, optional autocrop.
//
// frame_reintegration (one "integrate" phase): the calibrated subs of every
// eligible night are registered again to the same project reference (same
// geometry as their night stacks), measured, locally normalized to the
// reference and integrated together, ImageIntegration weighing each frame by
// PSF signal weight, with full-sample rejection; drizzled when the project's
// drizzle scale is above 1.
#include "lib/json_io.js"
#include "lib/measure.js"
#include "lib/integrate.js"

function altairMergeMeasure(job, result) {
   var measured = altairMeasure(job.nights.map(function (n) { return { sha256: n.sha256, path: n.path }; }));
   result.value.measurements = measured.map(function (m) {
      return { sha256: m.sha256, psf_signal_weight: m.psf_signal_weight, noise_sigma: m.noise_sigma, scale: 1.0, fwhm: m.fwhm };
   });
}

function altairCoverageMap(job, paths) {
   // Number of nights covering each pixel.
   var first = altairOpen(paths[0]);
   var width = first.mainView.image.width, height = first.mainView.image.height;
   first.forceClose();
   var cov = new ImageWindow(width, height, 1, 32, true, false, "coverage");
   var target = cov.mainView;
   target.beginProcess(UndoFlag_NoSwapFile);
   target.image.fill(0);
   var row = new Float32Array(width), acc = new Float32Array(width);
   for (var k = 0; k < paths.length; ++k) {
      var w = altairOpen(paths[k]);
      for (var y = 0; y < height; ++y) {
         w.mainView.image.getSamples(row, new Rect(0, y, width, y + 1), 0);
         target.image.getSamples(acc, new Rect(0, y, width, y + 1), 0);
         for (var x = 0; x < width; ++x)
            if (row[x] != 0) acc[x] += 1;
         target.image.setSamples(acc, new Rect(0, y, width, y + 1), 0);
      }
      w.forceClose();
   }
   target.endProcess();
   return cov;
}

function altairAutocrop(window, coverage, need) {
   var img = coverage.mainView.image, r = new Rect(img.width, img.height, 0, 0);
   var row = new Float32Array(img.width);
   for (var y = 0; y < img.height; ++y) {
      img.getSamples(row, new Rect(0, y, img.width, y + 1), 0);
      for (var x = 0; x < img.width; ++x)
         if (row[x] >= need) {
            if (x < r.x0) r.x0 = x; if (y < r.y0) r.y0 = y;
            if (x + 1 > r.x1) r.x1 = x + 1; if (y + 1 > r.y1) r.y1 = y + 1;
         }
   }
   if (r.x1 > r.x0 && r.y1 > r.y0) {
      var view = window.mainView;
      view.beginProcess();
      view.image.cropTo(r);
      view.endProcess();
   }
}

function altairReintegrate(job, result) {
   var frames = job.calibrated_frames;
   if (!frames || frames.length == 0)
      throw new Error("frame reintegration without calibrated frames");
   var S = new StarAlignment;
   S.referenceImage = job.reference.path;
   S.referenceIsFile = true;
   S.targets = frames.map(function (f) { return [true, true, f.path]; });
   S.outputDirectory = job.work_dir;
   S.outputPostfix = "_r";
   S.overwriteExistingFiles = true;
   S.generateDrizzleData = job.drizzle_scale > 1;
   if (!S.executeGlobal())
      throw new Error("StarAlignment failed");
   var items = [];
   for (var i = 0; i < frames.length; ++i)
      if (S.outputData[i] && S.outputData[i][0])
         items.push({ sha256: frames[i].sha256, night: frames[i].night_sha256, path: S.outputData[i][0], drizzle: S.outputData[i][1] || "" });
   if (items.length < 2)
      throw new Error("fewer than 2 frames could be registered to the reference");
   var measured = altairMeasure(items);
   var nightWeights = {};
   for (var k = 0; k < items.length; ++k)
      if (measured[k].measured && items[k].night)
         nightWeights[items[k].night] = (nightWeights[items[k].night] || 0) + measured[k].psf_signal_weight;
   var L = new LocalNormalization;
   L.referencePathOrViewId = job.reference.path;
   L.referenceIsView = false;
   L.targetItems = items.map(function (it) { return [true, it.path]; });
   L.outputDirectory = job.work_dir;
   L.generateNormalizedImages = LocalNormalization.prototype.GenerateNormalizedImages_Never;
   L.overwriteExistingFiles = true;
   if (!L.executeGlobal())
      throw new Error("LocalNormalization failed");
   var ln = items.map(function (it) { return File.changeExtension(it.path, ".xnml"); });
   var w = altairIntegrate({
      images: items.map(function (it) { return it.path; }), lnFiles: ln,
      drizzleFiles: items.map(function (it) { return it.drizzle; }),
      weightMode: ImageIntegration.prototype.PSFSignalWeight,
      normalization: ImageIntegration.prototype.LocalNormalization,
      rejection: items.length >= 25 ? ImageIntegration.prototype.Rejection_ESD
               : items.length >= 8 ? ImageIntegration.prototype.WinsorizedSigmaClip : ImageIntegration.prototype.PercentileClip,
      rejectionNormalization: ImageIntegration.prototype.LocalRejectionNormalization,
      rangeClipLow: true, rejectionMaps: true, updateDrizzle: job.drizzle_scale > 1
   });
   if (job.drizzle_scale > 1) {
      var drizzled = altairDrizzle(items.map(function (it) { return it.drizzle; }), ln, job.drizzle_scale);
      w.integration.forceClose();
      w.integration = drizzled;
   }
   // Coverage per night: a night covers a pixel if its night master does.
   var coverage = altairCoverageMap(job, job.nights.map(function (n) { return n.path; }));
   result.output("coverage", altairSave(coverage, job.output_dir + "/coverage.xisf"));
   if (job.autocrop)
      altairAutocrop(w.integration, coverage, job.min_coverage_nights == "all" ? job.nights.length : job.min_coverage_nights);
   coverage.forceClose();
   result.output("master", altairSave(w.integration, job.output_dir + "/multi_night_master.xisf"));
   if (w.low) result.output("rejection_low", altairSave(w.low, job.output_dir + "/rejection_low.xisf"));
   if (w.high) result.output("rejection_high", altairSave(w.high, job.output_dir + "/rejection_high.xisf"));
   altairCloseAll(w);
   result.value.metrics = { nights: job.nights.length, frames: items.length, night_weights: nightWeights, mode: "frame_reintegration" };
}

function altairMergeIntegrate(job, result) {
   if (job.mode == "frame_reintegration")
      return altairReintegrate(job, result);
   var copies = [];
   for (var i = 0; i < job.nights.length; ++i) {
      var n = job.nights[i];
      var w = altairOpen(n.path);
      altairSetKeyword(w, "ALTNWGHT", job.weights[n.sha256], "Altair night weight (" + job.weighting + ")");
      copies.push(altairSave(w, job.work_dir + "/weighted_" + n.night + ".xisf"));   // never the canonical file
      w.forceClose();
   }
   var reference = null;
   for (var j = 0; j < job.nights.length; ++j)
      if (job.nights[j].sha256 == job.normalization_reference)
         reference = copies[j];
   var ln = [];
   var normalization = ImageIntegration.prototype.AdditiveWithScaling;
   if (job.normalization == "local") {
      var L = new LocalNormalization;
      L.referencePathOrViewId = reference;
      L.referenceIsView = false;
      L.targetItems = copies.map(function (p) { return [true, p]; });
      L.outputDirectory = job.work_dir;
      L.generateNormalizedImages = LocalNormalization.prototype.GenerateNormalizedImages_Never;
      L.overwriteExistingFiles = true;
      if (!L.executeGlobal())
         throw new Error("LocalNormalization failed");
      ln = copies.map(function (p) { return File.changeExtension(p, ".xnml"); });
      normalization = ImageIntegration.prototype.LocalNormalization;
   }
   var w = altairIntegrate({
      images: copies, lnFiles: ln,
      weightMode: ImageIntegration.prototype.KeywordWeight, weightKeyword: "ALTNWGHT",
      normalization: normalization,
      rejection: job.rejection == "winsorized_sigma" ? ImageIntegration.prototype.WinsorizedSigmaClip : ImageIntegration.prototype.NoRejection,
      rangeClipLow: true, rejectionMaps: job.rejection != "none"
   });
   var coverage = altairCoverageMap(job, copies);
   result.output("coverage", altairSave(coverage, job.output_dir + "/coverage.xisf"));
   if (job.autocrop)   // to the pixels covered by at least min_coverage_nights nights
      altairAutocrop(w.integration, coverage, job.min_coverage_nights == "all" ? copies.length : job.min_coverage_nights);
   coverage.forceClose();
   result.output("master", altairSave(w.integration, job.output_dir + "/multi_night_master.xisf"));
   if (w.low) result.output("rejection_low", altairSave(w.low, job.output_dir + "/rejection_low.xisf"));
   if (w.high) result.output("rejection_high", altairSave(w.high, job.output_dir + "/rejection_high.xisf"));
   altairCloseAll(w);
   result.value.metrics = { nights: copies.length };
}
