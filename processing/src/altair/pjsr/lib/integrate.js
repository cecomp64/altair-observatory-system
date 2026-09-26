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
   P.generateDrizzleData = false;
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

function altairCloseAll(windows) {
   for (var k in windows)
      if (windows[k] && !windows[k].isNull)
         windows[k].forceClose();
}

#endif
