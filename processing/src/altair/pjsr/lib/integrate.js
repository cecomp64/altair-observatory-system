// ImageIntegration wrapper shared by masters, night stacks and merges.
#ifndef __ALTAIR_INTEGRATE__
#define __ALTAIR_INTEGRATE__

// opts: {images: [path], lnFiles: [path|""], drizzleFiles: [path|""], rejection, weightMode, weightKeyword,
//        normalization, rangeClipLow, rejectionMaps, evaluateNoise}
function altairIntegrate(opts) {
   var P = new ImageIntegration;
   P.images = opts.images.map(function (path, i) {
      return [true, path, (opts.drizzleFiles || [])[i] || "", (opts.lnFiles || [])[i] || ""];
   });
   P.combination = ImageIntegration.prototype.Average;
   P.weightMode = opts.weightMode === undefined ? ImageIntegration.prototype.DontCare : opts.weightMode;
   if (opts.weightKeyword)
      P.weightKeyword = opts.weightKeyword;
   P.normalization = opts.normalization === undefined ? ImageIntegration.prototype.NoNormalization : opts.normalization;
   P.rejection = opts.rejection === undefined ? ImageIntegration.prototype.NoRejection : opts.rejection;
   P.rejectionNormalization = opts.rejectionNormalization === undefined ? ImageIntegration.prototype.Scale : opts.rejectionNormalization;
   P.rangeClipLow = !!opts.rangeClipLow;
   P.rangeLow = 0;
   P.generateRejectionMaps = !!opts.rejectionMaps;
   P.generateIntegratedImage = true;
   // With drizzle, ImageIntegration writes its rejection data into the .xdrz files for DrizzleIntegration.
   P.generateDrizzleData = !!opts.updateDrizzle;
   P.evaluateSNR = true;
   P.useCache = false;
   if (!P.executeGlobal())
      throw new Error("ImageIntegration failed");
   var out = { integration: ImageWindow.windowById(P.integrationImageId) };
   if (opts.rejectionMaps) {
      out.low = ImageWindow.windowById(P.lowRejectionMapImageId);
      out.high = ImageWindow.windowById(P.highRejectionMapImageId);
   }
   return out;
}

// DrizzleIntegration of registered frames' .xdrz files (after ImageIntegration updated them)
// at the project's drizzle scale (SPEC §9.2: one scale per project).
function altairDrizzle(xdrzFiles, lnFiles, scale) {
   var D = new DrizzleIntegration;
   D.inputData = xdrzFiles.map(function (path, i) { return [true, path, (lnFiles || [])[i] || ""]; });
   D.scale = scale;
   D.dropShrink = 0.90;
   D.kernelFunction = DrizzleIntegration.prototype.Kernel_Square;
   D.enableRejection = true;
   D.enableImageWeighting = true;
   D.enableLocalNormalization = !!(lnFiles && lnFiles.length);
   D.useROI = false;
   if (!D.executeGlobal())
      throw new Error("DrizzleIntegration failed");
   var w = ImageWindow.windowById(D.integrationImageId);
   if (!w || w.isNull)
      throw new Error("DrizzleIntegration produced no image");
   var weights = D.weightImageId ? ImageWindow.windowById(D.weightImageId) : null;
   if (weights && !weights.isNull)
      weights.forceClose();
   return w;
}

function altairCloseAll(windows) {
   for (var k in windows)
      if (windows[k] && !windows[k].isNull)
         windows[k].forceClose();
}

#endif
