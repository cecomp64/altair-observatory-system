module Catalogue
  # Dynamic catalogues: lists that change on their own (AAVSO alerts and
  # campaigns, bright comets...) and are refreshed on a schedule by
  # DynamicCatalogueRefreshJob. A source class has KEY, NAME, SHORT_NAME,
  # DESCRIPTION, SETUP (how to configure it), SOURCE (the astro_objects.source
  # of objects it creates), .configured? and #fetch, which returns Records or
  # raises. It may also have #report (counts added to the refresh result) and
  # #retry_after (seconds; the job refreshes again then when the last fetch
  # deferred part of the list, e.g. because it was rate limited). LIVE_ATTRIBUTES
  # names astro_objects columns the refresh overwrites on objects already in
  # the catalogue (an import only fills gaps), e.g. a comet's position.
  module Dynamic
    # aliases: [[name, catalog], ...]; attributes: astro_objects columns;
    # details: what the list says about the object, kept on its entry.
    Record = Struct.new(:primary_name, :aliases, :attributes, :details, keyword_init: true)

    SOURCES = { AavsoCampaigns::KEY => AavsoCampaigns, BrightComets::KEY => BrightComets }.freeze
  end
end
