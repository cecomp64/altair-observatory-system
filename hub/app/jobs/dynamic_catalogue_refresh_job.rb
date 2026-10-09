# Refreshes dynamic catalogues (Catalogue::Dynamic::SOURCES): every
# configured one on the schedule in config/recurring.yml, or one from
# /admin/catalogue, and again after a rate limit if part of a list was
# deferred. The outcome is kept on the DynamicCatalogue row.
class DynamicCatalogueRefreshJob < ApplicationJob
  queue_as :default

  def perform(key = nil)
    keys = key ? [ key ] : Catalogue::Dynamic::SOURCES.select { |_, source| source.configured? }.keys
    keys.each { |k| refresh(k) }
  end

  private

  def refresh(key)
    catalogue = DynamicCatalogue.for(key)
    catalogue.update!(attempted_at: Time.current)
    refresher = Catalogue::Dynamic::Refresher.new(catalogue)
    result = refresher.refresh
    # Part of the list was deferred (rate limited): pick it up when allowed.
    retry_after = refresher.fetcher.try(:retry_after)
    self.class.set(wait: retry_after.seconds).perform_later(key) if result["deferred"].to_i.positive? && retry_after
  rescue StandardError => e
    Rails.logger.warn("[catalogue] refreshing #{key}: #{e.class}: #{e.message}")
    catalogue&.update_columns(last_error: e.message.truncate(250), updated_at: Time.current)
  end
end
