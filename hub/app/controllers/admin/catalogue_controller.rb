module Admin
  class CatalogueController < BaseController
    CATALOGUES = Catalogue::Downloader::IMPORTERS.keys

    def show
      @counts = AstroObject.group(:source).count
      @alias_count = ObjectAlias.count
      @statuses = CATALOGUES.index_with { |c| Rails.cache.read(CatalogueImportJob.status_key(c)) }
      @dynamic = DynamicCatalogue.all_sources
      @listed = DynamicCatalogueEntry.active.group(:dynamic_catalogue_id).count
    end

    def import
      catalogue = params[:catalogue].to_s
      return redirect_to(admin_catalogue_path, alert: "Unknown catalogue.") unless CATALOGUES.include?(catalogue)

      Rails.cache.write(CatalogueImportJob.status_key(catalogue), { "state" => "queued", "at" => Time.current.iso8601 })
      CatalogueImportJob.perform_later(catalogue)
      redirect_to admin_catalogue_path, notice: "#{catalogue.upcase} import queued."
    end

    # Refresh one dynamic catalogue now, rather than waiting for the schedule.
    def refresh
      source = Catalogue::Dynamic::SOURCES[params[:key].to_s]
      return redirect_to(admin_catalogue_path, alert: "Unknown list.") unless source
      return redirect_to(admin_catalogue_path, alert: "#{source::NAME} isn't configured.") unless source.configured?

      DynamicCatalogueRefreshJob.perform_later(source::KEY)
      redirect_to admin_catalogue_path, notice: "#{source::NAME} refresh queued."
    end
  end
end
