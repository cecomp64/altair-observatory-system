# GET /data_products/:id/download: redirects to a short-lived presigned link
# for a master in Altair's S3 archive (§12). Raw frames are never offered.
# GET /data_products/:id/report: Altair's Markdown report, rendered.
class DataProductsController < ApplicationController
  def report
    @product = DataProduct.find(params[:id])
    authorize @product
    raise ActiveRecord::RecordNotFound unless @product.report.attached?

    @html = ReportRenderer.render(@product.report.download.force_encoding(Encoding::UTF_8))
  end

  def download
    product = DataProduct.find(params[:id])
    authorize product
    redirect_to Archive::Presigner.default.url_for(product), allow_other_host: true
  rescue Archive::Presigner::NotDownloadable => e
    redirect_back fallback_location: target_path(product.target), alert: "Download unavailable: #{e.message}."
  end
end
