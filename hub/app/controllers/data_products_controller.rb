# GET /data_products/:id/download: redirects to a short-lived presigned link
# for a master in Altair's S3 archive (§12). Raw frames are never offered.
class DataProductsController < ApplicationController
  def download
    product = DataProduct.find(params[:id])
    authorize product
    redirect_to Archive::Presigner.default.url_for(product), allow_other_host: true
  rescue Archive::Presigner::NotDownloadable => e
    redirect_back fallback_location: target_path(product.target), alert: "Download unavailable: #{e.message}."
  end
end
