module Admin
  class CatalogueController < BaseController
    CATALOGUES = Catalogue::Downloader::IMPORTERS.keys

    def show
      @counts = AstroObject.group(:source).count
      @alias_count = ObjectAlias.count
      @statuses = CATALOGUES.index_with { |c| Rails.cache.read(CatalogueImportJob.status_key(c)) }
    end

    def import
      catalogue = params[:catalogue].to_s
      return redirect_to(admin_catalogue_path, alert: "Unknown catalogue.") unless CATALOGUES.include?(catalogue)

      Rails.cache.write(CatalogueImportJob.status_key(catalogue), { "state" => "queued", "at" => Time.current.iso8601 })
      CatalogueImportJob.perform_later(catalogue)
      redirect_to admin_catalogue_path, notice: "#{catalogue.upcase} import queued."
    end
  end
end
